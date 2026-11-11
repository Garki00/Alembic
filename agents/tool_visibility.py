# agents/tool_visibility.py
"""Keep extended tools hidden until the session activates them.

The agent is constructed with every enabled tool so LangChain's ToolNode can
execute an extended tool as soon as it is activated. This middleware is what
hides those tools from the model: every model call gets only the basic tools,
the meta tools, and the extended tools activated for this session.

``tools_search`` mutates the activation set while a tool call is running; the
next model call goes through ``wrap_model_call`` again, so the newly activated
tool schemas appear in the same turn.
"""

from __future__ import annotations

from typing import Any, Iterable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from tools.catalog import ToolActivation
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


class ToolVisibilityMiddleware(AgentMiddleware):
    """Filter model-facing tools and block unactivated extended calls."""

    def __init__(
        self,
        *,
        activation: ToolActivation | None,
        basic_names: Iterable[str],
        extended_names: Iterable[str],
        meta_names: Iterable[str] = ("tools_search",),
    ) -> None:
        super().__init__()
        self.activation = activation
        self.basic_names = frozenset(basic_names)
        self.extended_names = frozenset(extended_names)
        self.meta_names = frozenset(meta_names)

    def visible_names(self) -> frozenset[str]:
        active = self.activation.snapshot() if self.activation is not None else frozenset()
        return self.basic_names | self.meta_names | (self.extended_names & active)

    def _visible_tools(self, tools: Iterable[Any]) -> list[Any]:
        visible = self.visible_names()
        return [tool for tool in tools if _tool_name(tool) in visible]

    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=self._visible_tools(request.tools)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(tools=self._visible_tools(request.tools)))

    def _blocked(self, request) -> ToolMessage | None:
        call = request.tool_call or {}
        name = str(call.get("name") or "")
        if name not in self.extended_names:
            return None
        active = self.activation.snapshot() if self.activation is not None else frozenset()
        if name in active:
            return None
        return ToolMessage(
            content=tool_error(
                "tool_not_activated",
                f"扩展工具「{name}」尚未加载，本次调用没有执行。",
                hint="先调用 tools_search 搜索并加载该能力；加载后同一轮即可使用。",
            ),
            tool_call_id=call.get("id"),
            name=name,
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        blocked = self._blocked(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._blocked(request)
        if blocked is not None:
            return blocked
        return await handler(request)
