# tests/web_questions_test.py
"""Tests for the web question channel and the todo list API."""

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from tools.questions import Question
from web.server import create_app


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"qweb_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class QuestionStubAgent:
    def __init__(self, asker) -> None:
        self.asker = asker

    def stream(self, payload, stream_mode=None):
        messages = list(payload["messages"])
        question = Question(
            id=f"q-{uuid4().hex[:8]}",
            question="选哪个？",
            options=("A", "B"),
            allow_free_text=True,
            session_id="q1",
        )
        answer = self.asker(question) if self.asker else None
        yield ("messages", (AIMessageChunk(content="收到"), None))
        yield ("values", {"messages": [*messages, AIMessage(content=f"回答：{answer}")]})


class WebQuestionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        def factory(model_cfg, enabled_tools, workspace, **kwargs):
            return QuestionStubAgent(kwargs.get("asker")), None

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

    def test_question_event_then_answer(self):
        kinds: list[str] = []
        question: dict = {}
        done: dict = {}
        with self.client.stream(
            "POST", "/api/chat", json={"session_id": "q1", "message": "帮我选"}
        ) as response:
            self.assertEqual(response.status_code, 200)
            for event, data in _iter_events(response):
                kinds.append(event)
                if event == "question":
                    question = data
                    self.assertEqual(data["question"], "选哪个？")
                    self.assertEqual(data["options"], ["A", "B"])
                    answered = self.client.post(
                        f"/api/questions/{data['id']}", json={"answer": "B"}
                    )
                    self.assertEqual(answered.status_code, 200)
                elif event == "done":
                    done = data

        self.assertIn("question", kinds)
        self.assertIn("done", kinds)
        self.assertTrue(question.get("id"))
        self.assertIn("回答：B", done.get("text", ""))

    def test_answer_unknown_question_returns_404(self):
        response = self.client.post("/api/questions/no-such-id", json={"answer": "x"})
        self.assertEqual(response.status_code, 404)

    def test_todos_endpoint_and_session_delete_clears(self):
        self.app.state.todos.save("s1", [{"content": "a", "status": "pending"}])
        data = self.client.get("/api/sessions/s1/todos").json()
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["items"][0]["content"], "a")

        self.client.delete("/api/sessions/s1")
        data = self.client.get("/api/sessions/s1/todos").json()
        self.assertEqual(data["items"], [])


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
