"""Integration checks for extension discovery, publication and persisted settings."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from extensions.manager import ExtensionManager, _name
from tools.context import ToolContext
from tools.registry import TOOL_SPECS


class FakeClient:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.error = False
        self.discoveries = []
        self.reconciliations = []

    def reconcile(self, servers):
        self.reconciliations.append([dict(server) for server in servers])

    def discover(self, server):
        self.discoveries.append(dict(server))
        return {"tools": [{"name": "echo", "description": "Echo values", "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}}], "resources": [], "resourceTemplates": [{"uriTemplate": "test://{id}", "name": "test"}], "prompts": [], "capabilities": {"resources": {}, "prompts": {}}}

    def call_tool(self, server, name, args, **kwargs):
        self.calls.append((server, name, args))
        return {"isError": self.error, "content": [{"type": "text", "text": "hello"}], "structuredContent": args}

    def read_resource(self, *args, **kwargs):
        return {"contents": []}

    def get_prompt(self, *args, **kwargs):
        return {"messages": []}

    def close(self):
        self.closed = True


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeClient()
        self.patch = patch("extensions.mcp.MCPClient", return_value=self.fake)
        self.patch.start()
        self.manager = ExtensionManager(self.root / "data")

    def tearDown(self):
        self.manager.close()
        self.patch.stop()
        self.temp.cleanup()

    def server(self, **overrides):
        config = {"command": "python", "args": [], "enabled": True}
        config.update(overrides)
        self.manager.put_server("demo", config)

    def test_defaults_saved_and_duplicate_manager_cleanup(self):
        self.assertTrue(self.manager.file.is_file())
        old = self.manager
        self.manager = ExtensionManager(self.root / "other")
        self.assertTrue(old._closed)
        self.assertIs(TOOL_SPECS["load_skill"], self.manager._owned["load_skill"])
        old.close()
        self.assertIn("load_skill", TOOL_SPECS)

    def test_schema_catalog_templates_and_error_result(self):
        self.server()
        spec = TOOL_SPECS[_name("mcp", "demo", "echo")]
        self.assertEqual(spec.source, "plugin")
        tool = spec.builder({}, ToolContext())[0]
        self.assertEqual(tool.args_schema, self.fake.discover({})["tools"][0]["inputSchema"])
        self.assertEqual(json.loads(tool.invoke({"value": "x"}))["structuredContent"], {"value": "x"})
        self.fake.error = True
        result = tool.invoke({"name": tool.name, "args": {"value": "x"}, "id": "1", "type": "tool_call"})
        self.assertEqual(result.status, "error")
        self.assertIn(_name("mcp_resource", "demo"), TOOL_SPECS)
        self.assertIn(_name("mcp_prompt", "demo"), TOOL_SPECS)
        catalog = TOOL_SPECS[_name("list_mcp_catalog", "demo")].builder({}, ToolContext())[0]
        self.assertIn("test://{id}", catalog.invoke({}))

    def test_disabled_stale_tool_and_secret_preservation(self):
        self.server(env={"SECRET": "secret"}, headers={"Authorization": "secret"})
        tool = TOOL_SPECS[_name("mcp", "demo", "echo")].builder({}, ToolContext())[0]
        self.manager.put_server("demo", {"enabled": False})
        self.assertNotIn(_name("mcp", "demo", "echo"), TOOL_SPECS)
        self.assertIn("disabled", tool.invoke({"value": "x"}))
        self.assertEqual(self.manager.config["mcp"]["demo"]["env"], {"SECRET": "secret"})
        self.assertNotIn("secret", json.dumps(self.manager.snapshot()))

    def test_query_credentials_not_public_and_preserved_on_edit(self):
        self.server(transport="streamable_http", url="https://example.com/mcp?token=secret", bearer_token="hidden")
        visible = self.manager.snapshot()["mcp"][0]
        self.assertEqual(visible["url"], "https://example.com/mcp")
        self.assertNotIn("secret", json.dumps(visible))
        self.manager.put_server("demo", {"url": visible["url"], "name": "Updated"})
        self.assertEqual(self.manager.config["mcp"]["demo"]["url"], "https://example.com/mcp?token=secret")

    def test_invalid_server_updates_do_not_mutate(self):
        self.server(enabled=False)
        for patch_data in ({"enabled": "false"}, {"args": "bad"}, {"args": [1]}, {"env": {"x": 1}}, {"headers": []}, {"transport": "ftp"}, {"transport": "sse", "url": "file:///x"}, {"transport": "sse", "url": "https://user:pass@example.com"}, {"timeout": float("nan")}):
            with self.subTest(patch=patch_data), self.assertRaises(ValueError):
                self.manager.put_server("demo", patch_data)
        self.assertFalse(self.manager.config["mcp"]["demo"]["enabled"])

    def test_oauth_null_safe_metadata_and_timeout(self):
        self.server(transport="streamable_http", url="https://example.com/mcp", timeout=300,
            oauth={"redirect_uri": "http://127.0.0.1:47832/callback", "scope": "read", "token": "secret"})
        visible = self.manager.snapshot()["mcp"][0]
        self.assertEqual(visible["timeout"], 300)
        self.assertNotIn("secret", json.dumps(visible))
        self.manager.put_server("demo", {"oauth": None})
        self.assertIsNone(self.manager.config["mcp"]["demo"]["oauth"])

    def test_oauth_defaults_and_callback_validation(self):
        callback = "http://127.0.0.1:47832/callback"
        self.server(transport="streamable_http", url="https://example.com/mcp",
                    oauth={"redirect_uri": callback})
        self.assertEqual(self.manager.config["mcp"]["demo"]["timeout"], 300)
        self.assertEqual(self.fake.discoveries[-1]["timeout"], 300)
        for invalid in (
            "http://localhost:47832/callback", "http://[::1]:47832/callback",
            "http://127.0.0.1/callback", "http://127.0.0.1:0/callback",
            "http://127.0.0.1:65536/callback", "http://127.0.0.1:bad/callback",
            "https://127.0.0.1:47832/callback", "http://user@127.0.0.1:47832/callback",
            "http://127.0.0.1:47832/callback?code=x", "http://127.0.0.1:47832/callback#x",
        ):
            with self.subTest(callback=invalid), self.assertRaises(ValueError):
                self.manager.put_server("demo", {"oauth": {"redirect_uri": invalid}})
        self.assertEqual(self.manager.config["mcp"]["demo"]["oauth"]["redirect_uri"], callback)

    def test_refresh_reconciles_without_connecting_disabled_servers(self):
        self.server(enabled=False)
        self.assertEqual(self.fake.discoveries, [])
        self.assertIsNone(self.manager.client)
        self.manager.put_server("demo", {"enabled": True})
        self.assertEqual(len(self.fake.discoveries), 1)
        self.assertTrue(self.fake.reconciliations[-1][0]["enabled"])
        self.manager.put_server("demo", {"enabled": False})
        self.assertEqual(len(self.fake.discoveries), 1)
        self.assertFalse(self.fake.reconciliations[-1][0]["enabled"])
        self.manager.delete_server("demo")
        self.assertEqual(self.fake.reconciliations[-1], [])

    def test_failed_refresh_rolls_back_and_bad_server_isolated(self):
        self.server()
        before = dict(TOOL_SPECS)
        snapshot = self.manager.snapshot()
        with patch.object(self.manager, "_refresh_mcp", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                self.manager.refresh()
        self.assertEqual(before, TOOL_SPECS)
        self.assertEqual(snapshot, self.manager.snapshot())
        original = self.fake.discover
        def discovery(server):
            if server["id"] == "bad":
                return {"tools": [{"name": "fine", "inputSchema": {"type": "object"}}, {"name": "broken", "inputSchema": {"type": "invalid"}}]}
            return original(server)
        self.fake.discover = discovery
        self.manager.put_server("bad", {"command": "python", "enabled": True})
        self.assertNotIn(_name("mcp", "bad", "fine"), TOOL_SPECS)
        self.assertIn(_name("mcp", "demo", "echo"), TOOL_SPECS)

    def test_snapshot_is_detached_and_foreign_spec_not_deleted(self):
        self.server()
        snapshot = self.manager.snapshot()
        snapshot["directories"]["mcp"].clear()
        self.assertTrue(self.manager.config["directories"]["mcp"])
        name = _name("mcp", "demo", "echo")
        foreign = object()
        TOOL_SPECS[name] = foreign
        self.manager.close()
        self.assertIs(TOOL_SPECS[name], foreign)
        TOOL_SPECS.pop(name)

    def test_malformed_settings_do_not_crash(self):
        self.manager.close()
        (self.root / "data" / "extensions.json").write_text("[invalid", encoding="utf-8")
        self.manager = ExtensionManager(self.root / "data")
        self.assertIn("load_skill", TOOL_SPECS)

    def test_manifest_atomic_and_refresh_removes(self):
        folder = Path(self.manager.config["directories"]["mcp"][0])
        file = folder / "servers.json"
        file.write_text(json.dumps({"mcpServers": {"ok": {"command": "python", "enabled": True}, "bad": {"command": 5}}}), encoding="utf-8")
        self.manager.refresh()
        self.assertEqual(self.manager.servers, [])
        self.assertTrue(self.manager.errors)
        file.write_text(json.dumps({"mcpServers": {"ok": {"command": "python", "enabled": True}}}), encoding="utf-8")
        self.manager.refresh()
        self.assertIn(_name("mcp", "ok", "echo"), TOOL_SPECS)
        file.unlink()
        self.manager.refresh()
        self.assertNotIn(_name("mcp", "ok", "echo"), TOOL_SPECS)

    def test_plugin_default_disabled_bad_manifest_and_removal(self):
        root = Path(self.manager.config["directories"]["tools"][0])
        plugin = root / "demo"
        plugin.mkdir()
        (plugin / "tool.py").write_text("def invoke(arguments, ctx, config): return 'ok'", encoding="utf-8")
        manifest = {"version": 1, "id": "demo", "name": "Demo", "tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}, "entrypoint": "tool.py:invoke"}]}
        file = plugin / "plugin.json"
        file.write_text(json.dumps(manifest), encoding="utf-8")
        self.manager.refresh()
        self.assertFalse(TOOL_SPECS["plugin_demo_echo"].default_enabled)
        manifest["tools"][0]["entrypoint"] = "../tool.py:invoke"
        file.write_text(json.dumps(manifest), encoding="utf-8")
        self.manager.refresh()
        self.assertNotIn("plugin_demo_echo", TOOL_SPECS)
        self.assertTrue(self.manager.errors)

    def test_skill_toggle_resource_boundary_and_error_contract(self):
        root = Path(self.manager.config["directories"]["skills"][0])
        skill = root / "sample"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: sample\ndescription: Test skill\n---\nInstructions", encoding="utf-8")
        bad = root / "broken"
        bad.mkdir()
        (bad / "SKILL.md").write_text("bad", encoding="utf-8")
        self.manager.refresh()
        self.assertTrue(all(set(e) == {"path", "message"} for e in self.manager.errors))
        identity = next(s.id for s in self.manager.skills if s.name == "sample")
        tool = TOOL_SPECS["read_skill_resource"].builder({}, ToolContext())[0]
        with self.assertRaises(ValueError):
            tool.invoke({"skill": identity, "path": "../outside.txt"})
        self.manager.set_skill(identity, False)
        with self.assertRaises(ValueError):
            tool.invoke({"skill": identity, "path": "SKILL.md"})
        self.assertFalse(next(s for s in self.manager.snapshot()["skills"] if s["id"] == identity)["enabled"])


if __name__ == "__main__":
    unittest.main()
