# tests/usage_test.py
"""Provider usage accounting, especially the prompt-cache read/write buckets."""

import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.usage import (
    SessionUsage,
    TokenUsage,
    TurnUsage,
    fold_turn_usage,
    format_hit_rate,
    usage_from_message,
)


def ai(usage_metadata=None, **extra) -> AIMessage:
    return AIMessage(content="答", usage_metadata=usage_metadata, **extra)


class UsageFromMessageTest(unittest.TestCase):
    def test_deepseek_shape_cache_inside_input(self):
        # DeepSeek: prompt_tokens = hit + miss, total = prompt + completion
        message = ai({
            "input_tokens": 1000,
            "output_tokens": 50,
            "total_tokens": 1050,
            "input_token_details": {"cache_read": 800},
        })
        usage = usage_from_message(message)
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.input_tokens, 200)  # 1000 - 800
        self.assertEqual(usage.cache_read_tokens, 800)
        self.assertEqual(usage.prompt_tokens, 1000)
        self.assertAlmostEqual(usage.hit_rate or 0, 0.8)
        self.assertEqual(usage.output_tokens, 50)

    def test_anthropic_shape_cache_outside_input(self):
        # input_tokens excludes the cache buckets; total adds them back
        message = ai({
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 1000,
            "input_token_details": {"cache_read": 800, "cache_creation": 50},
        })
        usage = usage_from_message(message)
        assert usage is not None
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.cache_read_tokens, 800)
        self.assertEqual(usage.cache_write_tokens, 50)
        self.assertEqual(usage.prompt_tokens, 950)

    def test_missing_total_assumes_cache_inside_input(self):
        # 只给 input/output，没有 total：按 OpenAI/DeepSeek 的约定，缓存含在 input 里
        message = AIMessage(
            content="答",
            usage_metadata={
                "input_tokens": 500,
                "output_tokens": 10,
                "total_tokens": 510,
                "input_token_details": {"cache_read": 400},
            },
        )
        message.usage_metadata.pop("total_tokens", None)
        usage = usage_from_message(message)
        assert usage is not None
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.prompt_tokens, 500)

    def test_message_without_usage_is_ignored(self):
        self.assertIsNone(usage_from_message(ai(None)))
        self.assertIsNone(usage_from_message(HumanMessage(content="q")))
        self.assertIsNone(usage_from_message(ToolMessage(content="r", tool_call_id="1")))

    def test_reasoning_tokens_are_read(self):
        usage = usage_from_message(ai({
            "input_tokens": 10,
            "output_tokens": 90,
            "total_tokens": 100,
            "output_token_details": {"reasoning": 60},
        }))
        assert usage is not None
        self.assertEqual(usage.reasoning_tokens, 60)

    def test_provider_without_cache_reports_no_hit_rate(self):
        usage = usage_from_message(ai({
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
        }))
        assert usage is not None
        self.assertIsNone(usage.cache_read_tokens)
        self.assertIsNone(usage.hit_rate)


class FoldTurnUsageTest(unittest.TestCase):
    def test_sums_every_attempt(self):
        messages = [
            HumanMessage(content="q"),
            ai({"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010,
                "input_token_details": {"cache_read": 900}}),
            ToolMessage(content="r", tool_call_id="1"),
            ai({"input_tokens": 1200, "output_tokens": 20, "total_tokens": 1220,
                "input_token_details": {"cache_read": 1000}}),
        ]
        usage = fold_turn_usage(messages)
        self.assertEqual(usage.attempts, 2)
        self.assertEqual(usage.cache_read_tokens, 1900)
        self.assertEqual(usage.input_tokens, 300)  # (1000-900) + (1200-1000)
        self.assertEqual(usage.prompt_tokens, 2200)

    def test_partial_cache_reporting_is_dropped(self):
        messages = [
            ai({"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010,
                "input_token_details": {"cache_read": 900}}),
            ai({"input_tokens": 500, "output_tokens": 10, "total_tokens": 510}),
        ]
        usage = fold_turn_usage(messages)
        self.assertEqual(usage.attempts, 2)
        self.assertIsNone(usage.cache_read_tokens)
        self.assertIsNone(usage.hit_rate)

    def test_synthetic_messages_do_not_count_as_attempts(self):
        usage = fold_turn_usage([HumanMessage(content="q"), AIMessage(content="a")])
        self.assertEqual(usage.attempts, 0)
        self.assertFalse(usage.known)


class SessionUsageTest(unittest.TestCase):
    def test_accumulates_and_reports_hit_rate(self):
        session = SessionUsage()
        session.add_turn(TurnUsage(
            attempts=2, input_tokens=200, output_tokens=10,
            cache_read_tokens=800, cache_write_tokens=0, total_tokens=1010,
        ))
        session.add_turn(TurnUsage(
            attempts=1, input_tokens=100, output_tokens=5,
            cache_read_tokens=900, cache_write_tokens=0, total_tokens=1005,
        ))
        self.assertEqual(session.requests, 3)
        self.assertEqual(session.prompt_tokens, 2000)
        self.assertEqual(session.cache_read_tokens, 1700)
        self.assertAlmostEqual(session.hit_rate or 0, 0.85)

    def test_round_trips_through_dict(self):
        session = SessionUsage()
        session.add_turn(TurnUsage(
            attempts=1, input_tokens=10, output_tokens=2,
            cache_read_tokens=90, cache_write_tokens=0, total_tokens=102,
        ))
        restored = SessionUsage.from_dict(session.to_dict())
        self.assertEqual(restored.to_dict(), session.to_dict())

    def test_unknown_usage_keeps_hit_rate_none(self):
        session = SessionUsage()
        session.add_turn(TurnUsage(attempts=1, input_tokens=100, output_tokens=5))
        self.assertIsNone(session.hit_rate)

    def test_from_dict_ignores_garbage(self):
        session = SessionUsage.from_dict({"input_tokens": "x", "cache_read_tokens": -3})
        self.assertEqual(session.input_tokens, 0)
        self.assertEqual(session.cache_read_tokens, 0)


class FormatHitRateTest(unittest.TestCase):
    def test_formats_and_hides_unknown(self):
        self.assertEqual(format_hit_rate(0.9234), "92.3%")
        self.assertEqual(format_hit_rate(0.9234, 0), "92%")
        self.assertEqual(format_hit_rate(None), "")


class TokenUsageAddTest(unittest.TestCase):
    def test_optional_buckets_survive_only_when_both_sides_have_them(self):
        left = TokenUsage(input_tokens=10, output_tokens=1, cache_read_tokens=5)
        right = TokenUsage(input_tokens=20, output_tokens=2, cache_read_tokens=None)
        merged = left + right
        self.assertIsNone(merged.cache_read_tokens)
        self.assertEqual(merged.input_tokens, 30)


if __name__ == "__main__":
    unittest.main()
