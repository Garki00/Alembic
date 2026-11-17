# tests/context_test.py
"""Unit tests for session context, persistence, and compression."""

import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

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
    message_text,
    reasoning_text,
    session_slug,
)


@contextmanager
def workspace_tempdir():
    """Creates a temporary directory inside the project.

    Sandboxed or read-only environments restrict the system temp directory, so
    everything goes under .tmp/tests at the project root; tempfile.mkdtemp is
    avoided because it retries repeatedly on Windows permission errors.
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
    """Fake summarizer model that records how often it is called."""

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
    """Fake Agent: echoes the received history unchanged and appends an AI reply."""

    def __init__(self) -> None:
        self.payloads: list[list] = []

    def invoke(self, payload):
        messages = list(payload["messages"])
        self.payloads.append(messages)
        return {"messages": [*messages, AIMessage(content=f"第 {len(self.payloads)} 轮回复")]}


#: 足够长的正文，让「头部 → 检查点」真的能缩小上下文
BULK = "这是一段用来撑大上下文的说明文字，包含细节与结论。" * 40


class WordyAgent(StubAgent):
    """Echo Agent whose replies are long enough for compaction to shrink a span."""

    def invoke(self, payload):
        messages = list(payload["messages"])
        self.payloads.append(messages)
        return {
            "messages": [
                *messages,
                AIMessage(content=f"第 {len(self.payloads)} 轮回复：{BULK}"),
            ]
        }


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
    """Context token accounting: prefer provider usage, fall back to local estimation when missing."""

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
    """Compression boundaries: derived from the window ratio, with spacing between consecutive compressions."""

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
                WordyAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                max_context_tokens=1,  # 永远超阈值，只看间隔逻辑
                max_context_messages=10**6,
                keep_recent=1,
            )
            results = [session.ask(f"问题{i}") for i in range(1, 6)]

            self.assertEqual([r.compacted for r in results], [False, False, True, False, True])
            # 首轮标题一次 + 两次压缩
            self.assertEqual(summarizer.calls, 3)
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
    """Single tool outputs over budget are truncated before being written to history."""

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
            self.assertIn("truncated", tool_messages[0].content)
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
            HumanMessage(content=f"q1 {BULK}"),
            AIMessage(content=f"a1 {BULK}"),
            HumanMessage(content=f"q2 {BULK}"),
            AIMessage(content=f"a2 {BULK}"),
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
            HumanMessage(content=f"查天气和时间 {BULK}"),
            _tool_call_ai("call-1"),
            ToolMessage(content=f"晴 {BULK}", tool_call_id="call-1"),
            ToolMessage(content=f"10 点 {BULK}", tool_call_id="call-2"),
            HumanMessage(content="谢谢"),
            AIMessage(content="不客气"),
        ]
        new_messages, summary = compact_messages(messages, StubSummarizer(), keep_recent=4)
        self.assertIsNotNone(summary)
        # 保留的尾部从用户消息开始，工具调用与其结果都落在被压缩的头部里
        self.assertEqual(new_messages[1:], messages[4:])
        self.assertTrue(all(message.type != "tool" for message in new_messages[1:]))

    def test_non_shrinking_summary_is_rejected(self):
        messages = [
            HumanMessage(content="q1"),
            AIMessage(content="a1"),
            HumanMessage(content="q2"),
            AIMessage(content="a2"),
            HumanMessage(content="q3"),
            AIMessage(content="a3"),
        ]
        original = list(messages)
        summarizer = StubSummarizer(reply="很长的摘要 " * 400)
        new_messages, summary = compact_messages(messages, summarizer, keep_recent=2)
        self.assertIsNone(summary)
        self.assertEqual(new_messages, original)

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
        self.assertIn("summarization failed", new_messages[0].content)
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
                WordyAgent(),
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
            # 首轮标题一次 + 压缩一次
            self.assertEqual(summarizer.calls, 2)
            self.assertEqual(session.stats()["compactions"], 1)
            # 压缩后的历史以摘要开头，且仍保留最近的消息
            self.assertTrue(session.messages[0].content.startswith(SUMMARY_PREFIX))
            # 用户可见原文不受压缩影响
            visible = "\n".join(message_text(item) for item in session.display_messages)
            self.assertIn("问题 1", visible)
            self.assertFalse(session.display_messages[0].content.startswith(SUMMARY_PREFIX))
            reloaded = ChatSession(
                StubAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                hydrate=True,
            )
            reloaded_visible = "\n".join(
                message_text(item) for item in reloaded.display_messages
            )
            self.assertIn("问题 1", reloaded_visible)

    def test_manual_compact(self):
        with workspace_tempdir() as tmp:
            summarizer = StubSummarizer()
            session = ChatSession(
                WordyAgent(),
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
            # 首轮标题一次 + 手动压缩一次
            self.assertEqual(summarizer.calls, 2)
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


class StepThoughtTest(unittest.TestCase):
    """tool_calling has no separate thought channel, so thoughts are derived from AI text carrying tool_calls."""

    class ThoughtStreamAgent:
        emits_thought_events = False

        def stream(self, payload, stream_mode=None):
            history = list(payload["messages"])
            ai = AIMessage(
                content="先读文件确认。",
                tool_calls=[{"name": "read_file", "args": {"path": "a"}, "id": "call-1"}],
            )
            yield ("messages", (AIMessageChunk(content="先读文件确认。"), None))
            # values 会重复上报同一条消息，thought 只能发一次
            yield ("values", {"messages": [*history, ai]})
            yield ("values", {"messages": [*history, ai]})
            tool = ToolMessage(content="内容", tool_call_id="call-1", name="read_file")
            yield ("values", {"messages": [*history, ai, tool]})
            final = AIMessage(content="完成")
            yield ("messages", (AIMessageChunk(content="完成"), None))
            yield ("values", {"messages": [*history, ai, tool, final]})

    def test_tool_call_message_text_becomes_thought(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                self.ThoughtStreamAgent(),
                "s1",
                store=SessionStore(tmp),
                hydrate=False,
            )
            events = list(session.ask_stream("读一下"))

            thoughts = [payload for kind, payload in events if kind == "thought"]
            self.assertEqual(thoughts, ["先读文件确认。"])
            tools = [payload for kind, payload in events if kind == "tool"]
            self.assertEqual(tools[0]["name"], "read_file")
            done = next(payload for kind, payload in events if kind == "done")
            self.assertEqual(done.text, "完成")

    def test_native_reasoning_content_becomes_thought(self):
        class NativeReasoningAgent:
            emits_thought_events = False

            def stream(self, payload, stream_mode=None):
                history = list(payload["messages"])
                final = AIMessage(
                    content="完成",
                    additional_kwargs={"reasoning_content": "先分析依赖，再给出结论。"},
                )
                yield ("messages", (AIMessageChunk(content="完成"), None))
                yield ("values", {"messages": [*history, final]})

        with workspace_tempdir() as tmp:
            session = ChatSession(
                NativeReasoningAgent(),
                "s1",
                store=SessionStore(tmp),
                hydrate=False,
            )
            events = list(session.ask_stream("分析一下"))

        self.assertEqual(
            [payload for kind, payload in events if kind == "thought"],
            ["先分析依赖，再给出结论。"],
        )

    def test_native_reasoning_stream_updates_one_thought(self):
        class NativeReasoningStreamAgent:
            emits_thought_events = False

            def stream(self, payload, stream_mode=None):
                history = list(payload["messages"])
                parts = ["Let", " us", " inspect", " the dependency first."]
                for part in parts:
                    yield (
                        "messages",
                        (
                            AIMessageChunk(
                                content="",
                                id="reasoning-run",
                                additional_kwargs={"reasoning_content": part},
                            ),
                            None,
                        ),
                    )
                final = AIMessage(
                    content="完成",
                    additional_kwargs={"reasoning_content": f"  {''.join(parts)}\n"},
                )
                yield ("values", {"messages": [*history, final]})

        with workspace_tempdir() as tmp:
            session = ChatSession(
                NativeReasoningStreamAgent(),
                "s1",
                store=SessionStore(tmp),
                hydrate=False,
            )
            events = list(session.ask_stream("分析一下"))

        thoughts = [payload for kind, payload in events if kind == "thought"]
        self.assertEqual({item["id"] for item in thoughts}, {"reasoning-run"})
        self.assertTrue(all(item["append"] for item in thoughts))
        self.assertEqual(
            "".join(item["text"] for item in thoughts),
            "Let us inspect the dependency first.",
        )

    def test_reasoning_stream_is_not_emitted_twice_across_steps(self):
        """Two tool steps without chunk ids must not re-send the second full reasoning."""

        class MultiStepReasoningAgent:
            emits_thought_events = False

            def stream(self, payload, stream_mode=None):
                history = list(payload["messages"])
                first = "check the config"
                for part in ("check ", "the config"):
                    yield (
                        "messages",
                        (
                            AIMessageChunk(
                                content="",
                                additional_kwargs={"reasoning_content": part},
                            ),
                            None,
                        ),
                    )
                ai1 = AIMessage(
                    content="",
                    tool_calls=[{"name": "read_file", "args": {"path": "a"}, "id": "call-1"}],
                    additional_kwargs={"reasoning_content": f"  {first.replace(' ', '  ')}\n"},
                )
                yield ("values", {"messages": [*history, ai1]})
                tool = ToolMessage(content="内容", tool_call_id="call-1", name="read_file")
                second = "now summarize"
                for part in ("now ", "summarize"):
                    yield (
                        "messages",
                        (
                            AIMessageChunk(
                                content="",
                                additional_kwargs={"reasoning_content": part},
                            ),
                            None,
                        ),
                    )
                ai2 = AIMessage(
                    content="完成",
                    additional_kwargs={"reasoning_content": f"  {second}\n"},
                )
                yield ("values", {"messages": [*history, ai1, tool, ai2]})

        with workspace_tempdir() as tmp:
            session = ChatSession(
                MultiStepReasoningAgent(),
                "s1",
                store=SessionStore(tmp),
                hydrate=False,
            )
            events = list(session.ask_stream("分析一下"))

        thoughts = [payload for kind, payload in events if kind == "thought"]
        self.assertTrue(thoughts)
        self.assertTrue(all(isinstance(item, dict) and item.get("append") for item in thoughts))
        self.assertEqual(
            "".join(item["text"] for item in thoughts),
            "check the confignow summarize",
        )

    def test_reasoning_text_supports_content_blocks(self):
        message = AIMessage(content=[{
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "检查配置。"}],
        }])
        self.assertEqual(reasoning_text(message), "检查配置。")


class CacheUsageTest(unittest.TestCase):
    """Provider usage is recorded per turn, aggregated per session, and persisted."""

    class UsageAgent:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, payload):
            self.calls += 1
            messages = list(payload["messages"])
            usage = {
                "input_tokens": 1000 * self.calls,
                "output_tokens": 20,
                "total_tokens": 1000 * self.calls + 20,
                "input_token_details": {"cache_read": 1000 * self.calls - 100},
            }
            return {"messages": [*messages, AIMessage(content="答", usage_metadata=usage)]}

    def test_turn_and_session_usage_carry_cache_buckets(self):
        with workspace_tempdir() as tmp:
            store = SessionStore(tmp)
            session = ChatSession(self.UsageAgent(), "s1", store=store, hydrate=False)
            first = session.ask("一")
            second = session.ask("二")

            self.assertEqual(first.usage.attempts, 1)
            self.assertEqual(first.usage.cache_read_tokens, 900)
            self.assertEqual(first.usage.prompt_tokens, 1000)
            self.assertAlmostEqual(first.usage.hit_rate or 0, 0.9)
            self.assertEqual(second.usage.cache_read_tokens, 1900)

            stats = session.stats()
            self.assertEqual(stats["usage"]["cache_read_tokens"], 2800)
            self.assertEqual(stats["usage"]["prompt_tokens"], 3000)
            self.assertAlmostEqual(stats["usage"]["hit_rate"], 2800 / 3000, places=6)
            self.assertEqual(stats["usage"]["requests"], 2)

            reloaded = ChatSession(self.UsageAgent(), "s1", store=store, hydrate=True)
            self.assertEqual(reloaded.stats()["usage"]["cache_read_tokens"], 2800)

    def test_turn_without_provider_cache_reports_none(self):
        class NoCacheAgent:
            def invoke(self, payload):
                messages = list(payload["messages"])
                ai = AIMessage(
                    content="答",
                    usage_metadata={
                        "input_tokens": 500,
                        "output_tokens": 10,
                        "total_tokens": 510,
                    },
                )
                return {"messages": [*messages, ai]}

        with workspace_tempdir() as tmp:
            session = ChatSession(NoCacheAgent(), "s1", store=SessionStore(tmp), hydrate=False)
            result = session.ask("一")
        self.assertIsNone(result.usage.cache_read_tokens)
        self.assertIsNone(result.usage.hit_rate)
        self.assertIsNone(session.stats()["usage"]["hit_rate"])

    def test_old_messages_are_not_rewritten_between_turns(self):
        """A stable prefix is what makes the provider's cache reusable."""

        class ToolAgent:
            def __init__(self) -> None:
                self.calls = 0

            def invoke(self, payload):
                self.calls += 1
                messages = list(payload["messages"])
                if self.calls == 1:
                    ai = AIMessage(
                        content="",
                        tool_calls=[{"name": "run", "args": {}, "id": "c1"}],
                    )
                    tool = ToolMessage(
                        content="y" * 40_000, tool_call_id="c1", name="run"
                    )
                    return {"messages": [*messages, ai, tool, AIMessage(content="完成")]}
                return {"messages": [*messages, AIMessage(content="收到")]}

        with workspace_tempdir() as tmp:
            session = ChatSession(
                ToolAgent(),
                "s1",
                store=SessionStore(tmp),
                max_tool_output_tokens=100,
                hydrate=False,
            )
            session.ask("一")
            before = [message.content for message in session.messages]
            session.ask("二")
            after = [message.content for message in session.messages]

        # 已进入历史的消息一个字都不能变，否则前缀缓存从变化处起全部失效
        self.assertEqual(after[: len(before)], before)


