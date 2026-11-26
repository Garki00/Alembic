# tools/catalog.py
"""Tool exposure: resident tools, deferred tools, and on-demand discovery.

Every tool is declared once in its own module (see :mod:`tools.spec`). This
module decides what the model sees on one request:

- **Resident** tools are sent with their full contract (name, description,
  JSON schema) on every request.
- **Deferred** tools are not sent. The system prompt announces their names; the
  model loads a schema through ``tools_search`` and executes the tool through
  ``deferred_tool_call``. Approval runs on the resolved tool name, exactly as
  if the tool had been called directly.
- The two control tools are only present while at least one deferred tool
  exists. Turning the search entry off disables deferral, so every enabled tool
  becomes resident instead of becoming unreachable.

The search syntax is: plain keywords,
``"select:<name>[,<name>...]"`` for exact names, and ``+term`` to require a
term. Regular expressions are not supported.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from tools.spec import FIELD_NUMBER, ToolField, ToolSpec

TOOL_SEARCH_NAME = "tools_search"
DEFERRED_TOOL_CALL_NAME = "deferred_tool_call"

DEFAULT_SEARCH_RESULTS = 3
MAX_SEARCH_RESULTS = 10


class ToolActivation:
    """Thread-safe set of deferred tools whose schemas this session has loaded."""

    def __init__(self, active: set[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set(active or ())

    def activate(self, names: set[str]) -> tuple[set[str], set[str]]:
        """Add names; return ``(newly_discovered, already_discovered)``."""
        with self._lock:
            incoming = set(names)
            already = incoming & self._active
            new = incoming - self._active
            self._active |= new
            return new, already

    def snapshot(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._active)

    def clear(self) -> None:
        with self._lock:
            self._active.clear()


@dataclass(frozen=True)
class ToolContract:
    """One model-facing tool contract plus the exposure metadata."""

    name: str
    entry: str
    label: str
    description: str
    input_schema: Mapping[str, Any]
    capability: Mapping[str, Any]
    deferred: bool
    always_load: bool
    aliases: tuple[str, ...]
    search_hint: str

    def definition(self) -> str:
        """One ``<function>`` line: the complete schema a deferred tool needs."""
        payload = {
            "description": self.description,
            "name": self.name,
            "parameters": dict(self.input_schema or {}),
        }
        return f"<function>{json.dumps(payload, ensure_ascii=False)}</function>"


@dataclass(frozen=True)
class ToolExposure:
    """Resolved exposure for one model request."""

    contracts: tuple[ToolContract, ...]
    visible: tuple[str, ...]
    deferred: frozenset[str]
    controls: frozenset[str]
    mode: str
    reason: str

    @property
    def enabled(self) -> bool:
        return self.mode != "off"


def _entry_for(tool_name: str) -> str | None:
    from tools.registry import entry_for_tool

    return entry_for_tool(tool_name)


def _capability_for(entry: str | None, tool_name: str) -> dict:
    from tools.registry import capability_for

    return capability_for(entry, tool_name=tool_name)


def _input_schema(tool: Any) -> Mapping[str, Any]:
    """Return the complete JSON object schema the model needs to call the tool."""
    schema = getattr(tool, "tool_call_schema", None)
    if isinstance(schema, Mapping):
        return dict(schema)
    if schema is not None and hasattr(schema, "model_json_schema"):
        try:
            return dict(schema.model_json_schema())
        except Exception:
            pass
    properties = getattr(tool, "args", None)
    if isinstance(properties, Mapping):
        return {"type": "object", "properties": dict(properties)}
    args_schema = getattr(tool, "args_schema", None)
    if hasattr(args_schema, "model_json_schema"):
        try:
            return dict(args_schema.model_json_schema())
        except Exception:
            pass
    return {"type": "object", "properties": {}}


def contract_for_tool(tool: Any) -> ToolContract:
    """Build one contract from a built tool and its registry declaration."""
    name = str(getattr(tool, "name", "") or "")
    entry = _entry_for(name) or ""
    from tools.registry import TOOL_SPECS

    spec = TOOL_SPECS.get(entry) if entry else None
    return ToolContract(
        name=name,
        entry=entry,
        label=spec.label if spec is not None else name,
        description=str(getattr(tool, "description", "") or "") or name,
        input_schema=_input_schema(tool),
        capability=_capability_for(entry or None, name),
        deferred=bool(spec.defer) if spec is not None else False,
        always_load=bool(spec.always_load) if spec is not None else False,
        aliases=tuple(spec.aliases) if spec is not None else (),
        search_hint=(
            " ".join(
                [spec.label, spec.description, spec.search_hint]
                + [field.label for field in spec.fields]
            )
            if spec is not None
            else ""
        ),
    )


def build_contracts(tools: Iterable[Any]) -> list[ToolContract]:
    """Contracts for the tools built for this turn, in build order."""
    return [contract_for_tool(tool) for tool in tools]


def _split_name(name: str) -> list[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return [part for part in re.split(r"[\s_.\-]+", spaced.lower()) if part]


def _boundary(term: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(term)}\b")


def _resolve_name(name: str, catalog: Sequence[ToolContract]) -> ToolContract | None:
    lowered = name.strip().lower()
    for contract in catalog:
        if contract.name.lower() == lowered:
            return contract
        if any(alias.lower() == lowered for alias in contract.aliases):
            return contract
    return None


def search_contracts(
    query: str,
    contracts: Sequence[ToolContract],
    limit: int = MAX_SEARCH_RESULTS,
) -> list[str]:
    """Return matching tool names; deferred tools only for keyword searches."""
    text = (query or "").strip()
    if not text:
        return []
    deferred = [
        contract for contract in contracts if contract.deferred and not contract.always_load
    ]
    select = re.match(r"(?i)^select:(.+)$", text)
    if select:
        names: list[str] = []
        for raw in select.group(1).split(","):
            name = raw.strip()
            match = _resolve_name(name, deferred) or _resolve_name(name, contracts)
            if match is not None and match.name not in names:
                names.append(match.name)
        return names

    lowered = text.lower()
    exact = next(
        (contract for contract in deferred if contract.name.lower() == lowered), None
    ) or next((contract for contract in contracts if contract.name.lower() == lowered), None)
    if exact is not None:
        return [exact.name]

    terms = [term for term in re.split(r"\s+", lowered) if term]
    required = [term[1:] for term in terms if term.startswith("+") and len(term) > 1]
    keywords = (
        [*required, *(term for term in terms if not (term.startswith("+") and len(term) > 1))]
        if required
        else terms
    )
    patterns = {term: _boundary(term) for term in keywords}
    scored: list[tuple[int, str]] = []
    for contract in deferred:
        parts = _split_name(contract.name)
        aliases = [alias.lower() for alias in contract.aliases]
        haystack = " ".join([contract.label, contract.description, contract.search_hint]).lower()
        if not all(
            any(term in part for part in parts)
            or any(term in alias for alias in aliases)
            or patterns[term].search(haystack)
            for term in required
        ):
            continue
        score = 0
        for term in keywords:
            if term in parts:
                score += 10
            elif any(term in part for part in parts):
                score += 5
            if any(term in alias for alias in aliases):
                score += 8
            if patterns[term].search(haystack):
                score += 3
        if score > 0:
            scored.append((score, contract.name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _score, name in scored[: max(1, min(int(limit), MAX_SEARCH_RESULTS))]]


def resolve_exposure(
    contracts: Sequence[ToolContract],
    activation: ToolActivation | None = None,
    *,
    turn_disallowed: Iterable[str] = (),
    search_enabled: bool = True,
) -> ToolExposure:
    """Resolve which contracts this request carries.

    ``turn_disallowed`` removes tools that cannot run in this turn (no vision
    channel, no question channel, ...). ``search_enabled`` is the user's setting
    for the search entry; turning it off makes deferred tools resident instead
    of unreachable.
    """
    disallowed = set(turn_disallowed)
    all_contracts = tuple(
        contract for contract in contracts if contract.name not in disallowed
    )
    deferred = {
        contract.name
        for contract in all_contracts
        if contract.deferred and not contract.always_load
    }
    controls = {
        contract.name
        for contract in all_contracts
        if contract.name in {TOOL_SEARCH_NAME, DEFERRED_TOOL_CALL_NAME}
    }
    if not deferred or not search_enabled:
        visible = tuple(contract.name for contract in all_contracts if contract.name not in controls)
        return ToolExposure(
            contracts=all_contracts,
            visible=visible,
            # Nothing is hidden when deferral is off, so nothing is announced as
            # deferred and no direct call needs to be redirected.
            deferred=frozenset(),
            controls=frozenset(),
            mode="off",
            reason="provider_disabled" if not search_enabled else "no_deferred_tools",
        )
    visible = tuple(
        contract.name
        for contract in all_contracts
        if not contract.deferred or contract.always_load or contract.name in controls
    )
    return ToolExposure(
        contracts=all_contracts,
        visible=visible,
        deferred=frozenset(deferred),
        controls=frozenset(controls & {TOOL_SEARCH_NAME, DEFERRED_TOOL_CALL_NAME}),
        mode="local",
        reason="local",
    )


def render_definitions(names: Iterable[str], contracts: Sequence[ToolContract]) -> str:
    """Render loaded schemas in the same shape as the request tool list."""
    by_name = {contract.name: contract for contract in contracts}
    lines = "<functions>"
    for name in names:
        contract = by_name.get(name)
        if contract is not None:
            lines += "\n" + contract.definition()
    return lines + "\n</functions>"


def clamp_limit(value: int | str | None, default: int = DEFAULT_SEARCH_RESULTS) -> int:
    try:
        number = int(float(value)) if value not in (None, "") else default
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, MAX_SEARCH_RESULTS))


def _specs() -> dict:
    from tools.registry import TOOL_SPECS

    return TOOL_SPECS


def resident_tool_names() -> frozenset[str]:
    """Concrete tools that always travel with their full contract."""
    return frozenset(
        name
        for spec in _specs().values()
        if not spec.defer
        for name in spec.tool_names
        if name not in {TOOL_SEARCH_NAME, DEFERRED_TOOL_CALL_NAME}
    )


def extended_tool_names() -> frozenset[str]:
    """Concrete tools whose schemas are hidden behind the search control."""
    return frozenset(
        name
        for spec in _specs().values()
        if spec.defer and not spec.always_load
        for name in spec.tool_names
    )


def meta_tool_names() -> frozenset[str]:
    """Control tools that are only visible while deferral is active."""
    return frozenset({TOOL_SEARCH_NAME, DEFERRED_TOOL_CALL_NAME})


def enabled_tool_names(enabled_entries: set[str] | None) -> frozenset[str]:
    """Concrete tool names available from the user's enabled entries."""
    from tools.registry import expand_enabled_names

    specs = _specs()
    names = set(specs) if enabled_entries is None else expand_enabled_names(enabled_entries)
    result: set[str] = set()
    for entry, spec in specs.items():
        if entry in names:
            result.update(spec.tool_names)
    return frozenset(result)


