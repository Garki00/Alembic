# tools/errors.py
"""Unified tool output convention: failures start with ``Error:`` and never raise.

Failure format::

    Error: [<code>] <one-line description>
    <optional: suggested next step>

- ``code`` uses lowercase snake_case and stays stable so prompts and tests can
  reference it.
- Put the hint on the second line to keep the output readable.
- Successful output is not constrained by this convention.
"""

from __future__ import annotations

ERROR_PREFIX = "Error:"


def tool_error(code: str, message: str, *, hint: str = "") -> str:
    """Build a failure result: ``Error: [code] message`` with an optional hint line."""
    head = f"{ERROR_PREFIX} [{code}] {message}" if code else f"{ERROR_PREFIX} {message}"
    return f"{head}\n{hint}" if hint else head


def is_error(text: str) -> bool:
    """Return whether a tool output string represents a failure."""
    return isinstance(text, str) and text.startswith(ERROR_PREFIX)


def truncate(text: str, max_chars: int, *, keep_tail: bool = False) -> str:
    """Truncate tool output by character count and add an explicit marker.

    Args:
        text: Original output.
        max_chars: Maximum allowed characters, including the marker.
        keep_tail: When True, keep the end of the output (command errors usually
            appear last); when False, keep only the beginning (better for file
            reads and listings).
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    head_budget = max_chars if not keep_tail else int(max_chars * 0.7)
    tail_budget = max_chars - head_budget if keep_tail else 0
    omitted = len(text) - head_budget - tail_budget
    marker = f"\n…[truncated: {len(text)} characters total, {omitted} omitted]…"

    if not keep_tail:
        return text[:head_budget] + marker
    head = text[:head_budget]
    tail = text[-tail_budget:] if tail_budget > 0 else ""
    return f"{head}{marker}\n{tail}"
