# tests/compaction_test.py
"""Cache-preserving compaction: prefix replay, region choice, pruning, overflow."""

import unittest

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agents.compaction import (
    CHECKPOINT_OPEN,
    COMPACTION_INSTRUCTION,
    COMPACTION_PREAMBLE,
    LEGACY_SUMMARY_PREFIX,
    PrefixSummarizer,
    checkpoint_message,
    is_checkpoint_text,
    is_context_overflow_error,
    prune_tool_messages,
    select_shadow_start,
)


class RecordingModel:
    """Fake chat model that records the messages of each request."""

    def __init__(self, reply: str = "## 摘要\n- 要点") -> None:
        self.reply = reply
        self.requests: list[list] = []
        self.bound_tools: list | None = None
        self.bound_kwargs: dict = {}

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def bind(self, **kwargs):
        self.bound_kwargs.update(kwargs)
        return self

    def invoke(self, messages):
        self.requests.append(list(messages))
        return AIMessage(content=self.reply)


class PrefixSummarizerTest(unittest.TestCase):
    def test_replay_is_system_then_shadow_then_instruction(self):
        model = RecordingModel()
        summarizer = PrefixSummarizer(
            model=model,
            system_prompt="SYSTEM PROMPT",
            engine="tool_calling",
        )
        shadow = [
            HumanMessage(content="q1"),
            AIMessage(content="a1"),
            HumanMessage(content="q2"),
        ]
        summary = summarizer.summarize(shadow)

        self.assertEqual(summary, "## 摘要\n- 要点")
        self.assertEqual(len(model.requests), 1)
        sent = model.requests[0]
        self.assertIsInstance(sent[0], SystemMessage)
        self.assertEqual(sent[0].content, "SYSTEM PROMPT")
        # 被遮蔽的消息逐字回放，顺序与内容都不变
        self.assertEqual(sent[1:4], shadow)
        # 指令是最后一条 user 消息
        self.assertIsInstance(sent[-1], HumanMessage)
        self.assertEqual(sent[-1].content, COMPACTION_INSTRUCTION)

    def test_tool_declarations_are_bound_like_the_real_request(self):
        model = RecordingModel()
        tools = [{"name": "read_file"}, {"name": "run_command"}]
        summarizer = PrefixSummarizer(
            model=model,
            system_prompt="S",
            tools=tools,
            engine="tool_calling",
        )
        summarizer.summarize([HumanMessage(content="q")])
        self.assertEqual(model.bound_tools, tools)

    def test_max_tokens_is_applied_when_configured(self):
        model = RecordingModel()
        summarizer = PrefixSummarizer(
            model=model,
            system_prompt="S",
            max_tokens=4096,
        )
        summarizer.summarize([HumanMessage(content="q")])
        self.assertEqual(model.bound_kwargs.get("max_tokens"), 4096)

    def test_empty_reply_raises(self):
        summarizer = PrefixSummarizer(
            model=RecordingModel(reply="   "),
            system_prompt="S",
        )
        with self.assertRaises(ValueError):
            summarizer.summarize([HumanMessage(content="q")])

    def test_react_engine_renders_shadow_into_one_user_message(self):
        model = RecordingModel()
        summarizer = PrefixSummarizer(
            model=model,
            system_prompt="SYS",
            engine="react",
        )
        summarizer.summarize([HumanMessage(content="q1"), AIMessage(content="a1")])
        sent = model.requests[0]
        self.assertIsInstance(sent[0], SystemMessage)
        self.assertEqual(len(sent), 2)
        self.assertIn("q1", sent[1].content)
        self.assertTrue(sent[1].content.endswith(COMPACTION_INSTRUCTION))


class CheckpointFramingTest(unittest.TestCase):
    def test_checkpoint_carries_preamble_and_tags(self):
        message = checkpoint_message("## 摘要\n- 改动了 a.py")
        self.assertTrue(message.content.startswith(COMPACTION_PREAMBLE))
        self.assertIn(CHECKPOINT_OPEN, message.content)
        self.assertIn("改动了 a.py", message.content)
        self.assertTrue(is_checkpoint_text(message.content))

    def test_legacy_summary_is_still_recognised(self):
        self.assertTrue(is_checkpoint_text(f"{LEGACY_SUMMARY_PREFIX}\n旧摘要"))

    def test_plain_user_text_is_not_a_checkpoint(self):
        self.assertFalse(is_checkpoint_text("这个报错怎么修？"))


class SelectShadowStartTest(unittest.TestCase):
    def test_short_history_has_no_shadow(self):
        self.assertEqual(select_shadow_start([HumanMessage(content="q")], 8), 0)

    def test_never_starts_at_a_tool_message(self):
        messages = [
            HumanMessage(content="q1"),
            AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]),
            ToolMessage(content="r1", tool_call_id="c1"),
            HumanMessage(content="q2"),
            AIMessage(content="a2"),
        ]
        # 从尾部数 3 条会落在 ToolMessage 上：必须回退到发起调用的 AI 消息
        start = select_shadow_start(messages, 3, prefer_human=False)
        self.assertEqual(start, 1)

    def test_prefers_a_user_boundary(self):
        messages = [
            HumanMessage(content="q1"),
            AIMessage(content="a1"),
            HumanMessage(content="q2"),
            AIMessage(content="a2"),
        ]
        self.assertEqual(select_shadow_start(messages, 3), 2)
        self.assertEqual(messages[select_shadow_start(messages, 3)].type, "human")


class PruneToolMessagesTest(unittest.TestCase):
    def test_truncates_oversized_body_with_marker(self):
        message = ToolMessage(content="x" * 40_000, tool_call_id="c1", name="run")
        pruned, count = prune_tool_messages([message], max_tokens=100)
        self.assertEqual(count, 1)
        self.assertIn("tool output truncated", pruned[0].content)
        self.assertLess(len(pruned[0].content), 40_000)

    def test_is_idempotent(self):
        message = ToolMessage(content="x" * 40_000, tool_call_id="c1", name="run")
        once, _ = prune_tool_messages([message], max_tokens=100)
        twice, count = prune_tool_messages(once, max_tokens=100)
        self.assertEqual(count, 0)
        self.assertEqual(once[0].content, twice[0].content)

    def test_short_body_is_untouched(self):
        message = ToolMessage(content="ok", tool_call_id="c1")
        pruned, count = prune_tool_messages([message], max_tokens=100)
        self.assertEqual(count, 0)
        self.assertIs(pruned[0], message)


class OverflowDetectionTest(unittest.TestCase):
    def test_known_provider_wording(self):
        samples = [
            "Error code: 400 - This model's maximum context length is 65536 tokens.",
            "context_length_exceeded",
            "Please reduce the length of the messages.",
            "prompt is too long: 200000 tokens",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(is_context_overflow_error(RuntimeError(sample)))

    def test_unrelated_error_is_not_overflow(self):
        self.assertFalse(is_context_overflow_error(RuntimeError("rate limit exceeded")))
        self.assertFalse(is_context_overflow_error(RuntimeError("invalid api key")))


if __name__ == "__main__":
    unittest.main()
