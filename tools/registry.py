# tools/registry.py
"""Tool registry: aggregation only.

Each tool module declares its own entries next to the implementation (see
:mod:`tools.spec`). This module collects those entries in one table and exposes
the three operations every entry point needs:

- :data:`TOOL_SPECS` — the aggregated table used by the settings panel, the CLI,
  and the web chat;
- :func:`build_tools` — assemble the concrete tools for one turn, including the
  discovery control tools;
- :func:`capability_for` — resolve the permission capability row.

Adding a tool means adding a :class:`~tools.spec.ToolSpec` in its module. No
tool name list or tool description is maintained here.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Mapping

from tools.context import ToolContext
from tools.spec import (
    FIELD_KINDS,
    FIELD_NUMBER,
    FIELD_RADIO,
    FIELD_SELECT,
    FIELD_SWITCH,
    FIELD_TEXT,
    RISK_EXEC,
    RISK_LEVELS,
    RISK_READ,
    RISK_WRITE,
    SIDE_EFFECT_GIT,
    SIDE_EFFECT_NETWORK,
    SIDE_EFFECT_NONE,
    SIDE_EFFECT_SCOPES,
    SIDE_EFFECT_SESSION,
    SIDE_EFFECT_SYSTEM,
    SIDE_EFFECT_USER_INTERACTION,
    SIDE_EFFECT_WORKSPACE,
    ToolField,
    ToolSpec,
    single_tool,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
from tools.atomic_edit import TOOL_ENTRIES as _ATOMIC_EDIT_ENTRIES  # noqa: E402
from tools.catalog import TOOL_ENTRIES as _CATALOG_ENTRIES  # noqa: E402
from tools.documents import TOOL_ENTRIES as _DOCUMENT_ENTRIES  # noqa: E402
from tools.download import TOOL_ENTRIES as _DOWNLOAD_ENTRIES  # noqa: E402
from tools.fetch import TOOL_ENTRIES as _FETCH_ENTRIES  # noqa: E402
from tools.files import TOOL_ENTRIES as _FILE_ENTRIES  # noqa: E402
from tools.git import TOOL_ENTRIES as _GIT_ENTRIES  # noqa: E402
from tools.images import TOOL_ENTRIES as _IMAGE_ENTRIES  # noqa: E402
from tools.mock import TOOL_ENTRIES as _MOCK_ENTRIES  # noqa: E402
from tools.patch import TOOL_ENTRIES as _PATCH_ENTRIES  # noqa: E402
from tools.processes import TOOL_ENTRIES as _PROCESS_ENTRIES  # noqa: E402
from tools.questions import TOOL_ENTRIES as _QUESTION_ENTRIES  # noqa: E402
from tools.search import TOOL_ENTRIES as _SEARCH_ENTRIES  # noqa: E402
from tools.shell import TOOL_ENTRIES as _SHELL_ENTRIES  # noqa: E402
from tools.subagent import TOOL_ENTRIES as _SUBAGENT_ENTRIES  # noqa: E402
from tools.time import TOOL_ENTRIES as _TIME_ENTRIES  # noqa: E402
from tools.todo import TOOL_ENTRIES as _TODO_ENTRIES  # noqa: E402
from tools.weather import TOOL_ENTRIES as _WEATHER_ENTRIES  # noqa: E402

_MODULE_ENTRIES: tuple[tuple[ToolSpec, ...], ...] = (
    _TIME_ENTRIES,
    _FILE_ENTRIES,
    _DOCUMENT_ENTRIES,
    _IMAGE_ENTRIES,
    _PATCH_ENTRIES,
    _ATOMIC_EDIT_ENTRIES,
    _FETCH_ENTRIES,
    _SEARCH_ENTRIES,
    _DOWNLOAD_ENTRIES,
    _TODO_ENTRIES,
    _QUESTION_ENTRIES,
    _SUBAGENT_ENTRIES,
    _CATALOG_ENTRIES,
    _SHELL_ENTRIES,
    _PROCESS_ENTRIES,
    _GIT_ENTRIES,
    _WEATHER_ENTRIES,
    _MOCK_ENTRIES,
)


def _aggregate() -> dict[str, ToolSpec]:
    table: dict[str, ToolSpec] = {}
    for entries in _MODULE_ENTRIES:
        for spec in entries:
            if spec.name in table:
                raise RuntimeError(f"工具 {spec.name} 在多个模块中重复声明")
            table[spec.name] = spec
    return table


TOOL_SPECS: dict[str, ToolSpec] = _aggregate()

LEGACY_TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "file_read": ("list_dir", "read_file", "search_files", "find_files", "file_stat"),
    "file_write": ("write_file", "edit_file", "make_dir", "delete_file", "move_file", "copy_file"),
    "background_process": ("start_process", "stop_process"),
    "process_inspect": ("list_processes", "read_process_logs", "wait_process"),
    "git_read": ("git_status", "git_diff"),
}


def expand_enabled_names(enabled: set[str] | None) -> set[str]:
    """Expand retired entry names without changing individual tool choices."""
    if enabled is None:
        return set(TOOL_SPECS)
    return {member for name in enabled for member in LEGACY_TOOL_GROUPS.get(name, (name,))}


def default_enabled_names() -> set[str]:
    """Entry names enabled by default (used by the CLI)."""
    return {name for name, spec in tuple(TOOL_SPECS.items()) if spec.default_enabled}


def spec_of(name: str) -> ToolSpec | None:
    return TOOL_SPECS.get(name)


def _config_for(
    name: str,
    configs: Mapping[str, Mapping[str, str]],
) -> dict:
    legacy = next((group for group, members in LEGACY_TOOL_GROUPS.items() if name in members), None)
    return dict(configs.get(name, configs.get(legacy, {})) or {})


def build_tools(
    enabled: set[str] | None,
    configs: Mapping[str, Mapping[str, str]] | None,
    ctx: ToolContext,
) -> list:
    """Assemble the tools for one turn according to enabled state.

    Args:
        enabled: Set of enabled entry names; ``None`` means all enabled.
        configs: Entry name → parameter table (such as QWeather credentials);
            when omitted, tools fall back to .env.
        ctx: Workspace and output budget.

    Returns:
        List of LangChain tool instances, ordered like :data:`TOOL_SPECS`.
        Deferred tools are built too, because the executor resolves them by
        name; only the request exposure hides their schemas.
    """
    from tools.catalog import (
        TOOL_SEARCH_NAME,
        build_contracts,
        build_deferred_tool_call,
    )

    names = expand_enabled_names(enabled)
    configured = configs or {}
    built: list = []
    meta: list[tuple[ToolSpec, dict]] = []
    for name, spec in tuple(TOOL_SPECS.items()):
        if name not in names or spec.builder is None:
            continue
        config = _config_for(name, configured)
        if spec.name == TOOL_SEARCH_NAME:
            meta.append((spec, config))
            continue
        try:
            built.extend(spec.builder(config, ctx))
        except Exception:  # one entry failing to assemble must not break the whole turn
            logger.exception("Failed to assemble tool %s; skipped", name)

    contracts = build_contracts(built)
    if meta:
        meta_ctx = replace(ctx, tool_catalog=list(contracts))
        for spec, config in meta:
            try:
                built.extend(spec.builder(config, meta_ctx))
            except Exception:
                logger.exception("Failed to assemble tool %s; skipped", spec.name)
        if any(contract.deferred and not contract.always_load for contract in contracts):
            built.extend(build_deferred_tool_call())
    return built


# ---------------------------------------------------------------------------
# Capability matrix
#
# The base row is derived from the declaration's ``risk`` level; explicit
# ``capability`` overrides live next to the tool that declares them. The matrix
# describes what a tool can touch, not whether it is enabled: the permission
# engine reads these fields to decide allow / ask / deny before mode defaults.
# ---------------------------------------------------------------------------
_TOOL_ENTRIES: dict[str, str] = {
    tool_name: entry for entry, spec in TOOL_SPECS.items() for tool_name in spec.tool_names
}


def entry_for_tool(tool_name: str) -> str | None:
    """Map a concrete tool name back to its registry entry.

    Falls back to a live scan so tools registered after import (plugins/MCP) still
    resolve to the entry that declares them.
    """
    entry = _TOOL_ENTRIES.get(tool_name)
    if entry is not None:
        return entry
    for name, spec in TOOL_SPECS.items():
        if tool_name in spec.tool_names:
            return name
    return None


def capability_for(entry_name: str | None = None, *, tool_name: str | None = None) -> dict:
    """Resolve the capability matrix row for a registry entry or tool name.

    Unknown tools are treated conservatively: writable, system scope, high risk, and
    approval required, so a tool that skipped registration cannot slip through.
    """
    entry = entry_name or (entry_for_tool(tool_name) if tool_name else None)
    spec = TOOL_SPECS.get(entry) if entry else None
    risk = spec.risk if spec is not None else RISK_EXEC
    if risk == RISK_READ:
        base = {
            "read_only": True,
            "destructive": False,
            "side_effect_scope": SIDE_EFFECT_NONE,
            "risk_level": "low",
            "needs_approval": False,
            "permission_name": "read",
        }
    elif risk == RISK_WRITE:
        base = {
            "read_only": False,
            "destructive": False,
            "side_effect_scope": SIDE_EFFECT_WORKSPACE,
            "risk_level": "medium",
            "needs_approval": True,
            "permission_name": "file",
        }
    else:
        base = {
            "read_only": False,
            "destructive": True,
            "side_effect_scope": SIDE_EFFECT_SYSTEM,
            "risk_level": "high",
            "needs_approval": True,
            "permission_name": "command",
        }
    row = {
        **base,
        "always_ask": False,
        "allowed_in_plan_mode": False,
        "requires_user_interaction": False,
        "entry": entry or "",
        "tool_names": list(spec.tool_names) if spec is not None else ([tool_name] if tool_name else []),
    }
    if spec is not None:
        row.update(dict(spec.capability))
        if spec.source != "builtin":
            # Extensions (plugins / MCP) have no audited capability declaration: plan mode
            # asks for non-destructive ones instead of trusting a read risk label.
            row["permission_name"] = "mcp"
    return row


__all__ = [
    "FIELD_KINDS",
    "FIELD_NUMBER",
    "FIELD_RADIO",
    "FIELD_SELECT",
    "FIELD_SWITCH",
    "FIELD_TEXT",
    "LEGACY_TOOL_GROUPS",
    "RISK_EXEC",
    "RISK_LEVELS",
    "RISK_READ",
    "RISK_WRITE",
    "SIDE_EFFECT_GIT",
    "SIDE_EFFECT_NETWORK",
    "SIDE_EFFECT_NONE",
    "SIDE_EFFECT_SCOPES",
    "SIDE_EFFECT_SESSION",
    "SIDE_EFFECT_SYSTEM",
    "SIDE_EFFECT_USER_INTERACTION",
    "SIDE_EFFECT_WORKSPACE",
    "TOOL_SPECS",
    "ToolField",
    "ToolSpec",
    "build_tools",
    "capability_for",
    "default_enabled_names",
    "entry_for_tool",
    "expand_enabled_names",
    "spec_of",
    "single_tool",
]
