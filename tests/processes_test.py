# tests/processes_test.py
"""后台进程工具的测试：启动、日志、列表、停止与边界。"""

import shutil
import sys
import time
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.processes import ProcessManager, build_process_tools
from tools.sandbox.base import SandboxPolicy

from tests.sandbox_fake import FakeSandbox


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"proc_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class ProcessToolsTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.manager = ProcessManager(self.root / "logs")
        ctx = ToolContext(
            workspace=self.root,
            max_output_chars=5000,
            process_manager=self.manager,
            sandbox=FakeSandbox(),
            sandbox_policy=SandboxPolicy(workspace=self.root),
        )
        self.tools = {tool.name: tool for tool in build_process_tools(ctx)}
        self.addCleanup(self.manager.cleanup)

    def start_job(self) -> str:
        (self.root / "job.py").write_text(
            "import time\nprint('ready', flush=True)\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        command = f'"{sys.executable}" -u job.py'
        text = self.tools["start_process"].invoke({"command": command})
        self.assertIn("已启动后台进程", text)
        process_id = text.split("：", 1)[1].splitlines()[0].strip()
        return process_id

    def wait_for_ready(self, process_id: str) -> str:
        deadline = time.monotonic() + 10
        text = ""
        while time.monotonic() < deadline:
            text = self.tools["read_process_logs"].invoke({"process_id": process_id, "tail": 50})
            if "ready" in text:
                return text
            time.sleep(0.05)
        self.fail(f"进程没有输出 ready：{text}")

    def test_start_read_list_stop(self):
        process_id = self.start_job()
        log = self.wait_for_ready(process_id)
        self.assertIn("运行中", log)
        self.assertIn("job.py", log)

        listing = self.tools["list_processes"].invoke({})
        self.assertIn(process_id, listing)
        self.assertIn("运行中", listing)

        stopped = self.tools["stop_process"].invoke({"process_id": process_id})
        self.assertIn("已停止进程", stopped)
        self.assertFalse(self.manager.get(process_id).running)

        again = self.tools["stop_process"].invoke({"process_id": process_id})
        self.assertIn("已经退出", again)

    def test_wait_process_for_pattern(self):
        process_id = self.start_job()
        text = self.tools["wait_process"].invoke(
            {"process_id": process_id, "timeout": 10, "pattern": "ready"}
        )
        self.assertIn("命中：ready", text)
        self.tools["stop_process"].invoke({"process_id": process_id})

    def test_wait_process_timeout_reports_running(self):
        process_id = self.start_job()
        self.wait_for_ready(process_id)
        text = self.tools["wait_process"].invoke(
            {"process_id": process_id, "timeout": 1, "pattern": "never-appears"}
        )
        self.assertIn("等待超时", text)
        self.assertIn("运行中", text)
        self.tools["stop_process"].invoke({"process_id": process_id})

    def test_unknown_process(self):
        logs = self.tools["read_process_logs"].invoke({"process_id": "proc-nope"})
        self.assertTrue(logs.startswith("Error: [unknown_process]"), logs)

        stopped = self.tools["stop_process"].invoke({"process_id": "proc-nope"})
        self.assertTrue(stopped.startswith("Error: [unknown_process]"), stopped)

    def test_cwd_escape_is_rejected(self):
        outside = self.root.parent
        text = self.tools["start_process"].invoke({"command": "echo hi", "cwd": str(outside)})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)

    def test_empty_command(self):
        text = self.tools["start_process"].invoke({"command": "  "})
        self.assertTrue(text.startswith("Error: [empty_command]"), text)

    def test_background_command_without_sandbox_fails_closed(self):
        manager = ProcessManager(self.root / "logs-nosandbox")
        ctx = ToolContext(workspace=self.root, process_manager=manager)
        tools = {tool.name: tool for tool in build_process_tools(ctx)}
        text = tools["start_process"].invoke({"command": "echo hello"})
        self.assertTrue(text.startswith("Error: [sandbox_unavailable]"), text)
        manager.cleanup()


if __name__ == "__main__":
    unittest.main()
