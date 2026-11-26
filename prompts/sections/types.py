# prompts/sections/types.py
"""Prompt section contract shared by every section builder."""

from __future__ import annotations

from dataclasses import dataclass

#: Stable sections never change for a conversation; they form the cacheable prefix.
STABLE = "stable"
#: Dynamic sections change with the turn (tool set, repository state, clock).
DYNAMIC = "dynamic"
CACHE_HINTS = (STABLE, DYNAMIC)

#: Injected into the system message.
SYSTEM = "system"
#: Kept as a separate attachment for future surfaces; the system prompt currently
#: carries every section so one request stays byte-identical for prefix caching.
META_USER = "meta_user"
TARGETS = (SYSTEM, META_USER)


def estimate_tokens(text: str) -> int:
    """Rough token estimate used for section accounting only."""
    return max(1, len(text) // 4) if text else 0


@dataclass(frozen=True)
class PromptSection:
    """One named block of the system prompt.

    Attributes:
        name: Human-readable section name.
        source: Stable identifier used by tests and diagnostics.
        content: Rendered text; empty content means the section is omitted.
        cache_hint: ``stable`` before ``dynamic`` when messages are assembled.
        target: ``system`` for the system message, ``meta_user`` for an attachment.
    """

    name: str
    source: str
    content: str
    cache_hint: str = STABLE
    target: str = SYSTEM

    def __post_init__(self) -> None:
        if self.cache_hint not in CACHE_HINTS:
            raise ValueError(f"unknown cache hint {self.cache_hint!r}")
        if self.target not in TARGETS:
            raise ValueError(f"unknown injection target {self.target!r}")

    @property
    def trimmed(self) -> str:
        return self.content.strip()

    @property
    def chars(self) -> int:
        return len(self.content)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)
