"""Cache policy resolution and the native Anthropic request body."""

from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agents.anthropic_messages import ChatAnthropicMessages, _messages_url
from agents.compaction import COMPACTION_INSTRUCTION_KEEP_FIRST, PrefixSummarizer
from agents.prompt_cache import CachePolicy, cache_policy, plan_breakpoints


class CachePolicyTest(unittest.TestCase):
    def test_known_model_families(self):
        cases = {
            "claude-sonnet-4-5": "explicit",
            "gpt-4.1-mini": "implicit",
            "o3-mini": "implicit",
            "deepseek-chat": "implicit",
            "grok-4": "implicit",
            "gemini-2.5-pro": "implicit",
            "kimi-k2-0905-preview": "implicit",
            "glm-4.6": "implicit",
            "qwen-plus": "implicit",
            "doubao-seed-1-6": "implicit",
            "MiniMax-M1": "implicit",
        }
        for model, mode in cases.items():
            self.assertEqual(cache_policy(model).mode, mode, model)

    def test_unknown_model_defaults_to_implicit(self):
        self.assertEqual(cache_policy("my-local-model").mode, "implicit")
        self.assertEqual(cache_policy("").mode, "implicit")

    def test_breakpoints_respect_the_marker_limit(self):
        policy = CachePolicy("explicit", 4, 1024)
        self.assertEqual(
            plan_breakpoints(policy, has_first_turn=True),
            ("prefix", "first_turn", "tail"),
        )
        limited = CachePolicy("explicit", 1, 1024)
        self.assertEqual(plan_breakpoints(limited, has_first_turn=True), ("prefix",))

    def test_implicit_models_have_no_breakpoints(self):
        self.assertEqual(plan_breakpoints(cache_policy("deepseek-chat")), ())


class AnthropicPayloadTest(unittest.TestCase):
    def build(self, model: str = "claude-sonnet-4-5") -> ChatAnthropicMessages:
        return ChatAnthropicMessages(
            model_name=model,
            api_key="sk-test",
            base_url="https://api.anthropic.com",
        )

    def test_messages_url_normalizes_v1(self):
        self.assertEqual(
            _messages_url("https://api.anthropic.com/v1"), "https://api.anthropic.com/v1/messages"
        )
        self.assertEqual(
            _messages_url("https://proxy.test/anthropic/"), "https://proxy.test/anthropic/v1/messages"
        )

    def test_explicit_breakpoints_are_placed(self):
        model = self.build()
        messages = [
            SystemMessage(content="SYS"),
            HumanMessage(content="first question"),
            AIMessage(content="first answer"),
            HumanMessage(content="second question"),
            AIMessage(content="second answer"),
        ]
        payload = model._payload(messages)
        self.assertEqual(payload["system"][-1]["cache_control"], {"type": "ephemeral"})
        # First assistant block is the preserved first exchange anchor.
        self.assertEqual(
            payload["messages"][1]["content"][-1]["cache_control"], {"type": "ephemeral"}
        )
        self.assertEqual(
            payload["messages"][-1]["content"][-1]["cache_control"], {"type": "ephemeral"}
        )

    def test_implicit_model_carries_no_markers(self):
        model = self.build("deepseek-chat")
        payload = model._payload(
            [SystemMessage(content="SYS"), HumanMessage(content="q"), AIMessage(content="a")]
        )
        self.assertEqual(payload["system"], [{"type": "text", "text": "SYS"}])
        self.assertNotIn("cache_control", str(payload))

    def test_tool_calls_and_results_are_converted(self):
        model = self.build()
        messages = [
            SystemMessage(content="SYS"),
            HumanMessage(content="read it"),
            AIMessage(
                content="",
                tool_calls=[{"name": "read_file", "args": {"path": "a"}, "id": "call-1"}],
            ),
            ToolMessage(content="file body", tool_call_id="call-1", name="read_file"),
        ]
        payload = model._payload(
            messages,
            tools=[{"name": "read_file", "description": "read", "parameters": {"type": "object"}}],
        )
        self.assertEqual(payload["tools"][0]["name"], "read_file")
        assistant = payload["messages"][1]
        self.assertEqual(assistant["content"][0]["type"], "tool_use")
        self.assertEqual(assistant["content"][0]["id"], "call-1")
        tool_result = payload["messages"][2]
        self.assertEqual(tool_result["role"], "user")
        self.assertEqual(tool_result["content"][0]["type"], "tool_result")
        self.assertEqual(tool_result["content"][0]["tool_use_id"], "call-1")

    def test_usage_maps_cache_buckets(self):
        model = self.build()
        usage = model._usage(
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 900,
                "cache_creation_input_tokens": 90,
            }
        )
        self.assertEqual(usage["input_tokens"], 1000)
        self.assertEqual(usage["output_tokens"], 5)
        self.assertEqual(usage["input_token_details"], {"cache_read": 900, "cache_creation": 90})

    def test_image_blocks_are_converted(self):
        model = self.build()
        payload = model._payload(
            [
                SystemMessage(content="SYS"),
                HumanMessage(
                    content=[
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AAAA"},
                        },
                    ]
                ),
            ]
        )
        blocks = payload["messages"][0]["content"]
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")


class SummarizerReplayTest(unittest.TestCase):
    class RecordingModel:
        def __init__(self) -> None:
            self.bound_tools: list = []
            self.requests: list = []

        def bind_tools(self, tools):
            self.bound_tools = list(tools)
            return self

        def bind(self, **kwargs):  # noqa: ARG002
            return self

        def invoke(self, messages):
            self.requests.append(list(messages))
            return AIMessage(content="summary")

    def test_tool_callable_is_resolved_per_request(self):
        model = self.RecordingModel()
        visible = [{"name": "read_file"}]
        summarizer = PrefixSummarizer(
            model=model, system_prompt="S", tools=lambda: visible
        )
        summarizer.summarize([HumanMessage(content="q")])
        self.assertEqual(model.bound_tools, visible)

        visible = [{"name": "read_file"}, {"name": "run_command"}]
        summarizer.summarize([HumanMessage(content="q")])
        self.assertEqual(model.bound_tools, visible)

    def test_preserved_head_uses_the_keep_first_instruction(self):
        model = self.RecordingModel()
        summarizer = PrefixSummarizer(model=model, system_prompt="S")
        summarizer.summarize(
            [HumanMessage(content="later")],
            prefix=[HumanMessage(content="first q"), AIMessage(content="first a")],
        )
        request = model.requests[-1]
        self.assertEqual(request[0].content, "S")
        self.assertEqual(request[1].content, "first q")
        self.assertEqual(request[2].content, "first a")
        self.assertEqual(request[-1].content, COMPACTION_INSTRUCTION_KEEP_FIRST)


if __name__ == "__main__":
    unittest.main()
