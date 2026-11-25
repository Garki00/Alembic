# tests/react_test.py
"""Native ReAct loop tests: parsing, tools, approval, limits and streaming."""

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.approval import ApprovalGate, ApprovalPolicy
from agents.context import ChatSession, SessionStore
from agents.react import (
    ReActAgent,
    extract_action,
    extract_final_answer,
    extract_thought,
    parse_action_input,
)


class ScriptedModel:
    """Return canned responses and record the prompts it receives."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[list] = []

    def invoke(self, messages):
        self.prompts.append(messages)
        if not self.replies:
            raise RuntimeError("no more scripted replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply)


@tool
def echo(text: str) -> str:
    """Echo the given text back."""
    return f"echo:{text}"


@tool
def add(a: int, b: int) -> str:
    """Add two integers."""
    return str(a + b)


def make_agent(replies, tools=None, **kwargs):
    return ReActAgent(ScriptedModel(replies), tools or [echo, add], "base prompt", **kwargs)


class ParseTest(unittest.TestCase):
    def test_parse_json_and_code_fence(self):
        self.assertEqual(parse_action_input('{"path": "a.txt"}'), {"path": "a.txt"})
        self.assertEqual(
            parse_action_input('```json\n{"text": "hi"}\n```'),
            {"text": "hi"},
        )
        self.assertEqual(parse_action_input("{'text': 'hi'}"), {"text": "hi"})
        self.assertEqual(parse_action_input(""), {})

    def test_parse_rejects_scalar_and_bad_json(self):
        with self.assertRaises(ValueError):
            parse_action_input('"hello"')
        with self.assertRaises(ValueError):
            parse_action_input("{not json")

    def test_extract_action_and_final(self):
        text = (
            "Thought: I need to echo.\n"
            "Action: echo\n"
            "Action Input: {\"text\": \"hi\"}\n"
        )
        self.assertEqual(extract_action(text)[0], "echo")
        self.assertEqual(extract_thought(text), "I need to echo.")

        final = "Thought: done\nFinal Answer: The answer is 42."
        self.assertEqual(extract_final_answer(final), "The answer is 42.")

    def test_uses_last_action_when_model_echoes_scratchpad(self):
        text = (
            "Thought: old step\nAction: old_tool\nAction Input: {}\nObservation: old\n"
            "Thought: new step\nAction: echo\nAction Input: {\"text\": \"hi\"}\n"
        )
        name, raw = extract_action(text)
        self.assertEqual(name, "echo")
        self.assertIn('"text": "hi"', raw)


class ReActLoopTest(unittest.TestCase):
    def test_tool_then_final_answer(self):
        agent = make_agent([
            'Thought: I should echo it.\nAction: echo\nAction Input: {"text": "hi"}',
            "Thought: I now know the final answer\nFinal Answer: echo:hi",
        ])
        result = agent.invoke({"messages": [HumanMessage(content="say hi")]})

        messages = result["messages"]
        self.assertTrue(any(isinstance(m, ToolMessage) for m in messages))
        tool_message = next(m for m in messages if isinstance(m, ToolMessage))
        self.assertEqual(tool_message.content, "echo:hi")
        self.assertEqual(tool_message.name, "echo")
        self.assertEqual(messages[-1].content, "echo:hi")

        # Prompt is English and contains the ReAct contract.
        prompt = agent.model.prompts[0][1].content
        self.assertIn("Action Input", prompt)
        self.assertIn("Final Answer", prompt)
        self.assertIn("echo", prompt)
        self.assertNotIn("获取指定", prompt)  # tool catalogue is English

    def test_final_answer_without_tool(self):
        agent = make_agent([
            "Thought: I can answer directly.\nFinal Answer: 42",
        ])
        result = agent.invoke({"messages": [HumanMessage(content="answer?")]})
        self.assertEqual(result["messages"][-1].content, "42")

    def test_unknown_tool_is_observed_then_recovers(self):
        agent = make_agent([
            "Thought: try a tool.\nAction: missing_tool\nAction Input: {}",
            "Thought: use echo instead.\nAction: echo\nAction Input: {\"text\": \"ok\"}",
            "Thought: done\nFinal Answer: echo:ok",
        ])
        result = agent.invoke({"messages": [HumanMessage(content="go")]})
        self.assertEqual(result["messages"][-1].content, "echo:ok")
        second_prompt = agent.model.prompts[1][1].content
        self.assertIn("unknown_tool", second_prompt)

    def test_invalid_action_input_is_observed(self):
        agent = make_agent([
            "Thought: bad input.\nAction: echo\nAction Input: {bad}",
            "Thought: fix it.\nAction: echo\nAction Input: {\"text\": \"ok\"}",
            "Thought: done\nFinal Answer: echo:ok",
        ])
        result = agent.invoke({"messages": [HumanMessage(content="go")]})
        self.assertEqual(result["messages"][-1].content, "echo:ok")
        self.assertIn("bad_action_input", agent.model.prompts[1][1].content)

    def test_tool_call_limit(self):
        agent = make_agent([
            'Thought: one.\nAction: echo\nAction Input: {"text": "a"}',
            'Thought: two.\nAction: echo\nAction Input: {"text": "b"}',
        ], max_tool_calls=1)
        result = agent.invoke({"messages": [HumanMessage(content="go")]})
        self.assertIn("tool-call limit", result["messages"][-1].content)

    def test_model_call_limit(self):
        agent = make_agent([
            'Thought: one.\nAction: echo\nAction Input: {"text": "a"}',
        ], max_model_calls=1, max_tool_calls=10)
        result = agent.invoke({"messages": [HumanMessage(content="go")]})
        self.assertIn("model-call limit", result["messages"][-1].content)

    def test_model_retry(self):
        model = ScriptedModel([RuntimeError("boom"), "Thought: done\nFinal Answer: ok"])
        agent = ReActAgent(model, [echo], "base", model_max_retries=1)
        result = agent.invoke({"messages": [HumanMessage(content="go")]})
        self.assertEqual(result["messages"][-1].content, "ok")
        self.assertEqual(len(model.prompts), 2)

    def test_mixed_action_and_final_answer_is_rejected(self):
        """A model that writes Action + Final Answer together must not skip the tool."""
        agent = make_agent([
            'Thought: write now.\nAction: echo\nAction Input: {"text": "hi"}\n'
            "Final Answer: already done",
            'Thought: retry.\nAction: echo\nAction Input: {"text": "hi"}',
            "Thought: done\nFinal Answer: echo:hi",
        ])
        result = agent.invoke({"messages": [HumanMessage(content="go")]})

        tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].content, "echo:hi")
        self.assertEqual(result["messages"][-1].content, "echo:hi")
        # The corrective observation is fed back to the model on the retry.
        self.assertIn("protocol_violation", agent.model.prompts[1][1].content)

    def test_model_written_observation_is_rejected(self):
        """A fabricated Observation must never be trusted as a tool result."""
        agent = make_agent([
            'Thought: write it.\nAction: echo\nAction Input: {"text": "hi"}\n'
            "Observation: echo:fake\n"
            "Final Answer: saved",
            'Thought: retry.\nAction: echo\nAction Input: {"text": "hi"}',
            "Thought: done\nFinal Answer: echo:hi",
        ])
        result = agent.invoke({"messages": [HumanMessage(content="go")]})

        tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].content, "echo:hi")
        self.assertNotIn("echo:fake", tool_messages[0].content)
        self.assertIn("protocol_violation", agent.model.prompts[1][1].content)

    def test_multiple_actions_are_rejected(self):
        """Two actions in one message are ambiguous; none of them may run yet."""
        agent = make_agent([
            'Thought: two steps.\nAction: echo\nAction Input: {"text": "a"}\n'
            'Action: echo\nAction Input: {"text": "b"}',
            'Thought: one step.\nAction: echo\nAction Input: {"text": "ok"}',
            "Thought: done\nFinal Answer: echo:ok",
        ])
        result = agent.invoke({"messages": [HumanMessage(content="go")]})

        tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].content, "echo:ok")

    def test_protocol_errors_stop_after_limit(self):
        agent = make_agent(
            [
                'Action: echo\nAction Input: {"text": "a"}\nFinal Answer: done',
                'Action: echo\nAction Input: {"text": "a"}\nFinal Answer: done',
                'Action: echo\nAction Input: {"text": "a"}\nFinal Answer: done',
            ],
            max_protocol_errors=1,
        )
        result = agent.invoke({"messages": [HumanMessage(content="go")]})
        self.assertIn("ReAct format", result["messages"][-1].content)
        self.assertFalse(any(isinstance(m, ToolMessage) for m in result["messages"]))

    def test_stop_sequences_limit_each_completion_to_one_step(self):
        class StopAwareModel:
            def __init__(self):
                self.stops: list = []

            def invoke(self, _messages, stop=None):
                self.stops.append(stop)
                return AIMessage(content="Thought: done\nFinal Answer: ok")

        model = StopAwareModel()
        agent = ReActAgent(model, [echo], "base")
        agent.invoke({"messages": [HumanMessage(content="go")]})
        self.assertEqual(len(model.stops), 1)
        self.assertIn("\nObservation:", model.stops[0])

    def test_stream_emits_thought_tool_and_final(self):
        agent = make_agent([
            'Thought: echo it.\nAction: echo\nAction Input: {"text": "hi"}',
            "Thought: done\nFinal Answer: echo:hi",
        ])
        events = list(agent.stream({"messages": [HumanMessage(content="go")]}))
        kinds = [kind for kind, _ in events]
        self.assertIn("messages", kinds)
        self.assertIn("values", kinds)
        self.assertIn("thought", kinds)

        thoughts = [payload for kind, payload in events if kind == "thought"]
        self.assertTrue(any("echo it" in text for text in thoughts))

        deltas = [payload[0].content for kind, payload in events if kind == "messages"]
        self.assertIn("echo:hi", deltas)

        value_messages = [
            payload
            for kind, payload in events
            if kind == "values"
        ][-1]["messages"]
        self.assertEqual(value_messages[-1].content, "echo:hi")
        self.assertTrue(any(getattr(m, "tool_calls", None) for m in value_messages))


class PromptTest(unittest.TestCase):
    def test_every_registered_tool_has_an_english_summary(self):
        from prompts.react import TOOL_SUMMARIES
        from tools.registry import TOOL_SPECS

        names = {
            name
            for spec in TOOL_SPECS.values()
            for name in spec.tool_names
        }
        missing = names - set(TOOL_SUMMARIES)
        self.assertEqual(missing, set())
        for summary in TOOL_SUMMARIES.values():
            self.assertTrue(summary.strip())

    def test_prompt_and_tool_description_budgets(self):
        from prompts.react import REACT_SYSTEM_PROMPT, TOOL_SUMMARIES
        from prompts.system import SYSTEM_PROMPT

        self.assertLess(len(SYSTEM_PROMPT), 1500)
        self.assertLess(len(REACT_SYSTEM_PROMPT), 400)
        self.assertTrue(all(len(text) < 120 for text in TOOL_SUMMARIES.values()))


class ApprovalTest(unittest.TestCase):
    def gate(self, decision: str) -> tuple[ApprovalGate, ApprovalPolicy]:
        policy = ApprovalPolicy(mode="build")
        gate = ApprovalGate(policy=policy, ask=lambda _request: decision)
        return gate, policy

    def test_denied_tool_is_not_executed(self):
        gate, _policy = self.gate("deny")
        agent = make_agent([
            'Thought: echo it.\nAction: echo\nAction Input: {"text": "hi"}',
            "Thought: denied.\nFinal Answer: cannot",
        ], approval=gate)
        result = agent.invoke({"messages": [HumanMessage(content="go")]})
        tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
        self.assertIn("approval_denied", tool_message.content)
        self.assertEqual(result["messages"][-1].content, "cannot")

    def test_always_allows_and_remembers(self):
        gate, policy = self.gate("always")
        agent = make_agent([
            'Thought: echo it.\nAction: echo\nAction Input: {"text": "hi"}',
            "Thought: done.\nFinal Answer: echo:hi",
        ], approval=gate)
        result = agent.invoke({"messages": [HumanMessage(content="go")]})
        tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
        self.assertEqual(tool_message.content, "echo:hi")
        self.assertTrue(policy.memo)
        self.assertEqual(next(iter(policy.memo.values()))["tool"], "echo")


class BuildAgentTest(unittest.TestCase):
    def test_build_agent_react_opt_in(self):
        import os
        from unittest.mock import patch

        from agents.agent import build_agent
        from tools.context import ToolContext

        with patch.dict(os.environ, {"AGENT_ENGINE": "react"}):
            # settings is a class attribute snapshot; explicitly pass engine.
            agent = build_agent(
                model=ScriptedModel(["Thought: done\nFinal Answer: ok"]),
                tools=[echo],
                ctx=ToolContext(),
                engine="react",
            )
        self.assertIsInstance(agent, ReActAgent)

    def test_build_agent_tool_calling_still_available(self):
        from agents.agent import build_agent
        from tools.context import ToolContext

        # A stub model is enough: create_agent only binds tools at call time.
        agent = build_agent(
            model=ScriptedModel([]),
            tools=[echo],
            ctx=ToolContext(),
            engine="tool_calling",
        )
        self.assertNotIsInstance(agent, ReActAgent)


class SessionIntegrationTest(unittest.TestCase):
    def test_chat_session_streams_tool_and_final(self):
        root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / f"react_{uuid4().hex[:10]}"
        tmp.mkdir()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        agent = make_agent([
            'Thought: echo it.\nAction: echo\nAction Input: {"text": "hi"}',
            "Thought: done\nFinal Answer: echo:hi",
        ])
        session = ChatSession(
            agent,
            "react-session",
            store=SessionStore(tmp),
            hydrate=False,
        )
        events = list(session.ask_stream("say hi"))
        kinds = [kind for kind, _ in events]
        self.assertIn("tool", kinds)
        self.assertIn("tool_result", kinds)
        self.assertIn("thought", kinds)
        self.assertIn("delta", kinds)
        self.assertIn("done", kinds)

        tool_payload = next(payload for kind, payload in events if kind == "tool")
        self.assertEqual(tool_payload["name"], "echo")
        result_payload = next(payload for kind, payload in events if kind == "tool_result")
        self.assertEqual(result_payload["id"], tool_payload["id"])
        self.assertEqual(result_payload["content"], "echo:hi")
        done = next(payload for kind, payload in events if kind == "done")
        self.assertEqual(done.text, "echo:hi")
        self.assertTrue(any(isinstance(m, ToolMessage) for m in session.messages))


if __name__ == "__main__":
    unittest.main()
