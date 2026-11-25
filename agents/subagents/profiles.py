# agents/subagents/profiles.py
"""Subagent profiles: built-in agents plus project and user Markdown definitions.

A profile describes what a child agent may do: its prompt, the tools it can see,
an optional model, and the strictest permission mode it may run under. Profiles
are resolved by name, with project definitions overriding user definitions and
both overriding the built-ins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

import yaml

logger = logging.getLogger(__name__)

GENERAL_PURPOSE = "general-purpose"
EXPLORE = "Explore"

DEFAULT_SUBAGENT_TYPE = GENERAL_PURPOSE

#: Tools that must never reach a child agent: they would allow unbounded nesting.
NESTING_TOOLS = ("agent", "send_message")

#: Read-only tool surface for the built-in Explore agent. ``run_command`` is
#: excluded because command risk is decided per invocation, not by an AST-level
#: read-only classifier.
EXPLORE_TOOLS = (
    "list_dir",
    "read_file",
    "search_files",
    "find_files",
    "file_stat",
    "read_document",
    "read_image",
    "fetch_url",
    "web_search",
    "todo_write",
    "get_current_time",
    "list_timezones",
    "tools_search",
)

#: Strictest first; a profile may only narrow the parent mode.
PERMISSION_MODES = ("plan", "build", "edit", "yolo")
LEGACY_PERMISSION_MODES = {
    "read_only": "plan",
    "approval_all": "build",
    "workspace_write": "build",
    "full_access": "yolo",
}
_MODE_RANK = {name: index for index, name in enumerate(PERMISSION_MODES)}
THINKING_LEVELS = ("none", "low", "high", "max")


@dataclass(frozen=True)
class AgentProfile:
    """One subagent definition.

    Attributes:
        name: Identifier used by the ``agent`` tool and shown to the model.
        description: One-line capability summary used in the tool prompt.
        prompt: System prompt body for the child agent.
        tools: Explicit tool allowlist; ``None`` inherits every parent tool.
        disallowed: Tool names removed from the child surface after inheritance.
        model: Optional model id or display name; ``None`` inherits the parent.
        thinking: Optional reasoning level override for the child model.
        permission_mode: Optional cap on the parent permission mode.
        inject_context: Whether project context files are injected for the child.
        background: Launch this profile in the background by default.
        source: ``built-in``, ``user``, or ``project``.
        path: Markdown file the profile was read from, if any.
    """

    name: str
    description: str
    prompt: str = ""
    tools: tuple[str, ...] | None = None
    disallowed: tuple[str, ...] = ()
    model: str | None = None
    thinking: str | None = None
    permission_mode: str | None = None
    inject_context: bool = True
    background: bool = False
    source: str = "built-in"
    path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def child_tools(self, parent_tools: Iterable[str]) -> tuple[str, ...]:
        """Return the child tool surface: parent tools intersected with the profile."""
        available = {str(name) for name in parent_tools}
        selected = available if self.tools is None else available & set(self.tools)
        removed = set(self.disallowed) | set(NESTING_TOOLS)
        return tuple(sorted(selected - removed))

    def restrict_mode(self, parent_mode: str) -> str:
        """Return the effective permission mode, which can only become stricter."""
        if not self.permission_mode:
            return parent_mode
        requested = self.permission_mode
        if requested not in _MODE_RANK:
            return parent_mode
        parent = parent_mode if parent_mode in _MODE_RANK else "build"
        return requested if _MODE_RANK[requested] < _MODE_RANK[parent] else parent


GENERAL_PURPOSE_PROMPT = """You are an ALEMBIC agent. Given the task, use the tools available to complete it fully: do not gold-plate, and do not leave it half-done.

Your strengths:
- Searching code, configuration, and patterns across large repositories.
- Reading several files to understand how a feature works.
- Multi-step research that ends in a conclusion rather than a file dump.

Guidelines:
- Search broadly when you do not know where something lives; read directly when you do.
- Prefer the dedicated file tools over shell commands for reading and editing.
- Never create report, summary, findings, or analysis files. Return the report as your final assistant message.
- If a tool fails, adapt or report the blocker; do not invent a result.
- Reply with a concise report: what you did, what you found, and anything the caller must know. Include absolute file paths."""

EXPLORE_PROMPT = """You are ALEMBIC Explore, a read-only codebase research specialist.

=== READ-ONLY MODE ===
You must not create, modify, move, copy, or delete any file, and you must not change system state. You have no file-editing tools. Search and read only.

Your strengths:
- Finding files with patterns.
- Searching contents with regular expressions.
- Reading and analysing excerpts to answer a question.

