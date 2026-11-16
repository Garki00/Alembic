# tools/atomic_edit.py
"""原子化批量编辑工具：``batch_edit``。

一次调用可以提交多个文件、多个精确替换。所有编辑先在内存中校验并生成
最终内容，全部成功后才开始落盘；任何一个替换失败（找不到、匹配不唯一、
路径非法、文件被外部修改），磁盘上不会留下任何修改。落盘阶段如果某个
文件提交失败，会回滚已经写入的文件。

它和 ``apply_patch`` 的区别：

- ``apply_patch`` 处理 unified diff，适合有上下文的补丁；
- ``batch_edit`` 处理精确字符串替换，适合模型已经知道原文、但不想为每一处
  修改单独发一次 ``edit_file`` 的场景；
- ``batch_edit`` 的多次替换是一个整体，不能只成功一半。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.tools import tool
from pydantic import BaseModel, Field

from tools.context import ToolContext
from tools.errors import tool_error
from tools.files import (
    _check_read,
    _diff_stats,
    _mark_read,
    _protected_reason,
    _read_text,
)
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace

_MAX_EDITS = 200
_MAX_FILES = 50


class BatchEditItem(BaseModel):
    path: str = Field(description="工作区内的文件路径。")
    old_string: str = Field(description="要替换的原文；不要包含 read_file 的行号。")
    new_string: str = Field(description="替换后的文本。")
    replace_all: bool = Field(default=False, description="是否替换全部匹配；默认只替换唯一一处。")


class BatchEditInput(BaseModel):
    edits: list[BatchEditItem] = Field(description="按顺序应用的多处精确替换。")


class BatchEditError(ValueError):
    """批量编辑的校验错误；带机读错误码。"""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@dataclass
class _Edit:
    path: str
    old_string: str
    new_string: str
    replace_all: bool


@dataclass
class _Plan:
    target: Path
    name: str
    original_bytes: bytes
    original_text: str
    original_mtime: int
    text: str
    edits: int = 0
    staged_path: Path | None = field(default=None, repr=False)


def _normalize_edits(raw: Any) -> list[_Edit]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise BatchEditError(
                "bad_edits",
                "edits 不是合法的 JSON 数组。",
                hint='请传数组，例如 [{"path":"a.html","old_string":"旧","new_string":"新"}]。',
            ) from exc
    if not isinstance(raw, list):
        raise BatchEditError(
            "bad_edits",
            "edits 必须是数组。",
            hint='请传 [{"path": "...", "old_string": "...", "new_string": "..."}]。',
        )
    if not raw:
        raise BatchEditError("empty_edits", "edits 不能为空。")
    if len(raw) > _MAX_EDITS:
        raise BatchEditError(
            "too_many_edits",
            f"一次最多提交 {_MAX_EDITS} 处编辑，当前为 {len(raw)} 处。",
            hint="请拆成多次 batch_edit，或先合并相邻修改。",
        )
    edits: list[_Edit] = []
    for index, item in enumerate(raw, 1):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, Mapping):
            raise BatchEditError(
                "bad_edit",
                f"第 {index} 项不是对象。",
                hint='每一项必须包含 path、old_string、new_string。',
            )
        path = str(item.get("path") or "").strip()
        old_string = item.get("old_string")
        new_string = item.get("new_string")
        if not path:
            raise BatchEditError("bad_edit", f"第 {index} 项缺少 path。")
        if not isinstance(old_string, str) or not old_string:
            raise BatchEditError("bad_edit", f"第 {index} 项的 old_string 不能为空。")
        if not isinstance(new_string, str):
            raise BatchEditError("bad_edit", f"第 {index} 项的 new_string 必须是字符串。")
        if old_string == new_string:
            raise BatchEditError(
                "no_change",
                f"第 {index} 项的 old_string 与 new_string 相同。",
            )
        edits.append(
            _Edit(
                path=path,
                old_string=old_string,
                new_string=new_string,
                replace_all=bool(item.get("replace_all", False)),
            )
        )
    return edits


def _resolve(ctx: ToolContext, raw: str) -> Path:
    return resolve_in_workspace(ctx.workspace, raw)


def _line_positions(text: str, needle: str, limit: int = 5) -> list[int]:
    positions: list[int] = []
    start = 0
    for _ in range(limit):
        index = text.find(needle, start)
        if index < 0:
            break
        positions.append(text.count("\n", 0, index) + 1)
        start = index + len(needle)
    return positions


def _apply_one(plan: _Plan, edit: _Edit) -> None:
    count = plan.text.count(edit.old_string)
    if count == 0:
        raise BatchEditError(
            "not_found",
            f"在 {plan.name} 中没有找到第 {plan.edits + 1} 个替换的 old_string。",
            hint=(
                "请确认 old_string 与文件原文完全一致；"
                "read_file 返回的行号只是定位标注，不能复制进 old_string。"
            ),
        )
    if count > 1 and not edit.replace_all:
        lines = ", ".join(str(item) for item in _line_positions(plan.text, edit.old_string))
        raise BatchEditError(
            "ambiguous_match",
            f"old_string 在 {plan.name} 中出现了 {count} 次（行 {lines}），无法确定改哪一处。",
            hint="补足上下文使其唯一，或设置 replace_all=true。",
        )
    replaced = count if edit.replace_all else 1
    plan.text = (
        plan.text.replace(edit.old_string, edit.new_string)
        if edit.replace_all
        else plan.text.replace(edit.old_string, edit.new_string, 1)
    )
    plan.edits += replaced


def _stage(plan: _Plan) -> Path:
    parent = plan.target.parent
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{plan.target.name}.alembic-",
        suffix=".tmp",
        dir=str(parent),
    )
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(plan.text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            shutil.copystat(plan.target, staged)
        except OSError:
            pass
        return staged
    except BaseException:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _cleanup_staged(plans: list[_Plan]) -> None:
    for plan in plans:
        if plan.staged_path is None:
            continue
        try:
            plan.staged_path.unlink(missing_ok=True)
        except OSError:
            pass
        plan.staged_path = None


def apply_batch_edits(ctx: ToolContext, raw: Any) -> str:
    """校验并原子提交一批精确替换；拆出来方便测试。"""
    try:
        edits = _normalize_edits(raw)
    except BatchEditError as exc:
        return tool_error(exc.code, exc.message, hint=exc.hint)

    plans: dict[str, _Plan] = {}
    for edit in edits:
        try:
            target = _resolve(ctx, edit.path)
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        name = display_path(ctx.workspace, target)
        if _protected_reason(target, ctx.workspace):
            return tool_error("protected_path", f"不能修改{_protected_reason(target, ctx.workspace)}：{name}。")
        if not target.exists():
            return tool_error(
                "not_found",
                f"文件不存在：{name}。",
                hint="batch_edit 只修改已有文件；新建文件请使用 write_file。",
            )
        if target.is_dir():
            return tool_error("is_directory", f"{name} 是目录，不能编辑。")

        key = str(target)
        plan = plans.get(key)
        if plan is None:
            if len(plans) >= _MAX_FILES:
                return tool_error(
                    "too_many_files",
                    f"一次最多修改 {_MAX_FILES} 个文件。",
                    hint="请拆成多次 batch_edit。",
                )
            error, _partial = _check_read(target)
            if error:
                return error
            try:
                original_bytes = target.read_bytes()
                original_text = _read_text(target)
                original_mtime = target.stat().st_mtime_ns
            except OSError as exc:
                return tool_error("read_failed", f"读取 {name} 失败：{exc}。")
            plan = _Plan(
                target=target,
                name=name,
                original_bytes=original_bytes,
                original_text=original_text,
                original_mtime=original_mtime,
                text=original_text,
            )
            plans[key] = plan
        try:
            _apply_one(plan, edit)
        except BatchEditError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)

    changed = [plan for plan in plans.values() if plan.text != plan.original_text]
    if not changed:
        return tool_error("no_change", "这批编辑没有产生任何实际变化。")

    # 提交前再次确认文件没有被外部改动，避免覆盖别处的写入。
    for plan in changed:
        try:
            current_mtime = plan.target.stat().st_mtime_ns
        except OSError as exc:
            return tool_error("read_failed", f"读取 {plan.name} 失败：{exc}。")
        if current_mtime != plan.original_mtime:
            return tool_error(
                "stale_read",
                f"{plan.name} 在读取之后已被修改，本批编辑没有提交。",
                hint="请重新 read_file 获取最新内容，再生成整批编辑。",
            )

    try:
        for plan in changed:
            plan.staged_path = _stage(plan)
    except OSError as exc:
        _cleanup_staged(changed)
        return tool_error("stage_failed", f"无法准备临时文件，本批编辑没有提交：{exc}。")

    committed: list[_Plan] = []
    try:
        for plan in changed:
            assert plan.staged_path is not None
            os.replace(plan.staged_path, plan.target)
            plan.staged_path = None
            committed.append(plan)
    except OSError as exc:
        rollback_errors: list[str] = []
        for plan in reversed(committed):
            try:
                plan.target.write_bytes(plan.original_bytes)
                _mark_read(plan.target, complete=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"{plan.name}: {rollback_exc}")
        _cleanup_staged(changed)
        detail = (
            f"提交 {len(committed)} 个文件后失败：{exc}。"
            if committed
            else f"提交失败：{exc}。"
        )
        if rollback_errors:
            detail += " 回滚也失败：" + "；".join(rollback_errors)
        else:
            detail += " 已回滚已提交的文件，没有留下部分修改。"
        return tool_error("commit_failed", detail)

    lines: list[str] = []
    total_added = total_removed = total_edits = 0
    for plan in changed:
        _mark_read(plan.target, complete=True)
        added, removed = _diff_stats(plan.original_text, plan.text)
        total_added += added
        total_removed += removed
        total_edits += plan.edits
        lines.append(f"- {plan.name}：{plan.edits} 处，+{added} -{removed}")

    return (
        f"已原子编辑：{len(changed)} 个文件，{total_edits} 处替换\n"
        + "\n".join(lines)
        + f"\n变更：+{total_added} -{total_removed}\n"
        + "校验：所有替换一次性提交；任一处失败时不会留下部分修改。"
    )


def build_atomic_edit_tools(ctx: ToolContext) -> list:
    """构造 ``batch_edit``。"""

    @tool(args_schema=BatchEditInput)
    def batch_edit(edits: list[dict]) -> str:
        """Atomically apply several exact string replacements in one call.

        Use this instead of multiple ``edit_file`` calls when one logical change
        touches several places or files. All replacements are validated in memory
        first. If any replacement fails, no file is written; if a write fails
        midway, committed files are rolled back.

        Args:
            edits: A list of objects with:
                - ``path``: file path relative to the workspace.
                - ``old_string``: exact text to replace. Do not include the line
                  numbers shown by read_file.
                - ``new_string``: replacement text.
                - ``replace_all``: optional, replace every occurrence.

        Returns:
            A per-file summary, or an ``Error:`` string when any replacement is
            missing, ambiguous, unauthorized, stale, or the commit fails.
        """
        return apply_batch_edits(ctx, edits)

    return [batch_edit]
