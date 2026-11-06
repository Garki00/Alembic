# tools/files.py
"""文件读写工具：让工作区从「提示词里的一句话」变成 agent 真能操作的地方。

安全边界：
- 所有路径都经过 :func:`tools.paths.resolve_in_workspace`，越界（含 symlink
  与 ``..`` 逃逸）一律拒绝；
- 覆盖已有文件前必须先读过它（见 :func:`_check_read`），避免模型在没看到
  原文的情况下整文件覆盖；
- 单次输出按 ``ctx.max_output_chars`` 截断，防止一次读大文件撑爆上下文。
"""

from __future__ import annotations

import fnmatch
import os
import re
import threading
from pathlib import Path

from langchain.tools import tool

from tools.context import ToolContext
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace

# ---------------------------------------------------------------- 读后写校验
# path -> (mtime_ns, 是否完整读过)。进程内共享，允许跨轮次、跨工具使用。
_READ_STATE: dict[str, tuple[int, bool]] = {}
_READ_LOCK = threading.Lock()

_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".tmp",
}

_MAX_READ_LIMIT = 2000
_MAX_LIST_ENTRIES = 300
_MAX_SCAN_FILES = 3000


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _mark_read(path: Path, *, complete: bool) -> None:
    with _READ_LOCK:
        _READ_STATE[str(path)] = (_mtime_ns(path), complete)


def _check_read(path: Path) -> tuple[str | None, bool]:
    """检查写前是否读过该文件。返回 (错误信息, 是否只读过一部分)。"""
    with _READ_LOCK:
        entry = _READ_STATE.get(str(path))
    if entry is None:
        return (
            tool_error(
                "read_required",
                f"写入前必须先读取 {path.name} 的现有内容。",
                hint="请先调用 read_file 查看原文，再决定是整文件覆盖（write_file）还是局部替换（edit_file）。",
            ),
            False,
        )
    mtime, complete = entry
    if mtime != _mtime_ns(path):
        return (
            tool_error(
                "stale_read",
                f"{path.name} 在你上次读取之后已被修改。",
                hint="请重新 read_file 获取最新内容，再执行写入，避免覆盖别人的改动。",
            ),
            False,
        )
    return None, not complete


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(8192)
    except OSError:
        return False


def _as_error(exc: WorkspacePathError) -> str:
    return tool_error(exc.code, exc.message, hint=exc.hint)


def _resolve_or_none(ctx: ToolContext, raw: str) -> Path | str:
    """解析路径；失败时直接返回错误字符串，省掉每处的 try/except。"""
    try:
        return resolve_in_workspace(ctx.workspace, raw)
    except WorkspacePathError as exc:
        return _as_error(exc)


