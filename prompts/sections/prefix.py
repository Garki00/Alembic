# prompts/sections/prefix.py
"""Fixed product prefix: the first bytes of every system prompt."""

from __future__ import annotations

from prompts.sections.types import STABLE, SYSTEM, PromptSection

CLI_PREFIX = "You are a local interactive workspace agent."


def build_prefix_section() -> PromptSection:
    """Return the fixed prefix; it never depends on workspace or model state."""
    return PromptSection(
        name="Agent Prefix",
        source="cli_prefix",
        content=CLI_PREFIX,
        cache_hint=STABLE,
        target=SYSTEM,
    )
