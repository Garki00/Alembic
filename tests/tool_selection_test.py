"""Tool catalog: basic/extended split, activation, and tools_search."""

import unittest
from pathlib import Path

from langchain_core.utils.function_calling import convert_to_openai_tool

from prompts.tool_descriptions import TOOL_MODEL_DESCRIPTIONS
from tools.catalog import (
    BASIC_TOOLS,
    EXTENDED_TOOLS,
    META_TOOLS,
    ToolActivation,
    clamp_limit,
    enabled_tool_names,
    search_extended,
    visible_tool_names,
)
from tools.context import ToolContext
from tools.model_view import with_compact_descriptions
from tools.registry import TOOL_SPECS, build_tools


ALL_ENABLED = set(TOOL_SPECS)
ALL_NAMES = {name for spec in TOOL_SPECS.values() for name in spec.tool_names}


class CatalogTest(unittest.TestCase):
    def test_basic_extended_and_meta_cover_every_tool(self):
        from tools.catalog import extended_tool_names

        registered = {name for spec in TOOL_SPECS.values() for name in spec.tool_names}
        catalog = (set(BASIC_TOOLS) | extended_tool_names() | set(META_TOOLS)) & registered
        self.assertEqual(catalog, registered)
        self.assertEqual(len(BASIC_TOOLS), len(set(BASIC_TOOLS)))
        self.assertEqual(len(EXTENDED_TOOLS), len(set(EXTENDED_TOOLS)))
        self.assertFalse(set(BASIC_TOOLS) & set(EXTENDED_TOOLS))

    def test_disabled_entries_are_excluded(self):
        enabled = enabled_tool_names({"file_read", "run_command", "tools_search"})
        self.assertIn("read_file", enabled)
        self.assertIn("run_command", enabled)
        self.assertIn("tools_search", enabled)
        self.assertNotIn("git_commit", enabled)
        self.assertNotIn("read_document", enabled)

    def test_visible_tools_are_basic_plus_active(self):
        enabled = enabled_tool_names(ALL_ENABLED)
        activation = ToolActivation()
        initial = visible_tool_names(enabled, activation)
        self.assertIn("read_file", initial)
        self.assertIn("tools_search", initial)
        self.assertNotIn("git_commit", initial)
        self.assertNotIn("read_document", initial)

        activation.activate({"git_commit", "read_document"})
        after = visible_tool_names(enabled, activation)
        self.assertIn("git_commit", after)
        self.assertIn("read_document", after)
        self.assertNotIn("read_image", after)

    def test_activated_disabled_tool_stays_hidden(self):
        enabled = enabled_tool_names({"file_read", "run_command"})
        activation = ToolActivation({"read_document"})
        self.assertNotIn("read_document", visible_tool_names(enabled, activation))

    def test_limit_clamp(self):
        self.assertEqual(clamp_limit(None), 3)
        self.assertEqual(clamp_limit("0"), 1)
        self.assertEqual(clamp_limit("5"), 5)
        self.assertEqual(clamp_limit("99"), 10)
        self.assertEqual(clamp_limit("bad"), 3)


class SearchTest(unittest.TestCase):
    def test_search_finds_document_tool(self):
        enabled = enabled_tool_names(ALL_ENABLED)
        matches = search_extended("PDF 文档", enabled, set(), 3)
        names = [item.name for item in matches]
        self.assertIn("read_document", names)

    def test_search_skips_active_and_disabled(self):
        enabled = enabled_tool_names(ALL_ENABLED)
        matches = search_extended("git commit", enabled, {"git_commit"}, 3)
        self.assertNotIn("git_commit", [item.name for item in matches])

        disabled = enabled_tool_names({"file_read"})
        self.assertEqual(search_extended("git commit", disabled, set(), 3), [])

    def test_empty_query_returns_nothing(self):
        self.assertEqual(search_extended("   ", enabled_tool_names(ALL_ENABLED), set(), 3), [])


class ToolsSearchToolTest(unittest.TestCase):
    def build(self, limit: str = "3"):
        activation = ToolActivation()
        enabled = enabled_tool_names(ALL_ENABLED)
        ctx = ToolContext(
            workspace=Path.cwd(),
            activation=activation,
            enabled_tool_names=enabled,
        )
        tools = {
            tool.name: tool
            for tool in build_tools({"tools_search"}, {"tools_search": {"TOOLS_SEARCH_LIMIT": limit}}, ctx)
        }
        return tools["tools_search"], activation

    def test_tools_search_activates_and_reports(self):
        tool, activation = self.build()
        text = tool.invoke({"query": "PDF 文档"})
        self.assertIn("read_document", text)
        self.assertIn("read_document", activation.snapshot())

    def test_configured_limit(self):
        tool, activation = self.build(limit="1")
        tool.invoke({"query": "文档 图片 下载 进程 Git 天气"})
        self.assertLessEqual(len(activation.snapshot()), 1)

    def test_limit_is_capped_at_ten(self):
        tool, activation = self.build(limit="99")
        tool.invoke({"query": "文档 图片 下载 进程 Git 时区 天气 测试"})
        self.assertLessEqual(len(activation.snapshot()), 10)

    def test_disabled_extended_tool_is_not_found(self):
        activation = ToolActivation()
        enabled = enabled_tool_names({"file_read", "tools_search"})
        ctx = ToolContext(workspace=Path.cwd(), activation=activation, enabled_tool_names=enabled)
        tool = {
            item.name: item
            for item in build_tools({"tools_search"}, {}, ctx)
        }["tools_search"]
        text = tool.invoke({"query": "PDF 文档"})
        self.assertIn("No extended tool can be activated for:", text)
        self.assertEqual(activation.snapshot(), frozenset())


class CompactToolViewTest(unittest.TestCase):
    def test_model_description_is_short_and_tool_remains_invokable(self):
        original = build_tools(
            {"file_read"},
            {},
            ToolContext(workspace=Path.cwd()),
        )
        compacted = with_compact_descriptions(original)
        by_name = {tool.name: tool for tool in compacted}
        read_file = by_name["read_file"]

        self.assertEqual(
            read_file.description,
            TOOL_MODEL_DESCRIPTIONS["read_file"],
        )
        self.assertIn("registry.py", by_name["list_dir"].invoke({"path": "tools"}))

    def test_compact_descriptions_reduce_openai_tool_payload(self):
        original = build_tools(None, {}, ToolContext(workspace=Path.cwd()))
        compacted = with_compact_descriptions(original)
        original_chars = sum(
            len(str(convert_to_openai_tool(tool))) for tool in original
        )
        compact_chars = sum(
            len(str(convert_to_openai_tool(tool))) for tool in compacted
        )
        self.assertLess(compact_chars, original_chars)


if __name__ == "__main__":
    unittest.main()
