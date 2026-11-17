# tools/patch.py
"""Patch-style editing tool: ``apply_patch``.

Supports standard unified diff (``---`` / ``+++`` / ``@@``) and can modify several
files and several hunks per call. Compared with repeated ``edit_file`` calls, it:

- needs a single approval per patch, so cross-file refactors do not trigger many
  confirmations;
- validates context lines, so a misplaced patch does not silently modify the wrong
  place;
- supports creation via ``--- /dev/null`` and deletion via ``+++ /dev/null``.

It still follows read-before-write: an existing file must be read with
``read_file`` before it can be modified.
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
    """Patch format or context error; carries a machine-readable error code."""

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
    """Extract the path from ``--- a/foo\t2024-...``, stripping git's a/ b/ prefixes."""
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
    """Parse a unified diff; raise :class:`PatchError` when the format is incomplete."""
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
                f"Line {index + 1} has a --- header without a following +++ header.",
                hint="Provide a standard unified diff where each file starts with --- old path / +++ new path.",
            )
        old_path = _header_path(lines[index][4:])
        new_path = _header_path(lines[index + 1][4:])
        index += 2
        hunks: list[Hunk] = []
        while index < len(lines) and lines[index].startswith("@@"):
            match = _HUNK_RE.match(lines[index])
            if not match:
                raise PatchError("bad_hunk", f"Cannot parse hunk header: {lines[index]}.")
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
                    # Some tools omit the leading space of an empty context line;
                    # treat it as an empty context line
                    body.append((" ", ""))
                    old_left -= 1
                    new_left -= 1
                else:
                    raise PatchError(
                        "bad_hunk",
                        f"Unrecognized line inside hunk: {line[:80]!r}.",
                        hint="Every line must start with a space, +, or -.",
                    )
                index += 1
            if old_left != 0 or new_left != 0:
                raise PatchError(
                    "bad_hunk",
                    f"The hunk header line counts do not match its content (missing {old_left} old / {new_left} new lines).",
                )
            hunks.append(Hunk(old_start, old_count, new_start, new_count, body))
        if not hunks:
            raise PatchError("bad_patch", f"{new_path or old_path} has no hunks.")
        files.append(FilePatch(old_path, new_path, hunks))
    if not files:
        raise PatchError(
            "empty_patch",
            "No files were found in the patch.",
            hint="Provide a standard unified diff starting with --- / +++ / @@.",
        )
    return files


def _find_block(lines: list[str], block: list[str], start: int) -> int | None:
    """Find an exactly matching block of lines near ``start``; fall back to a unique whole-file match."""
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
                f"The patch context for {name} does not match the current file (expected to start at line {hunk.old_start}).",
                hint="Call read_file again for the current content, then regenerate the patch from it.",
            )
        result[position:position + len(old_lines)] = new_lines
        offset += len(new_lines) - len(old_lines)
    return result


def _load_text(target: Path) -> str:
    return _read_text(target)


def _resolve(ctx: ToolContext, raw: str) -> Path:
    return resolve_in_workspace(ctx.workspace, raw)


def apply_patch_text(ctx: ToolContext, patch: str) -> str:
    """Apply a patch and return the tool output; split out for testing."""
    try:
        files = parse_unified_diff(patch)
    except PatchError as exc:
        return tool_error(exc.code, exc.message, hint=exc.hint)

    resolved: list[tuple[FilePatch, Path]] = []
    seen: set[str] = set()
    for item in files:
        name = item.display_path
        if not name or name == "/dev/null":
            return tool_error("bad_patch", "The patch is missing a valid file path.")
        try:
            target = _resolve(ctx, name)
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        key = str(target)
        if key in seen:
            return tool_error("duplicate_path", f"The same patch modifies {name} more than once.")
        seen.add(key)
        resolved.append((item, target))

    # Validate and compute everything first, then write; a failed patch must not
    # leave half of its changes on disk
    plans: list[tuple[FilePatch, Path, str, int, int]] = []
    for item, target in resolved:
        name = display_path(ctx.workspace, target)
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"Cannot modify {reason}: {name}.")
        if item.creating:
            if target.exists():
                return tool_error(
                    "file_exists",
                    f"The patch creates {name}, but that file already exists.",
                    hint="Generate a modification patch against the existing file, or confirm whether it should be overwritten.",
                )
            before = ""
        else:
            if not target.exists():
                return tool_error(
                    "not_found",
                    f"The patch modifies {name}, but the file does not exist.",
                    hint="Create new files with a --- /dev/null patch, or use write_file first.",
                )
            if target.is_dir():
                return tool_error("is_directory", f"{name} is a directory; a patch cannot be applied to it.")
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
                changes.append(f"- {name}: deleted")
                total_removed += removed
                continue
            reason = _protected_reason(target, ctx.workspace)
            if reason:
                return tool_error("protected_path", f"Cannot modify {reason}: {name}.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(updated)
            _mark_read(target, complete=True)
            changes.append(f"- {name}: +{added} -{removed}")
            total_added += added
            total_removed += removed
        except OSError as exc:
            return tool_error("write_failed", f"Failed to write {name}: {exc}")

    return (
        f"Applied patch to {len(plans)} file(s)\n"
        + "\n".join(changes)
        + f"\nChanges: +{total_added} -{total_removed}"
    )


def build_patch_tools(ctx: ToolContext) -> list:
    """Build the ``apply_patch`` tool."""

    @tool
    def apply_patch(patch: str) -> str:
        """Apply a standard unified diff patch, modifying multiple files or locations in one call.

        Suited to cross-file refactors and batch rename-style changes. For a single
        change, edit_file is more direct. An existing file must be read with
        read_file before it is modified; create new files with --- /dev/null.

        Example:
            --- a/app.py
            +++ b/app.py
            @@ -1,3 +1,3 @@
             def main():
            -    print("old")
            +    print("new")

        Args:
            patch: The unified diff text, including the --- / +++ / @@ lines.

        Returns:
            The added and removed line counts per file plus the total change; returns
            ``Error:`` for a malformed patch, mismatched context, or a file that was
            not read first.
        """
        text = patch or ""
        if not text.strip():
            return tool_error("empty_patch", "patch must not be empty.")
        return apply_patch_text(ctx, text)

    return [apply_patch]
