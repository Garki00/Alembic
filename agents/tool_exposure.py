# agents/tool_exposure.py
"""Request exposure middleware: resident schemas, deferred discovery, execution.

The agent is constructed with every enabled tool so the tool node can execute a
deferred tool by name. This middleware is what keeps the request small:

- model calls carry only resident contracts plus the two control tools;
- calling a deferred tool directly returns a repair message instead of running
  it, because the model never received its schema;
- ``deferred_tool_call`` is rewritten into a real call before the approval
  middleware runs, so approval, command policy, and the tool handler all see the
  real tool name and arguments.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from tools.catalog import (
    DEFERRED_TOOL_CALL_NAME,
    TOOL_SEARCH_NAME,
    ToolActivation,
    ToolContract,
    ToolExposure,
)
from tools.errors import tool_error


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        if isinstance(tool.get("name"), str):
            return tool["name"]
        function = tool.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or "")
        return ""
    return str(getattr(tool, "name", "") or "")


class ToolExposureMiddleware(AgentMiddleware):
    """Filter request schemas and route deferred execution through approval."""

    def __init__(
        self,
        *,
        contracts: Sequence[ToolContract],
        exposure: ToolExposure,
        activation: ToolActivation | None,
        tools: Iterable[Any] = (),
        turn_disallowed: Iterable[str] = (),
    ) -> None:
        super().__init__()
        self.contracts = tuple(contracts)
        self.exposure = exposure
        self.activation = activation
        self.by_name: dict[str, Any] = {
            _tool_name(tool): tool for tool in tools if _tool_name(tool)
        }
        self.deferred = exposure.deferred
        self.visible = frozenset(exposure.visible)
        self.disallowed = frozenset(turn_disallowed)

    # ---- model request ----
    def _visible_tools(self, tools: Iterable[Any]) -> list[Any]:
        return [tool for tool in tools if _tool_name(tool) in self.visible]

    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=self._visible_tools(request.tools)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(tools=self._visible_tools(request.tools)))

    # ---- tool execution ----
    def _message(self, call: Mapping[str, Any], content: str, *, status: str = "error") -> ToolMessage:
        return ToolMessage(
            content=content,
            tool_call_id=call.get("id"),
            name=str(call.get("name") or ""),
            status=status,
        )

    def _direct_denied(self, name: str) -> str | None:
        if name in self.disallowed:
            return tool_error(
                "tool_unavailable_this_turn",
                f"{name} cannot run in this turn's runtime.",
                hint="Use another available capability; do not retry the same call.",
            )
        if name in self.deferred:
            hint = (
                f'Call {TOOL_SEARCH_NAME} with "select:{name}" to load its schema, '
                f"then call {DEFERRED_TOOL_CALL_NAME} with that name."
            )
            return tool_error("tool_not_loaded", f"{name} was not executed.", hint=hint)
        return None

    def _deferred_call(self, request, handler):
        args = request.tool_call.get("args") or {}
        if not isinstance(args, Mapping):
            return self._message(
                request.tool_call,
                tool_error("bad_deferred_call", "input must be an object."),
            )
        name = str(args.get("name") or "").strip()
        payload = args.get("input")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return self._message(
                request.tool_call,
                tool_error("bad_deferred_input", "input must be a JSON object."),
            )
        if not name:
            return self._message(
                request.tool_call,
                tool_error("empty_tool_name", "name must not be empty."),
            )
        if name not in self.deferred:
            return self._message(
                request.tool_call,
                tool_error(
                    "not_a_deferred_tool",
                    f"{name} is not a deferred tool.",
                    hint="Resident tools are called directly; use tools_search to find a deferred capability.",
                ),
            )
        discovered = self.activation.snapshot() if self.activation is not None else frozenset()
        if name not in discovered:
            return self._message(
                request.tool_call,
                tool_error(
                    "schema_not_loaded",
                    f"The schema of {name} has not been loaded in this session.",
                    hint=f'Call {TOOL_SEARCH_NAME} with "select:{name}" first.',
                ),
            )
        tool = self.by_name.get(name)
        if tool is None:
            return self._message(
                request.tool_call,
                tool_error(
                    "tool_unavailable",
                    f"{name} is no longer available.",
                    hint="The tool set changed; search again for the capability.",
                ),
            )
        inner_call = {
            "name": name,
            "args": payload,
            "id": request.tool_call.get("id"),
        }
        result = handler(request.override(tool=tool, tool_call=inner_call))
        if isinstance(result, ToolMessage):
            result.name = DEFERRED_TOOL_CALL_NAME
        return result

    def wrap_tool_call(self, request, handler):
        name = str((request.tool_call or {}).get("name") or "")
        if name == DEFERRED_TOOL_CALL_NAME:
            return self._deferred_call(request, handler)
        denied = self._direct_denied(name)
        if denied is not None:
            return self._message(request.tool_call or {}, denied)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        name = str((request.tool_call or {}).get("name") or "")
        if name == DEFERRED_TOOL_CALL_NAME:
            return await self._adeferred_call(request, handler)
        denied = self._direct_denied(name)
        if denied is not None:
            return self._message(request.tool_call or {}, denied)
        return await handler(request)

    async def _adeferred_call(self, request, handler):
        args = request.tool_call.get("args") or {}
        if not isinstance(args, Mapping):
            return self._message(
                request.tool_call, tool_error("bad_deferred_call", "input must be an object.")
            )
        name = str(args.get("name") or "").strip()
        payload = args.get("input")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return self._message(
                request.tool_call,
                tool_error("bad_deferred_input", "input must be a JSON object."),
            )
        if name not in self.deferred:
            return self._message(
                request.tool_call,
                tool_error("not_a_deferred_tool", f"{name} is not a deferred tool."),
            )
        discovered = self.activation.snapshot() if self.activation is not None else frozenset()
        if name not in discovered:
            return self._message(
                request.tool_call,
                tool_error(
                    "schema_not_loaded",
                    f"The schema of {name} has not been loaded in this session.",
                    hint=f'Call {TOOL_SEARCH_NAME} with "select:{name}" first.',
                ),
            )
        tool = self.by_name.get(name)
        if tool is None:
            return self._message(
                request.tool_call,
                tool_error("tool_unavailable", f"{name} is no longer available."),
            )
        inner_call = {"name": name, "args": payload, "id": request.tool_call.get("id")}
        result = await handler(request.override(tool=tool, tool_call=inner_call))
        if isinstance(result, ToolMessage):
            result.name = DEFERRED_TOOL_CALL_NAME
        return result
