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
import shutil
import threading
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from langchain.tools import tool

from tools.context import ToolContext
from tools.errors import tool_error, truncate
from tools.paths import (
    WorkspacePathError,
    display_path,
    is_protected_read,
    resolve_in_workspace,
)

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


def _diff_stats(before: str, after: str) -> tuple[int, int]:
    """按行统计新增 / 删除，用于对话结束后的修改摘要。"""
    old_lines = before.splitlines()
    new_lines = after.splitlines()
    added = removed = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag in {"replace", "delete"}:
            removed += i2 - i1
        if tag in {"replace", "insert"}:
            added += j2 - j1
    return added, removed


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(8192)
    except OSError:
        return False


def _glob_match(relative: str, name: str, pattern: str) -> bool:
    """文件名 / 相对路径的 glob 匹配，支持 ``**/`` 与无通配符的子串写法。"""
    text = (pattern or "").strip().replace("\\", "/")
    if not text:
        return False
    if not any(char in text for char in "*?[]"):
        return text.lower() in name.lower()
    if fnmatch.fnmatch(relative, text) or fnmatch.fnmatch(name, text):
        return True
    # ``**/*.py`` 在 fnmatch 里要求至少一级目录；根目录下的 a.py 也应当命中
    if text.startswith("**/"):
        rest = text[3:]
        return fnmatch.fnmatch(relative, rest) or fnmatch.fnmatch(name, rest)
    return False


def _as_error(exc: WorkspacePathError) -> str:
    return tool_error(exc.code, exc.message, hint=exc.hint)


def _resolve_or_none(ctx: ToolContext, raw: str, *, access: str = "read") -> Path | str:
    """解析路径；失败时直接返回错误字符串，省掉每处的 try/except。"""
    try:
        return resolve_in_workspace(ctx.workspace, raw, access=access)
    except WorkspacePathError as exc:
        return _as_error(exc)


def _protected_reason(target: Path, root: Path | None) -> str:
    """返回不能删除/覆盖的原因；空字符串表示允许。"""
    if root is None:
        return ""
    if target == root:
        return "工作区根目录"
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return ""
    if any(part.lower() == ".git" for part in parts):
        return ".git 目录"
    return ""


def _ensure_parent(target: Path) -> str | None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return tool_error("write_failed", f"无法创建目录 {target.parent}：{exc}。")
    return None


def _check_destination(destination: Path, overwrite: bool) -> str | None:
    """检查目标是否可写；覆盖已有文件时沿用「读后写」约束。"""
    if not destination.exists():
        return None
    if not overwrite:
        return tool_error(
            "destination_exists",
            f"目标已存在：{destination.name}。",
            hint="换一个目标名，或传 overwrite=True（覆盖前必须先 read_file）。",
        )
    if destination.is_dir():
        return tool_error(
            "destination_is_directory",
            f"目标 {destination.name} 是目录，不会自动覆盖目录。",
            hint="请先删除或改名该目录，再重试。",
        )
    error, _partial = _check_read(destination)
    if error:
        return error
    return None