def entry_names_for_tools(names: Iterable[str]) -> set[str]:
    """Map concrete tool names back to registry entry names."""
    wanted = set(names)
    return {
        entry
        for entry, spec in _specs().items()
        if wanted & set(spec.tool_names)
    }


def visible_tool_names(
    enabled_names: Iterable[str],
    activation: ToolActivation | None = None,
) -> frozenset[str]:
    """Legacy helper: enabled resident names plus controls when deferral is active.

    Callers that build a request should use :func:`resolve_exposure`, which also
    applies per-turn disallow rules and returns the deferred announcement.
    """
    enabled = set(enabled_names)
    deferred = extended_tool_names()
    visible = enabled - deferred
    if deferred & enabled and TOOL_SEARCH_NAME in enabled:
        visible.add(TOOL_SEARCH_NAME)
        visible.add(DEFERRED_TOOL_CALL_NAME)
    return frozenset(visible)


def is_visible(
    name: str,
    enabled_names: Iterable[str],
    activation: ToolActivation | None = None,
) -> bool:
    return name in visible_tool_names(enabled_names, activation)


def turn_disallowed_tools(ctx: Any) -> set[str]:
    """Tools that cannot run with this turn's runtime channels."""
    disallowed: set[str] = set()
    if getattr(ctx, "vision", None) is None:
        disallowed.add("read_image")
    if getattr(ctx, "asker", None) is None:
        disallowed.add("ask_user_question")
    return disallowed