class CachePreservingCompactionTest(unittest.TestCase):
    """Compaction replays the live prefix instead of sending a cold prompt."""

    class RecordingSummarizer:
        replays_prefix = True

        def __init__(self, reply: str = "## 摘要\n- 要点") -> None:
            self.reply = reply
            self.requests: list[list] = []

        def summarize(self, shadow):
            self.requests.append(list(shadow))
            return self.reply

    class SystemAwareSummarizer(RecordingSummarizer):
        system_prompt = "SYS"

    def test_summarizer_sees_the_exact_shadow(self):
        from agents.compaction import PrefixSummarizer

        class RecordingModel:
            def __init__(self) -> None:
                self.requests: list[list] = []

            def bind_tools(self, tools):  # noqa: ARG002 - 只为接受绑定
                return self

            def bind(self, **kwargs):  # noqa: ARG002
                return self

            def invoke(self, messages):
                self.requests.append(list(messages))
                return AIMessage(content="## 摘要\n- 要点")

        model = RecordingModel()
        summarizer = PrefixSummarizer(
            model=model, system_prompt="SYSTEM", engine="tool_calling"
        )
        with workspace_tempdir() as tmp:
            session = ChatSession(
                WordyAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                max_context_messages=10**6,
                max_context_tokens=10**9,
                keep_recent=2,
                hydrate=False,
            )
            session.ask("一")
            session.ask("二")
            shadow = list(session.messages[:-2])
            self.assertTrue(session.compact())

        self.assertEqual(len(model.requests), 2)  # 一次标题请求 + 一次摘要请求
        replayed = [r for r in model.requests if isinstance(r[0], SystemMessage)]
        self.assertEqual(len(replayed), 1)
        sent = replayed[0]
        self.assertEqual(sent[0].content, "SYSTEM")
        self.assertEqual(sent[1:-1], shadow)  # 逐字回放被遮蔽的历史
        self.assertIn("compaction engine", sent[-1].content)

    def test_retained_tail_is_byte_identical(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                WordyAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=self.RecordingSummarizer(),
                max_context_messages=10**6,
                max_context_tokens=10**9,
                keep_recent=2,
                hydrate=False,
            )
            session.ask("一")
            session.ask("二")
            tail = list(session.messages[-2:])
            self.assertTrue(session.compact())
            self.assertEqual(session.messages[-2:], tail)

    def test_prune_only_path_skips_the_summary_call(self):
        summarizer = self.RecordingSummarizer()
        with workspace_tempdir() as tmp:
            session = ChatSession(
                StubAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=summarizer,
                max_context_tokens=1000,
                max_context_messages=10**6,
                keep_recent=2,
                max_tool_output_tokens=100,
                hydrate=False,
            )
            session.messages = [
                HumanMessage(content="查日志"),
                AIMessage(content="", tool_calls=[{"name": "run", "args": {}, "id": "c1"}]),
                ToolMessage(content="z" * 40_000, tool_call_id="c1", name="run"),
                HumanMessage(content="继续"),
                AIMessage(content="好的"),
            ]
            self.assertTrue(session.compact())

        self.assertEqual(summarizer.requests, [])  # 没有发起摘要请求
        self.assertIn("tool output truncated", session.messages[2].content)


