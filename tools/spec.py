# tools/spec.py
"""Tool declaration types shared by every tool module.

Each tool module declares its own :class:`ToolSpec` entries next to the tool
implementation: the model-visible description lives in the tool docstring, the
parameter documentation lives in that same docstring, and the settings-panel
fields live in the entry declared beside the builder. :mod:`tools.registry`
only aggregates the entries; it does not own any tool text.

The declaration also carries the exposure metadata that decides whether a tool
is sent to the model on every request (resident) or behind the tool-search
control (deferred), plus the capability row the permission engine reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from tools.context import ToolContext

# Risk levels: read = read-only; write = modifies files in the workspace; exec = runs arbitrary commands.
RISK_READ = "read"
RISK_WRITE = "write"
RISK_EXEC = "exec"
RISK_LEVELS = ("low", "medium", "high", "critical")

# Capability matrix values, aligned with the permission engine's vocabulary.
SIDE_EFFECT_NONE = "none"
SIDE_EFFECT_WORKSPACE = "workspace"
SIDE_EFFECT_GIT = "git"
SIDE_EFFECT_NETWORK = "network"
SIDE_EFFECT_SYSTEM = "system"
SIDE_EFFECT_SESSION = "session"
SIDE_EFFECT_USER_INTERACTION = "userInteraction"
SIDE_EFFECT_SCOPES = (
    SIDE_EFFECT_NONE,
    SIDE_EFFECT_WORKSPACE,
    SIDE_EFFECT_GIT,
    SIDE_EFFECT_NETWORK,
    SIDE_EFFECT_SYSTEM,
    SIDE_EFFECT_SESSION,
    SIDE_EFFECT_USER_INTERACTION,
)

# Field kinds: determine the settings-panel control and how values are validated on save.
FIELD_TEXT = "text"
FIELD_NUMBER = "number"
FIELD_SELECT = "select"
FIELD_SWITCH = "switch"
FIELD_RADIO = "radio"
FIELD_KINDS = (FIELD_TEXT, FIELD_NUMBER, FIELD_SELECT, FIELD_SWITCH, FIELD_RADIO)

Builder = Callable[[Mapping[str, str], ToolContext], Sequence]


@dataclass(frozen=True)
class ToolField:
    """One editable tool parameter in the settings panel.

    Every field except ``key`` / ``label`` has a default, so new tools can use only
    what they need:

    - ``secret``: secret value, never echoed back; empty means keep the current value;
    - ``multiline``: multi-line text box (effective when ``kind="text"``);
    - ``kind``: control type, see ``FIELD_KINDS``;
    - ``options``: candidates for ``kind="select"`` or ``kind="radio"``, as ``((value, display name), ...)``;
    - ``enabled_when``: optional ``(field_key, value)`` rule for dimming the field in the panel;
    - ``required``: UI marker only (the tool itself reports whether the value is empty);
    - ``default``: initial value when the configuration file has no such key.
    """

    key: str
    label: str
    secret: bool = False
    multiline: bool = False
    placeholder: str = ""
    kind: str = FIELD_TEXT
    options: tuple[tuple[str, str], ...] = ()
    required: bool = False
    default: str = ""
    enabled_when: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"未知的字段类型 {self.kind!r}（{self.key}）")
        if self.kind in {FIELD_SELECT, FIELD_RADIO} and not self.options:
            raise ValueError(f"字段 {self.key} 是 {self.kind}，但没有给出 options")

    def public(self) -> dict:
        """Frontend-facing structure; value-related fields are filled in by :class:`ToolStore`."""
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "multiline": self.multiline,
            "placeholder": self.placeholder,
            "kind": self.kind,
            "options": [{"value": value, "label": label} for value, label in self.options],
            "required": self.required,
            "enabled_when": (
                {"key": self.enabled_when[0], "value": self.enabled_when[1]}
                if self.enabled_when
                else None
            ),
        }

    def normalize(self, value: str) -> str:
        """Validate and normalize the value to save according to the field type.

        Raises:
            ValueError: The value does not match the field type (converted to 400 by the API).
        """
        text = str(value)
        if self.kind == FIELD_SWITCH:
            return "true" if text.strip().lower() in {"1", "true", "on", "yes"} else "false"
        if self.kind == FIELD_NUMBER and text.strip():
            try:
                float(text)
            except ValueError as exc:
                raise ValueError(f"「{self.label}」需要填写数字") from exc
        if self.kind in {FIELD_SELECT, FIELD_RADIO}:
            allowed = {item for item, _label in self.options}
            if text.strip() and text not in allowed:
                raise ValueError(f"「{self.label}」只能是：{'、'.join(sorted(allowed))}")
        return text


@dataclass(frozen=True)
class ToolSpec:
    """One tool entry that can be enabled or disabled.

    Attributes:
        name: Entry name, also the key for /api/tools/{name} and data/tools.json.
        label: Display name in the settings panel.
        description: One-line description shown to the user.
        fields: Parameters the user needs to fill in.
        risk: Risk level, see ``RISK_*``.
        default_enabled: Whether the entry is enabled by default when data/tools.json is first generated.
        experimental: Whether the entry is marked experimental in the settings panel.
        tool_names: Names of the tool functions actually exposed to the model (one per entry).
        builder: Assembly function of type ``(config, ctx) -> Sequence[BaseTool]``.
        source: Built-in or externally installed source classification.
        source_id: Stable identifier of the supplying plugin or MCP server.
        capability: Capability-matrix overrides applied on top of the risk base row.
        defer: Hide the schemas behind the tool-search control instead of sending
            them on every request. Resident tools set this to ``False``.
        always_load: Keep a deferred-source tool resident (extensions may opt out
            of deferral); resident tools ignore it.
        aliases: Extra model-visible names accepted for the same tool.
        search_hint: Extra words used by tool search, in addition to the name,
            label, description, and UI fields.
    """

    name: str
    label: str
    description: str
    fields: tuple[ToolField, ...] = ()
    risk: str = RISK_READ
    default_enabled: bool = True
    experimental: bool = False
    tool_names: tuple[str, ...] = ()
    builder: Builder | None = field(default=None, repr=False)
    source: str = "builtin"
    source_id: str = ""
    capability: Mapping[str, Any] = field(default_factory=dict)
    defer: bool = False
    always_load: bool = False
    aliases: tuple[str, ...] = ()
    search_hint: str = ""


def single_tool(builder: Builder, name: str) -> Builder:
    """Reuse an existing multi-tool builder for exactly one named tool."""

    def build(config: Mapping[str, str], ctx: ToolContext) -> Sequence:
        return [tool for tool in builder(config, ctx) if getattr(tool, "name", "") == name]

    return build
