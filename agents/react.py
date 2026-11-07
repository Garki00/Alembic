# agents/react.py
"""Native ReAct loop: text Thought / Action / Action Input / Observation.

Why not ``create_agent``:
- ``create_agent`` delegates tool selection to the provider's native tool-calling
  API. That is reliable, but it hides the ReAct scratchpad and is not a text
  ReAct implementation.
- This module keeps the classic ReAct contract: the model writes Thoughts,
  chooses one Action, the loop executes it, appends an Observation, and repeats
  until ``Final Answer``.

The class implements the small interface used by :class:`agents.context.ChatSession`:
``invoke({"messages": [...]}) -> {"messages": [...]}`` and
``stream({"messages": [...]}, stream_mode=[...])`` yielding ``messages`` /
``values`` events. It therefore plugs into the existing CLI, SSE streaming,
context compression and history persistence without changing those layers.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agents.approval import (
    ApprovalGate,
    ApprovalRequest,
    denied_tool_message,
    describe_call,
)
from agents.context import SUMMARY_PREFIX, message_text
from prompts.react import REACT_SYSTEM_PROMPT, build_react_prompt
from tools.errors import tool_error, truncate

logger = logging.getLogger(__name__)

_MAX_HISTORY_MESSAGES = 40
_MAX_HISTORY_CHARS = 16000
_MAX_THOUGHT_CHARS = 1200


def _message_type(message: Any) -> str:
    return str(getattr(message, "type", "") or "")


def last_user_text(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if _message_type(message) == "human":
            return message_text(message).strip()
    return ""


def format_history(messages: Sequence[BaseMessage]) -> str:
    """Render prior turns as a compact text transcript for the ReAct prompt."""
    rows: list[str] = []
    for message in messages:
        kind = _message_type(message)
        text = message_text(message).strip()
        if not text:
            continue
        if kind == "human":
            if text.startswith(SUMMARY_PREFIX) or text.startswith("【历史对话摘要】"):
                rows.append(f"Summary: {text}")
            else:
                rows.append(f"User: {text}")
        elif kind == "ai":
            rows.append(f"Assistant: {text}")
        elif kind == "tool":
            name = str(getattr(message, "name", "") or "tool")
            rows.append(f"Observation [{name}]: {text}")
        # system / summary markers are already part of the system prompt
    if len(rows) > _MAX_HISTORY_MESSAGES:
        rows = ["…[earlier turns omitted]…", *rows[-_MAX_HISTORY_MESSAGES:]]
    text = "\n".join(rows)
    if len(text) > _MAX_HISTORY_CHARS:
        text = "…[earlier turns omitted]…\n" + text[-_MAX_HISTORY_CHARS:]
    return text


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines:
        lines = lines[1:]
    body = "\n".join(lines).strip()
    if body.endswith("```"):
        body = body[:-3].rstrip()
    return body


def parse_action_input(raw: str) -> dict:
    """Parse the JSON object after ``Action Input:``.

    Raises:
        ValueError: the payload is not a JSON object (or a Python dict literal).
    """
    candidate = _strip_code_fence(raw)
    if not candidate:
        return {}
    decoder = json.JSONDecoder()
    for text in (candidate, candidate[candidate.find("{"):] if "{" in candidate else ""):
        if not text:
            continue
        try:
            value, _end = decoder.raw_decode(text)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
        raise ValueError("Action Input must be a JSON object, not a scalar or array")

    # Some models emit a Python dict with single quotes; accept it as a fallback.
    try:
        value = ast.literal_eval(candidate)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Action Input must be a JSON object")
    return value


def extract_final_answer(text: str) -> str | None:
    matches = list(re.finditer(r"(?im)^\s*Final\s*Answer\s*:\s*", text))
    if not matches:
        return None
    answer = text[matches[-1].end():].strip()
    return answer or None


def extract_action(text: str) -> tuple[str, str] | None:
    """Return ``(tool_name, raw_action_input)`` or ``None``.

    Use the *last* Action marker: some models echo the scratchpad, and the last
    step is the one that has not been executed yet.
    """
    actions = list(re.finditer(r"(?im)^\s*Action\s*:\s*(.+?)\s*$", text))
    if not actions:
        return None
    action = actions[-1]
    name = action.group(1).strip().strip("`'\"")
    # Some models write "tool(arg=...)" instead of a bare tool name.
    name = re.split(r"[\s(]", name, maxsplit=1)[0].strip()
    if not name:
        return None
    remaining = text[action.end():]
    input_match = re.search(r"(?im)^\s*Action\s*Input\s*:\s*", remaining)
    raw = remaining[input_match.end():].strip() if input_match else "{}"
    return name, raw


def extract_thought(text: str) -> str:
    matches = list(
        re.finditer(
            r"(?is)\bThought\s*:\s*(.*?)(?=\bAction\s*:|\bFinal\s*Answer\s*:|\Z)",
            text,
        )
    )
    if not matches:
        return ""
    return matches[-1].group(1).strip()[:_MAX_THOUGHT_CHARS]


class ReActAgent:
    """A synchronous ReAct loop over LangChain tools.

    Args:
        model: chat model with an ``invoke`` method.
        tools: LangChain ``BaseTool`` instances.
        system_prompt: English base prompt (workspace boundary, tool rules).
        approval: optional approval gate; write/exec tools are gated here.
        max_model_calls: model calls allowed in one turn.
        max_tool_calls: tool executions allowed in one turn.
        model_max_retries: retries for transient model failures.
        max_output_chars: observation budget passed back to the model.
    """

    def __init__(
        self,
        model,
        tools: Sequence,
        system_prompt: str = "",
        *,
        approval: ApprovalGate | None = None,
        max_model_calls: int = 8,
        max_tool_calls: int = 15,
        model_max_retries: int = 2,
        max_output_chars: int = 20000,
    ) -> None:
        self.model = model
        self.tools = list(tools)
        self.tools_by_name: dict[str, Any] = {
            str(getattr(tool, "name", "")): tool for tool in self.tools
        }
        self.system_prompt = (
            f"{system_prompt.strip()}\n\n{REACT_SYSTEM_PROMPT}".strip()
            if system_prompt
            else REACT_SYSTEM_PROMPT
        )
        self.approval = approval
        self.max_model_calls = max(1, int(max_model_calls))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.model_max_retries = max(0, int(model_max_retries))
        self.max_output_chars = max(1000, int(max_output_chars))

    # ---- public interface expected by ChatSession ----
    def invoke(self, payload: Mapping[str, Any]) -> dict[str, list[BaseMessage]]:
        messages = list(payload.get("messages") or [])
        finished = messages
        for kind, data in self._run(messages, emit=False):
            if kind == "values":
                finished = list(data["messages"])
        return {"messages": finished}

    def stream(self, payload: Mapping[str, Any], stream_mode=None) -> Iterator[tuple[str, Any]]:
        messages = list(payload.get("messages") or [])
        yield from self._run(messages, emit=True)

    # ---- loop ----
    def _run(
        self,
        messages: list[BaseMessage],
        *,
        emit: bool,
    ) -> Iterator[tuple[str, Any]]:
        question = last_user_text(messages)
        # Remove the latest user message from the history section: it is rendered
        # separately as "Latest user request" to avoid duplication.
        last_user_index = next(
            (index for index in range(len(messages) - 1, -1, -1)
             if _message_type(messages[index]) == "human"),
            None,
        )
        history_messages = (
            messages[:last_user_index] + messages[last_user_index + 1:]
            if last_user_index is not None
            else messages
        )
        history = format_history(history_messages)
        scratchpad = ""
        tool_calls = 0

        for _step in range(self.max_model_calls):
            prompt = build_react_prompt(
                tools=self.tools,
                history=history,
                question=question,
                scratchpad=scratchpad,
            )
            try:
                response = self._ask_model(prompt)
            except Exception as exc:  # exhausted retries
                logger.exception("ReAct model call failed")
                final = f"I could not reach the model: {exc}"
                messages.append(AIMessage(content=final))
                if emit:
                    yield ("messages", (AIMessageChunk(content=final), None))
                yield ("values", {"messages": list(messages)})
                return

            text = self._text_of(response).strip()
            if not text:
                final = "The model returned an empty response."
                messages.append(AIMessage(content=final))
                if emit:
                    yield ("messages", (AIMessageChunk(content=final), None))
                yield ("values", {"messages": list(messages)})
                return

            final = extract_final_answer(text)
            if final is not None:
                messages.append(AIMessage(content=final))
                if emit:
                    yield ("messages", (AIMessageChunk(content=final), None))
                yield ("values", {"messages": list(messages)})
                return

            action = extract_action(text)
            if action is None:
                # No Action and no Final Answer: treat the raw output as the answer
                # rather than looping forever.
                messages.append(AIMessage(content=text))
                if emit:
                    yield ("messages", (AIMessageChunk(content=text), None))
                yield ("values", {"messages": list(messages)})
                return

            name, raw_input = action
            thought = extract_thought(text)
            if emit and thought:
                # Keep reasoning separate from the final answer delta: the UI can
                # show it as a work-step preview without polluting the answer.
                yield ("thought", thought)

            tool = self.tools_by_name.get(name) or self.tools_by_name.get(name.lower())
            if tool is None:
                available = ", ".join(sorted(self.tools_by_name)) or "(none)"
                scratchpad += (
                    f"Thought: {thought}\nAction: {name}\nAction Input: {raw_input}\n"
                    f"Observation: {tool_error('unknown_tool', f'Unknown tool: {name}.', hint=f'Choose one of: {available}')}\n"
                )
                continue

            try:
                args = parse_action_input(raw_input)
            except ValueError as exc:
                scratchpad += (
                    f"Thought: {thought}\nAction: {name}\nAction Input: {raw_input}\n"
                    f"Observation: {tool_error('bad_action_input', f'Invalid Action Input: {exc}', hint='Return one valid JSON object.')}\n"
                )
                continue

            tool_calls += 1
            if tool_calls > self.max_tool_calls:
                final = (
                    "I stopped because the tool-call limit was reached. "
                    "Please narrow the request or continue in a new turn."
                )
                messages.append(AIMessage(content=final))
                if emit:
                    yield ("messages", (AIMessageChunk(content=final), None))
                yield ("values", {"messages": list(messages)})
                return

            call_id = f"call_{_step}_{uuid4().hex[:6]}"
            call = {"name": tool.name, "args": args, "id": call_id}
            messages.append(AIMessage(content=thought, tool_calls=[call]))
            if emit:
                yield ("values", {"messages": list(messages)})

            blocked = self._gate_tool(tool.name, args, call_id)
            if blocked is not None:
                observation = message_text(blocked)
                messages.append(blocked)
            else:
                observation = self._execute_tool(tool, args)
                messages.append(
                    ToolMessage(
                        content=observation,
                        tool_call_id=call_id,
                        name=tool.name,
                    )
                )
            if emit:
                yield ("values", {"messages": list(messages)})

            scratchpad += (
                f"Thought: {thought}\n"
                f"Action: {tool.name}\n"
                f"Action Input: {json.dumps(args, ensure_ascii=False)}\n"
                f"Observation: {observation}\n"
            )

        final = (
            "I stopped because the model-call limit was reached. "
            "Please narrow the request or continue in a new turn."
        )
        messages.append(AIMessage(content=final))
        if emit:
            yield ("messages", (AIMessageChunk(content=final), None))
        yield ("values", {"messages": list(messages)})

    # ---- model ----
    def _ask_model(self, prompt: str):
        last_error: Exception | None = None
        for attempt in range(self.model_max_retries + 1):
            try:
                return self.model.invoke(
                    [
                        SystemMessage(content=self.system_prompt),
                        HumanMessage(content=prompt),
                    ]
                )
            except Exception as exc:
                last_error = exc
                if attempt < self.model_max_retries:
                    logger.warning("ReAct model retry %s/%s: %s", attempt + 1, self.model_max_retries, exc)
                    continue
                break
        raise last_error or RuntimeError("model call failed")

    @staticmethod
    def _text_of(response: Any) -> str:
        if isinstance(response, str):
            return response
        return message_text(response)

    # ---- tools ----
    def _gate_tool(self, name: str, args: dict, call_id: str) -> ToolMessage | None:
        gate = self.approval
        if gate is None or not gate.policy.needs_approval(name):
            return None
        if gate.policy.is_allowed(name, args):
            return None

        target, summary, preview = describe_call(name, args)
        request = ApprovalRequest(
            id=uuid4().hex[:12],
            tool=name,
            risk=gate.policy.risk_of(name),
            target=target,
            summary=summary,
            preview=preview,
            args=dict(args),
        )
        call = {"name": name, "args": args, "id": call_id}
        try:
            decision = gate.ask(request)
        except Exception:
            logger.exception("ReAct approval request failed; denying")
            return denied_tool_message(call)
        if decision == "deny":
            return denied_tool_message(call)
        if decision == "always":
            gate.policy.remember(name, args)
        return None

    def _execute_tool(self, tool: Any, args: dict) -> str:
        try:
            result = tool.invoke(args)
        except Exception as exc:
            return tool_error("tool_failed", f"Tool {tool.name} failed: {exc}")
        text = result if isinstance(result, str) else str(result)
        return truncate(text, self.max_output_chars, keep_tail=True)
