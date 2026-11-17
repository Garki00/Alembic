# tests/web_approval_test.py
"""Web approval channel tests: relay, timeout, HTTP endpoints, and full SSE interaction."""

import json
import shutil
import threading
import time
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from agents.approval import ApprovalRequest
from web.approvals import ApprovalBroker, ApprovalRegistry
from web.server import create_app


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"appr_{uuid4().hex[:12]}"
    path.mkdir()
    return path


def make_request(request_id: str = "req-1") -> ApprovalRequest:
    return ApprovalRequest(
        id=request_id,
        tool="run_command",
        risk="exec",
        target="echo hi",
        summary="执行命令：echo hi",
        preview="echo hi",
        args={"command": "echo hi"},
    )


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.registry = ApprovalRegistry()
        self.events: list[tuple[str, dict]] = []

    def broker(self, timeout: float = 5.0) -> ApprovalBroker:
        return ApprovalBroker(
            registry=self.registry,
            emit=lambda kind, data: self.events.append((kind, data)),
            timeout=timeout,
        )

    def test_ask_blocks_until_resolved(self):
        broker = self.broker()
        results: list[str] = []
        thread = threading.Thread(target=lambda: results.append(broker.ask(make_request())))
        thread.start()

        deadline = time.monotonic() + 2
        while not len(self.registry) and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(len(self.registry), 1)
        self.assertEqual(self.events[0][0], "approval")
        self.assertEqual(self.events[0][1]["tool"], "run_command")

        self.assertTrue(self.registry.resolve("req-1", "always"))
        thread.join(2)
        self.assertEqual(results, ["always"])

    def test_timeout_denies_and_emits_expiry(self):
        broker = self.broker(timeout=0.05)
        self.assertEqual(broker.ask(make_request()), "deny")
        self.assertEqual([kind for kind, _ in self.events], ["approval", "approval_expired"])
        self.assertEqual(len(self.registry), 0)

    def test_close_unblocks_pending(self):
        broker = self.broker(timeout=5)
        results: list[str] = []
        thread = threading.Thread(target=lambda: results.append(broker.ask(make_request())))
        thread.start()
        deadline = time.monotonic() + 2
        while not len(self.registry) and time.monotonic() < deadline:
            time.sleep(0.01)

        broker.close()
        thread.join(2)
        self.assertEqual(results, ["deny"])

        # 关闭后的新请求直接拒绝，不再阻塞
        self.assertEqual(broker.ask(make_request("req-2")), "deny")

    def test_resolve_rejects_unknown_and_invalid(self):
        self.assertFalse(self.registry.resolve("missing", "once"))
        self.assertFalse(self.registry.resolve("missing", "bogus"))


class SSEApprovalFlowTest(unittest.TestCase):
    """Gets an approval event from a real SSE stream; after the response the tool thread continues."""

    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.seen: list[str] = []

        def factory(model_cfg, enabled_tools, workspace, **kwargs):
            return ApprovalStubAgent(kwargs.get("approval"), self.seen), None

        self.app = create_app(
            data_dir=self.tmp / "data",
            memory_dir=self.tmp / "memory",
            runtime_factory=factory,
        )
        self.client = TestClient(self.app)
        self.client.post("/api/providers", json={
            "provider": "openai",
            "name": "测试供应商",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test",
            "models": [{"model": "gpt-test", "thinking": "none"}],
        })
        self.client.post("/api/workspaces", json={"path": str(self.tmp)})

    def test_approval_event_then_resolve(self):
        kinds: list[str] = []
        approval: dict = {}
        done: dict = {}
        with self.client.stream(
            "POST", "/api/chat", json={"session_id": "appr", "message": "跑个命令"}
        ) as response:
            self.assertEqual(response.status_code, 200)
            for event, data in _iter_events(response):
                kinds.append(event)
                if event == "approval":
                    approval = data
                    self.assertEqual(data["tool"], "run_command")
                    self.assertEqual(data["risk"], "exec")
                    self.assertEqual(data["summary"], "执行命令：echo hi")
                    # 拿到事件后从测试线程回执，模拟浏览器点按钮
                    decided = self.client.post(
                        f"/api/approvals/{data['id']}", json={"decision": "always"}
                    )
                    self.assertEqual(decided.status_code, 200)
                elif event == "done":
                    done = data

        self.assertIn("approval", kinds)
        self.assertIn("done", kinds)
        self.assertTrue(approval.get("id"))
        # 决策传回了工具线程：模型看到的是 always，而不是拒绝
        self.assertIn("决策：always", done.get("text", ""))

    def test_unknown_request_returns_404(self):
        response = self.client.post("/api/approvals/no-such-id", json={"decision": "once"})
        self.assertEqual(response.status_code, 404)

    def test_invalid_decision_returns_400(self):
        response = self.client.post("/api/approvals/any", json={"decision": "maybe"})
        self.assertEqual(response.status_code, 400)

    def test_memo_list_and_clear(self):
        self.app.state.approval_memo["s1"] = {
            "write_file:a.txt": {"key": "write_file:a.txt", "tool": "write_file",
                                 "risk": "write", "target": "a.txt", "summary": "写入文件：a.txt"},
        }
        listed = self.client.get("/api/approvals", params={"session_id": "s1"}).json()
        self.assertEqual(len(listed["items"]), 1)
        self.assertEqual(listed["items"][0]["target"], "a.txt")

        cleared = self.client.delete("/api/approvals", params={"session_id": "s1"}).json()
        self.assertEqual(cleared["cleared"], 1)
        self.assertEqual(self.client.get("/api/approvals", params={"session_id": "s1"}).json()["items"], [])


class ApprovalStubAgent:
    """Triggers one approval during streaming to verify block, response, then resume."""

    def __init__(self, gate, seen: list[str]) -> None:
        self.gate = gate
        self.seen = seen

    def stream(self, payload, stream_mode=None):
        messages = list(payload["messages"])
        request = make_request(f"req-{uuid4().hex[:8]}")
        self.seen.append(request.id)
        decision = self.gate.ask(request) if self.gate else "no-gate"
        yield ("messages", (AIMessageChunk(content="已确认"), None))
        yield ("values", {"messages": [*messages, AIMessage(content=f"决策：{decision}")]})


def _iter_events(response):
    buffer = ""
    for chunk in response.iter_text():
        buffer += chunk
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            event, data = "message", ""
            for line in raw.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data += line[5:].strip()
            if data:
                yield event, json.loads(data)


if __name__ == "__main__":
    unittest.main()
