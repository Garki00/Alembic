# tests/questions_test.py
"""提问工具与网页中继的测试。"""

import threading
import time
import unittest

from tools.context import SessionRef, ToolContext
from tools.questions import Question, QuestionCancelled, build_question_tools
from web.questions import QuestionBroker, QuestionRegistry


def make_question(question_id: str = "q-1") -> Question:
    return Question(
        id=question_id,
        question="选哪个？",
        options=("A", "B"),
        allow_free_text=True,
        session_id="s1",
    )


class QuestionToolTest(unittest.TestCase):
    def tool(self, asker):
        ctx = ToolContext(asker=asker, session=SessionRef("s1"))
        return next(t for t in build_question_tools(ctx) if t.name == "ask_user_question")

    def test_returns_answer(self):
        seen: list[Question] = []

        def asker(question: Question) -> str:
            seen.append(question)
            return "A"

        text = self.tool(asker).invoke({"question": "选哪个？", "options": ["A", "B"]})
        self.assertEqual(text, "用户回答：A")
        self.assertEqual(seen[0].options, ("A", "B"))
        self.assertEqual(seen[0].session_id, "s1")

    def test_no_channel(self):
        text = self.tool(None).invoke({"question": "选哪个？"})
        self.assertTrue(text.startswith("Error: [no_ask_channel]"), text)

    def test_cancelled(self):
        def asker(_question):
            raise QuestionCancelled("stop")

        text = self.tool(asker).invoke({"question": "选哪个？"})
        self.assertTrue(text.startswith("Error: [question_cancelled]"), text)

    def test_timeout_is_none(self):
        text = self.tool(lambda _question: None).invoke({"question": "选哪个？"})
        self.assertTrue(text.startswith("Error: [question_timeout]"), text)

    def test_empty_question(self):
        text = self.tool(lambda _question: "x").invoke({"question": "  "})
        self.assertTrue(text.startswith("Error: [empty_question]"), text)

    def test_options_only_requires_options(self):
        text = self.tool(lambda _question: "x").invoke({
            "question": "选哪个？", "options": [], "allow_free_text": False,
        })
        self.assertTrue(text.startswith("Error: [no_options]"), text)

    def test_too_many_options_is_rejected_instead_of_truncated(self):
        seen: list[Question] = []
        text = self.tool(lambda q: (seen.append(q), "A")[1]).invoke({
            "question": "选哪个？",
            "options": [f"选项{i}" for i in range(7)],
        })
        self.assertTrue(text.startswith("Error: [too_many_options]"), text)
        self.assertEqual(seen, [])


class QuestionBrokerTest(unittest.TestCase):
    def setUp(self):
        self.registry = QuestionRegistry()
        self.events: list[tuple[str, dict]] = []

    def broker(self, timeout: float = 5.0) -> QuestionBroker:
        return QuestionBroker(
            registry=self.registry,
            emit=lambda kind, data: self.events.append((kind, data)),
            timeout=timeout,
        )

    def about_to_ask(self, broker: QuestionBroker) -> tuple[threading.Thread, list[str | None]]:
        answers: list[str | None] = []
        thread = threading.Thread(target=lambda: answers.append(broker.ask(make_question())))
        thread.start()
        deadline = time.monotonic() + 2
        while not len(self.registry) and time.monotonic() < deadline:
            time.sleep(0.01)
        return thread, answers

    def test_blocks_until_resolved(self):
        broker = self.broker()
        thread, answers = self.about_to_ask(broker)
        self.assertEqual(self.events[0][0], "question")
        self.assertEqual(self.events[0][1]["options"], ["A", "B"])

        self.assertTrue(self.registry.resolve("q-1", "B"))
        thread.join(2)
        self.assertEqual(answers, ["B"])

    def test_timeout_returns_none_and_emits_expiry(self):
        broker = self.broker(timeout=0.05)
        self.assertIsNone(broker.ask(make_question()))
        self.assertEqual([kind for kind, _ in self.events], ["question", "question_expired"])

    def test_close_cancels_pending(self):
        broker = self.broker()
        thread, answers = self.about_to_ask(broker)
        broker.close()
        thread.join(2)
        self.assertEqual(answers, [None])
        self.assertIsNone(broker.ask(make_question("q-2")))

    def test_resolve_unknown_returns_false(self):
        self.assertFalse(self.registry.resolve("missing", "x"))


if __name__ == "__main__":
    unittest.main()
