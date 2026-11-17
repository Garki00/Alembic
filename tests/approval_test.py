# tests/approval_test.py
"""Tool approval tests: policy, middleware interception, CLI prompts, and fail-closed behavior."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from agents.approval import (
    ApprovalGate,
    ApprovalMiddleware,
    ApprovalPolicy,
    ApprovalRequest,
    console_asker,
    describe_call,
    describe_steps,
    gated_tools,
    permission_tools,
)


def make_call(name: str, args: dict):
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "call-1"})


class Recorder:
    """Records incoming requests and answers them in a preset order."""

    def __init__(self, *decisions: str) -> None:
        self.decisions = list(decisions) or ["deny"]
        self.requests: list[ApprovalRequest] = []

    def __call__(self, request: ApprovalRequest) -> str:
        self.requests.append(request)
        return self.decisions.pop(0) if self.decisions else "deny"


class DescribeCallTest(unittest.TestCase):
    def test_command_is_described_in_plain_language(self):
        target, summary, preview = describe_call("run_command", {"command": "pytest -q"})
        self.assertEqual(target, "pytest -q")
        self.assertEqual(summary, "运行测试")
        self.assertEqual(preview, "pytest -q")

    def test_command_uses_model_description_when_present(self):
        _, summary, _ = describe_call(
            "run_command",
            {
                "command": "Get-ChildItem *.html | Select-Object Name,Length",
                "description": "扫描当前目录的 HTML 文件，列出名称和大小",
            },
        )
        self.assertEqual(summary, "扫描当前目录的 HTML 文件，列出名称和大小")

    def test_command_description_equal_to_command_is_ignored(self):
        _, summary, _ = describe_call(
            "run_command", {"command": "git status", "description": "git status"}
        )
        self.assertEqual(summary, "查看 Git 状态")

    def test_long_pipeline_is_split_into_steps(self):
        command = (
            'Get-ChildItem *.html | Select-Object Name,Length; '
            'Select-String -Path *.html -Pattern \'href="([^"]+)"\' | '
            'Format-Table -AutoSize'
        )
        _, summary, _ = describe_call("run_command", {"command": command})
        self.assertIn("*.html", summary)
        steps = describe_steps("run_command", {"command": command})
        self.assertEqual(len(steps), 2)
        self.assertIn("href", steps[1])
        self.assertEqual(describe_steps("write_file", {"path": "a.txt"}), ())

    def test_long_command_preview_is_complete(self):
        command = "\n".join(f"Get-ChildItem *.html | Select-Object Name,Length | Format-Table" for _ in range(40))
        _, _, preview = describe_call("run_command", {"command": command})
        self.assertEqual(preview, command)
        self.assertGreater(len(preview), 800)

    def test_empty_command_gets_generic_summary(self):
        _, summary, _ = describe_call("run_command", {"command": ""})
        self.assertEqual(summary, "执行一条 shell 命令，原文见下方")

    def test_write_file(self):
        target, summary, preview = describe_call("write_file", {"path": "a.txt", "content": "hi"})
        self.assertEqual(target, "a.txt")
        self.assertIn("写入文件", summary)
        self.assertEqual(preview, "hi")

    def test_edit_file_shows_diff(self):
        _, _, preview = describe_call(
            "edit_file", {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}
        )
        self.assertIn("- x = 1", preview)
        self.assertIn("+ x = 2", preview)

    def test_preview_is_clipped(self):
        _, _, preview = describe_call("write_file", {"path": "big.txt", "content": "x" * 5000})
        self.assertLess(len(preview), 900)
        self.assertTrue(preview.endswith("…"))


class GatingTest(unittest.TestCase):
    def test_only_write_and_exec_are_gated(self):
        gated = gated_tools({"get_weather", "file_read", "file_write", "run_command"})
        self.assertNotIn("get_weather", gated)
        self.assertNotIn("read_file", gated)
        self.assertEqual(gated["write_file"], "write")
        self.assertEqual(gated["edit_file"], "write")
        self.assertEqual(gated["run_command"], "exec")

    def test_disabled_tool_is_not_gated(self):
        gated = gated_tools({"file_read"})
        self.assertEqual(gated, {})

    def test_permission_modes(self):
        enabled = {"file_read", "file_write", "run_command"}
        read_only = permission_tools(enabled, "read_only")
        self.assertEqual(read_only, {})

        approval_all = permission_tools(enabled, "approval_all")
        self.assertIn("read_file", approval_all)
        self.assertIn("write_file", approval_all)
        self.assertIn("run_command", approval_all)

        workspace = permission_tools(enabled, "workspace_write")
        self.assertNotIn("write_file", workspace)
        self.assertIn("run_command", workspace)

        self.assertEqual(permission_tools(enabled, "full_access"), {})


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = ApprovalPolicy({"write_file": "write", "edit_file": "write"})

    def test_remember_and_allow(self):
        args = {"path": "a.txt", "content": "x"}
        self.assertFalse(self.policy.is_allowed("write_file", args))
        self.policy.remember("write_file", args)
        self.assertTrue(self.policy.is_allowed("write_file", args))
        # 不同目标不算同一条授权
        self.assertFalse(self.policy.is_allowed("write_file", {"path": "b.txt", "content": "x"}))

    def test_write_tools_share_one_grant(self):
        """After allowing an a.py edit, switching to edit_file on the same file must not prompt again."""
        self.policy.remember("write_file", {"path": "a.py", "content": "x"})
        self.assertTrue(self.policy.is_allowed("edit_file", {"path": "a.py", "old_string": "a", "new_string": "b"}))
        self.assertFalse(self.policy.is_allowed("edit_file", {"path": "b.py", "old_string": "a", "new_string": "b"}))

    def test_items_and_clear(self):
        self.policy.remember("write_file", {"path": "a.txt", "content": "x"})
        items = self.policy.items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["target"], "a.txt")
        self.assertEqual(self.policy.clear(), 1)
        self.assertEqual(self.policy.items(), [])


class MiddlewareTest(unittest.TestCase):
    def setUp(self):
        self.policy = ApprovalPolicy({"write_file": "write", "run_command": "exec"})
        self.executed: list[dict] = []

    def build(self, ask):
        gate = ApprovalGate(policy=self.policy, ask=ask)
        middleware = ApprovalMiddleware(gate)

        def handler(request):
            self.executed.append(request.tool_call)
            return f"执行了 {request.tool_call['name']}"

        return middleware, handler

    def test_read_tool_never_asks(self):
        ask = Recorder("deny")
        middleware, handler = self.build(ask)
        result = middleware.wrap_tool_call(make_call("read_file", {"path": "a.txt"}), handler)
        self.assertEqual(result, "执行了 read_file")
        self.assertEqual(ask.requests, [])

    def test_allow_once_executes_and_forgets(self):
        ask = Recorder("once", "once")
        middleware, handler = self.build(ask)
        args = {"path": "a.txt", "content": "x"}

        self.assertEqual(middleware.wrap_tool_call(make_call("write_file", args), handler),
                         "执行了 write_file")
        middleware.wrap_tool_call(make_call("write_file", args), handler)
        self.assertEqual(len(ask.requests), 2)  # 允许本次不记忆
        self.assertEqual(self.policy.items(), [])

    def test_always_executes_and_remembers(self):
        ask = Recorder("always")
        middleware, handler = self.build(ask)
        args = {"path": "a.txt", "content": "x"}

        middleware.wrap_tool_call(make_call("write_file", args), handler)
        middleware.wrap_tool_call(make_call("write_file", args), handler)

        self.assertEqual(len(ask.requests), 1)  # 第二次不再打扰
        self.assertEqual(len(self.executed), 2)  # 两次都真的执行了
        self.assertEqual(len(self.policy.items()), 1)

    def test_deny_blocks_tool_and_returns_error_message(self):
        ask = Recorder("deny")
        middleware, handler = self.build(ask)

        result = middleware.wrap_tool_call(make_call("run_command", {"command": "echo x"}), handler)
        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.tool_call_id, "call-1")
        self.assertIn("Error: [approval_denied]", result.content)
        self.assertEqual(self.executed, [])

    def test_hardline_command_is_blocked_without_asking(self):
        ask = Recorder("once")
        middleware, handler = self.build(ask)

        result = middleware.wrap_tool_call(
            make_call("run_command", {"command": "format D:"}), handler
        )
        self.assertIsInstance(result, ToolMessage)
        self.assertIn("Error: [blocked_by_policy]", result.content)
        self.assertEqual(ask.requests, [])
        self.assertEqual(self.executed, [])

    def test_elevated_command_carries_reason_into_request(self):
        ask = Recorder("deny")
        middleware, handler = self.build(ask)

        middleware.wrap_tool_call(
            make_call("run_command", {"command": "git push --force origin main"}), handler
        )
        request = ask.requests[0]
        self.assertIn("Git", request.reason)
        self.assertEqual(request.rule, "git_rewrite")
        self.assertIn("reason", request.as_event())

    def test_read_only_blocks_write_and_exec_before_approval(self):
        policy = ApprovalPolicy(
            {},
            mode="read_only",
            risks={"read_file": "read", "write_file": "write", "run_command": "exec"},
        )
        ask = Recorder("once")
        executed: list[str] = []

        def handler(request):
            executed.append(request.tool_call["name"])
            return "ok"

        middleware = ApprovalMiddleware(ApprovalGate(policy=policy, ask=ask))

        denied = middleware.wrap_tool_call(
            make_call("write_file", {"path": "a.txt", "content": "x"}), handler
        )
        self.assertIsInstance(denied, ToolMessage)
        self.assertIn("[read_only]", denied.content)
        denied = middleware.wrap_tool_call(
            make_call("run_command", {"command": "echo x"}), handler
        )
        self.assertIn("[read_only]", denied.content)
        self.assertEqual(
            middleware.wrap_tool_call(make_call("read_file", {"path": "a.txt"}), handler),
            "ok",
        )
        self.assertEqual(ask.requests, [])
        self.assertEqual(executed, ["read_file"])

    def test_asker_exception_fails_closed(self):
        def boom(_request):
            raise RuntimeError("提问通道断了")

        middleware, handler = self.build(boom)
        result = middleware.wrap_tool_call(make_call("run_command", {"command": "echo x"}), handler)
        self.assertIsInstance(result, ToolMessage)
        self.assertIn("approval_denied", result.content)
        self.assertEqual(self.executed, [])

    def test_request_payload_for_ui(self):
        ask = Recorder("deny")
        middleware, handler = self.build(ask)
        middleware.wrap_tool_call(make_call("run_command", {"command": "git status"}), handler)

        request = ask.requests[0]
        event = request.as_event()
        self.assertEqual(event["tool"], "run_command")
        self.assertEqual(event["risk"], "exec")
        self.assertEqual(event["summary"], "查看 Git 状态")
        self.assertNotIn("steps", event)  # 只有一步时用说明就够了
        self.assertNotIn("args", event)  # 参数可能很大，不往事件里塞

    def test_event_carries_steps_for_multi_step_command(self):
        ask = Recorder("deny")
        middleware, handler = self.build(ask)
        middleware.wrap_tool_call(
            make_call("run_command", {"command": "Get-ChildItem *.html; git status"}),
            handler,
        )
        event = ask.requests[0].as_event()
        self.assertEqual(event["steps"], ["查找并列出 *.html 文件", "查看 Git 状态"])


class ConsoleAskerTest(unittest.TestCase):
    def test_choices(self):
        request = ApprovalRequest("1", "run_command", "exec", "echo x", "执行命令：echo x", "echo x", {})
        with patch("builtins.input", return_value="y"):
            self.assertEqual(console_asker(request), "once")
        with patch("builtins.input", return_value="A"):
            self.assertEqual(console_asker(request), "always")
        with patch("builtins.input", return_value="n"):
            self.assertEqual(console_asker(request), "deny")
        with patch("builtins.input", return_value=""):
            self.assertEqual(console_asker(request), "deny")

    def test_unknown_answer_denies(self):
        request = ApprovalRequest("1", "run_command", "exec", "echo x", "执行命令：echo x", "", {})
        with patch("builtins.input", return_value="whatever"):
            self.assertEqual(console_asker(request), "deny")

    def test_eof_denies(self):
        request = ApprovalRequest("1", "run_command", "exec", "echo x", "执行命令：echo x", "", {})
        with patch("builtins.input", side_effect=EOFError):
            self.assertEqual(console_asker(request), "deny")


if __name__ == "__main__":
    unittest.main()
