"""Offline tests for subagent profiles, manager lifecycle, and tool contracts."""

from __future__ import annotations

import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import ToolMessage

from agents.subagents.manager import LaunchContext, SubagentManager
from agents.subagents.profiles import (
    EXPLORE,
    GENERAL_PURPOSE,
    find_profile,
    load_profiles,
    parse_profile_markdown,
)
from agents.subagents.prompts import build_subagent_system_prompt
from tools.context import ToolContext
from tools.subagent import build_subagent_tools


def make_tempdir(prefix: str = "subagent_") -> Path:
    """Temporary directory inside the project (system temp is unwritable here)."""
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}{uuid4().hex[:12]}"
    path.mkdir()
    return path


class FakeResult:
    def __init__(self, text: str, usage: dict | None = None, tools: int = 1) -> None:
        self.text = text
        self.new_messages = [
            ToolMessage(content="ok", tool_call_id=f"call-{index}")
            for index in range(tools)
        ]
        self._usage = usage or {"total_tokens": 100, "input_tokens": 80, "output_tokens": 20}

    def usage_dict(self) -> dict:
        return dict(self._usage)


class FakeSession:
    """ChatSession-shaped double that streams a fixed number of turns."""

    def __init__(self, replies: list[str], *, release: threading.Event | None = None) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.display_messages: list = []
        self.release = release

    def ask_stream(self, message: str):
        self.prompts.append(message)
        if self.release is not None:
            self.release.wait(5)
        yield ("thought", "checking the workspace")
        yield ("tool", {"id": "call-1", "name": "read_file", "args": {"path": "a.txt"}})
        yield ("tool_result", {"id": "call-1", "name": "read_file", "content": "hello"})
        text = self.replies.pop(0) if self.replies else "(done)"
        self.display_messages.append(text)
        yield ("done", FakeResult(text))


def launch_context(tmp: Path, *, enabled: set[str] | None = None, **kwargs) -> LaunchContext:
    enabled = enabled or {"read_file", "run_command", "agent", "send_message"}
    return LaunchContext(
        session_id="session-1",
        workspace=tmp,
        workspace_id="ws-1",
        model=SimpleNamespace(name="fake-model"),
        model_cfg=SimpleNamespace(provider_id="test", model="fake", context_length=0),
        enabled_tools=set(enabled),
        visible_tools=set(enabled),
        **kwargs,
    )


def build_manager(tmp: Path, session: FakeSession, **kwargs) -> SubagentManager:
    return SubagentManager(
        profiles_dir=tmp / "agents",
        store_dir=tmp / "store",
        output_dir=tmp / "out",
        auto_background_seconds=0,
        session_factory=lambda task, ctx, profile: session,
        **kwargs,
    )


def collect_events() -> tuple[list[tuple[str, dict]], object]:
    events: list[tuple[str, dict]] = []
    return events, lambda kind, data: events.append((kind, dict(data)))


class ProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_tempdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_builtins_are_always_present(self) -> None:
        profiles = load_profiles(user_dir=self.tmp / "agents", workspace=self.tmp)
        names = [item.name for item in profiles]
        self.assertIn(GENERAL_PURPOSE, names)
        self.assertIn(EXPLORE, names)
        explore = find_profile(profiles, "explore")
        self.assertIsNotNone(explore)
        self.assertIn("read_file", explore.child_tools({"read_file", "run_command"}))
        self.assertNotIn("run_command", explore.child_tools({"read_file", "run_command"}))

    def test_markdown_profile_overrides_by_name(self) -> None:
        directory = self.tmp / "agents"
        directory.mkdir()
        (directory / "reviewer.md").write_text(
            "---\n"
            "name: reviewer\n"
            "description: Reviews diffs\n"
            "tools: read_file, search_files\n"
            "permissionMode: read_only\n"
            "model: reviewer-model\n"
            "---\n"
            "You review changes carefully.\n",
            encoding="utf-8",
        )
        profiles = load_profiles(user_dir=directory, workspace=self.tmp)
        reviewer = find_profile(profiles, "reviewer")
        self.assertIsNotNone(reviewer)
        self.assertEqual(reviewer.tools, ("read_file", "search_files"))
        self.assertEqual(reviewer.restrict_mode("workspace_write"), "read_only")
        self.assertEqual(reviewer.restrict_mode("read_only"), "read_only")

    def test_invalid_markdown_is_skipped(self) -> None:
        profile = parse_profile_markdown("---\nname: [\n---\nbody", source="user")
        self.assertIsNone(profile)

    def test_child_surface_never_exposes_delegation(self) -> None:
        profile = find_profile(load_profiles(user_dir=self.tmp / "agents"), GENERAL_PURPOSE)
        tools = profile.child_tools({"agent", "send_message", "read_file"})
        self.assertEqual(tools, ("read_file",))

    def test_child_prompt_keeps_the_base_language_rule(self) -> None:
        base = "Answer in the user's language."
        prompt = build_subagent_system_prompt(
            profile_prompt="Review the change.",
            base_prompt=base,
        )
        self.assertIn(base, prompt)


class ManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_tempdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_foreground_run_returns_framed_report_and_usage(self) -> None:
        session = FakeSession(["Found it in a.txt"])
        manager = build_manager(self.tmp, session)
        events, emit = collect_events()
        ctx = launch_context(self.tmp, emit=emit)
        service = manager.bind(ctx)

        result = service.run(description="locate", prompt="find hello")

        self.assertIn("[Subagent hand-back]", result)
        self.assertIn("  Found it in a.txt", result)
        self.assertIn("<usage>", result)
        self.assertIn("tool_uses: 1", result)
        self.assertIn("send_message", result)
        kinds = [kind for kind, _ in events]
        self.assertIn("subagent_started", kinds)
        self.assertIn("subagent_progress", kinds)
        self.assertIn("subagent_completed", kinds)
        tasks = manager.list_tasks("session-1")
        self.assertEqual(tasks[0]["status"], "completed")
        self.assertEqual(tasks[0]["tool_uses"], 1)
        self.assertTrue(Path(tasks[0]["output_file"]).is_file())
        # The child prompt is the caller's prompt, never the parent conversation.
        self.assertEqual(session.prompts, ["find hello"])

    def test_child_tools_exclude_nesting(self) -> None:
        session = FakeSession(["ok"])
        manager = build_manager(self.tmp, session)
        ctx = launch_context(self.tmp)
        manager.bind(ctx).run(description="x", prompt="y")
        task = manager.list_tasks("session-1")[0]
        self.assertNotIn("agent", task["allowed_tools"])
        self.assertNotIn("send_message", task["allowed_tools"])
        self.assertIn("read_file", task["allowed_tools"])

    def test_unknown_profile_returns_error(self) -> None:
        manager = build_manager(self.tmp, FakeSession(["unused"]))
        result = manager.bind(launch_context(self.tmp)).run(
            description="x", prompt="y", agent_type="missing"
        )
        self.assertTrue(result.startswith("Error: [subagent_unknown_type]"))
        self.assertEqual(manager.list_tasks("session-1"), [])

    def test_background_completion_is_notified(self) -> None:
        release = threading.Event()
        session = FakeSession(["background report"], release=release)
        manager = build_manager(self.tmp, session)
        events, emit = collect_events()
        result = manager.bind(launch_context(self.tmp, emit=emit)).run(
            description="bg", prompt="work", run_in_background=True
        )
        self.assertIn("Async agent launched successfully", result)
        time.sleep(0.1)
        release.set()
        deadline = time.time() + 5
        while time.time() < deadline and manager.running_count("session-1"):
            time.sleep(0.02)
        notifications = manager.drain_notifications("session-1")
        self.assertEqual(len(notifications), 1)
        self.assertIn("<task-notification>", notifications[0])
        self.assertIn("background report", notifications[0])
        # Draining is one-shot.
        self.assertEqual(manager.drain_notifications("session-1"), [])

    def test_foreground_auto_backgrounds_and_reports_later(self) -> None:
        release = threading.Event()
        session = FakeSession(["late report"], release=release)
        manager = build_manager(self.tmp, session)
        manager.auto_background_seconds = 0.1
        service = manager.bind(launch_context(self.tmp))
        result = service.run(description="slow", prompt="work")
        self.assertIn("Async agent launched successfully", result)
        release.set()
        deadline = time.time() + 5
        while time.time() < deadline and manager.running_count("session-1"):
            time.sleep(0.02)
        self.assertEqual(len(manager.drain_notifications("session-1")), 1)

    def test_stop_marks_task_stopped(self) -> None:
        release = threading.Event()
        session = FakeSession(["never"], release=release)
        manager = build_manager(self.tmp, session)
        service = manager.bind(launch_context(self.tmp))
        service.run(description="stop me", prompt="work", run_in_background=True)
        time.sleep(0.1)
        agent_id = manager.list_tasks("session-1")[0]["id"]
        self.assertTrue(manager.stop(agent_id))
        deadline = time.time() + 5
        while time.time() < deadline and manager.running_count("session-1"):
            time.sleep(0.02)
        self.assertEqual(manager.list_tasks("session-1")[0]["status"], "stopped")

    def test_send_message_resumes_finished_agent(self) -> None:
        session = FakeSession(["first", "second"])
        manager = build_manager(self.tmp, session)
        service = manager.bind(launch_context(self.tmp))
        first = service.run(description="chat", prompt="one")
        self.assertIn("first", first)
        agent_id = manager.list_tasks("session-1")[0]["id"]
        second = service.send(agent_id=agent_id, message="two")
        self.assertIn("  second", second)
        self.assertEqual(session.prompts, ["one", "two"])

    def test_send_message_rejects_foreign_agent(self) -> None:
        manager = build_manager(self.tmp, FakeSession(["x"]))
        result = manager.bind(launch_context(self.tmp)).send(
            agent_id="subagent-missing", message="hi"
        )
        self.assertTrue(result.startswith("Error: [subagent_unknown]"))

    def test_thinking_chunks_keep_spacing_and_append_flag(self) -> None:
        manager = build_manager(self.tmp, FakeSession(["x"]))
        ctx = launch_context(self.tmp)
        task = manager._new_task(
            ctx,
            profile=find_profile(
                load_profiles(user_dir=self.tmp / "agents", workspace=self.tmp),
                GENERAL_PURPOSE,
            ),
            description="think",
            prompt="think",
            background=False,
            tool_call_id="",
        )
        events: list[tuple[str, dict]] = []
        task.emitter = lambda kind, data: events.append((kind, dict(data)))

        manager._mirror(task, "thought", {"id": "r1", "text": "Let ", "append": True})
        manager._mirror(task, "thought", {"id": "r1", "text": "me think.", "append": True})
        manager._mirror(task, "thought", "Full thought.")

        texts = [data["text"] for _kind, data in events]
        appends = [data.get("append") for _kind, data in events]
        self.assertEqual(texts, ["Let ", "me think.", "Full thought."])
        self.assertEqual(appends, [True, True, False])

    def test_child_gate_inherits_policy_and_restricts_mode(self) -> None:
        from agents.approval import ApprovalGate, ApprovalPolicy

        memo: dict = {}
        parent = ApprovalGate(
            policy=ApprovalPolicy({"run_command": "exec"}, memo, mode="workspace_write"),
            ask=lambda _request: "allow",
        )
        manager = build_manager(self.tmp, FakeSession(["x"]))
        ctx = launch_context(self.tmp, approval=parent)
        task = manager._new_task(
            ctx,
            profile=find_profile(
                load_profiles(user_dir=self.tmp / "agents", workspace=self.tmp),
                EXPLORE,
            ),
            description="read",
            prompt="read",
            background=False,
            tool_call_id="",
        )
        gate = manager._child_gate(task, ctx, "read_only", ("read_file",))
        self.assertIsNotNone(gate)
        self.assertEqual(gate.policy.mode, "read_only")
        self.assertIs(gate.policy.memo, memo)

    def test_detached_approval_uses_persistent_channel(self) -> None:
        from web.approvals import ApprovalRegistry

        class Request:
            id = "approval-1"
            tool = "run_command"
            risk = "exec"
            target = "echo"
            summary = "run"
            preview = "echo"
            args: dict = {}
            reason = ""
            rule = ""
            steps: tuple = ()

            def as_event(self):
                return {"id": self.id, "tool": self.tool, "summary": self.summary}

        registry = ApprovalRegistry()
        manager = build_manager(self.tmp, FakeSession(["x"]), approval_registry=registry)
        task = manager._new_task(
            launch_context(self.tmp),
            profile=find_profile(load_profiles(user_dir=self.tmp / "agents"), GENERAL_PURPOSE),
            description="bg",
            prompt="x",
            background=True,
            tool_call_id="",
        )
        self.assertTrue(task.detached)
        result: dict = {}

        def ask() -> None:
            result["decision"] = manager._ask(task, Request())

        thread = threading.Thread(target=ask)
        thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and len(registry) == 0:
            time.sleep(0.01)
        self.assertTrue(registry.resolve("approval-1", "once"))
        thread.join(5)
        self.assertEqual(result["decision"], "once")


class ToolContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_tempdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_agent_tool_receives_tool_call_id(self) -> None:
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import (
            FakeMessagesListChatModel,
        )
        from langchain_core.messages import AIMessage

        class ToolCallingFake(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        tmp = self.tmp

        class FakeService:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def profiles(self):
                return load_profiles(user_dir=tmp / "agents", workspace=tmp)

            def run(self, **kwargs):
                self.calls.append(kwargs)
                return "child report"

            def send(self, **kwargs):  # pragma: no cover - not exercised here
                return "continued"

        model = ToolCallingFake(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "agent",
                            "args": {"description": "look up", "prompt": "find it"},
                            "id": "call_abc",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="final"),
            ]
        )
        service = FakeService()
        ctx = ToolContext(workspace=self.tmp, subagents=service)
        agent = create_agent(
            model,
            tools=build_subagent_tools(ctx),
            system_prompt="sys",
        )
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        self.assertEqual(service.calls[0]["tool_call_id"], "call_abc")
        self.assertEqual(service.calls[0]["agent_type"], "")

    def test_tools_expose_no_runtime_argument_and_list_profiles(self) -> None:
        tmp = self.tmp

        class FakeService:
            def profiles(self):
                return load_profiles(user_dir=tmp / "agents", workspace=tmp)

            def run(self, **kwargs):  # pragma: no cover - schema-only test
                return ""

            def send(self, **kwargs):  # pragma: no cover - schema-only test
                return ""

        ctx = ToolContext(workspace=self.tmp, subagents=FakeService())
        tools = build_subagent_tools(ctx)
        names = sorted(tool.name for tool in tools)
        self.assertEqual(names, ["agent", "send_message"])
        agent = next(tool for tool in tools if tool.name == "agent")
        schema = agent.args_schema.model_json_schema()
        self.assertEqual(
            sorted(schema["properties"]), ["description", "prompt", "run_in_background", "subagent_type"]
        )
        self.assertIn("Explore", agent.description)
        self.assertIn("general-purpose", agent.description)
        self.assertIn("English", agent.description)
        self.assertIn("English", schema["properties"]["prompt"]["description"])


if __name__ == "__main__":
    unittest.main()