# --------------------------------------------------------------------- 只读组
def build_file_read_tools(ctx: ToolContext) -> list:
    """目录浏览、文件读取、内容搜索（不修改磁盘）。"""

    @tool
    def list_dir(path: str = ".") -> str:
        """List the files and subdirectories in a directory within the workspace.

        Args:
            path: The directory path relative to the workspace; defaults to "." for the workspace root.

        Returns:
            One item per line: directories end with "/", files include their size; when over the limit,
            the number of omitted entries is noted.
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
        """Read the contents of a text file within the workspace, with line numbers.

        Args:
            path: The file path relative to the workspace.
            offset: The starting line number, beginning at 1.
            limit: The maximum number of lines to read this time (upper limit 2000); for large files, read in segments.

        Returns:
            The first line is a file overview, followed by "line number| content"; when the file is longer,
            a hint for the next segment's offset is provided.
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
        """Search file contents within the workspace using a regular expression, returning matching lines.

        Args:
            pattern: A Python regular expression, e.g. "def build_" or "TODO|FIXME".
            path: The search starting point (file or directory); defaults to the workspace root.
            glob: Only search file names matching this wildcard, e.g. "*.py"; leave empty to search all.
            max_results: The maximum number of matching lines to return (upper limit 200).

        Returns:
            Each line is "path:line number: content"; when there are no matches, the number of files searched
            is reported.
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
            if is_protected_read(file_path, ctx.workspace):
                return True
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

    @tool
    def find_files(pattern: str, path: str = ".", max_results: int = 100) -> str:
        """Find files by file name or relative path, supporting wildcards (e.g. *.py, **/*.test.ts).

        Difference from search_files: search_files searches file **contents**, while this tool matches only by
        path/file name, making it suitable for locating a file when you don't know where it is.

        Args:
            pattern: A glob for the file name or relative path; when it contains no wildcard, matches by file name substring.
            path: The search starting point (directory); defaults to the workspace root.
            max_results: The maximum number of entries to return (upper limit 300).

        Returns:
            One relative path and size per line; when there are no matches, the number of files scanned is reported.
        """
        text = (pattern or "").strip()
        if not text:
            return tool_error("empty_pattern", "pattern 不能为空。", hint="例如 *.py 或 **/*.test.ts。")
        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"查找路径不存在：{path}。")
        if not target.is_dir():
            return tool_error("not_a_directory", f"{path} 不是目录。", hint="查找起点需要是目录。")

        limit = max(1, min(int(max_results or 100), 300))
        found: list[tuple[str, int]] = []
        scanned = 0
        truncated = False
        try:
            for root, dirs, files in os.walk(target):
                dirs[:] = sorted(item for item in dirs if item not in _SKIP_DIRS)
                for name in sorted(files):
                    scanned += 1
                    if scanned > _MAX_SCAN_FILES:
                        truncated = True
                        break
                    if not _glob_match(
                        (Path(root) / name).relative_to(target).as_posix(),
                        name,
                        text,
                    ):
                        continue
                    try:
                        size = (Path(root) / name).stat().st_size
                    except OSError:
                        size = 0
                    found.append(((Path(root) / name).relative_to(target).as_posix(), size))
                    if len(found) >= limit:
                        truncated = True
                        break
                if truncated:
                    break
        except OSError as exc:
            return tool_error("search_failed", f"查找失败：{exc}。")

        shown = found[:limit]
        header = f"匹配 {text}：找到 {len(shown)} 个文件（已扫描 {scanned} 个）"
        if not shown:
            return header + "\n没有匹配的文件。"
        lines = [header]
        for relative, size in shown:
            lines.append(f"{relative}  ({_format_size(size)})")
        if truncated:
            lines.append(f"…结果或扫描范围已达到上限（{limit} 条 / {_MAX_SCAN_FILES} 个文件），请缩小查找范围。")
        return truncate("\n".join(lines), ctx.max_output_chars)

    @tool
    def file_stat(path: str = ".") -> str:
        """View metadata for a file or directory: type, size, modification time, and line count.

        Suitable for checking the size before reading a large file, or confirming whether a path still exists.

        Args:
            path: The file or directory path relative to the workspace; defaults to "." for the workspace root.

        Returns:
            Type, size, and modification time; for text files, the line count is also provided.
        """
        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"路径不存在：{path}。")
        try:
            info = target.stat()
        except OSError as exc:
            return tool_error("stat_failed", f"无法读取 {path} 的信息：{exc}。")

        kind = "目录" if target.is_dir() else "文件"
        lines = [
            f"路径：{display_path(ctx.workspace, target)}",
            f"类型：{kind}",
            f"大小：{_format_size(info.st_size)}（{info.st_size} 字节）",
            f"修改时间：{datetime.fromtimestamp(info.st_mtime):%Y-%m-%d %H:%M:%S}",
        ]
        if target.is_file():
            lines.append(f"后缀：{target.suffix.lower() or '(无)'}")
            if not _looks_binary(target) and info.st_size <= 2_000_000:
                try:
                    lines.append(f"行数：{len(_read_text(target).splitlines())}")
                except OSError:
                    pass
        return "\n".join(lines)

    return [list_dir, read_file, search_files, find_files, file_stat]