class OverflowRecoveryTest(unittest.TestCase):
    """A provider-confirmed overflow compacts the head and retries once."""

    class OverflowOnceAgent:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, payload, stream_mode=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(
                    "Error code: 400 - This model's maximum context length is 8192 tokens."
                )
            messages = list(payload["messages"])
            yield ("values", {"messages": [*messages, AIMessage(content="恢复后的回答")]})

    def test_overflow_triggers_compaction_and_one_retry(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                self.OverflowOnceAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=StubSummarizer(),
                max_context_messages=10**6,
                max_context_tokens=10**9,
                keep_recent=2,
                hydrate=False,
            )
            # 头部足够大，压缩才有内容可缩
            session.messages = [
                HumanMessage(content=f"旧问题 {BULK}"),
                AIMessage(content=f"旧回答 {BULK}"),
            ]
            events = list(session.ask_stream("新问题"))

        kinds = [kind for kind, _ in events]
        self.assertIn("done", kinds)
        done = next(payload for kind, payload in events if kind == "done")
        self.assertEqual(done.text, "恢复后的回答")
        self.assertEqual(session.agent.calls, 2)
        self.assertEqual(session.stats()["compactions"], 1)

    class OverflowOnceInvokeAgent:
        """Non-streaming agent: fails once with a context-overflow error, then answers."""

        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, payload):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(
                    "This model's maximum context length is 8192 tokens."
                )
            messages = list(payload["messages"])
            return {"messages": [*messages, AIMessage(content="恢复后的回答")]}

    def test_ask_path_recovers_too(self):
        with workspace_tempdir() as tmp:
            session = ChatSession(
                self.OverflowOnceInvokeAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=StubSummarizer(),
                max_context_messages=10**6,
                max_context_tokens=10**9,
                keep_recent=2,
                hydrate=False,
            )
            session.messages = [
                HumanMessage(content=f"旧问题 {BULK}"),
                AIMessage(content=f"旧回答 {BULK}"),
            ]
            result = session.ask("新问题")

        self.assertEqual(result.text, "恢复后的回答")
        self.assertEqual(session.agent.calls, 2)
        self.assertEqual(session.stats()["compactions"], 1)

    def test_unrelated_failure_is_not_retried(self):
        class BrokenAgent:
            def __init__(self) -> None:
                self.calls = 0

            def stream(self, payload, stream_mode=None):  # noqa: ARG002
                self.calls += 1
                raise RuntimeError("rate limit exceeded")
                yield  # pragma: no cover - 生成器形状

        with workspace_tempdir() as tmp:
            session = ChatSession(
                BrokenAgent(),
                "s1",
                store=SessionStore(tmp),
                summarizer=StubSummarizer(),
                hydrate=False,
            )
            with self.assertRaises(RuntimeError):
                list(session.ask_stream("问题"))
        self.assertEqual(session.agent.calls, 1)
        self.assertEqual(len(session.messages), 0)


if __name__ == "__main__":
    unittest.main()
