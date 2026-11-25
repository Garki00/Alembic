"""Dynamic registry extensions participate in discovery and both runtime engines."""

import unittest
from unittest.mock import patch

from agents.permission_service import evaluate
from prompts.system import build_system_prompt
from tools.catalog import (
    BASIC_TOOLS, ToolActivation, enabled_tool_names, extended_tool_names,
    search_extended, visible_tool_names,
)
from tools.registry import TOOL_SPECS, ToolSpec, capability_for


class DynamicCatalogTest(unittest.TestCase):
    def test_late_registration_search_activation_and_removal(self):
        name = "plugin_test_capability"
        self.assertNotIn(name, extended_tool_names())
        spec = ToolSpec(name=name, label="Dynamic capability", description="quasar lookup",
                        tool_names=(name,), risk="exec")
        with patch.dict(TOOL_SPECS, {name: spec}):
            enabled = enabled_tool_names({name})
            active = ToolActivation()
            self.assertIn(name, extended_tool_names())
            self.assertNotIn(name, visible_tool_names(enabled, active))
            self.assertEqual([m.name for m in search_extended("quasar", enabled, set(), 3)], [name])
            self.assertEqual(search_extended("quasar", set(), set(), 3), [])
            active.activate({name})
            self.assertIn(name, visible_tool_names(enabled, active))
            self.assertEqual(search_extended("quasar", enabled, active.snapshot(), 3), [])
            self.assertEqual(evaluate(name, {}, mode="build").behavior, "ask")
        self.assertNotIn(name, extended_tool_names())
        self.assertNotIn(name, visible_tool_names(enabled, active))

    def test_builtin_skill_tools_are_immediately_visible_when_enabled(self):
        names = {"load_skill", "read_skill_resource"}
        specs = {name: ToolSpec(name=name, label=name, description=name, tool_names=(name,)) for name in names}
        with patch.dict(TOOL_SPECS, specs):
            self.assertTrue(names <= set(BASIC_TOOLS))
            self.assertEqual(visible_tool_names(enabled_tool_names(names), ToolActivation()), names)
            self.assertFalse(names & extended_tool_names())
            self.assertEqual(visible_tool_names(set(), ToolActivation(names)), set())

    def test_legacy_groups_expand_in_catalog_and_approval(self):
        self.assertIn("write_file", enabled_tool_names({"file_write"}))
        self.assertFalse(capability_for(tool_name="write_file")["read_only"])
        self.assertEqual(evaluate("write_file", {"path": "a.txt"}, mode="build").behavior, "ask")
        self.assertEqual(evaluate("write_file", {"path": "a.txt"}, mode="edit").behavior, "allow")


class RuntimeExtensionsTest(unittest.TestCase):
    def test_web_engines_use_live_catalog(self):
        from types import SimpleNamespace
        from web.runtime import build_runtime

        name = "plugin_late_runtime"
        spec = ToolSpec(name=name, label=name, description="Late capability", tool_names=(name,))
        model_config = SimpleNamespace(supports_images=False)
        for engine in ("react", "tool_calling"):
            with self.subTest(engine=engine), patch.dict(TOOL_SPECS, {name: spec}), patch("web.runtime.create_chat_model"), patch("web.runtime.build_tools", return_value=[]), patch("web.runtime.ReActAgent") as react, patch("web.runtime.create_agent"), patch("web.runtime.PrefixSummarizer"), patch("agents.tool_visibility.ToolVisibilityMiddleware") as visibility:
                build_runtime(model_config, {name}, "", engine=engine)
                constructor = react if engine == "react" else visibility
                self.assertIn(name, constructor.call_args.kwargs["extended_names"])

    def test_cli_managed_engines_use_live_catalog(self):
        from agents.agent import assemble_agent
        from langchain_core.tools import StructuredTool

        name = "plugin_late_cli"
        dynamic_tool = StructuredTool.from_function(lambda: "ok", name=name, description="Late capability")
        spec = ToolSpec(name=name, label=name, description="Late capability", tool_names=(name,))
        for engine in ("react", "tool_calling"):
            with self.subTest(engine=engine), patch.dict(TOOL_SPECS, {name: spec}), patch("agents.agent.build_default_tools", return_value=[dynamic_tool]), patch("agents.agent.cli_enabled_tools", return_value={name}), patch("agents.agent.ReActAgent") as react, patch("agents.agent.create_agent"), patch("agents.agent.PrefixSummarizer"), patch("agents.tool_visibility.ToolVisibilityMiddleware") as visibility:
                assemble_agent(model=object(), engine=engine, middleware=None if engine == "react" else [])
                constructor = react if engine == "react" else visibility
                self.assertIn(name, constructor.call_args.kwargs["extended_names"])


class SkillPromptTest(unittest.TestCase):
    def test_catalog_only_requested_when_loader_is_visible(self):
        with patch("extensions.manager.skill_catalog", return_value="# Skills\nUnique skill summary") as catalog, patch("extensions.manager.get_default_manager") as manager:
            hidden = build_system_prompt(tools={"read_file"}, project_context="")
            self.assertNotIn("Unique skill summary", hidden)
            catalog.assert_not_called()
            visible = build_system_prompt(tools={"load_skill"}, project_context="")
            self.assertIn("Unique skill summary", visible)
            catalog.assert_called_once_with()
            manager.assert_not_called()


class CliExtensionsTest(unittest.TestCase):
    def test_cli_initializes_manager_before_reading_persisted_enablement(self):
        from agents.agent import cli_enabled_tools
        calls = []
        with patch("extensions.manager.get_default_manager", side_effect=lambda: calls.append("manager")), patch("config.store.ToolStore") as store:
            store.return_value.enabled_tools.side_effect = lambda: calls.append("store") or {"persisted"}
            self.assertEqual(cli_enabled_tools(), {"persisted"})
        self.assertEqual(calls, ["manager", "store"])

    def test_default_tools_receive_persisted_configs(self):
        from agents.agent import build_default_tools
        context = object()
        configs = {"persisted": {"OPTION": "value"}}
        with patch("agents.agent.cli_enabled_tools", return_value={"persisted"}), patch("config.store.ToolStore") as store, patch("agents.agent.build_tools", return_value=[]) as build:
            store.return_value.configs.return_value = configs
            self.assertEqual(build_default_tools(context), [])
            build.assert_called_once_with({"persisted"}, configs, context)


if __name__ == "__main__":
    unittest.main()