# --------------------------------------------------------------------- 只读组
def build_file_read_tools(ctx: ToolContext) -> list:
    """目录浏览、文件读取、内容搜索（不修改磁盘）。"""

    @tool
    def list_dir(path: str = ".") -> str:
        """列出工作区内某个目录的文件与子目录。

        Args:
            path: 相对工作区的目录路径，默认 "." 表示工作区根目录。

        Returns:
            每行一项：目录以 "/" 结尾，文件附大小；超出上限时说明省略数量。
        """
        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"目录不存在：{path}。", hint="可以先用 list_dir(\".\") 查看根目录结构。")
        if not target.is_dir():
            return tool_error("not_a_directory", f"{path} 不是目录。", hint="读取文件内容请用 read_file。")

        entries = []
        try:
            for child in target.iterdir():
                try:
                    is_dir = child.is_dir()
                    size = 0 if is_dir else child.stat().st_size
                except OSError:
                    is_dir, size = False, 0
                entries.append((0 if is_dir else 1, child.name.lower(), child.name, is_dir, size))
        except OSError as exc:
            return tool_error("list_failed", f"无法列出目录 {path}：{exc}。")

        entries.sort()
        shown = entries[:_MAX_LIST_ENTRIES]
        lines = [f"目录：{display_path(ctx.workspace, target)}（{len(entries)} 项）"]
        for _, _, name, is_dir, size in shown:
            lines.append(f"{name}/" if is_dir else f"{name}  ({_format_size(size)})")
        if len(entries) > len(shown):
            lines.append(f"…还有 {len(entries) - len(shown)} 项未列出，请缩小目录范围。")
        return truncate("\n".join(lines), ctx.max_output_chars)

    @tool
    def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
        """读取工作区内文本文件的内容，带行号。

        Args:
            path: 相对工作区的文件路径。
            offset: 起始行号，从 1 开始。
            limit: 本次最多读取的行数（上限 2000），大文件请分段读取。

        Returns:
            首行为文件概况，随后是 "行号| 内容"；文件更长时提示下一段的 offset。
        """
        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"文件不存在：{path}。", hint="可以用 list_dir 或 search_files 定位文件。")
        if target.is_dir():
            return tool_error("is_directory", f"{path} 是目录。", hint="用 list_dir 查看目录内容。")
        if _looks_binary(target):
            return tool_error("binary_file", f"{path} 看起来是二进制文件，无法按文本读取。")

        try:
            text = _read_text(target)
        except OSError as exc:
            return tool_error("read_failed", f"读取 {path} 失败：{exc}。")

        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            _mark_read(target, complete=True)
            return f"文件：{display_path(ctx.workspace, target)}（空文件，0 行）"

        start = max(1, int(offset or 1))
        take = max(1, min(int(limit or 200), _MAX_READ_LIMIT))
        if start > total:
            return tool_error(
                "offset_out_of_range",
                f"起始行 {start} 超过文件总行数 {total}。",
                hint=f"该文件共 {total} 行，请把 offset 设在 1..{total} 之间。",
            )
        end = min(total, start + take - 1)
        complete = start == 1 and end == total
        _mark_read(target, complete=complete)

        width = len(str(end))
        body = [f"{index:>{width}}| {lines[index - 1]}" for index in range(start, end + 1)]
        header = (
            f"文件：{display_path(ctx.workspace, target)}"
            f"（共 {total} 行，本次第 {start}-{end} 行）"
        )
        footer = []
        if end < total:
            footer.append(f"…还有 {total - end} 行，用 offset={end + 1} 继续读取。")
        return truncate("\n".join([header, *body, *footer]), ctx.max_output_chars)

    @tool
    def search_files(pattern: str, path: str = ".", glob: str = "", max_results: int = 50) -> str:
        """按正则表达式搜索工作区内文件的内容，返回匹配行。

        Args:
            pattern: Python 正则表达式，例如 "def build_"、"TODO|FIXME"。
            path: 搜索起点（文件或目录），默认工作区根目录。
            glob: 只搜索匹配该通配符的文件名，例如 "*.py"；留空表示全部。
            max_results: 最多返回的匹配行数（上限 200）。

        Returns:
            每行 "路径:行号: 内容"；没有匹配时说明已搜索的文件数。
        """
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return tool_error("bad_pattern", f"正则表达式无法编译：{exc}。", hint="请检查括号、方括号是否配对。")

        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"搜索路径不存在：{path}。")

        limit = max(1, min(int(max_results or 50), 200))
        matches: list[str] = []
        scanned = 0
        skipped_binary = 0

        def scan(file_path: Path) -> bool:
            """扫描单个文件；返回 False 表示已经凑够结果。"""
            nonlocal skipped_binary
            if _looks_binary(file_path):
                skipped_binary += 1
                return True
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                    for lineno, line in enumerate(handle, 1):
                        if regex.search(line):
                            shown = line.strip()
                            if len(shown) > 200:
                                shown = shown[:200] + "…"
                            matches.append(
                                f"{display_path(ctx.workspace, file_path)}:{lineno}: {shown}"
                            )
                            if len(matches) >= limit:
                                return False
            except OSError:
                pass
            return True

        if target.is_file():
            scanned = 1
            scan(target)
        else:
            stop = False
            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for name in files:
                    if glob and not fnmatch.fnmatch(name, glob):
                        continue
                    scanned += 1
                    if scanned > _MAX_SCAN_FILES or not scan(Path(root) / name):
                        stop = True
                        break
                if stop:
                    break

        if not matches:
            return (
                f"没有匹配 {pattern!r} 的内容（已搜索 {scanned} 个文件"
                f"{f'，跳过 {skipped_binary} 个二进制文件' if skipped_binary else ''}）。"
            )
        header = f"匹配 {pattern!r}：共 {len(matches)} 行（已搜索 {scanned} 个文件）"
        return truncate("\n".join([header, *matches]), ctx.max_output_chars)

    return [list_dir, read_file, search_files]


