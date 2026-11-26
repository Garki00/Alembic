# prompts/sections/skills.py
"""Skill attachment: available skill ids, names, and descriptions."""

from __future__ import annotations

from prompts.sections.types import DYNAMIC, SYSTEM, PromptSection


def build_skills_section(catalog: str) -> PromptSection | None:
    """Return the skill listing rendered by the extension manager, if any."""
    text = (catalog or "").strip()
    if not text:
        return None
    return PromptSection(
        name="Skills",
        source="skills",
        content=text,
        cache_hint=DYNAMIC,
        target=SYSTEM,
    )
