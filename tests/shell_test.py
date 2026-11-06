# tests/shell_test.py
"""命令执行工具的测试：退出码、cwd 边界、超时与输出截断。"""

import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.shell import build_shell_tools


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"shell_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.run_command = {
            tool.name: tool
            for tool in build_shell_tools(
                ToolContext(workspace=self.root, max_output_chars=2000, command_timeout=20)
            )
        }["run_command"]

    # ---- 正常路径 ----
    def test_success_reports_exit_code_and_stdout(self):
        text = self.run_command.invoke({"command": "echo hello"})
        self.assertIn("退出码：0", text)
        self.assertIn("hello", text)
        self.assertIn("--- stdout ---", text)

    def test_stderr_is_captured_separately(self):
        text = self.run_command.invoke({"command": "echo boom 1>&2"})
        self.assertIn("--- stderr ---", text)
        self.assertIn("boom", text)

    def test_nonzero_exit_is_reported_but_not_as_tool_error(self):
        text = self.run_command.invoke({"command": "exit 3"})
        self.assertFalse(text.startswith("Error:"), text)
        self.assertIn("退出码：3", text)
        self.assertIn("失败", text)

    def test_default_cwd_is_workspace(self):
        command = f'"{sys.executable}" -c "import os;print(os.getcwd())"'
        text = self.run_command.invoke({"command": command})
        self.assertIn(str(self.root), text.replace("\\\\", "\\"))

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

    # ---- 超时与截断 ----
    def test_timeout_kills_command(self):
        command = f'"{sys.executable}" -c "import time;time.sleep(10)"'
        text = self.run_command.invoke({"command": command, "timeout": 1})
        self.assertTrue(text.startswith("Error: [timeout]"), text)

    def test_output_is_truncated(self):
        command = f'"{sys.executable}" -c "print(\'x\' * 20000)"'
        text = self.run_command.invoke({"command": command})
        self.assertLessEqual(len(text), 2000 + 200)
        self.assertIn("已截断", text)


if __name__ == "__main__":
    unittest.main()
