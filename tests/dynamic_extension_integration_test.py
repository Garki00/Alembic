"""Dynamic registry extensions participate in discovery and both runtime engines."""

import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.tools import StructuredTool

from agents.permission_service import evaluate
from prompts.builder import build_system_prompt
from prompts.sections.git_status import GitSnapshot
from tools.catalog import (
    ToolActivation,
    build_contracts,
    enabled_tool_names,
    extended_tool_names,
    resident_tool_names,
    resolve_exposure,
    search_contracts,
)
from tools.context import ToolContext
from tools.registry import TOOL_SPECS, ToolSpec, build_tools, capability_for


def _dynamic_tool(name: str, description: str = "Late capability") -> StructuredTool:
    return StructuredTool.from_function(lambda: "ok", name=name, description=description)


class DynamicCatalogTest(unittest.TestCase):
    def test_late_registration_search_activation_and_removal(self):
        name = "plugin_test_capability"
        self.assertNotIn(name, extended_tool_names())
        spec = ToolSpec(
            name=name,
            label="Dynamic capability",
            description="quasar lookup",
            tool_names=(name,),
            risk="exec",
            defer=True,
            builder=lambda _config, _ctx: [_dynamic_tool(name)],
        )
        with patch.dict(TOOL_SPECS, {name: spec}):
            enabled = enabled_tool_names({name})
            activation = ToolActivation()
            ctx = ToolContext(workspace=Path.cwd(), activation=activation, enabled_tool_names=enabled)
            contracts = build_contracts(build_tools({name}, {}, ctx))
            self.assertIn(name, extended_tool_names())
            exposure = resolve_exposure(contracts, activation, search_enabled=True)
            self.assertNotIn(name, exposure.visible)
            self.assertIn(name, exposure.deferred)
            self.assertEqual(search_contracts("quasar", contracts, 3), [name])
            activation.activate({name})
            self.assertNotIn(
                name,
                resolve_exposure(contracts, activation, search_enabled=True).visible,
            )
            self.assertEqual(evaluate(name, {}, mode="build").behavior, "ask")
        self.assertNotIn(name, extended_tool_names())

    def test_builtin_skill_tools_are_immediately_visible_when_enabled(self):
        names = {"load_skill", "read_skill_resource"}
        specs = {
            name: ToolSpec(
                name=name,
                label=name,
                description=name,
                tool_names=(name,),
                builder=lambda _config, _ctx, n=name: [_dynamic_tool(n)],
            )
            for name in names
        }
        with patch.dict(TOOL_SPECS, specs):
            contracts = build_contracts(build_tools(names, {}, ToolContext()))
            exposure = resolve_exposure(contracts)
            self.assertTrue(names <= set(resident_tool_names()))
            self.assertEqual(set(exposure.visible), names)
            self.assertFalse(names & extended_tool_names())

    def test_legacy_groups_expand_in_catalog_and_approval(self):
        self.assertIn("write_file", enabled_tool_names({"file_write"}))
        self.assertFalse(capability_for(tool_name="write_file")["read_only"])
        self.assertEqual(evaluate("write_file", {"path": "a.txt"}, mode="build").behavior, "ask")
        self.assertEqual(evaluate("write_file", {"path": "a.txt"}, mode="edit").behavior, "allow")


class RuntimeExtensionsTest(unittest.TestCase):
    def test_web_engines_use_live_catalog(self):
        from types import SimpleNamespace
        from web.runtime import build_runtime

        from tools.catalog import TOOL_SEARCH_NAME

        name = "plugin_late_runtime"
        spec = ToolSpec(
            name=name,
            label=name,
            description="Late capability",
            tool_names=(name,),
            defer=True,
            builder=lambda _config, _ctx: [_dynamic_tool(name)],
        )
        runtime_tools = [_dynamic_tool(name), _dynamic_tool(TOOL_SEARCH_NAME)]
        model_config = SimpleNamespace(supports_images=False, model="m")
        for engine in ("react", "tool_calling"):
            with self.subTest(engine=engine), patch.dict(TOOL_SPECS, {name: spec}), patch(
                "web.runtime.create_chat_model"
            ), patch("web.runtime.build_tools", return_value=runtime_tools), patch(
                "web.runtime.ReActAgent"
            ) as react, patch("web.runtime.create_agent"), patch(
                "web.runtime.PrefixSummarizer"
            ), patch(
                "agents.tool_exposure.ToolExposureMiddleware"
            ) as exposure:
                build_runtime(model_config, {name, TOOL_SEARCH_NAME}, "", engine=engine)
                if engine == "react":
                    self.assertIn(name, react.call_args.kwargs["extended_names"])
                else:
                    self.assertIn(name, exposure.call_args.kwargs["exposure"].deferred)

    def test_cli_managed_engines_use_live_catalog(self):
        from agents.agent import assemble_agent

        from tools.catalog import TOOL_SEARCH_NAME

        name = "plugin_late_cli"
        dynamic_tool = _dynamic_tool(name)
        search_tool = _dynamic_tool(TOOL_SEARCH_NAME)
        spec = ToolSpec(
            name=name,
            label=name,
            description="Late capability",
            tool_names=(name,),
            defer=True,
        )
        for engine in ("react", "tool_calling"):
            with self.subTest(engine=engine), patch.dict(TOOL_SPECS, {name: spec}), patch(
                "agents.agent.build_default_tools", return_value=[dynamic_tool, search_tool]
            ), patch(
                "agents.agent.cli_enabled_tools", return_value={name, TOOL_SEARCH_NAME}
            ), patch(
                "agents.agent.ReActAgent"
            ) as react, patch("agents.agent.create_agent"), patch(
                "agents.agent.PrefixSummarizer"
            ), patch(
                "agents.tool_exposure.ToolExposureMiddleware"
            ) as exposure:
                assemble_agent(model=object(), engine=engine, middleware=None if engine == "react" else [])
                if engine == "react":
                    self.assertIn(name, react.call_args.kwargs["extended_names"])
                else:
                    self.assertIn(name, exposure.call_args.kwargs["exposure"].deferred)


class SkillPromptTest(unittest.TestCase):
    def test_catalog_only_requested_when_loader_is_visible(self):
        with patch(
            "extensions.manager.skill_catalog",
            return_value="# Skills\nUnique skill summary",
        ) as catalog, patch("extensions.manager.get_default_manager") as manager:
            hidden = build_system_prompt(
                tools={"read_file"}, project_context="", git_snapshot=GitSnapshot()
            )
            self.assertNotIn("Unique skill summary", hidden)
            catalog.assert_not_called()
            visible = build_system_prompt(
                tools={"load_skill"}, project_context="", git_snapshot=GitSnapshot()
            )
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