# --------------------------------------------------------------------- 写入组
def build_file_write_tools(ctx: ToolContext) -> list:
    """整文件写入与局部替换（都会落盘，受工作区与读后写约束）。"""

    @tool
    def write_file(path: str, content: str) -> str:
        """Write content fully to a file (overwriting existing content, creating directories if necessary).

        Before overwriting an existing file, you must first read it by calling read_file.

        Args:
            path: The file path relative to the workspace.
            content: The complete file content (not an incremental patch; for partial modifications, use edit_file).

        Returns:
            The path, line count, and byte count of the write result.
        """
        target = _resolve_or_none(ctx, path, access="write")
        if isinstance(target, str):
            return target
        if target.is_dir():
            return tool_error("is_directory", f"{path} 是目录，不能写入。", hint="请给出具体的文件名。")
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"不能写入{reason}：{path}。")

        existed = target.exists()
        partial_warning = False
        old_text = ""
        if existed:
            error, partial = _check_read(target)
            if error:
                return error
            partial_warning = partial
            try:
                old_text = _read_text(target)
            except OSError:
                old_text = ""

        payload = content.encode("utf-8")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                handle.write(payload)
        except OSError as exc:
            return tool_error("write_failed", f"写入 {path} 失败：{exc}。")

        # 落盘后立刻回读元数据：工具返回值只能是「真的写成功」，
        # 不能让模型用一句“已保存”代替事实（见 write_unverified）。
        try:
            actual = target.stat().st_size
        except OSError as exc:
            return tool_error("write_unverified", f"写入 {path} 后无法确认文件状态：{exc}。")
        if actual != len(payload):
            return tool_error(
                "write_unverified",
                f"{path} 写入后大小不一致（磁盘 {actual} 字节，预期 {len(payload)} 字节）。",
                hint="请用 file_stat 检查文件，再重试写入。",
            )

        _mark_read(target, complete=True)
        action = "已覆盖" if existed else "已创建"
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        added, removed = _diff_stats(old_text, content)
        result = [
            f"{action}：{display_path(ctx.workspace, target)}",
            f"行数：{lines}，字节数：{actual}",
            f"变更：+{added} -{removed}",
            "校验：文件已存在，大小与写入内容一致。",
        ]
        if partial_warning:
            result.append("注意：上次只读取了该文件的局部内容，覆盖写入可能丢失未读部分。")
        return "\n".join(result)

    @tool
    def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """Modify part of a file's content by exact string replacement.

        Before editing, you must first read the file with read_file. ``old_string`` must appear uniquely in the file;
        otherwise, add more surrounding context or use ``replace_all=True`` instead.

        Args:
            path: The file path relative to the workspace.
            old_string: The original text to be replaced (including indentation; must match exactly).
            new_string: The new text to replace it with.
            replace_all: When True, replace all occurrences.

        Returns:
            The number of replacements and the file path.
        """
        target = _resolve_or_none(ctx, path, access="write")
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"文件不存在：{path}。", hint="新建文件请用 write_file。")
        if target.is_dir():
            return tool_error("is_directory", f"{path} 是目录。")
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"不能修改{reason}：{path}。")
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
        added, removed = _diff_stats(text, updated)
        return (
            f"已修改：{display_path(ctx.workspace, target)}，替换 {replaced} 处。\n"
            f"变更：+{added} -{removed}"
        )

    @tool
    def make_dir(path: str) -> str:
        """Create a directory within the workspace (creating multiple parent levels if necessary).

        Args:
            path: The directory path relative to the workspace.

        Returns:
            The creation result; if it already exists and is a directory, a notice is returned instead of a failure.
        """
        target = _resolve_or_none(ctx, path, access="write")
        if isinstance(target, str):
            return target
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"不能在{reason}下创建目录：{path}。")
        if target.exists():
            if target.is_dir():
                return f"目录已存在：{display_path(ctx.workspace, target)}"
            return tool_error("not_a_directory", f"{path} 已存在且是文件。", hint="换一个目录名。")
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return tool_error("write_failed", f"创建目录 {path} 失败：{exc}。")
        return f"已创建目录：{display_path(ctx.workspace, target)}"

    @tool
    def delete_file(path: str, recursive: bool = False) -> str:
        """Delete a file or directory inside the workspace. Deletion is
        permanent and cannot be undone.

        This tool is destructive. Before calling it, make sure the resolved
        target is exactly what the user intends to remove — confirm with the
        user when the request is ambiguous, and be aware that relative paths
        may resolve differently than expected. Do not infer targets from
        guesses; when the path was not given explicitly, verify it by
        whatever means are available (listing, reading surrounding context,
        or asking), not by assuming a fixed tool sequence.

        Safety rules (not an exhaustive allowlist — err toward refusing):
            - The workspace root itself must never be deleted.
            - VCS metadata directories (e.g. .git and equivalents) must
              never be deleted.
            - Any path that looks like credentials, secrets, or user data
              outside the task's scope should be treated as protected when
              in doubt.
            - Deleting a non-empty directory requires recursive=True; do
              not add recursive=True merely to make a failed call succeed —
              first confirm the directory's contents are intended for removal.

        Args:
            path: Path to the file or directory, relative to the workspace.
            recursive: Whether to delete directory contents recursively.
                Only set this after confirming the directory's contents
                should be removed.

        Returns:
            On success: confirmation of what was deleted.
            On failure: an ``Error:`` message that distinguishes between
            (a) target not found — the path may be wrong; re-check it before
            retrying, and (b) target protected or refused — stop and report
            to the user rather than attempting workarounds.
        """
        target = _resolve_or_none(ctx, path, access="write")
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"路径不存在：{path}。")
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"不能删除{reason}：{path}。")
        try:
            if target.is_dir():
                if any(target.iterdir()) and not recursive:
                    return tool_error(
                        "directory_not_empty",
                        f"目录 {path} 不是空的。",
                        hint="确认要删除全部内容时传 recursive=True。",
                    )
                shutil.rmtree(target)
                return f"已删除目录：{display_path(ctx.workspace, target)}"
            target.unlink()
            return f"已删除文件：{display_path(ctx.workspace, target)}"
        except OSError as exc:
            return tool_error("delete_failed", f"删除 {path} 失败：{exc}。")

    @tool
    def move_file(source: str, destination: str, overwrite: bool = False) -> str:
        """Move or rename a file/directory within the workspace (requires approval before execution).

        A directory cannot be moved into its own subdirectory; before overwriting an existing file,
        you must first read it.

        Args:
            source: The original path (relative to the workspace).
            destination: The destination path (relative to the workspace); parent directories are created automatically.
            overwrite: Whether to overwrite when the target already exists (files only).

        Returns:
            The move result; cases such as path traversal or an existing target return ``Error:``.
        """
        src = _resolve_or_none(ctx, source)
        if isinstance(src, str):
            return src
        dst = _resolve_or_none(ctx, destination, access="write")
        if isinstance(dst, str):
            return dst
        if not src.exists():
            return tool_error("not_found", f"源路径不存在：{source}。")
        if src == dst:
            return tool_error("no_change", "源路径与目标路径相同。")
        source_reason = _protected_reason(src, ctx.workspace)
        if source_reason:
            return tool_error("protected_path", f"不能移动{source_reason}：{source}。")
        reason = _protected_reason(dst, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"不能覆盖{reason}：{destination}。")
        if src.is_dir() and dst.is_relative_to(src):
            return tool_error(
                "invalid_destination",
                "不能把目录移动到它自己的子目录里。",
                hint="请换一个工作区内的其他目标目录。",
            )
        if src.is_dir() and dst.exists():
            return tool_error(
                "destination_exists",
                f"目标已存在：{destination}。",
                hint="覆盖目录有风险，请换一个目标名或先删除旧目录。",
            )
        error = _check_destination(dst, overwrite)
        if error:
            return error
        error = _ensure_parent(dst)
        if error:
            return error

        try:
            if overwrite and dst.exists():
                dst.unlink()
            shutil.move(str(src), str(dst))
        except (OSError, shutil.Error) as exc:
            return tool_error("move_failed", f"移动 {source} 失败：{exc}。")
        return f"已移动：{display_path(ctx.workspace, src)} → {display_path(ctx.workspace, dst)}"

    @tool
    def copy_file(source: str, destination: str, overwrite: bool = False) -> str:
        """Copy a file or directory within the workspace (requires approval before execution).

        If the target already exists, it is rejected by default; before overwriting an existing file,
        you must first read it.

        Args:
            source: The original path (relative to the workspace).
            destination: The destination path (relative to the workspace); parent directories are created automatically.
            overwrite: Whether to overwrite when the target already exists (files only).

        Returns:
            The copy result; cases such as path traversal or an existing target return ``Error:``.
        """
        src = _resolve_or_none(ctx, source)
        if isinstance(src, str):
            return src
        dst = _resolve_or_none(ctx, destination, access="write")
        if isinstance(dst, str):
            return dst
        if not src.exists():
            return tool_error("not_found", f"源路径不存在：{source}。")
        if src == dst:
            return tool_error("no_change", "源路径与目标路径相同。")
        if src.is_dir() and dst.is_relative_to(src):
            return tool_error(
                "invalid_destination",
                "不能把目录复制到它自己的子目录里。",
                hint="请换一个工作区内的其他目标目录。",
            )
        if src.is_dir() and dst.exists():
            return tool_error(
                "destination_exists",
                f"目标已存在：{destination}。",
                hint="覆盖目录有风险，请换一个目标名或先删除旧目录。",
            )
        error = _check_destination(dst, overwrite)
        if error:
            return error
        error = _ensure_parent(dst)
        if error:
            return error

        try:
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        except (OSError, shutil.Error) as exc:
            return tool_error("copy_failed", f"复制 {source} 失败：{exc}。")
        return f"已复制：{display_path(ctx.workspace, src)} → {display_path(ctx.workspace, dst)}"

    return [write_file, edit_file, make_dir, delete_file, move_file, copy_file]
