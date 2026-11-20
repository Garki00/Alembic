"""Tests for trusted local tool plugin discovery and invocation."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import ValidationError
from extensions.plugins import build_plugin_tool, discover_tools
from tools.context import ToolContext


class PluginsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.schema = {"type": "object", "properties": {"value": {"type": "integer", "minimum": 2}},
                       "required": ["value"], "additionalProperties": False}

    def install(self, folder="one", source="sample", tools=None, code=None):
        root = self.root / folder
        root.mkdir(exist_ok=True)
        (root / "plugin.json").write_text(json.dumps({"version": 1, "id": source, "name": "Sample",
            "tools": tools if tools is not None else [{"name": "echo", "description": "Echo",
                  "inputSchema": self.schema, "entrypoint": "tool.py:run"}]}), encoding="utf-8")
        (root / "tool.py").write_text(code or "def run(arguments, ctx, config):\n    return [arguments, str(ctx.workspace), config]\n", encoding="utf-8")
        return root

    def test_discovery_and_build_never_execute(self):
        self.install(code="raise RuntimeError('imported')\n")
        plugins, errors = discover_tools([self.root])
        self.assertEqual(errors, [])
        self.assertEqual(plugins[0].name, "plugin_sample_echo")
        self.assertEqual(plugins[0].source_id, "sample")
        tool = build_plugin_tool(plugins[0], ToolContext(workspace=self.root), {})
        self.assertEqual(tool.args_schema, self.schema)
        self.assertEqual(tool.tool_call_schema, self.schema)
        with self.assertRaisesRegex(RuntimeError, "imported"):
            tool.invoke({"value": 2})

    def test_validation_precedes_import_and_preserves_schema(self):
        self.install(code="raise RuntimeError('imported')\n")
        plugins, _ = discover_tools([self.root])
        tool = build_plugin_tool(plugins[0], ToolContext(), {})
        for arguments in ({}, {"value": "2"}, {"value": 1}, {"value": 2, "other": 1}):
            with self.subTest(arguments=arguments), self.assertRaises(ValidationError):
                tool.invoke(arguments)

    def test_invoke_context_config_and_refresh_same_length(self):
        root = self.install()
        plugins, _ = discover_tools([self.root])
        config = {"key": "secret"}
        tool = build_plugin_tool(plugins[0], ToolContext(workspace=self.root), config)
        config["key"] = "changed"
        original_path = list(sys.path)
        self.assertEqual(tool.invoke({"value": 2}), [{"value": 2}, str(self.root), {"key": "secret"}])
        for value in ("one", "two"):
            (root / "tool.py").write_text(f"def run(arguments, ctx, config):\n    return '{value}'\n", encoding="utf-8")
            self.assertEqual(tool.invoke({"value": 2}), value)
        self.assertEqual(sys.path, original_path)
        self.assertFalse(any(name.startswith("_alembic_plugin_") for name in sys.modules))

    def test_relative_helper_refresh(self):
        root = self.install(code="from .helper import value\ndef run(arguments, ctx, config):\n    return value\n")
        plugins, _ = discover_tools([self.root])
        tool = build_plugin_tool(plugins[0], ToolContext(), {})
        original_meta_path = list(sys.meta_path)
        for value in ("one", "two"):
            (root / "helper.py").write_text(f"value = '{value}'\n", encoding="utf-8")
            self.assertEqual(tool.invoke({"value": 2}), value)
        self.assertEqual(sys.meta_path, original_meta_path)

    def test_nested_schema_constraints_and_local_references(self):
        self.schema = {"type": "object", "$defs": {"choice": {"enum": ["a", "b"]}},
                       "properties": {"items": {"type": "array", "minItems": 1,
                           "items": {"$ref": "#/$defs/choice"}}},
                       "required": ["items"], "additionalProperties": False}
        self.install()
        plugins, errors = discover_tools([self.root])
        self.assertEqual(errors, [])
        tool = build_plugin_tool(plugins[0], ToolContext(), {})
        self.assertEqual(tool.tool_call_schema, self.schema)
        self.assertEqual(tool.invoke({"items": ["a"]})[0], {"items": ["a"]})
        with self.assertRaises(ValidationError):
            tool.invoke({"items": ["c"]})

    def test_invalid_entrypoints_are_isolated(self):
        entries = ("../outside.py:run", "C:/outside.py:run", "/outside.py:run",
                   "folder\\\\outside.py:run", "tool.py:missing.attribute", "tool.txt:run")
        tools = [{"name": f"bad{index}", "description": "bad", "inputSchema": self.schema,
                  "entrypoint": value} for index, value in enumerate(entries)]
        self.install(tools=tools)
        found, errors = discover_tools([self.root])
        self.assertEqual(found, [])
        self.assertEqual(len(errors), len(entries))

    def test_async_callable(self):
        self.install(code="async def run(arguments, ctx, config):\n    return arguments['value']\n")
        plugins, _ = discover_tools([self.root])
        tool = build_plugin_tool(plugins[0], ToolContext(), {})
        self.assertEqual(asyncio.run(tool.ainvoke({"value": 3})), 3)
        self.assertEqual(tool.invoke({"value": 3}), 3)

    def test_invalid_tools_and_duplicate_plugins_are_isolated(self):
        root = self.install()
        data = json.loads((root / "plugin.json").read_text())
        data["tools"] += [data["tools"][0], {"name": "bad", "description": "bad", "inputSchema": self.schema,
                                             "entrypoint": "../outside.py:run"}]
        (root / "plugin.json").write_text(json.dumps(data))
        self.install(folder="two")
        self.install(folder="three", source="other")
        tools, errors = discover_tools([self.root])
        self.assertEqual(len(tools), 2)
        self.assertEqual(len(errors), 3)

    def test_missing_and_repeated_roots(self):
        root = self.install()
        tools, errors = discover_tools([self.root / "missing", root, self.root])
        self.assertEqual(len(tools), 1)
        self.assertEqual(errors, [])

    def test_stable_bounded_names(self):
        self.install(source="a" * 100)
        first, _ = discover_tools([self.root])
        second, _ = discover_tools([self.root])
        self.assertEqual(first[0].name, second[0].name)
        self.assertEqual(len(first[0].name), 64)

    def test_invalid_manifest_does_not_hide_valid_plugins(self):
        self.install()
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "plugin.json").write_text('{"version": 2}')
        tools, errors = discover_tools([self.root])
        self.assertEqual(len(tools), 1)
        self.assertEqual(len(errors), 1)

    def test_symlink_escape_is_rechecked_on_execution(self):
        root = self.install()
        tools, _ = discover_tools([self.root])
        outside = self.root / "outside.py"
        outside.write_text("raise RuntimeError('outside')")
        entry = root / "tool.py"
        entry.unlink()
        try:
            entry.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation unavailable")
        tool = build_plugin_tool(tools[0], ToolContext(), {})
        with self.assertRaisesRegex(ValueError, "escapes"):
            tool.invoke({"value": 2})
        found, errors = discover_tools([self.root])
        self.assertEqual(found, [])
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
