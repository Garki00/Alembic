# tests/tool_visibility_test.py
"""Deferred tools stay out of the request and run through the call control."""

import unittest
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from agents.tool_exposure import ToolExposureMiddleware
from tools.catalog import (
    DEFERRED_TOOL_CALL_NAME,
    TOOL_SEARCH_NAME,
    ToolActivation,
    ToolContract,
    resolve_exposure,
)


def fake_tool(name: str):
    return SimpleNamespace(name=name)


def contract(name: str, *, deferred: bool = False) -> ToolContract:
    return ToolContract(
        name=name,
        entry=name,
        label=name,
        description=name,
        input_schema={},
        capability={},
        deferred=deferred,
        always_load=False,
        aliases=(),
        search_hint="",
    )


class FakeRequest:
    def __init__(self, tools, tool_call=None, tool=None):
        self.tools = tools
        self.tool_call = tool_call or {}
        self.tool = tool

    def override(self, **changes):
        data = {"tools": self.tools, "tool_call": self.tool_call, "tool": self.tool}
        data.update(changes)
        return FakeRequest(**data)


class VisibilityTest(unittest.TestCase):
    def setUp(self):
        self.activation = ToolActivation()
        self.contracts = [
            contract("read_file"),
            contract("run_command"),
            contract(TOOL_SEARCH_NAME),
            contract(DEFERRED_TOOL_CALL_NAME),
            contract("git_commit", deferred=True),
            contract("read_document", deferred=True),
        ]
        self.exposure = resolve_exposure(self.contracts)
        self.tools = [
            fake_tool("read_file"),
            fake_tool("run_command"),
            fake_tool(TOOL_SEARCH_NAME),
            fake_tool(DEFERRED_TOOL_CALL_NAME),
            fake_tool("git_commit"),
            fake_tool("read_document"),
        ]
        self.middleware = ToolExposureMiddleware(
            contracts=self.contracts,
            exposure=self.exposure,
            activation=self.activation,
            tools=self.tools,
        )

    def visible(self):
        seen = {}

        def handler(request):
            seen["names"] = [tool.name for tool in request.tools]
            return "ok"

        result = self.middleware.wrap_model_call(FakeRequest(self.tools), handler)
        self.assertEqual(result, "ok")
        return seen["names"]

    def test_deferred_tools_are_hidden(self):
        self.assertEqual(
            self.visible(),
            ["read_file", "run_command", TOOL_SEARCH_NAME, DEFERRED_TOOL_CALL_NAME],
        )

    def test_direct_deferred_call_is_blocked(self):
        request = FakeRequest(
            self.tools,
            tool_call={"name": "git_commit", "args": {}, "id": "call-1"},
        )
        result = self.middleware.wrap_tool_call(request, lambda _req: "executed")
        self.assertIsInstance(result, ToolMessage)
        self.assertIn("[tool_not_loaded]", result.content)
        self.assertIn(TOOL_SEARCH_NAME, result.content)
        self.assertEqual(result.tool_call_id, "call-1")

    def test_deferred_call_requires_loaded_schema(self):
        request = FakeRequest(
            self.tools,
            tool_call={
                "name": DEFERRED_TOOL_CALL_NAME,
                "args": {"name": "git_commit", "input": {}},
                "id": "call-1",
            },
        )
        result = self.middleware.wrap_tool_call(request, lambda _req: "executed")
        self.assertIn("[schema_not_loaded]", result.content)

    def test_deferred_call_runs_the_named_tool(self):
        self.activation.activate({"git_commit"})
        seen = {}

        def handler(req):
            seen["name"] = req.tool_call["name"]
            seen["args"] = req.tool_call["args"]
            seen["tool"] = req.tool.name
            return ToolMessage(
                content="committed",
                tool_call_id=req.tool_call["id"],
                name=req.tool_call["name"],
            )

        request = FakeRequest(
            self.tools,
            tool_call={
                "name": DEFERRED_TOOL_CALL_NAME,
                "args": {"name": "git_commit", "input": {"message": "m"}},
                "id": "call-1",
            },
        )
        result = self.middleware.wrap_tool_call(request, handler)
        self.assertEqual(seen, {"name": "git_commit", "args": {"message": "m"}, "tool": "git_commit"})
        self.assertEqual(result.name, DEFERRED_TOOL_CALL_NAME)
        self.assertEqual(result.content, "committed")

    def test_deferred_call_rejects_resident_tools(self):
        request = FakeRequest(
            self.tools,
            tool_call={
                "name": DEFERRED_TOOL_CALL_NAME,
                "args": {"name": "read_file", "input": {}},
                "id": "call-1",
            },
        )
        result = self.middleware.wrap_tool_call(request, lambda _req: "executed")
        self.assertIn("[not_a_deferred_tool]", result.content)

    def test_resident_tool_call_is_allowed(self):
        request = FakeRequest(
            self.tools,
            tool_call={"name": "read_file", "args": {"path": "a"}, "id": "call-1"},
        )
        self.assertEqual(self.middleware.wrap_tool_call(request, lambda _req: "executed"), "executed")


if __name__ == "__main__":
    unittest.main()
