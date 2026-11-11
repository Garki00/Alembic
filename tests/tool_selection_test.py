"""Dynamic tool pack selection and compact model descriptions."""

import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.utils.function_calling import convert_to_openai_tool

from prompts.tool_descriptions import TOOL_MODEL_DESCRIPTIONS
from tools.context import ToolContext
from tools.model_view import with_compact_descriptions
from tools.registry import TOOL_SPECS, build_tools
from tools.selection import CORE_ENTRIES, merge_tool_entries, select_tool_entries


ALL_ENABLED = set(TOOL_SPECS)


class ToolSelectionTest(unittest.TestCase):
    def test_read_only_request_uses_core_tools(self):
        selected = select_tool_entries(ALL_ENABLED, "Inspect the project structure")
        self.assertEqual(selected, set(CORE_ENTRIES))
        self.assertIn("file_read", selected)
        self.assertNotIn("file_write", selected)
        self.assertNotIn("web_search", selected)

    def test_write_request_adds_editing_tools(self):
        selected = select_tool_entries(ALL_ENABLED, "Fix the failing tests")
        self.assertIn("file_write", selected)
        self.assertIn("apply_patch", selected)

    def test_specialized_packs_are_added_by_intent(self):
        selected = select_tool_entries(
            ALL_ENABLED,
            "Read report.pdf, inspect screenshot.png, then check git status.",
        )
        self.assertIn("read_document", selected)
        self.assertIn("read_image", selected)
        self.assertIn("git_read", selected)
        self.assertIn("git_commit", selected)

    def test_web_and_process_intents_are_independent(self):
        web = select_tool_entries(ALL_ENABLED, "Fetch https://example.com")
        self.assertTrue({"web_search", "fetch_url", "download_file"} <= web)

        process = select_tool_entries(ALL_ENABLED, "Show the dev server logs")
        self.assertTrue({"background_process", "process_inspect"} <= process)
        self.assertNotIn("web_search", process)

    def test_missing_request_returns_all_enabled_entries(self):
        self.assertEqual(select_tool_entries(ALL_ENABLED, ""), ALL_ENABLED)
        self.assertEqual(select_tool_entries(ALL_ENABLED, None), ALL_ENABLED)

    def test_environment_switch_returns_all_enabled_entries(self):
        with patch.dict("os.environ", {"DYNAMIC_TOOL_PACKS": "0"}):
            self.assertEqual(
                select_tool_entries(ALL_ENABLED, "Inspect the project"),
                ALL_ENABLED,
            )

    def test_disabled_entries_are_never_added(self):
        enabled = {"file_read", "run_command"}
        selected = select_tool_entries(enabled, "Fix the code and fetch a URL")
        self.assertEqual(selected, enabled)


class SessionToolSetTest(unittest.TestCase):
    def test_previously_selected_tools_are_kept(self):
        enabled = {"file_read", "run_command", "file_write", "apply_patch"}
        first = merge_tool_entries(set(), {"file_read", "file_write"}, enabled)
        second = merge_tool_entries(first, {"file_read"}, enabled)
        self.assertIn("file_write", second)
        self.assertIn("file_read", second)
        self.assertEqual(second, {"file_read", "file_write"})

    def test_new_tools_are_added_and_disabled_tools_removed(self):
        enabled = {"file_read", "file_write", "web_search"}
        merged = merge_tool_entries(
            {"file_read", "file_write"},
            {"file_read", "web_search"},
            enabled,
        )
        self.assertEqual(merged, {"file_read", "file_write", "web_search"})

        merged = merge_tool_entries(
            {"file_read", "file_write"},
            {"file_read", "web_search"},
            {"file_read", "web_search"},
        )
        self.assertEqual(merged, {"file_read", "web_search"})


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
