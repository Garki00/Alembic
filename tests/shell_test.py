# tests/shell_test.py
"""Command execution tool tests: terminal, exit codes, cwd boundaries, timeouts, environment allowlist, and truncation."""

import os
import shutil
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tools.context import ToolContext
from tools.sandbox.base import SandboxPolicy
from tools.shell import build_shell_tools
from tools.terminals import available_terminals

from tests.sandbox_fake import FakeSandbox


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"shell_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class ShellTest(unittest.TestCase):
    def make_context(self, **kwargs):
        return ToolContext(
            workspace=self.root,
            max_output_chars=2000,
            command_timeout=20,
            sandbox=FakeSandbox(),
            sandbox_policy=SandboxPolicy(workspace=self.root),
            **kwargs,
        )

    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.run_command = {
            tool.name: tool
            for tool in build_shell_tools(self.make_context())
        }["run_command"]
        self.available = {item.value: item.available for item in available_terminals()}

        def script(name: str, body: str) -> str:
            path = self.root / name
            path.write_text(body, encoding="utf-8")
            return f'"{sys.executable}" "{path}"'

        self.cwd_command = script(
            "cwd.py", "import os\nprint(os.getcwd())\n"
        )
        self.stderr_command = script(
            "stderr.py", "import sys\nprint('boom', file=sys.stderr)\nraise SystemExit(2)\n"
        )
        self.fail_command = script("fail.py", "raise SystemExit(3)\n")
        self.sleep_command = script(
            "sleep.py", "import time\ntime.sleep(10)\n"
        )
        self.big_command = script(
            "big.py", "print('x' * 20000)\n"
        )
        self.env_command = script(
            "env.py", "import os\nprint(os.getenv('DEEPSEEK_API_KEY', '<missing>'))\n"
        )

    # ---- 正常路径 ----
    def test_success_reports_exit_code_and_stdout(self):
        text = self.run_command.invoke({"command": "echo hello"})
        self.assertIn("Exit code: 0", text)
        self.assertIn("hello", text)
        self.assertIn("--- stdout ---", text)
        self.assertIn("Terminal: PowerShell", text)

    def test_stderr_is_captured_separately(self):
        text = self.run_command.invoke({"command": self.stderr_command})
        self.assertIn("--- stderr ---", text)
        self.assertIn("boom", text)

    def test_nonzero_exit_is_reported_but_not_as_tool_error(self):
        text = self.run_command.invoke({"command": self.fail_command})
        self.assertFalse(text.startswith("Error:"), text)
        self.assertIn("Exit code: 3", text)
        self.assertIn("failed", text)

    def test_default_cwd_is_workspace(self):
        text = self.run_command.invoke({"command": self.cwd_command})
        self.assertIn(str(self.root), text)

    # ---- 终端 ----
    def test_cmd_terminal(self):
        if not self.available.get("cmd"):
            self.skipTest("CMD 不可用")
        text = self.run_command.invoke({"command": "echo hello", "terminal": "cmd"})
        self.assertIn("Terminal: CMD", text)
        self.assertIn("hello", text)

    def test_gitbash_terminal(self):
        if not self.available.get("gitbash"):
            self.skipTest("Git Bash 不可用")
        text = self.run_command.invoke({"command": "echo hello", "terminal": "gitbash"})
        # 某些受限环境会禁止 Git Bash 创建信号管道（Win32 error 5），
        # 这是宿主限制而不是终端适配失败。
        if "signal pipe" in text or "Win32 error 5" in text:
            self.skipTest("宿主环境禁止 Git Bash 创建信号管道")
        self.assertIn("Terminal: Git Bash", text)
        self.assertIn("hello", text)

    def test_wsl_terminal(self):
        if not self.available.get("wsl"):
            self.skipTest("WSL 不可用")
        text = self.run_command.invoke({"command": "echo hello", "terminal": "wsl"})
        # 可能已安装 wsl.exe 但没有任何发行版；只要走 WSL 分支并给出明确结果即可。
        # The Windows sandbox deliberately rejects WSL, so accept that explicit error too.
        if "sandbox_unsupported" in text:
            self.assertIn("WSL terminals are not supported", text)
        else:
            self.assertIn("Terminal: WSL", text)

    def test_unknown_terminal_rejected(self):
        text = self.run_command.invoke({"command": "echo x", "terminal": "fish"})
        self.assertTrue(text.startswith("Error: [unknown_terminal]"), text)

    # ---- 边界 ----
    def test_empty_command_rejected(self):
        text = self.run_command.invoke({"command": "   "})
        self.assertTrue(text.startswith("Error: [empty_command]"), text)

    def test_cwd_escape_rejected(self):
        text = self.run_command.invoke({"command": "echo x", "cwd": "../.."})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)

    def test_missing_cwd_rejected(self):
        text = self.run_command.invoke({"command": "echo x", "cwd": "no-such-dir"})
        self.assertTrue(text.startswith("Error: [not_a_directory]"), text)

    def test_no_workspace(self):
        tools = {
            tool.name: tool
            for tool in build_shell_tools(ToolContext(workspace=None))
        }
        text = tools["run_command"].invoke({"command": "echo x"})
        self.assertTrue(text.startswith("Error: [no_workspace]"), text)

    # ---- 环境变量 ----
    def test_secret_environment_is_not_inherited(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-super-secret"}, clear=False):
            text = self.run_command.invoke({"command": self.env_command})
        self.assertIn("<missing>", text)
        self.assertNotIn("sk-super-secret", text)

    # ---- 超时与截断 ----
    def test_timeout_kills_command(self):
        text = self.run_command.invoke({"command": self.sleep_command, "timeout": 1})
        self.assertTrue(text.startswith("Error: [timeout]"), text)

    def test_output_is_truncated(self):
        text = self.run_command.invoke({"command": self.big_command})
        self.assertLessEqual(len(text), 2000 + 200)
        self.assertIn("truncated", text)

    # ---- 实时输出与取消 ----
    def test_output_events_are_emitted_while_running(self):
        events = []
        tool = {
            item.name: item
            for item in build_shell_tools(
                self.make_context(emit=lambda kind, data: events.append((kind, data)))
            )
        }["run_command"]
        text = tool.invoke({"command": self.cwd_command})
        self.assertIn("Exit code: 0", text)
        self.assertTrue(
            any(kind == "tool_output" and data.get("text") for kind, data in events),
            events,
        )

    def test_cancel_event_terminates_running_command(self):
        cancel = threading.Event()
        tool = {
            item.name: item
            for item in build_shell_tools(
                self.make_context(cancel_event=cancel)
            )
        }["run_command"]
        timer = threading.Timer(0.4, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            text = tool.invoke({"command": self.sleep_command})
        finally:
            timer.cancel()
        self.assertTrue(text.startswith("Error: [cancelled]"), text)
        self.assertLess(time.monotonic() - started, 8)

    def test_command_without_sandbox_fails_closed(self):
        tool = {
            item.name: item
            for item in build_shell_tools(ToolContext(workspace=self.root))
        }["run_command"]
        text = tool.invoke({"command": "echo hello"})
        self.assertTrue(text.startswith("Error: [sandbox_unavailable]"), text)


if __name__ == "__main__":
    unittest.main()
