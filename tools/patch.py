# tools/patch.py
"""补丁式编辑工具：``apply_patch``。

支持标准 unified diff（``---`` / ``+++`` / ``@@``），一次可以改多个文件、多个
hunk。相比逐个 ``edit_file``，它的优势是：

- 一个补丁就是一次审批，跨文件重构不会弹很多次确认；
- 上下文行会做校验，错位的补丁不会悄悄改到别处；
- 允许 ``--- /dev/null`` 新建、``+++ /dev/null`` 删除。

仍然遵守「读后写」：修改已有文件前必须先用 ``read_file`` 读过它。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from langchain.tools import tool

from tools.context import ToolContext
from tools.errors import tool_error
from tools.files import _check_read, _diff_stats, _mark_read, _protected_reason, _read_text
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    """补丁格式或上下文错误；带机读错误码。"""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class FilePatch:
    old_path: str
    new_path: str
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def display_path(self) -> str:
        candidate = self.new_path if self.new_path != "/dev/null" else self.old_path
        return candidate

    @property
    def creating(self) -> bool:
        return self.old_path == "/dev/null"

    @property
    def deleting(self) -> bool:
        return self.new_path == "/dev/null"


def _header_path(raw: str) -> str:
    """从 ``--- a/foo\t2024-...`` 里取出路径，去掉 git 的 a/ b/ 前缀。"""
    text = raw.strip().split("\t", 1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    if text == "/dev/null":
        return text
    text = text.replace("\\", "/")
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    while text.startswith("./"):
        text = text[2:]
    return text


def parse_unified_diff(patch: str) -> list[FilePatch]:
    """解析 unified diff；格式不完整时抛 :class:`PatchError`。"""
    lines = (patch or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    files: list[FilePatch] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise PatchError(
                "bad_patch",
                f"第 {index + 1} 行的 --- 后面缺少 +++ 文件头。",
                hint="请提供标准 unified diff，每个文件以 --- 旧路径 / +++ 新路径开头。",
            )
        old_path = _header_path(lines[index][4:])
        new_path = _header_path(lines[index + 1][4:])
        index += 2
        hunks: list[Hunk] = []
        while index < len(lines) and lines[index].startswith("@@"):
            match = _HUNK_RE.match(lines[index])
            if not match:
                raise PatchError("bad_hunk", f"无法解析 hunk 头：{lines[index]}。")
            old_start = int(match.group(1))
            old_count = int(match.group(2) or 1)
            new_start = int(match.group(3))
            new_count = int(match.group(4) or 1)
            index += 1
            body: list[tuple[str, str]] = []
            old_left, new_left = old_count, new_count
            while index < len(lines) and (old_left > 0 or new_left > 0):
                line = lines[index]
                if line.startswith("\\"):
                    index += 1
                    continue
                if line.startswith("+"):
                    body.append(("+", line[1:]))
                    new_left -= 1
                elif line.startswith("-"):
                    body.append(("-", line[1:]))
                    old_left -= 1
                elif line.startswith(" "):
                    body.append((" ", line[1:]))
                    old_left -= 1
                    new_left -= 1
                elif line == "":
                    # 有些工具会省略上下文行前的空格；按空上下文行处理
                    body.append((" ", ""))
                    old_left -= 1
                    new_left -= 1
                else:
                    raise PatchError(
                        "bad_hunk",
                        f"hunk 内出现无法识别的行：{line[:80]!r}。",
                        hint="每行必须以空格、+ 或 - 开头。",
                    )
                index += 1
            if old_left != 0 or new_left != 0:
                raise PatchError(
                    "bad_hunk",
                    f"hunk 声明的行数与实际内容不一致（还差 {old_left} 旧行 / {new_left} 新行）。",
                )
            hunks.append(Hunk(old_start, old_count, new_start, new_count, body))
        if not hunks:
            raise PatchError("bad_patch", f"{new_path or old_path} 没有任何 hunk。")
        files.append(FilePatch(old_path, new_path, hunks))
    if not files:
        raise PatchError(
            "empty_patch",
            "补丁里没有找到任何文件。",
            hint="请提供以 --- / +++ / @@ 开头的标准 unified diff。",
        )
    return files


def _find_block(lines: list[str], block: list[str], start: int) -> int | None:
    """在 ``start`` 附近找完全匹配的行块；找不到时再做一次全文唯一匹配。"""
    if not block:
        return max(0, min(start, len(lines)))
    start = max(0, min(start, max(0, len(lines) - len(block))))
    span = len(block)
    for offset in range(0, 61):
        for candidate in {start - offset, start + offset}:
            if candidate < 0 or candidate + span > len(lines):
                continue
            if lines[candidate:candidate + span] == block:
                return candidate
    matches = [
        index
        for index in range(0, len(lines) - span + 1)
        if lines[index:index + span] == block
    ]
    return matches[0] if len(matches) == 1 else None


def _apply_hunks(source: list[str], hunks: list[Hunk], name: str) -> list[str]:
    result = list(source)
    offset = 0
    for hunk in hunks:
        old_lines = [text for kind, text in hunk.lines if kind in {" ", "-"}]
        new_lines = [text for kind, text in hunk.lines if kind in {" ", "+"}]
        if old_lines:
            start = hunk.old_start - 1 + offset if hunk.old_start > 0 else offset
        else:
            start = hunk.new_start - 1 + offset if hunk.new_start > 0 else offset
        position = _find_block(result, old_lines, start)
        if position is None:
            raise PatchError(
                "context_mismatch",
                f"{name} 的补丁上下文与当前文件不一致（期望从第 {hunk.old_start} 行开始）。",
                hint="请重新 read_file 获取最新内容，再基于当前内容生成补丁。",
            )
        result[position:position + len(old_lines)] = new_lines
        offset += len(new_lines) - len(old_lines)
    return result


def _load_text(target: Path) -> str:
    return _read_text(target)


def _resolve(ctx: ToolContext, raw: str) -> Path:
    return resolve_in_workspace(ctx.workspace, raw)


def apply_patch_text(ctx: ToolContext, patch: str) -> str:
    """应用补丁并返回工具输出（拆出来方便测试）。"""
    try:
        files = parse_unified_diff(patch)
    except PatchError as exc:
        return tool_error(exc.code, exc.message, hint=exc.hint)

    resolved: list[tuple[FilePatch, Path]] = []
    seen: set[str] = set()
    for item in files:
        name = item.display_path
        if not name or name == "/dev/null":
            return tool_error("bad_patch", "补丁里缺少有效的文件路径。")
        try:
            target = _resolve(ctx, name)
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        key = str(target)
        if key in seen:
            return tool_error("duplicate_path", f"同一个补丁里重复修改了 {name}。")
        seen.add(key)
        resolved.append((item, target))

    # 先全部校验 / 计算，再统一落盘，避免补丁中途失败留下半套修改
    plans: list[tuple[FilePatch, Path, str, int, int]] = []
    for item, target in resolved:
        name = display_path(ctx.workspace, target)
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"不能修改{reason}：{name}。")
        if item.creating:
            if target.exists():
                return tool_error(
                    "file_exists",
                    f"补丁要新建 {name}，但该文件已经存在。",
                    hint="请基于现有文件生成修改补丁，或先确认是否要覆盖。",
                )
            before = ""
        else:
            if not target.exists():
                return tool_error(
                    "not_found",
                    f"补丁要修改 {name}，但文件不存在。",
                    hint="新建文件请使用 --- /dev/null 的补丁，或先用 write_file 创建。",
                )
            if target.is_dir():
                return tool_error("is_directory", f"{name} 是目录，不能应用补丁。")
            error, _partial = _check_read(target)
            if error:
                return error
            before = _load_text(target)

        source = [] if item.creating else before.split("\n")
        try:
            updated_lines = _apply_hunks(source, item.hunks, name)
        except PatchError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        updated = "" if item.deleting else "\n".join(updated_lines)
        if item.creating and updated and not updated.endswith("\n"):
            updated += "\n"
        elif not item.creating and before.endswith("\n") and updated and not updated.endswith("\n"):
            updated += "\n"
        added, removed = _diff_stats(before, updated)
        plans.append((item, target, updated, added, removed))

    changes: list[str] = []
    total_added = total_removed = 0
    for item, target, updated, added, removed in plans:
        name = display_path(ctx.workspace, target)
        try:
            if item.deleting:
                target.unlink()
                changes.append(f"- {name}：已删除")
                total_removed += removed
                continue
            reason = _protected_reason(target, ctx.workspace)
            if reason:
                return tool_error("protected_path", f"不能修改{reason}：{name}。")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(updated)
            _mark_read(target, complete=True)
            changes.append(f"- {name}：+{added} -{removed}")
            total_added += added
            total_removed += removed
        except OSError as exc:
            return tool_error("write_failed", f"写入 {name} 失败：{exc}。")

    return (
        f"已应用补丁：{len(plans)} 个文件\n"
        + "\n".join(changes)
        + f"\n变更：+{total_added} -{total_removed}"
    )


def build_patch_tools(ctx: ToolContext) -> list:
    """构造 ``apply_patch``。"""

    @tool
    def apply_patch(patch: str) -> str:
        """应用一个标准 unified diff 补丁，可一次修改多个文件/多个位置。

        适合跨文件重构、批量重命名式修改。只改一处时 edit_file 更直接。
        修改已有文件前必须先 read_file 读过它；新建文件用 --- /dev/null。

        示例：
            --- a/app.py
            +++ b/app.py
            @@ -1,3 +1,3 @@
             def main():
            -    print("old")
            +    print("new")

        Args:
            patch: unified diff 文本，包含 --- / +++ / @@ 行。

        Returns:
            每个文件的增删行数与总变更；格式错误、上下文不匹配或未读文件时
            返回 ``Error:``。
        """
        text = patch or ""
        if not text.strip():
            return tool_error("empty_patch", "patch 不能为空。")
        return apply_patch_text(ctx, text)

    return [apply_patch]
