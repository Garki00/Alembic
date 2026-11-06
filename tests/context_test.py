# tests/context_test.py
"""会话上下文、持久化与压缩的单元测试。

运行方式（在项目根目录）:
    .venv\\Scripts\\python.exe -m unittest discover -s tests -p "*_test.py" -v
"""

import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.context import (
    MIN_TURNS_BETWEEN_COMPACTIONS,
    SUMMARY_PREFIX,
    ChatSession,
    SessionMeta,
    SessionStore,
    cap_tool_outputs,
    compact_messages,
    context_tokens,
    estimate_tokens,
    session_slug,
)


@contextmanager
def workspace_tempdir():
    """在项目内建临时目录。

    沙箱/只读环境会限制系统临时目录，这里统一放到项目根的 .tmp/tests 下，
    并且不用 tempfile.mkdtemp（Windows 上遇到权限错误会反复重试）。
    """
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"ctx_{uuid4().hex[:12]}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class StubSummarizer:
    """假的摘要模型，记录调用次数。"""

    def __init__(self, reply: str = "这是对话摘要") -> None:
        self.reply = reply
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        return AIMessage(content=self.reply)


class BoomSummarizer:
    def invoke(self, prompt):
        raise RuntimeError("摘要服务不可用")


class StubAgent:
    """假的 Agent：把收到的历史原样回显，并追加一条 AI 回复。"""

    def __init__(self) -> None:
        self.payloads: list[list] = []

    def invoke(self, payload):
        messages = list(payload["messages"])
        self.payloads.append(messages)
        return {"messages": [*messages, AIMessage(content=f"第 {len(self.payloads)} 轮回复")]}


class FailingAgent:
    def invoke(self, payload):
        raise RuntimeError("模型服务不可用")


def _tool_call_ai(call_id: str = "call-1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "get_weather", "args": {"city": "北京"}, "id": call_id}],
    )


class SessionSlugTest(unittest.TestCase):
    def test_normal_id_is_kept(self):
        self.assertEqual(session_slug("default"), "default")
        self.assertEqual(session_slug("work-2026"), "work-2026")

    def test_path_traversal_is_blocked(self):
        for raw in ("../evil", "a/b", "..\\..\\x", "  "):
            with self.subTest(raw=raw):
                slug = session_slug(raw)
                self.assertNotIn("/", slug)
                self.assertNotIn("\\", slug)
                self.assertNotIn("..", slug)
                self.assertTrue(slug)


class EstimateTokensTest(unittest.TestCase):
    def test_counts_grow_with_content(self):
        short = estimate_tokens([HumanMessage(content="你好")])
        long = estimate_tokens([HumanMessage(content="你" * 500)])
        self.assertGreater(short, 0)
        self.assertGreater(long, short)

    def test_empty_history_is_zero(self):
        self.assertEqual(estimate_tokens([]), 0)

    def test_cjk_counts_more_than_ascii_of_same_length(self):
        chinese = estimate_tokens([HumanMessage(content="汉" * 100)])
        ascii_text = estimate_tokens([HumanMessage(content="a" * 100)])
        self.assertGreater(chinese, ascii_text)


