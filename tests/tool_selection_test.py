"""Tool exposure: resident/deferred partition, search, and schema loading."""

import unittest
from pathlib import Path

from tools.catalog import (
    DEFERRED_TOOL_CALL_NAME,
    TOOL_SEARCH_NAME,
    ToolActivation,
    build_contracts,
    clamp_limit,
    enabled_tool_names,
    extended_tool_names,
    resident_tool_names,
    resolve_exposure,
    search_contracts,
    turn_disallowed_tools,
)
from tools.context import ToolContext
from tools.registry import TOOL_SPECS, build_tools


ALL_ENABLED = set(TOOL_SPECS)


def all_tools(ctx=None):
    return build_tools(ALL_ENABLED, {}, ctx or ToolContext(workspace=Path.cwd()))


class CatalogTest(unittest.TestCase):
    def test_resident_and_deferred_cover_every_tool(self):
        registered = {name for spec in TOOL_SPECS.values() for name in spec.tool_names}
        self.assertEqual(
            resident_tool_names() | extended_tool_names() | {TOOL_SEARCH_NAME},
            registered,
        )
        self.assertFalse(resident_tool_names() & extended_tool_names())

    def test_disabled_entries_are_excluded(self):
        enabled = enabled_tool_names({"file_read", "run_command", "tools_search"})
        self.assertIn("read_file", enabled)
        self.assertIn("run_command", enabled)
        self.assertIn("tools_search", enabled)
        self.assertNotIn("git_commit", enabled)
        self.assertNotIn("read_document", enabled)

    def test_exposure_hides_deferred_schemas(self):
        contracts = build_contracts(all_tools())
        exposure = resolve_exposure(contracts)
        self.assertEqual(exposure.mode, "local")
        self.assertIn("read_file", exposure.visible)
        self.assertIn(TOOL_SEARCH_NAME, exposure.visible)
        self.assertIn(DEFERRED_TOOL_CALL_NAME, exposure.visible)
        self.assertNotIn("git_commit", exposure.visible)
        self.assertIn("git_commit", exposure.deferred)

    def test_discovery_does_not_promote_deferred_tools_into_the_request(self):
        contracts = build_contracts(all_tools())
        activation = ToolActivation({"git_commit", "read_document"})
        exposure = resolve_exposure(contracts, activation)
        self.assertNotIn("git_commit", exposure.visible)
        self.assertNotIn("read_document", exposure.visible)
        self.assertIn("git_commit", exposure.deferred)

    def test_disabling_search_makes_deferred_tools_resident(self):
        contracts = build_contracts(all_tools())
        exposure = resolve_exposure(contracts, None, search_enabled=False)
        self.assertEqual(exposure.mode, "off")
        self.assertIn("git_commit", exposure.visible)
        self.assertNotIn(TOOL_SEARCH_NAME, exposure.visible)
        self.assertEqual(exposure.deferred, frozenset())

    def test_turn_disallowed_tools_are_removed_everywhere(self):
        ctx = ToolContext(workspace=Path.cwd(), vision=None, asker=None)
        self.assertEqual(turn_disallowed_tools(ctx), {"read_image", "ask_user_question"})
        contracts = build_contracts(all_tools())
        exposure = resolve_exposure(
            contracts, None, turn_disallowed=turn_disallowed_tools(ctx)
        )
        self.assertNotIn("read_image", exposure.visible)
        self.assertNotIn("read_image", exposure.deferred)
        self.assertNotIn("ask_user_question", exposure.visible)

    def test_limit_clamp(self):
        self.assertEqual(clamp_limit(None), 3)
        self.assertEqual(clamp_limit("0"), 1)
        self.assertEqual(clamp_limit("5"), 5)
        self.assertEqual(clamp_limit("99"), 10)
        self.assertEqual(clamp_limit("bad"), 3)


class SearchTest(unittest.TestCase):
    def contracts(self):
        return build_contracts(all_tools())

    def test_search_finds_document_tool(self):
        names = search_contracts("PDF 文档", self.contracts(), 3)
        self.assertIn("read_document", names)

    def test_select_loads_exact_names(self):
        names = search_contracts("select:git_commit,read_image", self.contracts(), 3)
        self.assertEqual(names, ["git_commit", "read_image"])

    def test_required_term_is_enforced(self):
        names = search_contracts("+git status", self.contracts(), 5)
        self.assertTrue(names)
        self.assertTrue(all("git" in name for name in names))

    def test_empty_query_returns_nothing(self):
        self.assertEqual(search_contracts("   ", self.contracts(), 3), [])


class ToolsSearchToolTest(unittest.TestCase):
    def build(self, entries=None, limit: str = "3"):
        activation = ToolActivation()
        enabled = enabled_tool_names(ALL_ENABLED if entries is None else entries)
        ctx = ToolContext(
            workspace=Path.cwd(),
            activation=activation,
            enabled_tool_names=enabled,
        )
        tools = {
            tool.name: tool
            for tool in build_tools(
                set(ALL_ENABLED) if entries is None else set(entries),
                {"tools_search": {"TOOLS_SEARCH_LIMIT": limit}},
                ctx,
            )
        }
        return tools, activation

    def test_tools_search_returns_schemas_and_records_discovery(self):
        tools, activation = self.build()
        text = tools[TOOL_SEARCH_NAME].invoke({"query": "PDF 文档"})
        self.assertIn("<functions>", text)
        self.assertIn('"name": "read_document"', text)
        self.assertIn('"parameters"', text)
        self.assertIn("read_document", activation.snapshot())

    def test_configured_limit(self):
        tools, activation = self.build(limit="1")
        tools[TOOL_SEARCH_NAME].invoke({"query": "文档 图片 下载 进程 Git 天气"})
        self.assertLessEqual(len(activation.snapshot()), 1)

    def test_limit_is_capped_at_ten(self):
        tools, activation = self.build(limit="99")
        tools[TOOL_SEARCH_NAME].invoke({"query": "文档 图片 下载 进程 Git 时区 天气 测试"})
        self.assertLessEqual(len(activation.snapshot()), 10)

    def test_disabled_extended_tool_is_not_found(self):
        tools, activation = self.build(entries={"file_read", "tools_search"})
        text = tools[TOOL_SEARCH_NAME].invoke({"query": "PDF 文档"})
        self.assertIn("No tool matches", text)
        self.assertEqual(activation.snapshot(), frozenset())


class ToolDescriptionTest(unittest.TestCase):
    def test_description_comes_from_the_tool_module(self):
        tools = {tool.name: tool for tool in all_tools()}
        self.assertIn("Read a text file within the workspace", tools["read_file"].description)
        self.assertIn("Write content fully to a file", tools["write_file"].description)
        self.assertIn("Apply a standard unified diff", tools["apply_patch"].description)

    def test_contract_description_is_not_rewritten(self):
        contracts = {item.name: item for item in build_contracts(all_tools())}
        self.assertIn("Read a text file within the workspace", contracts["read_file"].description)
        self.assertIn("path", contracts["read_file"].input_schema.get("properties", {}))


if __name__ == "__main__":
    unittest.main()
