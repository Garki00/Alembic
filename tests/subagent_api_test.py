"""HTTP surface for subagent listing, detail, and stop."""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from web.server import create_app


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"subagent_api_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class SubagentApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_tempdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.app = create_app(
            data_dir=self.tmp / "data",
            memory_dir=self.tmp / "memory",
            runtime_factory=lambda *args, **kwargs: (None, None),
        )
        self.client = TestClient(self.app)

    def test_list_requires_session(self) -> None:
        response = self.client.get("/api/subagents")
        self.assertEqual(response.status_code, 400)

    def test_list_is_empty_for_new_session(self) -> None:
        response = self.client.get("/api/subagents", params={"session_id": "chat-x"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["subagents"], [])

    def test_unknown_agent_is_404_and_stop_is_409(self) -> None:
        detail = self.client.get("/api/subagents/subagent-missing")
        self.assertEqual(detail.status_code, 404)
        stop = self.client.post("/api/subagents/subagent-missing/stop")
        self.assertEqual(stop.status_code, 409)

    def test_persisted_task_and_transcript_are_readable(self) -> None:
        import json

        from langchain_core.messages import AIMessage, HumanMessage

        from agents.context import SessionMeta, SessionStore

        manager = self.app.state.subagents
        session_id = "chat-persist"
        agent_id = "subagent-abc12345"
        tasks_dir = manager.store_dir / session_id / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / f"{agent_id}.json").write_text(
            json.dumps(
                {
                    "id": agent_id,
                    "parent_session_id": session_id,
                    "agent_type": "general-purpose",
                    "description": "saved task",
                    "prompt": "do the work",
                    "child_session_id": agent_id,
                    "status": "completed",
                    "background": False,
                    "started_at": 1.0,
                    "finished_at": 2.0,
                    "report": "saved report",
                    "error": "",
                    "usage": {},
                    "tool_uses": 1,
                    "duration_ms": 1000,
                    "output_file": "",
                    "model": "",
                    "allowed_tools": ["read_file"],
                    "notified": False,
                }
            ),
            encoding="utf-8",
        )
        store = SessionStore(manager.store_dir / session_id)
        store.save(
            agent_id,
            [
                HumanMessage(content="do the work"),
                AIMessage(content="saved report"),
            ],
            SessionMeta(session_id=agent_id),
        )
        self.client = TestClient(create_app(
            data_dir=self.tmp / "data",
            memory_dir=self.tmp / "memory",
            runtime_factory=lambda *args, **kwargs: (None, None),
        ))
        listed = self.client.get("/api/subagents", params={"session_id": session_id})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["id"] for item in listed.json()["subagents"]], [agent_id])
        detail = self.client.get(f"/api/subagents/{agent_id}")
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()["subagent"]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][1]["content"], "saved report")


if __name__ == "__main__":
    unittest.main()
