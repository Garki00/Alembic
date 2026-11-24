"""Tests for the experimental praise note and its settings entry."""

from __future__ import annotations

import shutil
import threading
import time
import unittest
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage

from agents.subagents.admission import ModelRequestAdmission
from agents.subagents.manager import LaunchContext, SubagentManager
from agents.subagents.praise import _clean_lines, generate_praise
from config.store import ToolStore
from tools.registry import TOOL_SPECS


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"praise_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.new_messages: list = []

    def usage_dict(self) -> dict:
        return {"total_tokens": 10}


class FakeSession:
    def __init__(self, report: str = "Checked both entry points, found the root cause.") -> None:
        self.report = report
        self.display_messages: list = []

    def ask_stream(self, message: str):
        yield ("done", FakeResult(self.report))


class PraiseModel:
    def __init__(self, text: str = "定位很准，先验证再下结论这一步做得稳。") -> None:
        self.text = text
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content=self.text)


class PraiseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_entry_is_experimental_and_disabled_by_default(self) -> None:
        spec = TOOL_SPECS["praise_subagent"]
        self.assertTrue(spec.experimental)
        self.assertFalse(spec.default_enabled)
        self.assertIsNone(spec.builder)
        self.assertEqual(spec.tool_names, ())
        self.assertNotIn("妈妈", spec.description)

    def test_tool_store_serializes_experimental_and_default(self) -> None:
        store = ToolStore(self.tmp / "tools.json")
        store.ensure_saved()
        entry = next(item for item in store.public() if item["name"] == "praise_subagent")
        self.assertTrue(entry["experimental"])
        self.assertFalse(entry["enabled"])

    def test_clean_lines_drops_framing_and_caps(self) -> None:
        lines = _clean_lines("```\n- 第一行\n2. 第二行\n\n* 第三行\n第四行\n第五行\n```")
        self.assertEqual(lines, ["第一行", "第二行", "第三行", "第四行"])

    def test_generate_praise_uses_the_report_and_returns_lines(self) -> None:
        model = PraiseModel("- 先确认了入口再修改\n- 结论清楚")
        lines = generate_praise(
            model,
            description="修复登录问题",
            agent_type="general-purpose",
            report="Found the root cause and added a regression test.",
            admission=ModelRequestAdmission(2),
        )
        self.assertEqual(lines, ["先确认了入口再修改", "结论清楚"])
        self.assertEqual(len(model.calls), 1)
        human = model.calls[0][1].content
        self.assertIn("Found the root cause", human)

    def test_generate_praise_skips_empty_report(self) -> None:
        model = PraiseModel()
        self.assertEqual(
            generate_praise(model, description="x", agent_type="general-purpose", report="  "),
            [],
        )
        self.assertEqual(model.calls, [])

    def test_manager_emits_praise_only_when_enabled(self) -> None:
        events: list[tuple[str, dict]] = []
        event = threading.Event()

        def emit(kind: str, data: dict) -> None:
            events.append((kind, dict(data)))
            if kind == "praise":
                event.set()

        model = PraiseModel()
        manager = SubagentManager(
            profiles_dir=self.tmp / "agents",
            store_dir=self.tmp / "store",
            output_dir=self.tmp / "out",
            auto_background_seconds=0,
            session_factory=lambda task, ctx, profile: FakeSession(),
        )
        ctx = LaunchContext(
            session_id="praise-session",
            workspace=self.tmp,
            model=model,
            enabled_tools={"read_file", "praise_subagent"},
            visible_tools={"read_file"},
            emit=emit,
        )
        manager.bind(ctx).run(description="review", prompt="check it")
        self.assertTrue(event.wait(5), "praise event was not emitted")
        praise = [data for kind, data in events if kind == "praise"][0]
        self.assertEqual(len(praise["lines"]), 1)
        self.assertIn("定位很准", praise["lines"][0])

        model.calls.clear()
        events.clear()
        event.clear()
        manager2 = SubagentManager(
            profiles_dir=self.tmp / "agents",
            store_dir=self.tmp / "store2",
            output_dir=self.tmp / "out2",
            auto_background_seconds=0,
            session_factory=lambda task, ctx, profile: FakeSession(),
        )
        ctx2 = LaunchContext(
            session_id="no-praise",
            workspace=self.tmp,
            model=model,
            enabled_tools={"read_file"},
            visible_tools={"read_file"},
            emit=emit,
        )
        manager2.bind(ctx2).run(description="review", prompt="check it")
        time.sleep(0.2)
        self.assertEqual([kind for kind, _ in events if kind == "praise"], [])
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
