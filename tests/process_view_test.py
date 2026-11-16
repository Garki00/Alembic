# tests/process_view_test.py
"""后台任务视图、完成事件、附件与取消接口的测试。"""

import json
import shutil
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from agents.context import SessionMeta
from tools.processes import ProcessManager
from tools.sandbox.base import SandboxPolicy
from web.process_events import ProcessEventHub
from web.server import create_app

from tests.sandbox_fake import FakeSandbox


def make_tempdir(prefix: str = "proc_") -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}{uuid4().hex[:12]}"
    path.mkdir()
    return path


class StubStreamAgent:
    def stream(self, payload, stream_mode=None):
        messages = list(payload["messages"])
        yield ("messages", (AIMessageChunk(content="好"), None))
        yield ("values", {"messages": [*messages, AIMessage(content="好")]})


def stub_factory(model_cfg, enabled_tools, workspace, **kwargs):
    return StubStreamAgent(), None


class ProcessManagerEventTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.events = []
        self.ready = threading.Event()
        self.manager = ProcessManager(
            self.tmp / "logs",
            on_event=self._on_event,
        )
        self.sandbox = FakeSandbox()
        self.policy = SandboxPolicy(workspace=self.tmp)
        self.addCleanup(self.manager.cleanup)

    def _on_event(self, kind, payload):
        self.events.append((kind, payload))
        if kind == "process_completed":
            self.ready.set()

    def test_completion_event_has_status_and_duration(self):
        command = f'"{sys.executable}" -c "print(\'done\')"'
        record = self.manager.start(
            command,
            self.tmp,
            self.tmp,
            session_id="session-1",
            description="test",
            notify=True,
            sandbox=self.sandbox,
            policy=self.policy,
        )
        self.assertTrue(self.ready.wait(8), self.events)
        completed = [item for kind, item in self.events if kind == "process_completed"][0]
        self.assertEqual(completed["id"], record.id)
        self.assertFalse(completed["running"])
        self.assertEqual(completed["exit_code"], 0)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["session_id"], "session-1")
        self.assertGreater(completed["duration_seconds"], 0)
        self.assertTrue(completed["notify"])
        self.assertIn("done", completed.get("log_tail", ""))

    def test_user_stop_disables_auto_continue(self):
        command = f'"{sys.executable}" -c "import time; time.sleep(10)"'
        record = self.manager.start(
            command, self.tmp, self.tmp, session_id="session-2",
            sandbox=self.sandbox, policy=self.policy,
        )
        time.sleep(0.5)
        stopped = self.manager.stop(record.id)
        self.assertIsNotNone(stopped)
        self.assertFalse(stopped.notify_on_exit)
        self.assertEqual(stopped.status, "stopped")


class ProcessEventHubTest(unittest.TestCase):
    def test_publish_reaches_subscriber(self):
        hub = ProcessEventHub()
        subscriber = hub.subscribe()
        try:
            hub.publish("process_started", {"id": "p1"})
            kind, payload = subscriber.get(timeout=1)
            self.assertEqual(kind, "process_started")
            self.assertEqual(payload["id"], "p1")
        finally:
            hub.unsubscribe(subscriber)


class ProcessViewApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        env = patch.dict("os.environ", {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""})
        env.start()
        self.addCleanup(env.stop)
        self.app = create_app(
            data_dir=self.tmp / "data",
            memory_dir=self.tmp / "memory",
            runtime_factory=stub_factory,
        )
        self.client = TestClient(self.app)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        info = self.app.state.workspaces.add(str(self.workspace), "ws")
        self.workspace_id = info.id
        self.sandbox = FakeSandbox()
        self.policy = SandboxPolicy(workspace=self.workspace)

    def test_process_list_detail_and_stop(self):
        record = self.app.state.processes.start(
            f'"{sys.executable}" -c "import time; time.sleep(10)"',
            self.workspace,
            self.workspace,
            session_id="s1",
            sandbox=self.sandbox,
            policy=self.policy,
        )
        listing = self.client.get("/api/processes").json()
        self.assertEqual(listing["running"], 1)
        self.assertEqual(listing["processes"][0]["id"], record.id)
        detail = self.client.get(f"/api/processes/{record.id}").json()
        self.assertIn("process", detail)
        self.assertEqual(detail["process"]["terminal"], "powershell")
        stopped = self.client.post(f"/api/processes/{record.id}/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(stopped.json()["process"]["running"])

    def test_cancel_endpoint_sets_event(self):
        event = threading.Event()
        self.app.state.cancels["s1"] = event
        command = f'"{sys.executable}" -c "import time; time.sleep(10)"'
        mine = self.app.state.processes.start(
            command, self.workspace, self.workspace, session_id="s1",
            sandbox=self.sandbox, policy=self.policy,
        )
        other = self.app.state.processes.start(
            command, self.workspace, self.workspace, session_id="s2",
            sandbox=self.sandbox, policy=self.policy,
        )
        self.addCleanup(self.app.state.processes.stop, other.id)
        response = self.client.post("/api/sessions/s1/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(event.is_set())
        self.assertEqual(response.json()["stopped_processes"], 1)
        self.assertIsNotNone(mine.process.poll())
        self.assertTrue(other.running)
        self.client.post("/api/sessions/s2/cancel?background=0")
        self.assertTrue(other.running)
        self.assertFalse(self.client.post("/api/sessions/missing/cancel").json()["cancelled"])

    def test_attachment_upload_and_chat_history(self):
        self.client.post("/api/providers", json={
            "provider": "openai",
            "name": "test",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test",
            "models": [{"model": "test-model", "name": "Test", "thinking": "high"}],
        })
        uploaded = self.client.post(
            f"/api/sessions/s1/attachments?workspace_id={self.workspace_id}&filename=note.txt",
            content=b"hello attachment",
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(uploaded.status_code, 200)
        body = uploaded.json()
        self.assertEqual(body["name"], "note.txt")
        self.assertTrue((self.workspace / body["path"]).is_file())

        response = self.client.post("/api/chat", json={
            "session_id": "s1",
            "workspace_id": self.workspace_id,
            "message": "read this",
            "attachments": [body["path"]],
        })
        self.assertEqual(response.status_code, 200)
        history = self.client.get("/api/sessions/s1/messages").json()
        user = [item for item in history["messages"] if item["role"] == "user"][0]
        self.assertIn("[Attached files]", user["content"])
        self.assertIn("note.txt", user["content"])
        self.assertGreater(history["max_tokens"], 0)

    def test_internal_message_is_marked_for_history(self):
        self.client.post("/api/providers", json={
            "provider": "openai",
            "name": "test",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test",
            "models": [{"model": "test-model", "name": "Test", "thinking": "high"}],
        })
        response = self.client.post("/api/chat", json={
            "session_id": "s2",
            "workspace_id": self.workspace_id,
            "message": "[Background process update] finished",
            "internal": True,
            "process_id": "proc-test-1",
        })
        self.assertEqual(response.status_code, 200)
        history = self.client.get("/api/sessions/s2/messages").json()
        user = [item for item in history["messages"] if item["role"] == "user"][0]
        self.assertTrue(user.get("internal"))

    def test_compact_endpoint_keeps_visible_history(self):
        self.client.post("/api/providers", json={
            "provider": "openai",
            "name": "test",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test",
            "models": [{"model": "test-model", "name": "Test", "thinking": "high"}],
        })
        messages = []
        for index in range(20):
            messages.append(HumanMessage(content=f"question {index}"))
            messages.append(AIMessage(content=f"answer {index}"))
        self.app.state.sessions.save("s3", messages, SessionMeta(session_id="s3"))

        class SummaryModel:
            def invoke(self, prompt):
                return AIMessage(content="- summarized")

        with patch("web.server.create_chat_model", return_value=SummaryModel()):
            response = self.client.post("/api/sessions/s3/compact")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["compacted"])
        self.assertLess(body["messages"], len(messages))
        self.assertGreater(body["max_tokens"], 0)

        history = self.client.get("/api/sessions/s3/messages").json()
        self.assertEqual(len(history["messages"]), len(messages))
        self.assertIn("question 0", history["messages"][0]["content"])
        self.assertFalse(any("[Conversation summary]" in item["content"] for item in history["messages"]))


if __name__ == "__main__":
    unittest.main()
