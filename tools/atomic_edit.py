# tools/atomic_edit.py
"""Atomic batch editing tool: ``batch_edit``.

A single call can submit several files and several exact replacements. Every edit is
validated in memory and the final content is built first; only when all of them
succeed does anything reach the disk. If any replacement fails (not found, not
unique, invalid path, file modified externally), the disk is left untouched. If a
file commit fails during the write phase, files already written are rolled back.

Differences from ``apply_patch``:

- ``apply_patch`` consumes unified diffs and suits patches with context;
- ``batch_edit`` consumes exact string replacements, for cases where the model
  already knows the original text but does not want one ``edit_file`` call per
  change;
- all replacements in ``batch_edit`` are one unit and cannot half-succeed.
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
from tools.spec import RISK_WRITE, ToolSpec
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
    path: str = Field(description="File path inside the workspace.")
    old_string: str = Field(description="Exact original text to replace; do not include the line numbers returned by read_file.")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Whether to replace every match; by default only the single unique occurrence is replaced.")


class BatchEditInput(BaseModel):
    edits: list[BatchEditItem] = Field(description="Exact replacements applied in order.")


class BatchEditError(ValueError):
    """Validation error for batch edits; carries a machine-readable error code."""

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
                "edits is not a valid JSON array.",
                hint='Pass an array, for example [{"path":"a.html","old_string":"old","new_string":"new"}].',
            ) from exc
    if not isinstance(raw, list):
        raise BatchEditError(
            "bad_edits",
            "edits must be an array.",
            hint='Pass [{"path": "...", "old_string": "...", "new_string": "..."}].',
        )
    if not raw:
        raise BatchEditError("empty_edits", "edits must not be empty.")
    if len(raw) > _MAX_EDITS:
        raise BatchEditError(
            "too_many_edits",
            f"At most {_MAX_EDITS} edits can be submitted per call; got {len(raw)}.",
            hint="Split the work across several batch_edit calls, or merge adjacent changes first.",
        )
    edits: list[_Edit] = []
    for index, item in enumerate(raw, 1):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, Mapping):
            raise BatchEditError(
                "bad_edit",
                f"Item {index} is not an object.",
                hint='Each item must contain path, old_string, and new_string.',
            )
        path = str(item.get("path") or "").strip()
        old_string = item.get("old_string")
        new_string = item.get("new_string")
        if not path:
            raise BatchEditError("bad_edit", f"Item {index} is missing path.")
        if not isinstance(old_string, str) or not old_string:
            raise BatchEditError("bad_edit", f"Item {index} has an empty old_string.")
        if not isinstance(new_string, str):
            raise BatchEditError("bad_edit", f"Item {index} new_string must be a string.")
        if old_string == new_string:
            raise BatchEditError(
                "no_change",
                f"Item {index} has identical old_string and new_string.",
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
            f"old_string for replacement {plan.edits + 1} was not found in {plan.name}.",
            hint=(
                "Make sure old_string matches the original text exactly; "
                "the line numbers returned by read_file are locator annotations and must not be copied into old_string."
            ),
        )
    if count > 1 and not edit.replace_all:
        lines = ", ".join(str(item) for item in _line_positions(plan.text, edit.old_string))
        raise BatchEditError(
            "ambiguous_match",
            f"old_string occurs {count} times in {plan.name} (lines {lines}); the target occurrence is ambiguous.",
            hint="Add surrounding context to make it unique, or set replace_all=true.",
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
    """Validate and atomically commit a batch of exact replacements; split out for testing."""
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
            return tool_error("protected_path", f"Cannot modify {_protected_reason(target, ctx.workspace)}: {name}.")
        if not target.exists():
            return tool_error(
                "not_found",
                f"File not found: {name}.",
                hint="batch_edit only modifies existing files; use write_file to create a new file.",
            )
        if target.is_dir():
            return tool_error("is_directory", f"{name} is a directory and cannot be edited.")

        key = str(target)
        plan = plans.get(key)
        if plan is None:
            if len(plans) >= _MAX_FILES:
                return tool_error(
                    "too_many_files",
                    f"At most {_MAX_FILES} files can be modified per call.",
                    hint="Split the work across several batch_edit calls.",
                )
            error, _partial = _check_read(target)
            if error:
                return error
            try:
                original_bytes = target.read_bytes()
                original_text = _read_text(target)
                original_mtime = target.stat().st_mtime_ns
            except OSError as exc:
                return tool_error("read_failed", f"Failed to read {name}: {exc}")
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
        return tool_error("no_change", "These edits produced no actual change.")

    # Re-check before committing that no file was modified externally, so writes
    # made elsewhere are not overwritten.
    for plan in changed:
        try:
            current_mtime = plan.target.stat().st_mtime_ns
        except OSError as exc:
            return tool_error("read_failed", f"Failed to read {plan.name}: {exc}")
        if current_mtime != plan.original_mtime:
            return tool_error(
                "stale_read",
                f"{plan.name} was modified after it was read; this batch was not committed.",
                hint="Call read_file again for the current content, then rebuild the whole batch.",
            )

    try:
        for plan in changed:
            plan.staged_path = _stage(plan)
    except OSError as exc:
        _cleanup_staged(changed)
        return tool_error("stage_failed", f"Cannot prepare a temporary file; this batch was not committed: {exc}")

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
            f"Failed after committing {len(committed)} files: {exc}"
            if committed
            else f"Commit failed: {exc}"
        )
        if rollback_errors:
            detail += " Rollback also failed: " + "; ".join(rollback_errors)
        else:
            detail += " Rolled back the committed files; no partial changes remain."
        return tool_error("commit_failed", detail)

    lines: list[str] = []
    total_added = total_removed = total_edits = 0
    for plan in changed:
        _mark_read(plan.target, complete=True)
        added, removed = _diff_stats(plan.original_text, plan.text)
        total_added += added
        total_removed += removed
        total_edits += plan.edits
        lines.append(f"- {plan.name}: {plan.edits} edits, +{added} -{removed}")

    return (
        f"Atomically edited {len(changed)} file(s) with {total_edits} replacement(s)\n"
        + "\n".join(lines)
        + f"\nChanges: +{total_added} -{total_removed}\n"
        + "Verified: every replacement was committed at once; a failure leaves no partial changes."
    )


def build_atomic_edit_tools(ctx: ToolContext) -> list:
    """Build the ``batch_edit`` tool."""

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


TOOL_ENTRIES: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="batch_edit",
        label="原子批量编辑",
        description="多处精确替换，全部成功才落盘",
        risk=RISK_WRITE,
        tool_names=("batch_edit",),
        builder=lambda _config, ctx: build_atomic_edit_tools(ctx),
        capability={"permission_name": "edit"},
    ),
)
