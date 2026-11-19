"""Token-budget compaction and the preserved first exchange."""

from __future__ import annotations

import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from agents.compaction import checkpoint_message, is_checkpoint_text
from agents.context import ChatSession, SessionStore, context_budget, estimate_tokens, message_text

BULK = "这是一段用来撑大上下文的说明文字，包含细节与结论。" * 30


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"budget_{uuid4().hex[:12]}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class StubSummarizer:
    def __init__(self, reply: str = "这是对话摘要") -> None:
        self.reply = reply
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        return AIMessage(content=self.reply)


class EchoAgent:
    """Echoes the history and appends a long answer so a span can shrink."""

    def invoke(self, payload):
        messages = list(payload["messages"])
        return {"messages": [*messages, AIMessage(content=BULK)]}


class ContextBudgetTest(unittest.TestCase):
    def test_small_window_stays_positive(self):
        trigger, retain = context_budget(4096, 8192)
        self.assertGreater(trigger, 0)
        self.assertLess(trigger, 4096)
        self.assertGreater(retain, 0)
        self.assertLess(retain, trigger)

    def test_large_window_does_not_compact_early(self):
        trigger_200k, _ = context_budget(200_000, 8192)
        trigger_32k, _ = context_budget(32_768, 8192)
        self.assertGreater(trigger_200k / 200_000, 0.70)
        self.assertGreater(trigger_32k / 32_768, 0.55)
        self.assertLess(trigger_200k, 200_000)

    def test_retain_is_share_of_usable_window(self):
        _trigger, retain = context_budget(200_000, 8192)
        self.assertAlmostEqual(retain / (200_000 - 8192), 0.16, delta=0.01)

    def test_tiny_window_never_negative(self):
        for window in (256, 512, 1024):
            trigger, retain = context_budget(window, 8192)
            self.assertGreater(trigger, 0, window)
            self.assertGreater(retain, 0, window)


class TokenBudgetCompactionTest(unittest.TestCase):
    def test_tail_is_bounded_by_tokens_not_message_count(self):
        with workspace_tempdir() as tmp:
            summarizer = StubSummarizer()
            session = ChatSession(
                EchoAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                hydrate=False,
            )
            session.compact_retain_tokens = 400
            for index in range(4):
                session.ask(f"问题 {index} {BULK}")
            self.assertTrue(session.compact(force=True))
            tail_tokens = estimate_tokens(session.messages[1:])
            self.assertLess(tail_tokens, 1200)
            self.assertTrue(is_checkpoint_text(message_text(session.messages[0])))

    def test_keep_first_turn_stays_byte_identical(self):
        with workspace_tempdir() as tmp:
            summarizer = StubSummarizer()
            session = ChatSession(
                EchoAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                keep_first_turn=True,
                hydrate=False,
            )
            session.compact_retain_tokens = 200
            for index in range(4):
                session.ask(f"问题 {index} {BULK}")
            first_turn = list(session.messages[:2])
            self.assertTrue(session.compact(force=True))
            self.assertEqual(session.messages[:2], first_turn)
            self.assertTrue(is_checkpoint_text(message_text(session.messages[2])))
            self.assertGreater(len(session.messages), 3)

    def test_keep_first_turn_off_compacts_the_head(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                EchoAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=StubSummarizer(),
                hydrate=False,
            )
            session.compact_retain_tokens = 200
            for index in range(4):
                session.ask(f"问题 {index} {BULK}")
            self.assertTrue(session.compact(force=True))
            self.assertTrue(is_checkpoint_text(message_text(session.messages[0])))

    def test_preserved_head_survives_a_second_compaction(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                EchoAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=StubSummarizer(),
                keep_first_turn=True,
                hydrate=False,
            )
            session.compact_retain_tokens = 150
            for index in range(3):
                session.ask(f"问题 {index} {BULK}")
            self.assertTrue(session.compact(force=True))
            first_turn = list(session.messages[:2])
            for index in range(3, 5):
                session.ask(f"问题 {index} {BULK}")
            self.assertTrue(session.compact(force=True))
            self.assertEqual(session.messages[:2], first_turn)

    def test_manual_compaction_works_below_the_budget(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                EchoAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=StubSummarizer(),
                hydrate=False,
            )
            for index in range(8):
                session.ask(f"短问题 {index}")
            self.assertTrue(session.compact(force=True))

    def test_no_message_count_trigger_by_default(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                EchoAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=StubSummarizer(),
                hydrate=False,
            )
            session.messages = [HumanMessage(content="q")] * 200
            session.messages.append(AIMessage(content="a"))
            # Far below the token budget: 201 short messages do not trigger.
            self.assertFalse(session.needs_compaction())


if __name__ == "__main__":
    unittest.main()