Guidelines:
- Use search_files and find_files to locate candidates, then read_file for the exact text.
- Read excerpts rather than whole files; return the conclusion, not the file dumps.
- Adapt the breadth of the search to the requested thoroughness.
- Return findings directly as your final message with absolute file paths. Never write a report file."""


def _split_list(value: Any) -> tuple[str, ...]:
    """Normalize a YAML value into a tuple of non-empty strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        raw = value.replace("\n", ",")
        parts = [item.strip() for item in raw.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value]
    else:
        parts = [str(value).strip()]
    return tuple(item for item in parts if item)


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def parse_profile_markdown(text: str, *, source: str, path: str = "") -> AgentProfile | None:
    """Parse one Markdown profile.

    Frontmatter keys accept both camelCase and snake_case. A profile without a
    name is rejected; malformed YAML is reported and skipped so one broken file
    cannot hide the other definitions.
    """
    body = text
    meta: dict[str, Any] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                loaded = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as exc:
                logger.warning("跳过无法解析的子代理配置 %s：%s", path, exc)
                return None
            if isinstance(loaded, dict):
                meta = loaded
            body = parts[2]

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in meta:
                return meta[key]
        return None

    name = str(pick("name") or "").strip()
    if not name:
        logger.warning("子代理配置缺少 name，已跳过：%s", path)
        return None
    description = str(pick("description") or "").strip()
    tools_value = pick("tools", "allowedTools", "allowed_tools")
    tools = _split_list(tools_value) if tools_value not in (None, "*") else None
    disallowed = _split_list(pick("disallowedTools", "disallowed_tools"))
    model = str(pick("model") or "").strip() or None
    thinking = str(
        pick("thinking", "reasoningLevel", "reasoning_level") or ""
    ).strip().lower() or None
    permission = str(pick("permissionMode", "permission_mode") or "").strip().lower() or None
    if permission is not None:
        permission = LEGACY_PERMISSION_MODES.get(permission, permission)
    if permission not in PERMISSION_MODES:
        permission = None
    return AgentProfile(
        name=name,
        description=description or f"{name} subagent",
        prompt=body.strip(),
        tools=tools,
        disallowed=disallowed,
        model=model,
        thinking=thinking,
        permission_mode=permission,
        inject_context=_parse_bool(
            pick("injectContext", "inject_context", "injectAgentsMd", "inject_agents_md"),
            True,
        ),
        background=_parse_bool(pick("background"), False),
        source=source,
        path=path,
        metadata=dict(meta),
    )


def builtin_profiles() -> list[AgentProfile]:
    """Return the built-in profiles, general-purpose first."""
    return [
        AgentProfile(
            name=GENERAL_PURPOSE,
            description=(
                "General-purpose agent for multi-step tasks, code search, and research. "
                "Use it when the work spans several files or would flood the parent context."
            ),
            prompt=GENERAL_PURPOSE_PROMPT,
            tools=None,
            source="built-in",
        ),
        AgentProfile(
            name=EXPLORE,
            description=(
                "Read-only search agent for broad codebase exploration. Returns conclusions "
                "and absolute paths, not file dumps."
            ),
            prompt=EXPLORE_PROMPT,
            tools=EXPLORE_TOOLS,
            source="built-in",
        ),
    ]


def _read_profile_dir(directory: Path, source: str) -> list[AgentProfile]:
    if not directory.is_dir():
        return []
    profiles: list[AgentProfile] = []
    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取子代理配置失败 %s：%s", path, exc)
            continue
        profile = parse_profile_markdown(text, source=source, path=str(path))
        if profile is not None:
            profiles.append(profile)
    return profiles


def load_profiles(
    *,
    user_dir: str | Path | None = None,
    workspace: str | Path | None = None,
) -> list[AgentProfile]:
    """Resolve the active profile set.

    Precedence is built-in, then user, then project; a later definition with the
    same name replaces the earlier one entirely.
    """
    resolved: dict[str, AgentProfile] = {item.name: item for item in builtin_profiles()}
    for directory, source in (
        (Path(user_dir) if user_dir else None, "user"),
        (Path(workspace) / ".alembic" / "agents" if workspace else None, "project"),
    ):
        if directory is None:
            continue
        for profile in _read_profile_dir(directory, source):
            resolved[profile.name] = profile
    return list(resolved.values())


def find_profile(profiles: Iterable[AgentProfile], name: str | None) -> AgentProfile | None:
    """Resolve a profile by exact or case-insensitive name; empty means the default."""
    wanted = (name or DEFAULT_SUBAGENT_TYPE).strip()
    items = list(profiles)
    for profile in items:
        if profile.name == wanted:
            return profile
    lowered = wanted.lower()
    for profile in items:
        if profile.name.lower() == lowered:
            return profile
    return None


def format_profiles_for_prompt(profiles: Iterable[AgentProfile]) -> str:
    """Render the agent type list shown to the parent model."""
    lines = ["Available agent types:"]
    for profile in profiles:
        tools = "inherit parent tools" if profile.tools is None else ", ".join(profile.tools)
        lines.append(f"- {profile.name}: {profile.description} (Tools: {tools})")
    return "\n".join(lines)


def with_model(profile: AgentProfile, model: str | None) -> AgentProfile:
    """Return a copy of ``profile`` with a resolved model override."""
    return replace(profile, model=model) if model is not None else profile