class ContextTokensTest(unittest.TestCase):
    """上下文 token 统计：优先 provider 用量，缺失时本地估算。"""

    def test_provider_usage_is_authoritative(self):
        ai = AIMessage(
            content="答",
            usage_metadata={"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
        )
        tokens, source = context_tokens([HumanMessage(content="问题"), ai])
        self.assertEqual(source, "provider")
        self.assertEqual(tokens, 1050)

    def test_provider_usage_plus_newer_messages(self):
        ai = AIMessage(
            content="答",
            usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        )
        tokens, source = context_tokens(
            [HumanMessage(content="问题"), ai, HumanMessage(content="x" * 400)]
        )
        self.assertEqual(source, "provider")
        self.assertGreater(tokens, 110)

    def test_fallback_without_usage(self):
        tokens, source = context_tokens([HumanMessage(content="你好")])
        self.assertEqual(source, "estimate")
        self.assertGreater(tokens, 0)

    def test_last_reported_usage_wins(self):
        older = AIMessage(
            content="旧",
            usage_metadata={"input_tokens": 9999, "output_tokens": 1, "total_tokens": 10000},
        )
        newer = AIMessage(
            content="新",
            usage_metadata={"input_tokens": 200, "output_tokens": 20, "total_tokens": 220},
        )
        tokens, source = context_tokens([HumanMessage(content="q"), older, HumanMessage(content="q2"), newer])
        self.assertEqual(source, "provider")
        self.assertEqual(tokens, 220)


class CompressionBoundaryTest(unittest.TestCase):
    """压缩边界：由窗口比例推导，且两次压缩之间留出间隔。"""

    def test_threshold_derived_from_window_ratio_and_reserve(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                StubAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=None,
                model_context_window=1000,
                trigger_ratio=0.5,
                compact_reserve_tokens=100,
            )
        self.assertEqual(session.max_context_tokens, 400)

    def test_absolute_threshold_overrides_window(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                StubAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=None,
                max_context_tokens=1234,
                model_context_window=100_000,
            )
        self.assertEqual(session.max_context_tokens, 1234)

    def test_hysteresis_prevents_immediate_recompaction(self):
        with workspace_tempdir() as tmp:
            summarizer = StubSummarizer()
            session = ChatSession(
                StubAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                max_context_tokens=1,  # 永远超阈值，只看间隔逻辑
                max_context_messages=10**6,
                keep_recent=1,
            )
            results = [session.ask(f"问题{i}") for i in range(1, 6)]

            self.assertEqual([r.compacted for r in results], [False, False, True, False, True])
            self.assertEqual(summarizer.calls, 2)
            gap = results[4].compacted and (4 - 2) >= MIN_TURNS_BETWEEN_COMPACTIONS
            self.assertTrue(gap)

    def test_stats_report_token_source(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(StubAgent(), "s1", store=SessionStore(tmp))
            session.ask("你好")
            self.assertEqual(session.stats()["token_source"], "estimate")

            class UsageAgent:
                def invoke(self, payload):
                    messages = list(payload["messages"])
                    ai = AIMessage(
                        content="答",
                        usage_metadata={"input_tokens": 500, "output_tokens": 40, "total_tokens": 540},
                    )
                    return {"messages": [*messages, ai]}

            session = ChatSession(UsageAgent(), "s2", store=SessionStore(tmp))
            result = session.ask("你好")
            self.assertEqual(result.token_source, "provider")
            self.assertEqual(session.stats()["tokens"], 540)


class ToolOutputCapTest(unittest.TestCase):
    """单条工具输出超出预算时截断后再写入历史。"""

    def test_large_tool_output_is_truncated(self):
        with workspace_tempdir() as tmp:
            class ToolAgent:
                def invoke(self, payload):
                    messages = list(payload["messages"])
                    return {
                        "messages": [
                            *messages,
                            ToolMessage(content="x" * 10_000, tool_call_id="call-1"),
                            AIMessage(content="完成"),
                        ]
                    }

            session = ChatSession(
                ToolAgent(),
                "s1",
                store=SessionStore(tmp),
                max_tool_output_tokens=50,
            )
            session.ask("查一下")
            tool_messages = [m for m in session.messages if isinstance(m, ToolMessage)]
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("已截断", tool_messages[0].content)
            self.assertLess(len(tool_messages[0].content), 1000)

    def test_small_tool_output_untouched(self):
        with workspace_tempdir() as tmp:
            class ToolAgent:
                def invoke(self, payload):
                    messages = list(payload["messages"])
                    return {
                        "messages": [
                            *messages,
                            ToolMessage(content="25 度，晴", tool_call_id="call-1"),
                            AIMessage(content="完成"),
                        ]
                    }

            session = ChatSession(
                ToolAgent(),
                "s1",
                store=SessionStore(tmp),
                max_tool_output_tokens=1000,
            )
            session.ask("查一下")
            tool_messages = [m for m in session.messages if isinstance(m, ToolMessage)]
            self.assertEqual(tool_messages[0].content, "25 度，晴")

    def test_cap_helper_handles_empty_budget(self):
        messages = [ToolMessage(content="x" * 100, tool_call_id="c")]
        self.assertEqual(cap_tool_outputs(messages, 0), messages)


class SessionStoreTest(unittest.TestCase):
    def test_roundtrip_keeps_message_types(self):
        with workspace_tempdir() as tmp:
            store = SessionStore(tmp)
            messages = [
                HumanMessage(content="你好"),
                _tool_call_ai("call-9"),
                ToolMessage(content="25 度", tool_call_id="call-9"),
                AIMessage(content="北京 25 度"),
            ]
            meta = SessionMeta(session_id="demo", turns=2, compactions=1)
            self.assertTrue(store.save("demo", messages, meta))

            loaded, loaded_meta = store.load("demo")
            self.assertEqual([m.type for m in loaded], ["human", "ai", "tool", "ai"])
            self.assertEqual(loaded[2].content, "25 度")
            self.assertEqual(loaded[1].tool_calls[0]["id"], "call-9")
            self.assertEqual(loaded_meta.turns, 2)
            self.assertEqual(loaded_meta.compactions, 1)
            self.assertEqual(loaded_meta.message_count, 4)

    def test_missing_session_returns_empty(self):
        with workspace_tempdir() as tmp:
            messages, meta = SessionStore(tmp).load("nope")
        self.assertEqual(messages, [])
        self.assertEqual(meta.session_id, "nope")
        self.assertEqual(meta.turns, 0)

    def test_list_sessions_sorted_by_update_time(self):
        with workspace_tempdir() as tmp:
            store = SessionStore(tmp)
            store.save("old", [HumanMessage("a")], SessionMeta("old", updated_at="2026-01-01T00:00:00"))
            store.save("new", [HumanMessage("b")], SessionMeta("new", updated_at="2026-02-01T00:00:00"))
            sessions = store.list_sessions()
        self.assertEqual([s.session_id for s in sessions], ["new", "old"])
        self.assertEqual(sessions[0].message_count, 1)

    def test_delete_removes_file(self):
        with workspace_tempdir() as tmp:
            store = SessionStore(tmp)
            store.save("gone", [HumanMessage("a")], SessionMeta("gone"))
            self.assertTrue(store.delete("gone"))
            self.assertFalse(store.path("gone").exists())


class CompactMessagesTest(unittest.TestCase):
    def test_keeps_recent_messages_and_adds_summary(self):
        messages = [
            HumanMessage(content="q1"),
            AIMessage(content="a1"),
            HumanMessage(content="q2"),
            AIMessage(content="a2"),
            HumanMessage(content="q3"),
            AIMessage(content="a3"),
        ]
        summarizer = StubSummarizer()
        new_messages, summary = compact_messages(messages, summarizer, keep_recent=2)

        self.assertEqual(summary, "这是对话摘要")
        self.assertEqual(summarizer.calls, 1)
        self.assertEqual(len(new_messages), 3)
        self.assertTrue(new_messages[0].content.startswith(SUMMARY_PREFIX))
        self.assertEqual(new_messages[-2:], messages[-2:])

    def test_does_not_split_tool_call_pair(self):
        messages = [
            HumanMessage(content="查天气和时间"),
            _tool_call_ai("call-1"),
            ToolMessage(content="晴", tool_call_id="call-1"),
            ToolMessage(content="10 点", tool_call_id="call-2"),
            HumanMessage(content="谢谢"),
            AIMessage(content="不客气"),
        ]
        new_messages, _ = compact_messages(messages, StubSummarizer(), keep_recent=4)
        self.assertEqual(new_messages[1].type, "ai")
        self.assertEqual(new_messages[1].tool_calls[0]["id"], "call-1")
        self.assertEqual(new_messages[2].type, "tool")

    def test_short_history_is_untouched(self):
        messages = [HumanMessage(content="q"), AIMessage(content="a")]
        original = list(messages)
        new_messages, summary = compact_messages(messages, StubSummarizer(), keep_recent=8)
        self.assertEqual(new_messages, original)
        self.assertIsNone(summary)

    def test_summary_failure_falls_back_to_trimming(self):
        messages = [
            HumanMessage(content="q1"),
            AIMessage(content="a1"),
            HumanMessage(content="q2"),
            AIMessage(content="a2"),
        ]
        new_messages, summary = compact_messages(messages, BoomSummarizer(), keep_recent=2)
        self.assertIsNone(summary)
        self.assertIn("压缩失败", new_messages[0].content)
        self.assertEqual(len(new_messages), 3)


class ChatSessionTest(unittest.TestCase):
    def _session(self, tmp, **kwargs) -> ChatSession:
        kwargs.setdefault("store", SessionStore(tmp))
        kwargs.setdefault("summarizer", None)
        return ChatSession(StubAgent(), "s1", **kwargs)

    def test_history_is_passed_between_turns(self):
        with workspace_tempdir() as tmp:
            session = self._session(tmp)
            first = session.ask("你好")
            second = session.ask("再见")
            agent = session.agent

            self.assertEqual(first.text, "第 1 轮回复")
            self.assertEqual(second.text, "第 2 轮回复")
            self.assertEqual(len(agent.payloads[0]), 1)
            self.assertEqual(len(agent.payloads[1]), 3)
            self.assertEqual(agent.payloads[1][0].content, "你好")
            self.assertEqual(session.stats()["turns"], 2)
            self.assertTrue(session.store.path("s1").exists())

    def test_hydrates_existing_session(self):
        with workspace_tempdir() as tmp:
            store = SessionStore(tmp)
            store.save(
                "s1",
                [HumanMessage(content="旧问题"), AIMessage(content="旧回答")],
                SessionMeta("s1", turns=1),
            )
            session = ChatSession(StubAgent(), "s1", store=store)
            self.assertEqual(len(session), 2)
            self.assertEqual(session.stats()["turns"], 1)
            session.ask("新问题")
            self.assertEqual(len(session.agent.payloads[0]), 3)

    def test_reset_clears_memory_and_file(self):
        with workspace_tempdir() as tmp:
            session = self._session(tmp)
            session.ask("你好")
            session.reset()
            self.assertEqual(len(session), 0)
            self.assertFalse(session.store.path("s1").exists())

    def test_auto_compaction_on_threshold(self):
        with workspace_tempdir() as tmp:
            summarizer = StubSummarizer()
            session = ChatSession(
                StubAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                max_context_messages=6,
                max_context_tokens=10**9,
                keep_recent=2,
            )
            results = [session.ask(f"问题 {i}") for i in range(1, 5)]
            self.assertFalse(any(result.compacted for result in results[:3]))
            self.assertTrue(results[-1].compacted)
            self.assertEqual(summarizer.calls, 1)
            self.assertEqual(session.stats()["compactions"], 1)
            # 压缩后的历史以摘要开头，且仍保留最近的消息
            self.assertTrue(session.messages[0].content.startswith(SUMMARY_PREFIX))

    def test_manual_compact(self):
        with workspace_tempdir() as tmp:
            summarizer = StubSummarizer()
            session = ChatSession(
                StubAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                max_context_messages=10**6,
                max_context_tokens=10**9,
                keep_recent=2,
            )
            session.ask("一")
            session.ask("二")
            session.ask("三")
            self.assertTrue(session.compact())
            self.assertEqual(summarizer.calls, 1)
            self.assertEqual(session.stats()["compactions"], 1)

    def test_failed_turn_rolls_back_question(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(FailingAgent(), "s1", store=SessionStore(tmp))
            with self.assertRaises(RuntimeError):
                session.ask("会失败的问题")
            self.assertEqual(len(session), 0)
            self.assertFalse(session.store.path("s1").exists())

    def test_empty_question_rejected(self):
        with workspace_tempdir() as tmp:
            session = self._session(tmp)
            with self.assertRaises(ValueError):
                session.ask("   ")

    def test_auto_compaction_skipped_without_summarizer(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                StubAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=None,
                max_context_messages=1,
                keep_recent=1,
            )
            session.ask("第一个问题")
            result = session.ask("第二个问题")
            self.assertFalse(result.compacted)
            self.assertGreater(len(session), 2)


if __name__ == "__main__":
    unittest.main()