def build_tools_search(config: Mapping[str, str], ctx: Any) -> list:
    """Build the ``tools_search`` control tool for one agent turn."""
    from langchain.tools import tool

    from tools.errors import tool_error

    default_limit = clamp_limit((config or {}).get("TOOLS_SEARCH_LIMIT"))

    @tool
    def tools_search(query: str, limit: int = 0) -> str:
        """Load the complete schema of deferred tools so they can be executed.

        Deferred tools appear by name only; until this tool returns a schema the
        tool cannot be called. Prefer this over guessing a tool name or its
        parameters. Section headers and status lines inside the result are not
        part of the schema.

        Args:
            query: Space-separated keywords for a broad search, or
                "select:<name>[,<name>...]" to fetch exact tools by name.
                "+term" requires that term in the name. Regular expressions are
                not supported; characters such as "|" and ".*" are literal.
            limit: Maximum number of tools to return (1-10); 0 uses the
                configured default.

        Returns:
            A ``<functions>`` block with one JSON schema per matched tool, plus a
            state line naming tools that were already loaded.
        """
        text = (query or "").strip()
        if not text:
            return tool_error(
                "empty_query",
                "query must not be empty.",
                hint='Describe the capability, e.g. "PDF document", or use "select:<name>".',
            )
        contracts = list(getattr(ctx, "tool_catalog", ()) or ())
        if not contracts:
            return tool_error(
                "catalog_unavailable",
                "This session has no tool catalog, so no schema can be loaded.",
            )
        if ctx.activation is None:
            return tool_error(
                "activation_unavailable",
                "This session has no tool discovery state.",
            )
        try:
            requested = int(limit or 0)
        except (TypeError, ValueError):
            requested = 0
        wanted = clamp_limit(requested, default_limit) if requested > 0 else default_limit
        matched = search_contracts(text, contracts, wanted)
        if not matched:
            return (
                f"No tool matches: {text}.\n"
                'Try broader capability words, or "select:<name>" with an exact name from the deferred list.'
            )
        activation = ctx.activation
        new, already = activation.activate(set(matched))
        body = render_definitions(matched, contracts)
        lines = [body]
        if new:
            lines.append(f"Loaded: {', '.join(sorted(new))}. Call each one through deferred_tool_call.")
        if already:
            lines.append(
                f"Already loaded in this session: {', '.join(sorted(already))}. "
                "Reusing the schema above does not require another search."
            )
        return "\n\n".join(lines)

    return [tools_search]