# --------------------------------------------------------------------- 写入组
def build_file_write_tools(ctx: ToolContext) -> list:
    """整文件写入与局部替换（都会落盘，受工作区与读后写约束）。"""

    @tool
    def write_file(path: str, content: str) -> str:
        """把内容完整写入文件（覆盖已有内容，必要时创建目录）。

        覆盖已有文件前，必须先调用 read_file 读过它。

        Args:
            path: 相对工作区的文件路径。
            content: 完整的文件内容（不是增量补丁；局部修改请用 edit_file）。

        Returns:
            写入结果的路径、行数与字节数。
        """
        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if target.is_dir():
            return tool_error("is_directory", f"{path} 是目录，不能写入。", hint="请给出具体的文件名。")

        existed = target.exists()
        partial_warning = False
        if existed:
            error, partial = _check_read(target)
            if error:
                return error
            partial_warning = partial

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except OSError as exc:
            return tool_error("write_failed", f"写入 {path} 失败：{exc}。")

        _mark_read(target, complete=True)
        action = "已覆盖" if existed else "已创建"
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        result = [
            f"{action}：{display_path(ctx.workspace, target)}",
            f"行数：{lines}，字节数：{len(content.encode('utf-8'))}",
        ]
        if partial_warning:
            result.append("注意：上次只读取了该文件的局部内容，覆盖写入可能丢失未读部分。")
        return "\n".join(result)

    @tool
    def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """用精确字符串替换修改文件的局部内容。

        编辑前必须先 read_file 读该文件。``old_string`` 必须在文件中唯一出现，
        否则请补足上下文或改用 ``replace_all=True``。

        Args:
            path: 相对工作区的文件路径。
            old_string: 要被替换的原文（含缩进，必须精确匹配）。
            new_string: 替换后的新文本。
            replace_all: 为 True 时替换所有出现位置。

        Returns:
            替换处数与文件路径。
        """
        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"文件不存在：{path}。", hint="新建文件请用 write_file。")
        if target.is_dir():
            return tool_error("is_directory", f"{path} 是目录。")
        if not old_string:
            return tool_error("empty_old_string", "old_string 不能为空。")
        if old_string == new_string:
            return tool_error("no_change", "old_string 与 new_string 相同，没有需要修改的内容。")

        error, _ = _check_read(target)
        if error:
            return error

        try:
            text = _read_text(target)
        except OSError as exc:
            return tool_error("read_failed", f"读取 {path} 失败：{exc}。")

        count = text.count(old_string)
        if count == 0:
            return tool_error(
                "not_found",
                f"在 {path} 中没有找到 old_string 指定的内容。",
                hint="请先用 read_file 确认原文（缩进、空格、换行都要一致），再重试。",
            )
        if count > 1 and not replace_all:
            positions = []
            start = 0
            for _ in range(min(count, 5)):
                index = text.find(old_string, start)
                positions.append(text[:index].count("\n") + 1)
                start = index + len(old_string)
            return tool_error(
                "ambiguous_match",
                f"old_string 在 {path} 中出现了 {count} 次（行 {', '.join(map(str, positions))}），无法确定改哪一处。",
                hint="请补足上下文使其唯一，或传 replace_all=True 替换全部。",
            )

        updated = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        try:
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(updated)
        except OSError as exc:
            return tool_error("write_failed", f"写入 {path} 失败：{exc}。")

        _mark_read(target, complete=True)
        replaced = count if replace_all else 1
        return f"已修改：{display_path(ctx.workspace, target)}，替换 {replaced} 处。"

    return [write_file, edit_file]
