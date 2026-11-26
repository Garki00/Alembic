# tools/files.py
"""File read/write tools: make the workspace something the agent can actually operate on.

Safety boundaries:
- Every path passes through :func:`tools.paths.resolve_in_workspace`; any escape
  (including symlinks and ``..``) is rejected.
- An existing file must be read before it is overwritten (see :func:`_check_read`),
  so the model cannot replace content it has never seen.
- ``read_file`` enforces a whole-file byte ceiling and a model-token ceiling; a
  whole-file read over the token ceiling falls back to a paginated partial view.
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
from tools.spec import RISK_WRITE, ToolSpec, single_tool

# --------------------------------------------------- read-before-write check
# path -> (mtime_ns, fully_read). Shared within the process across turns and tools.
_READ_STATE: dict[str, tuple[int, bool]] = {}
_READ_LOCK = threading.Lock()

_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".tmp",
    ".alembic",
}

_MAX_LIST_ENTRIES = 300
_MAX_SCAN_FILES = 3000

# Read guards follow the same budget shape as the reference runtime: whole-file
# reads have a byte ceiling, and every read is bounded by an estimated token
# ceiling. Range reads that exceed the token ceiling fail instead of returning a
# silently truncated slice.
_READ_MAX_FILE_SIZE_BYTES = 256 * 1024
_READ_MAX_OUTPUT_TOKENS = 25_000
_READ_PARTIAL_FIT_RATIO = 0.85
_READ_PARTIAL_SHRINK_RATIO = 0.7
_READ_PARTIAL_SHRINK_STEPS = 6
_CHINESE_CHARS = re.compile(r"[\u4e00-\u9fff]")


def _estimate_tokens(text: str) -> int:
    """Estimate model tokens, weighting CJK characters more heavily."""
    chinese = len(_CHINESE_CHARS.findall(text))
    other = len(text) - chinese
    return (chinese * 2 + other + 2) // 3


def _token_capped_view(
    text: str,
    token_count: int,
) -> tuple[str, int, bool] | None:
    """Build a partial view that fits the token ceiling.

    Returns ``(view, shown_lines, by_characters)`` or ``None`` when even a
    one-character view cannot fit.
    """
    lines = text.splitlines()
    if not lines:
        return None
    line_count = max(
        1,
        min(
            len(lines),
            int(len(lines) * _READ_MAX_OUTPUT_TOKENS / token_count * _READ_PARTIAL_FIT_RATIO),
        ),
    )
    view = "\n".join(lines[:line_count])
    for _ in range(_READ_PARTIAL_SHRINK_STEPS):
        if _estimate_tokens(view) <= _READ_MAX_OUTPUT_TOKENS or line_count <= 1:
            break
        line_count = max(1, int(line_count * _READ_PARTIAL_SHRINK_RATIO))
        view = "\n".join(lines[:line_count])

    by_characters = False
    if _estimate_tokens(view) > _READ_MAX_OUTPUT_TOKENS or not view.strip():
        chars_per_token = max(0.5, len(text) / token_count)
        char_count = max(
            1,
            int(_READ_MAX_OUTPUT_TOKENS * chars_per_token * _READ_PARTIAL_FIT_RATIO),
        )
        for _ in range(_READ_PARTIAL_SHRINK_STEPS):
            view = text[:char_count]
            if _estimate_tokens(view) <= _READ_MAX_OUTPUT_TOKENS:
                break
            char_count = max(1, int(char_count * _READ_PARTIAL_SHRINK_RATIO))
        if _estimate_tokens(view) > _READ_MAX_OUTPUT_TOKENS:
            return None
        by_characters = True

    shown_lines = len(view.split("\n")) if by_characters else line_count
    return view, shown_lines, by_characters


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
    """Check that the file was read before writing. Return (error message, partial read flag)."""
    with _READ_LOCK:
        entry = _READ_STATE.get(str(path))
    if entry is None:
        return (
            tool_error(
                "read_required",
                f"Read the current contents of {path.name} before writing.",
                hint="Call read_file first to inspect the original text, then choose a full overwrite (write_file) or a targeted replacement (edit_file).",
            ),
            False,
        )
    mtime, complete = entry
    if mtime != _mtime_ns(path):
        return (
            tool_error(
                "stale_read",
                f"{path.name} has changed since you last read it.",
                hint="Call read_file again for the current content before writing, so no one else's changes are overwritten.",
            ),
            False,
        )
    return None, not complete


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _diff_stats(before: str, after: str) -> tuple[int, int]:
    """Count added and removed lines for the change summary shown after an edit."""
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
    """Match a file name or relative path against a glob, supporting ``**/`` and plain substring patterns."""
    text = (pattern or "").strip().replace("\\", "/")
    if not text:
        return False
    if not any(char in text for char in "*?[]"):
        return text.lower() in name.lower()
    if fnmatch.fnmatch(relative, text) or fnmatch.fnmatch(name, text):
        return True
    # ``**/*.py`` requires at least one directory level in fnmatch; a.py at the
    # search root should still match
    if text.startswith("**/"):
        rest = text[3:]
        return fnmatch.fnmatch(relative, rest) or fnmatch.fnmatch(name, rest)
    return False


def _as_error(exc: WorkspacePathError) -> str:
    return tool_error(exc.code, exc.message, hint=exc.hint)


def _resolve_or_none(ctx: ToolContext, raw: str, *, access: str = "read") -> Path | str:
    """Resolve a path; return the error string directly on failure, avoiding a try/except at each call site."""
    try:
        return resolve_in_workspace(ctx.workspace, raw, access=access)
    except WorkspacePathError as exc:
        return _as_error(exc)


def _protected_reason(target: Path, root: Path | None) -> str:
    """Return why the target cannot be deleted or overwritten; an empty string means allowed."""
    if root is None:
        return ""
    if target == root:
        return "the workspace root"
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return ""
    if any(part.lower() == ".git" for part in parts):
        return "the .git directory"
    return ""


def _ensure_parent(target: Path) -> str | None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return tool_error("write_failed", f"Cannot create directory {target.parent}: {exc}")
    return None


def _check_destination(destination: Path, overwrite: bool) -> str | None:
    """Check that the destination is writable; overwriting an existing file follows the read-before-write rule."""
    if not destination.exists():
        return None
    if not overwrite:
        return tool_error(
            "destination_exists",
            f"The destination already exists: {destination.name}.",
            hint="Choose another destination name, or pass overwrite=True (read_file is required first).",
        )
    if destination.is_dir():
        return tool_error(
            "destination_is_directory",
            f"The destination {destination.name} is a directory and will not be overwritten automatically.",
            hint="Delete or rename that directory first, then retry.",
        )
    error, _partial = _check_read(destination)
    if error:
        return error
    return None


# ---------------------------------------------------------- read-only group
def build_file_read_tools(ctx: ToolContext) -> list:
    """Directory listing, file reading, and content search (no disk changes)."""

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
            return tool_error("not_found", f"Directory not found: {path}.", hint="Call list_dir(\".\") first to inspect the workspace root.")
        if not target.is_dir():
            return tool_error("not_a_directory", f"{path} is not a directory.", hint="Use read_file to read file contents.")

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
            return tool_error("list_failed", f"Cannot list directory {path}: {exc}")

        entries.sort()
        shown = entries[:_MAX_LIST_ENTRIES]
        lines = [f"Directory: {display_path(ctx.workspace, target)} ({len(entries)} entries)"]
        for _, _, name, is_dir, size in shown:
            lines.append(f"{name}/" if is_dir else f"{name}  ({_format_size(size)})")
        if len(entries) > len(shown):
            lines.append(f"…{len(entries) - len(shown)} more entries not listed; narrow the directory scope.")
        return truncate("\n".join(lines), ctx.max_output_chars)

    @tool
    def read_file(path: str, offset: int = 1, limit: int | None = None) -> str:
        """Read a text file within the workspace, with line numbers.

        Whole-file reads are bounded by a 256 KiB byte ceiling and an estimated
        25,000-token ceiling. When a whole-file read exceeds the token ceiling, a
        paginated partial view is returned instead of the full content. When
        ``limit`` is supplied, the byte ceiling does not apply, but a range that
        exceeds the token ceiling fails with an error.

        Args:
            path: The file path relative to the workspace.
            offset: The starting line number, beginning at 1.
            limit: The maximum number of lines to read; omit to read the whole file.

        Returns:
            The first line is a file overview, followed by "line number| content";
            when the result is partial, a continuation hint is included.
        """
        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"File not found: {path}.", hint="Use list_dir or search_files to locate the file.")
        if target.is_dir():
            return tool_error("is_directory", f"{path} is a directory.", hint="Use list_dir to view the directory contents.")
        if _looks_binary(target):
            return tool_error("binary_file", f"{path} appears to be a binary file and cannot be read as text.")

        if limit is None:
            try:
                size = target.stat().st_size
            except OSError as exc:
                return tool_error("read_failed", f"Failed to read {path}: {exc}")
            if size > _READ_MAX_FILE_SIZE_BYTES:
                return tool_error(
                    "file_too_large",
                    f"File content ({size} bytes) exceeds the maximum allowed size "
                    f"({_READ_MAX_FILE_SIZE_BYTES} bytes).",
                    hint="Use offset and limit to read part of the file, or search_files to find a specific section.",
                )

        try:
            text = _read_text(target)
        except OSError as exc:
            return tool_error("read_failed", f"Failed to read {path}: {exc}")

        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            _mark_read(target, complete=True)
            return f"File: {display_path(ctx.workspace, target)} (empty file, 0 lines)"

        start = max(1, int(offset or 1))
        if start > total:
            return tool_error(
                "offset_out_of_range",
                f"Start line {start} is beyond the file's {total} lines.",
                hint=f"The file has {total} lines; set offset within 1..{total}.",
            )
        if limit is None:
            end = total
            initial_read = start == 1
        else:
            take = max(1, int(limit))
            end = min(total, start + take - 1)
            initial_read = False

        selected = "\n".join(lines[start - 1:end])
        original_chars = len(selected)
        token_count = _estimate_tokens(selected)
        partial = False
        by_characters = False
        shown_count = end - start + 1
        if token_count > _READ_MAX_OUTPUT_TOKENS:
            if not initial_read:
                return tool_error(
                    "read_too_many_tokens",
                    f"File content ({token_count} tokens) exceeds the maximum allowed "
                    f"tokens ({_READ_MAX_OUTPUT_TOKENS}).",
                    hint="Use offset and limit to read a smaller range, or search_files to find a specific section.",
                )
            view_data = _token_capped_view(selected, token_count)
            if view_data is None:
                return tool_error(
                    "read_too_many_tokens",
                    f"File content ({token_count} tokens) exceeds the maximum allowed "
                    f"tokens ({_READ_MAX_OUTPUT_TOKENS}) and cannot be split.",
                    hint="Use offset and limit to read a smaller range, or search_files to find a specific section.",
                )
            selected, shown_lines, by_characters = view_data
            end = start + shown_lines - 1
            shown_count = len(selected.split("\n"))
            partial = True

        if limit is not None:
            _mark_read(target, complete=(start == 1 and end == total))
        elif partial:
            _mark_read(target, complete=False)
        else:
            _mark_read(target, complete=initial_read)

        width = len(str(end))
        body = [f"{index:>{width}}| {lines[index - 1]}" for index in range(start, min(end, total) + 1)]
        if partial:
            # The selected view may end mid-line in the character fallback, so
            # number the displayed lines directly instead of indexing the file.
            rendered = selected.split("\n")
            width = len(str(start + len(rendered) - 1))
            body = [
                f"{start + index:>{width}}| {line}"
                for index, line in enumerate(rendered)
            ]

        header = (
            f"File: {display_path(ctx.workspace, target)}"
            f" ({total} lines, showing {start}-{end})"
        )
        footer = []
        if partial and by_characters:
            footer.append(
                f"…[partial view: showing the first {len(selected)} of {original_chars} "
                f"characters ({token_count} tokens, cap {_READ_MAX_OUTPUT_TOKENS})]"
            )
        elif partial:
            footer.append(
                f"…[partial view: showing {shown_count} of {total} lines "
                f"({token_count} tokens, cap {_READ_MAX_OUTPUT_TOKENS})]"
            )
        if end < total:
            footer.append(f"…{total - end} more lines; continue with offset={end + 1}.")
        return "\n".join([header, *body, *footer])

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
            return tool_error("bad_pattern", f"The regular expression does not compile: {exc}", hint="Check that parentheses and brackets are balanced.")

        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"Search path not found: {path}.")

        limit = max(1, min(int(max_results or 50), 200))
        matches: list[str] = []
        scanned = 0
        skipped_binary = 0

        def scan(file_path: Path) -> bool:
            """Scan one file; return False when enough matches have been collected."""
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
                f"No matches for {pattern!r} (searched {scanned} files"
                f"{f', skipped {skipped_binary} binary files' if skipped_binary else ''})."
            )
        header = f"Matches for {pattern!r}: {len(matches)} lines (searched {scanned} files)"
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
            return tool_error("empty_pattern", "pattern must not be empty.", hint="For example *.py or **/*.test.ts.")
        target = _resolve_or_none(ctx, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return tool_error("not_found", f"Search path not found: {path}.")
        if not target.is_dir():
            return tool_error("not_a_directory", f"{path} is not a directory.", hint="The search start point must be a directory.")

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
            return tool_error("search_failed", f"Search failed: {exc}")

        shown = found[:limit]
        header = f"Matches for {text}: {len(shown)} files found ({scanned} scanned)"
        if not shown:
            return header + "\nNo matching files."
        lines = [header]
        for relative, size in shown:
            lines.append(f"{relative}  ({_format_size(size)})")
        if truncated:
            lines.append(f"…Results or scan range reached the limit ({limit} entries / {_MAX_SCAN_FILES} files); narrow the search.")
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
            return tool_error("not_found", f"Path not found: {path}.")
        try:
            info = target.stat()
        except OSError as exc:
            return tool_error("stat_failed", f"Cannot read metadata for {path}: {exc}")

        kind = "directory" if target.is_dir() else "file"
        lines = [
            f"Path: {display_path(ctx.workspace, target)}",
            f"Type: {kind}",
            f"Size: {_format_size(info.st_size)} ({info.st_size} bytes)",
            f"Modified: {datetime.fromtimestamp(info.st_mtime):%Y-%m-%d %H:%M:%S}",
        ]
        if target.is_file():
            lines.append(f"Extension: {target.suffix.lower() or '(none)'}")
            if not _looks_binary(target) and info.st_size <= 2_000_000:
                try:
                    lines.append(f"Lines: {len(_read_text(target).splitlines())}")
                except OSError:
                    pass
        return "\n".join(lines)

    return [list_dir, read_file, search_files, find_files, file_stat]


# -------------------------------------------------------------- write group
def build_file_write_tools(ctx: ToolContext) -> list:
    """Whole-file writes and targeted replacements (both persist to disk under the workspace and read-before-write rules)."""

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
            return tool_error("is_directory", f"{path} is a directory and cannot be written.", hint="Provide a specific file name.")
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"Cannot write to {reason}: {path}.")

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
            return tool_error("write_failed", f"Failed to write {path}: {exc}")

        # Re-read metadata immediately after the write: the tool result must reflect a
        # real write, not a claim that a write happened (see write_unverified).
        try:
            actual = target.stat().st_size
        except OSError as exc:
            return tool_error("write_unverified", f"Cannot verify {path} after writing: {exc}")
        if actual != len(payload):
            return tool_error(
                "write_unverified",
                f"Size mismatch after writing {path} (on disk {actual} bytes, expected {len(payload)} bytes).",
                hint="Use file_stat to inspect the file, then retry the write.",
            )

        _mark_read(target, complete=True)
        action = "Overwritten" if existed else "Created"
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        added, removed = _diff_stats(old_text, content)
        result = [
            f"{action}: {display_path(ctx.workspace, target)}",
            f"Lines: {lines}, bytes: {actual}",
            f"Changes: +{added} -{removed}",
            "Verified: the file exists and its size matches the written content.",
        ]
        if partial_warning:
            result.append("Note: only part of this file was read last time; overwriting may drop the unread portion.")
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
            return tool_error("not_found", f"File not found: {path}.", hint="Use write_file to create a new file.")
        if target.is_dir():
            return tool_error("is_directory", f"{path} is a directory.")
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"Cannot modify {reason}: {path}.")
        if not old_string:
            return tool_error("empty_old_string", "old_string must not be empty.")
        if old_string == new_string:
            return tool_error("no_change", "old_string and new_string are identical; there is nothing to change.")

        error, _ = _check_read(target)
        if error:
            return error

        try:
            text = _read_text(target)
        except OSError as exc:
            return tool_error("read_failed", f"Failed to read {path}: {exc}")

        count = text.count(old_string)
        if count == 0:
            return tool_error(
                "not_found",
                f"old_string was not found in {path}.",
                hint="Use read_file to confirm the exact original text (indentation, spaces, and line breaks must match), then retry.",
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
                f"old_string occurs {count} times in {path} (lines {', '.join(map(str, positions))}); the target occurrence is ambiguous.",
                hint="Add surrounding context to make it unique, or pass replace_all=True to replace all occurrences.",
            )

        updated = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        try:
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(updated)
        except OSError as exc:
            return tool_error("write_failed", f"Failed to write {path}: {exc}")

        _mark_read(target, complete=True)
        replaced = count if replace_all else 1
        added, removed = _diff_stats(text, updated)
        return (
            f"Modified: {display_path(ctx.workspace, target)}, {replaced} replacement(s).\n"
            f"Changes: +{added} -{removed}"
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
            return tool_error("protected_path", f"Cannot create a directory under {reason}: {path}.")
        if target.exists():
            if target.is_dir():
                return f"Directory already exists: {display_path(ctx.workspace, target)}"
            return tool_error("not_a_directory", f"{path} already exists and is a file.", hint="Choose another directory name.")
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return tool_error("write_failed", f"Failed to create directory {path}: {exc}")
        return f"Created directory: {display_path(ctx.workspace, target)}"

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
            return tool_error("not_found", f"Path not found: {path}.")
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"Cannot delete {reason}: {path}.")
        try:
            if target.is_dir():
                if any(target.iterdir()) and not recursive:
                    return tool_error(
                        "directory_not_empty",
                        f"Directory {path} is not empty.",
                        hint="Pass recursive=True once you have confirmed that all of its contents should be deleted.",
                    )
                shutil.rmtree(target)
                return f"Deleted directory: {display_path(ctx.workspace, target)}"
            target.unlink()
            return f"Deleted file: {display_path(ctx.workspace, target)}"
        except OSError as exc:
            return tool_error("delete_failed", f"Failed to delete {path}: {exc}")

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
            return tool_error("not_found", f"Source path not found: {source}.")
        if src == dst:
            return tool_error("no_change", "The source and destination paths are identical.")
        source_reason = _protected_reason(src, ctx.workspace)
        if source_reason:
            return tool_error("protected_path", f"Cannot move {source_reason}: {source}.")
        reason = _protected_reason(dst, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"Cannot overwrite {reason}: {destination}.")
        if src.is_dir() and dst.is_relative_to(src):
            return tool_error(
                "invalid_destination",
                "A directory cannot be moved into its own subdirectory.",
                hint="Choose another destination directory inside the workspace.",
            )
        if src.is_dir() and dst.exists():
            return tool_error(
                "destination_exists",
                f"The destination already exists: {destination}.",
                hint="Overwriting a directory is risky; choose another target name or delete the old directory first.",
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
            return tool_error("move_failed", f"Failed to move {source}: {exc}")
        return f"Moved: {display_path(ctx.workspace, src)} → {display_path(ctx.workspace, dst)}"

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
            return tool_error("not_found", f"Source path not found: {source}.")
        if src == dst:
            return tool_error("no_change", "The source and destination paths are identical.")
        if src.is_dir() and dst.is_relative_to(src):
            return tool_error(
                "invalid_destination",
                "A directory cannot be copied into its own subdirectory.",
                hint="Choose another destination directory inside the workspace.",
            )
        if src.is_dir() and dst.exists():
            return tool_error(
                "destination_exists",
                f"The destination already exists: {destination}.",
                hint="Overwriting a directory is risky; choose another target name or delete the old directory first.",
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
            return tool_error("copy_failed", f"Failed to copy {source}: {exc}")
        return f"Copied: {display_path(ctx.workspace, src)} → {display_path(ctx.workspace, dst)}"

    return [write_file, edit_file, make_dir, delete_file, move_file, copy_file]


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------
def _read_entry(name: str, label: str, description: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        label=label,
        description=description,
        tool_names=(name,),
        builder=single_tool(lambda _config, ctx: build_file_read_tools(ctx), name),
    )


TOOL_ENTRIES: tuple[ToolSpec, ...] = (
    _read_entry("list_dir", "目录浏览", "列出目录内容"),
    _read_entry("read_file", "文件读取", "读取文本文件"),
    _read_entry("search_files", "内容搜索", "搜索文件内容"),
    _read_entry("find_files", "文件查找", "按名称查找文件"),
    _read_entry("file_stat", "文件信息", "查看文件元数据"),
    ToolSpec(
        name="write_file", label="文件写入", description="写入文件内容",
        risk=RISK_WRITE, tool_names=("write_file",),
        builder=single_tool(lambda _config, ctx: build_file_write_tools(ctx), "write_file"),
        capability={"permission_name": "edit"},
    ),
    ToolSpec(
        name="edit_file", label="文件编辑", description="精确替换文件内容",
        risk=RISK_WRITE, tool_names=("edit_file",),
        builder=single_tool(lambda _config, ctx: build_file_write_tools(ctx), "edit_file"),
        capability={"permission_name": "edit"},
    ),
    ToolSpec(
        name="make_dir", label="创建目录", description="创建工作区目录",
        risk=RISK_WRITE, tool_names=("make_dir",),
        builder=single_tool(lambda _config, ctx: build_file_write_tools(ctx), "make_dir"),
        capability={"risk_level": "low", "permission_name": "file"},
    ),
    ToolSpec(
        name="delete_file", label="删除文件", description="删除文件或目录",
        risk=RISK_WRITE, tool_names=("delete_file",),
        builder=single_tool(lambda _config, ctx: build_file_write_tools(ctx), "delete_file"),
        capability={"risk_level": "high", "destructive": True, "permission_name": "file"},
    ),
    ToolSpec(
        name="move_file", label="移动文件", description="移动文件或目录",
        risk=RISK_WRITE, tool_names=("move_file",),
        builder=single_tool(lambda _config, ctx: build_file_write_tools(ctx), "move_file"),
        capability={"risk_level": "medium", "permission_name": "file"},
    ),
    ToolSpec(
        name="copy_file", label="复制文件", description="复制文件或目录",
        risk=RISK_WRITE, tool_names=("copy_file",),
        builder=single_tool(lambda _config, ctx: build_file_write_tools(ctx), "copy_file"),
        capability={"risk_level": "medium", "permission_name": "file"},
    ),
)