def build_deferred_tool_call() -> list:
    """Build the ``deferred_tool_call`` executor.

    The call is rewritten to the resolved tool by the exposure middleware, so
    approval, command policy, and the tool handler all see the real tool name.
    Reaching this function body means no middleware was installed.
    """
    from langchain.tools import tool

    from tools.errors import tool_error

    @tool
    def deferred_tool_call(name: str, input: dict | None = None) -> str:
        """Execute a deferred tool by name using the schema returned by tools_search.

        Args:
            name: Exact deferred tool name from the loaded schema.
            input: Argument object matching that tool's ``parameters`` schema;
                omit it when the tool takes no arguments.

        Returns:
            The tool result. Approval and policy run on the named tool, exactly
            as for a direct call.
        """
        return tool_error(
            "deferred_execution_unavailable",
            f"Deferred execution is not installed, so {name!r} was not run.",
            hint="Call tools_search again, or restart the session runtime.",
        )

    return [deferred_tool_call]


TOOL_ENTRIES: tuple[ToolSpec, ...] = (
    ToolSpec(
        name=TOOL_SEARCH_NAME,
        label="扩展工具搜索",
        description="搜索并加载扩展工具",
        fields=(
            ToolField(
                "TOOLS_SEARCH_LIMIT",
                "每次最多加载",
                kind=FIELD_NUMBER,
                default="3",
                placeholder="1-10，默认 3",
            ),
        ),
        tool_names=(TOOL_SEARCH_NAME,),
        builder=build_tools_search,
    ),
)
