# tests/tool_visibility_test.py
"""Extended tools stay out of the model schema until tools_search loads them."""

import unittest
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from agents.tool_visibility import ToolVisibilityMiddleware
from tools.catalog import ToolActivation


def fake_tool(name: str):
    return SimpleNamespace(name=name)


class FakeModelRequest:
    def __init__(self, tools, tool_call=None):
        self.tools = tools
        self.tool_call = tool_call or {}

    def override(self, **changes):
        data = {"tools": self.tools, "tool_call": self.tool_call}
        data.update(changes)
        return FakeModelRequest(**data)


class VisibilityTest(unittest.TestCase):
    def setUp(self):
        self.activation = ToolActivation()
        self.middleware = ToolVisibilityMiddleware(
            activation=self.activation,
            basic_names={"read_file", "run_command"},
            extended_names={"git_commit", "read_document"},
            meta_names={"tools_search"},
        )
        self.tools = [
            fake_tool("read_file"),
            fake_tool("run_command"),
            fake_tool("tools_search"),
            fake_tool("git_commit"),
            fake_tool("read_document"),
        ]

    def visible(self):
        seen = {}

        def handler(request):
            seen["names"] = [tool.name for tool in request.tools]
            return "ok"

        result = self.middleware.wrap_model_call(FakeModelRequest(self.tools), handler)
        self.assertEqual(result, "ok")
        return seen["names"]

    def test_extended_tools_are_hidden_initially(self):
        self.assertEqual(self.visible(), ["read_file", "run_command", "tools_search"])

    def test_activated_extended_tool_becomes_visible(self):
        self.activation.activate({"git_commit"})
        self.assertEqual(
            self.visible(),
            ["read_file", "run_command", "tools_search", "git_commit"],
        )

    def test_unactivated_extended_call_is_blocked(self):
        request = FakeModelRequest(
            self.tools,
            tool_call={"name": "git_commit", "args": {}, "id": "call-1"},
        )
        result = self.middleware.wrap_tool_call(request, lambda _req: "executed")
        self.assertIsInstance(result, ToolMessage)
        self.assertIn("[tool_not_activated]", result.content)
        self.assertEqual(result.tool_call_id, "call-1")

    def test_activated_extended_call_is_allowed(self):
        self.activation.activate({"git_commit"})
        request = FakeModelRequest(
            self.tools,
            tool_call={"name": "git_commit", "args": {}, "id": "call-1"},
        )
        self.assertEqual(self.middleware.wrap_tool_call(request, lambda _req: "executed"), "executed")

    def test_basic_tool_call_is_always_allowed(self):
        request = FakeModelRequest(
            self.tools,
            tool_call={"name": "read_file", "args": {"path": "a"}, "id": "call-1"},
        )
        self.assertEqual(self.middleware.wrap_tool_call(request, lambda _req: "executed"), "executed")


if __name__ == "__main__":
    unittest.main()
