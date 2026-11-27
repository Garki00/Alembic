# prompts/builder.py
"""System-prompt composition.

The prompt is assembled from named sections instead of one large constant:

1. fixed product prefix;
2. agent identity (default, or the user's selected prompt);
3. operating rules and execution discipline (skipped when a prompt is selected,
   because the prompt replaces both identity and rules);
4. presentation surface (desktop app or terminal);
5. tool guidance derived from this turn's visible and deferred tools;
6. repository snapshot at the start of the conversation;
7. runtime announcement (directories, platform, shell, model, time);
8. available skills;
9. workspace instruction files.

Sections are emitted stable-first, then dynamic, so a provider matching the
longest stable prefix reuses as much of the prompt as possible. Tool
descriptions do not live here: they travel in the request's ``tools`` field,
which is generated from the tools' own declarations.

Project context loading, its injection screening, and the skill listing are
reused from the existing modules; this file only orders and renders sections.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from prompts.context import load_project_context, render_project_context
from prompts.sections.environment import build_environment_section
from prompts.sections.git_status import (
    GitSnapshot,
    build_git_status_section,
    collect_git_snapshot,
)
from prompts.sections.identity import build_identity_section
from prompts.sections.interface import (
    SURFACE_CLI,
    SURFACE_WEB,
    SURFACES,
    build_interface_section,
)
from prompts.sections.prefix import build_prefix_section
from prompts.sections.rules import (
    build_rules_section,
    build_tool_guidance_section,
)
from prompts.sections.skills import build_skills_section
from prompts.sections.types import DYNAMIC, STABLE, PromptSection, estimate_tokens

__all__ = [
    "PromptBuild",
    "SURFACE_CLI",
    "SURFACE_WEB",
    "SURFACES",
    "build_prompt",
    "build_system_prompt",
    "estimate_tokens",
]


@dataclass(frozen=True)
class PromptBuild:
    """One assembled prompt plus the section accounting."""

    sections: tuple[PromptSection, ...]
    system: str
    stable: str
    dynamic: str
    total_chars: int
    total_tokens: int


def _ordered(sections: Iterable[PromptSection]) -> tuple[PromptSection, ...]:
    items = [section for section in sections if section is not None and section.trimmed]
    return tuple(
        [item for item in items if item.cache_hint == STABLE]
        + [item for item in items if item.cache_hint == DYNAMIC]
    )


def _join(sections: Iterable[PromptSection]) -> str:
    return "\n\n".join(section.trimmed for section in sections if section.trimmed)


def build_prompt(
    workspace: str = "",
    *,
    tools: Iterable[str] | None = None,
    deferred_tools: Iterable[str] = (),
    terminal: str = "powershell",
    surface: str = SURFACE_WEB,
    engine: str = "tool_calling",
    now: datetime | None = None,
    project_context: str | None = None,
    custom_prompt: str = "",
    model: str = "",
    additional_directories: Iterable[str] = (),
    git_snapshot: GitSnapshot | None = None,
    detect_git: bool = True,
) -> PromptBuild:
    """Assemble the system prompt for one turn.

    Args:
        workspace: Absolute workspace path shown to the model.
        tools: Concrete tool names visible in this request; ``None`` omits the
            tool section entirely.
        deferred_tools: Tool names whose schemas are not in the request.
        terminal: Preferred terminal id used for the runtime line.
        surface: ``web`` or ``cli``; the interface contract differs.
        engine: ``tool_calling`` or ``react``; ReAct must not be told to batch.
        now: Injectable clock for tests.
        project_context: Pre-rendered workspace instructions; ``None`` loads the
            first project context file, ``""`` skips the section.
        custom_prompt: User-selected prompt body; it replaces the identity and
            rules sections. Empty means the built-in default.
        model: Current model identifier announced to the model.
        additional_directories: Extra roots visible to the tool runtime.
        git_snapshot: Pre-collected repository snapshot; ``None`` collects one
            when ``detect_git`` is true.
        detect_git: Whether to collect a snapshot when none was supplied.

    Returns:
        A :class:`PromptBuild` with the ordered sections and the rendered text.
    """
    tool_names = set(tools) if tools is not None else None
    deferred = tuple(deferred_tools or ())
    if git_snapshot is None and detect_git:
        git_snapshot = collect_git_snapshot(workspace)

    sections: list[PromptSection] = [
        build_prefix_section(),
        build_identity_section(custom_prompt),
    ]
    if not (custom_prompt or "").strip():
        # A selected prompt owns both the identity and the operating rules; the
        # fixed prefix, interface contract, and tool guidance still apply.
        sections.append(build_rules_section(engine))
    sections.extend(
        [
            build_interface_section(surface),
            build_tool_guidance_section(tool_names, deferred),
        ]
    )
    git_section = build_git_status_section(git_snapshot)
    if git_section is not None:
        sections.append(git_section)
    sections.append(
        build_environment_section(
            workspace,
            terminal=terminal,
            now=now,
            model=model,
            additional_directories=additional_directories,
            is_git_repository=bool(git_snapshot and git_snapshot.is_repo),
        )
    )

    if tool_names and "load_skill" in tool_names:
        # This helper reads the existing manager; prompt assembly must not start one.
        from extensions.manager import skill_catalog

        skill_section = build_skills_section(skill_catalog())
        if skill_section is not None:
            sections.append(skill_section)

    if project_context is None:
        project_block = render_project_context(load_project_context(workspace))
    else:
        project_block = (project_context or "").strip()
    if project_block:
        sections.append(
            PromptSection(
                name="Project Context",
                source="request_user_context",
                content=project_block,
                cache_hint=DYNAMIC,
            )
        )

    ordered = _ordered(sections)
    stable = _join(section for section in ordered if section.cache_hint == STABLE)
    dynamic = _join(section for section in ordered if section.cache_hint == DYNAMIC)
    system = "\n\n".join(part for part in (stable, dynamic) if part)
    return PromptBuild(
        sections=ordered,
        system=system,
        stable=stable,
        dynamic=dynamic,
        total_chars=sum(section.chars for section in ordered),
        total_tokens=sum(section.tokens for section in ordered),
    )


def build_system_prompt(workspace: str = "", **kwargs) -> str:
    """Return only the rendered system text (compatibility helper)."""
    return build_prompt(workspace, **kwargs).system
