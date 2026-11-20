# tests/registry_test.py
"""Tool registry and error contract tests: single source, assembly, and injection."""

import unittest
from pathlib import Path
from unittest.mock import patch

from tools.context import ToolContext
from tools.errors import is_error, tool_error, truncate
from tools.registry import (
    TOOL_SPECS,
    LEGACY_TOOL_GROUPS,
    build_tools,
    default_enabled_names,
    spec_of,
)
from tools.weather import _setting, build_weather_tools


class RegistryTest(unittest.TestCase):
    def test_registry_is_single_source(self):
        # 注册表是全量清单，且顺序稳定（设置面板按此渲染）
        self.assertEqual(
            [name for name, spec in tuple(TOOL_SPECS.items())
             if spec.source == "builtin" and name not in {"load_skill", "read_skill_resource"}],
            [
                "get_current_time",
                "list_timezones",
                "list_dir",
                "read_file",
                "search_files",
                "find_files",
                "file_stat",
                "read_document",
                "read_image",
                "write_file",
                "edit_file",
                "make_dir",
                "delete_file",
                "move_file",
                "copy_file",
                "apply_patch",
                "batch_edit",
                "fetch_url",
                "web_search",
                "download_file",
                "todo_write",
                "ask_user_question",
                "tools_search",
                "run_command",
                "start_process",
                "stop_process",
                "list_processes",
                "read_process_logs",
                "wait_process",
                "git_status",
                "git_diff",
                "git_commit",
                "get_weather",
                "mock_data",
            ],
        )
        # config.store 只是转手导出同一份定义，不再是第二份清单
        from config.store import TOOL_SPECS as store_specs

        self.assertIs(store_specs, TOOL_SPECS)

    def test_run_command_defaults_on_but_gated(self):
        """Enabled by default after approval was introduced; the risk level is still exec, so execution is intercepted to ask the user first."""
        self.assertIn("run_command", default_enabled_names())
        self.assertTrue(TOOL_SPECS["run_command"].default_enabled)
        self.assertEqual(TOOL_SPECS["run_command"].risk, "exec")

    def test_specs_declare_risk_and_tools(self):
        for group, members in LEGACY_TOOL_GROUPS.items():
            risk = "write" if group == "file_write" else "exec" if group == "background_process" else "read"
            self.assertNotIn(group, TOOL_SPECS)
            for name in members:
                with self.subTest(tool=name):
                    self.assertEqual(TOOL_SPECS[name].risk, risk)
                    self.assertEqual(TOOL_SPECS[name].tool_names, (name,))
        self.assertEqual(TOOL_SPECS["read_document"].risk, "read")
        self.assertEqual(TOOL_SPECS["read_image"].risk, "read")
        self.assertEqual(TOOL_SPECS["apply_patch"].risk, "write")
        self.assertEqual(TOOL_SPECS["batch_edit"].risk, "write")
        self.assertEqual(TOOL_SPECS["fetch_url"].risk, "read")
        self.assertEqual(TOOL_SPECS["web_search"].risk, "read")
        self.assertEqual(TOOL_SPECS["download_file"].risk, "write")
        self.assertEqual(TOOL_SPECS["git_commit"].risk, "exec")
        self.assertFalse(TOOL_SPECS["get_weather"].default_enabled)
        self.assertIsNotNone(spec_of("get_weather"))
        self.assertIsNone(spec_of("nope"))

    def test_build_tools_respects_enabled_set(self):
        ctx = ToolContext(workspace=Path.cwd())
        only_read = build_tools({"file_read"}, {}, ctx)
        self.assertEqual(
            {tool.name for tool in only_read},
            {"list_dir", "read_file", "search_files", "find_files", "file_stat"},
        )

        # 期望集合从注册表派生，新增工具不需要改这条断言
        expected = {
            tool_name
            for spec in TOOL_SPECS.values()
            if spec.builder is not None
            for tool_name in spec.tool_names
        }
        everything = build_tools(None, {}, ctx)
        self.assertEqual({tool.name for tool in everything}, expected)

    def test_unknown_enabled_name_is_ignored(self):
        built = build_tools({"no_such_tool"}, {}, ToolContext(workspace=Path.cwd()))
        self.assertEqual(built, [])

    def test_config_is_injected_into_weather_tool(self):
        captured = {}

        def fake_builder(config):
            captured.update(config)
            return []

        with patch("tools.registry.build_weather_tools", fake_builder):
            build_tools({"get_weather"}, {"get_weather": {"QWEATHER_KEY_ID": "kid-1"}}, ToolContext())

        self.assertEqual(captured, {"QWEATHER_KEY_ID": "kid-1"})


class WeatherConfigTest(unittest.TestCase):
    def test_config_wins_over_env(self):
        with patch.dict("os.environ", {"QWEATHER_KEY_ID": "from-env"}):
            self.assertEqual(_setting({"QWEATHER_KEY_ID": "from-config"}, "QWEATHER_KEY_ID"), "from-config")
            self.assertEqual(_setting({}, "QWEATHER_KEY_ID"), "from-env")
            self.assertEqual(_setting({"QWEATHER_KEY_ID": "  "}, "QWEATHER_KEY_ID"), "from-env")

    def test_empty_city_returns_error_contract(self):
        tool = build_weather_tools({})[0]
        text = tool.invoke({"city": "  "})
        self.assertTrue(text.startswith("Error: [empty_city]"), text)

    def test_missing_credentials_are_named(self):
        tool = build_weather_tools({})[0]
        with patch.dict("os.environ", {"QWEATHER_KEY_ID": ""}):
            text = tool.invoke({"city": "北京"})
        self.assertTrue(text.startswith("Error: [auth_config]"), text)
        self.assertIn("QWEATHER_KEY_ID", text)


class RuntimeAssemblyTest(unittest.TestCase):
    """Assembles a real Agent, which surfaces issues such as invalid tool schemas early."""

    def test_agent_assembles_with_all_tools(self):
        from config.store import ModelConfig
        from web.runtime import build_runtime

        model_cfg = ModelConfig(
            id="stub",
            name="stub",
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key="sk-test-only-no-network",
        )
        agent, model = build_runtime(model_cfg, set(TOOL_SPECS), ".", tool_configs={})
        self.assertIsNotNone(agent)
        self.assertIsNotNone(model)


class ErrorContractTest(unittest.TestCase):
    def test_tool_error_shape(self):
        text = tool_error("not_found", "文件不存在。", hint="先 list_dir 看看。")
        lines = text.splitlines()
        self.assertEqual(lines[0], "Error: [not_found] 文件不存在。")
        self.assertEqual(lines[1], "先 list_dir 看看。")
        self.assertTrue(is_error(text))

    def test_success_is_not_error(self):
        self.assertFalse(is_error("Exit code: 0 (succeeded)"))

    def test_truncate_keeps_head_or_tail(self):
        text = "a" * 100 + "TAIL-END"

        head = truncate(text, 60)
        self.assertTrue(head.startswith("a"))
        self.assertIn("truncated", head)
        self.assertNotIn("TAIL-END", head)

        tail = truncate(text, 60, keep_tail=True)
        self.assertIn("TAIL-END", tail)
        self.assertIn("truncated", tail)

    def test_truncate_passthrough(self):
        self.assertEqual(truncate("short", 100), "short")


if __name__ == "__main__":
    unittest.main()
