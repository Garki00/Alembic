# tests/cli_test.py
"""Unit tests for the CLI interaction layer (main.ChatCLI)."""

import io
import shutil
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from agents.approval import ApprovalPolicy
from agents.context import SessionMeta, SessionStore
from main import ChatCLI


@contextmanager
def workspace_tempdir():
    """Temporary directory inside the project (the system temp directory may be unwritable in the sandbox)."""
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"cli_{uuid4().hex[:12]}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class StubSummarizer:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        return AIMessage(content="这是对话摘要")


class StubAgent:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, payload):
        messages = list(payload["messages"])
        self.calls += 1
        return {"messages": [*messages, AIMessage(content=f"第 {self.calls} 轮回复")]}


class FailingAgent:
    def invoke(self, payload):
        raise RuntimeError("模型服务不可用")


def make_cli(tmp, agent=None, summarizer=None, session_id="cli") -> ChatCLI:
    return ChatCLI(
        agent or StubAgent(),
        summarizer if summarizer is not None else StubSummarizer(),
        session_id=session_id,
        store=SessionStore(tmp),
    )


def run_command(cli: ChatCLI, command: str) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli._handle_command(command)
    return buffer.getvalue()


class CommandTest(unittest.TestCase):
    def test_help_and_unknown_and_exit(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            self.assertIn("/compact", run_command(cli, "/help"))
            self.assertIn("未知命令", run_command(cli, "/nope"))
            with redirect_stdout(io.StringIO()):
                self.assertFalse(cli._handle_command("/exit"))

    def test_debug_toggle(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            self.assertFalse(cli.debug)
            self.assertIn("开", run_command(cli, "/debug"))
            self.assertTrue(cli.debug)

    def test_status_shows_context_stats(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            cli.session.ask("你好")
            output = run_command(cli, "/status")
            self.assertIn("tokens", output)
            self.assertIn("cli", output)

    def test_history_prints_roles(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            cli.session.ask("你好")
            output = run_command(cli, "/history 5")
            self.assertIn("[用户]", output)
            self.assertIn("[助手]", output)
            self.assertIn("你好", output)


class SessionCommandTest(unittest.TestCase):
    def test_new_session_switches_and_clears(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            cli.session.ask("旧会话的问题")
            self.assertTrue(cli.session.store.path("cli").exists())

            output = run_command(cli, "/new work")
            self.assertEqual(cli.session.session_id, "work")
            self.assertEqual(len(cli.session), 0)
            self.assertIn("已开启新会话", output)
            self.assertFalse(cli.session.store.path("work").exists())

    def test_new_without_id_generates_timestamp_id(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            run_command(cli, "/new")
            self.assertTrue(cli.session.session_id.startswith("session-"))

    def test_switch_session_restores_history(self):
        with workspace_tempdir() as tmp:
            store = SessionStore(tmp)
            store.save(
                "saved",
                [HumanMessage(content="旧问题"), AIMessage(content="旧回答")],
                SessionMeta("saved", turns=1),
            )
            cli = make_cli(tmp)
            output = run_command(cli, "/session saved")
            self.assertEqual(cli.session.session_id, "saved")
            self.assertEqual(len(cli.session), 2)
            self.assertIn("已切换", output)

    def test_sessions_lists_saved_conversations(self):
        with workspace_tempdir() as tmp:
            store = SessionStore(tmp)
            store.save("a", [HumanMessage(content="x")], SessionMeta("a", updated_at="2026-01-01T00:00:00"))
            store.save("b", [HumanMessage(content="y")], SessionMeta("b", updated_at="2026-02-01T00:00:00"))
            cli = make_cli(tmp, session_id="a")
            output = run_command(cli, "/sessions")
            self.assertIn("a", output)
            self.assertIn("b", output)
            current_lines = [line for line in output.splitlines() if " a\t" in line]
            self.assertTrue(current_lines and current_lines[0].startswith("*"))


class AskFlowTest(unittest.TestCase):
    def test_ask_once_prints_reply(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                cli.ask_once("你好")
            self.assertIn("第 1 轮回复", buffer.getvalue())

    def test_debug_mode_prints_trace(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            cli.debug = True
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                cli.ask_once("你好")
            self.assertIn("本轮消息轨迹", buffer.getvalue())

    def test_ask_once_reports_errors_without_crashing(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp, agent=FailingAgent())
            errors = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                cli.ask_once("会失败的问题")
            self.assertIn("[错误]", errors.getvalue())

    def test_compact_command(self):
        with workspace_tempdir() as tmp:
            summarizer = StubSummarizer()
            cli = make_cli(tmp, summarizer=summarizer)
            cli.session.keep_recent = 1
            bulk = "这是一段用来撑大上下文的说明文字。" * 40
            cli.session.ask(f"第一个问题 {bulk}")
            cli.session.ask(f"第二个问题 {bulk}")
            output = run_command(cli, "/compact")
            self.assertIn("已压缩", output)
            # 首轮标题一次 + 手动压缩一次
            self.assertEqual(summarizer.calls, 2)

    def test_compact_without_history(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            output = run_command(cli, "/compact")
            self.assertIn("没有可压缩", output)


class ApprovalCommandTest(unittest.TestCase):
    def _cli_with_policy(self, tmp, policy: ApprovalPolicy) -> ChatCLI:
        return ChatCLI(
            StubAgent(),
            StubSummarizer(),
            session_id="cli",
            store=SessionStore(tmp),
            approval=policy,
        )

    def test_lists_and_clears_remembered_approvals(self):
        with workspace_tempdir() as tmp:
            policy = ApprovalPolicy({"write_file": "write"})
            policy.remember("write_file", {"path": "a.txt", "content": "x"})
            cli = self._cli_with_policy(tmp, policy)

            output = run_command(cli, "/approvals")
            self.assertIn("a.txt", output)
            self.assertIn("1 条", output)

            output = run_command(cli, "/approvals clear")
            self.assertIn("已清除 1", output)
            self.assertEqual(policy.items(), [])

    def test_new_session_drops_approvals(self):
        with workspace_tempdir() as tmp:
            policy = ApprovalPolicy({"write_file": "write"})
            policy.remember("write_file", {"path": "a.txt", "content": "x"})
            cli = self._cli_with_policy(tmp, policy)

            run_command(cli, "/new other")
            self.assertEqual(policy.items(), [])

    def test_disabled_approval_reports(self):
        with workspace_tempdir() as tmp:
            cli = make_cli(tmp)
            self.assertIn("未启用", run_command(cli, "/approvals"))


if __name__ == "__main__":
    unittest.main()
