# tests/tool_activation_flow_test.py
"""tools_search loads a schema; deferred_tool_call executes it in the same turn."""

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agents.react import ReActAgent
from agents.tool_exposure import ToolExposureMiddleware
from tools.catalog import (
    DEFERRED_TOOL_CALL_NAME,
    TOOL_SEARCH_NAME,
    ToolActivation,
    build_contracts,
    enabled_tool_names,
    resolve_exposure,
)
from tools.context import ToolContext
from tools.registry import build_tools


class ScriptedModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages)
        return AIMessage(content=self.replies.pop(0))


class ToolFake(FakeMessagesListChatModel):
    """FakeMessagesListChatModel does not implement bind_tools by default."""

    def bind_tools(self, tools, **kwargs):
        return self


class Recorder(AgentMiddleware):
    def __init__(self):
        super().__init__()
        self.calls: list[list[str]] = []

    def wrap_model_call(self, request, handler):
        self.calls.append([getattr(tool, "name", "") for tool in request.tools])
        return handler(request)


class ActivationFlowTest(unittest.TestCase):
    def test_search_then_deferred_call_in_same_turn(self):
        activation = ToolActivation()
        enabled = enabled_tool_names({"tools_search", "mock_data", "file_read"})
        workspace = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
        workspace.mkdir(parents=True, exist_ok=True)
        root = workspace / f"activation_{uuid4().hex[:10]}"
        root.mkdir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        ctx = ToolContext(
            workspace=root,
            activation=activation,
            enabled_tool_names=enabled,
        )
        tools = build_tools({"tools_search", "mock_data", "file_read"}, {}, ctx)
        model = ToolFake(responses=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "tools_search",
                    "args": {"query": "模拟数据"},
                    "id": "c1",
                }],
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": DEFERRED_TOOL_CALL_NAME,
                    "args": {"name": "mock_lookup", "input": {}},
                    "id": "c2",
                }],
            ),
            AIMessage(content="done"),
        ])
        contracts = build_contracts(tools)
        exposure = resolve_exposure(contracts, activation)
        middleware = ToolExposureMiddleware(
            contracts=contracts,
            exposure=exposure,
            activation=activation,
            tools=tools,
        )
        recorder = Recorder()
        agent = create_agent(model, tools=tools, middleware=[middleware, recorder])
        result = agent.invoke({"messages": [{"role": "user", "content": "test"}]})

        self.assertEqual(len(recorder.calls), 3)
        first = set(recorder.calls[0])
        self.assertIn("read_file", first)
        self.assertIn(TOOL_SEARCH_NAME, first)
        self.assertIn(DEFERRED_TOOL_CALL_NAME, first)
        self.assertNotIn("mock_lookup", first)
        # Loading the schema does not promote it into the request tool list.
        self.assertNotIn("mock_lookup", set(recorder.calls[1]))
        self.assertIn("mock_lookup", activation.snapshot())
        self.assertEqual(result["messages"][-1].content, "done")


class ReactActivationFlowTest(unittest.TestCase):
    def test_react_sees_deferred_name_and_executes_through_control(self):
        activation = ToolActivation()
        enabled = enabled_tool_names({"tools_search", "mock_data", "file_read"})
        ctx = ToolContext(
            workspace=Path.cwd(),
            activation=activation,
            enabled_tool_names=enabled,
        )
        tools = build_tools({"tools_search", "mock_data", "file_read"}, {}, ctx)
        contracts = build_contracts(tools)
        exposure = resolve_exposure(contracts, activation)
        model = ScriptedModel([
            'Thought: search it.\nAction: tools_search\nAction Input: {"query": "模拟数据"}',
            'Thought: call it.\nAction: deferred_tool_call\nAction Input: {"name": "mock_lookup", "input": {}}',
            "Thought: done\nFinal Answer: ok",
        ])
        agent = ReActAgent(
            model,
            tools,
            "base",
            activation=activation,
            basic_names=[name for name in exposure.visible if name not in exposure.controls],
            extended_names=sorted(exposure.deferred),
            meta_names=sorted(exposure.controls),
            deferred_names=sorted(exposure.deferred),
        )
        result = agent.invoke({"messages": [HumanMessage(content="go")]})

        self.assertIn("mock_lookup", activation.snapshot())
        self.assertIn("mock_lookup", str(model.prompts[0]))
        self.assertIn("mock_lookup", str(model.prompts[1]))
        self.assertEqual(result["messages"][-1].content, "ok")


if __name__ == "__main__":
    unittest.main()
