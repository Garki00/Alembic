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
    describe_steps,
    policy_blocked_message,
)
from agents.compaction import is_checkpoint_text
from agents.context import message_text
from prompts.react import REACT_SYSTEM_PROMPT, build_react_prompt
from tools.catalog import DEFERRED_TOOL_CALL_NAME
from tools.command_policy import assess_command
from tools.errors import tool_error, truncate

logger = logging.getLogger(__name__)

_MAX_HISTORY_MESSAGES = 24
_MAX_HISTORY_CHARS = 10000
_MAX_THOUGHT_CHARS = 600

#: Stop sequences that keep the model from writing the *next* ReAct step
#: (notably ``Observation:``) inside the same completion. Observations must
#: only ever be produced by the loop, never by the model.
_DEFAULT_STOP_SEQUENCES = ("\nObservation:", "\nObservation :")

#: A line the model authored as an observation. Since the system is the only
#: legitimate author, any match inside a completion is a protocol violation.
_OBSERVATION_LINE = re.compile(r"(?im)^[ \t]*Observation\s*:")
_ACTION_LINES = re.compile(r"(?im)^\s*Action\s*:\s*(.+?)\s*$")

_PROTOCOL_HINT = (
    "Reply with exactly one Thought + one Action + one Action Input, then stop "
    "and wait for the system Observation; or reply with a standalone Final Answer."
)


def _protocol_note(reason: str) -> str:
    """Scratchpad entry that tells the model how to repair a malformed step."""
    return (
        "Thought: [format correction] I must follow the ReAct contract exactly.\n"
        f"Observation: {tool_error('protocol_violation', reason, hint=_PROTOCOL_HINT)}\n"
    )


def _count_actions(text: str) -> int:
    return len(list(_ACTION_LINES.finditer(text)))


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
            if is_checkpoint_text(text) or text.startswith("【历史对话摘要】"):
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
        max_protocol_errors: malformed steps tolerated before giving up.
        stop_sequences: generation stops that prevent the model from writing
            the next step (``Observation:``) itself.
    """

    #: This agent emits its own ``thought`` events; ``ChatSession`` must not
    #: derive them a second time from AI messages that carry tool calls.
    emits_thought_events = True

    def __init__(
        self,
        model,
        tools: Sequence,
        system_prompt: str = "",
        *,
        approval: ApprovalGate | None = None,
        activation=None,
        basic_names: Sequence[str] = (),
        extended_names: Sequence[str] = (),
        meta_names: Sequence[str] = (),
        deferred_names: Sequence[str] = (),
        max_model_calls: int = 8,
        max_tool_calls: int = 15,
        model_max_retries: int = 2,
        max_output_chars: int = 20000,
        max_protocol_errors: int = 2,
        stop_sequences: Sequence[str] | None = None,
        admission=None,
        admission_cancel=None,
    ) -> None:
        self.model = model
        self.admission = admission
        self.admission_cancel = admission_cancel
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
        self.activation = activation
        self.basic_names = frozenset(basic_names)
        self.extended_names = frozenset(extended_names)
        self.meta_names = frozenset(meta_names)
        self.deferred_names = frozenset(deferred_names) or self.extended_names
        # Only catalog-managed agents hide tools; custom tool lists in tests or
        # embedding code remain fully visible.
        self._catalog_managed = bool(
            activation is not None
            and (self.basic_names or self.extended_names or self.meta_names)
        )
        self.max_model_calls = max(1, int(max_model_calls))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.model_max_retries = max(0, int(model_max_retries))
        self.max_output_chars = max(1000, int(max_output_chars))
        self.max_protocol_errors = max(0, int(max_protocol_errors))
        self.stop_sequences = (
            tuple(stop_sequences) if stop_sequences is not None else _DEFAULT_STOP_SEQUENCES
        )

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

    def _visible_names(self) -> set[str]:
        if not self._catalog_managed:
            return set(self.tools_by_name)
        # Deferred schemas are never part of the prompt; the model loads them
        # through the search control and executes them through the call control.
        return set(self.basic_names | self.meta_names)

    def _visible_tools(self) -> list[Any]:
        names = self._visible_names()
        return [tool for tool in self.tools if getattr(tool, "name", "") in names]

    def _is_visible(self, name: str) -> bool:
        return not self._catalog_managed or name in self._visible_names()

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
        protocol_errors = 0

        for _step in range(self.max_model_calls):
            prompt = build_react_prompt(
                tools=self._visible_tools(),
                history=history,
                question=question,
                scratchpad=scratchpad,
                deferred=self.deferred_names,
            )
            try:
                response = self._ask_model(prompt)
            except Exception as exc:  # exhausted retries
                logger.exception("ReAct model call failed")
                yield from self._finish(messages, f"I could not reach the model: {exc}", emit)
                return

            text = self._text_of(response).strip()
            logger.debug("ReAct step %s raw output: %s", _step, text)
            if not text:
                yield from self._finish(messages, "The model returned an empty response.", emit)
                return

            # The model must never author observations: the loop is the only
            # legitimate source. A model that invents one has not actually run
            # the tool, so its "result" must not be trusted.
            if _OBSERVATION_LINE.search(text):
                protocol_errors += 1
                logger.warning("ReAct protocol violation: model wrote an Observation itself")
                if protocol_errors > self.max_protocol_errors:
                    yield from self._finish(messages, self._protocol_failure(), emit)
                    return
                scratchpad += _protocol_note(
                    "You wrote an Observation yourself. Only the system may append "
                    "Observations; never copy or invent one."
                )
                continue

            action = extract_action(text)
            final = extract_final_answer(text)

            # A step is either an Action or a Final Answer, never both. Without
            # this guard a model that emits the whole loop in one message (with a
            # fabricated Observation) has its action silently discarded: the
            # final answer is accepted while no tool ever ran.
            if action is not None and final is not None:
                protocol_errors += 1
                logger.warning("ReAct protocol violation: Action and Final Answer in one step")
                if protocol_errors > self.max_protocol_errors:
                    yield from self._finish(messages, self._protocol_failure(), emit)
                    return
                scratchpad += _protocol_note(
                    "You wrote both an Action and a Final Answer in one step. "
                    "Run the action first and wait for the Observation; only then answer."
                )
                continue

            if final is not None:
                yield from self._finish(messages, final, emit)
                return

            if action is None:
                protocol_errors += 1
                if protocol_errors > self.max_protocol_errors:
                    # Weak model that answers in plain prose: keep the graceful
                    # fallback instead of failing the turn.
                    yield from self._finish(messages, text, emit)
                    return
                scratchpad += _protocol_note(
                    "Your output contained neither an Action nor a Final Answer."
                )
                continue

            if _count_actions(text) > 1:
                protocol_errors += 1
                logger.warning("ReAct protocol violation: multiple Actions in one step")
                if protocol_errors > self.max_protocol_errors:
                    yield from self._finish(messages, self._protocol_failure(), emit)
                    return
                scratchpad += _protocol_note(
                    "You wrote more than one Action in one step. Emit exactly one."
                )
                continue

            name, raw_input = action
            thought = extract_thought(text)
            if emit and thought:
                # Keep reasoning separate from the final answer delta: the UI can
                # show it as a work-step preview without polluting the answer.
                yield ("thought", thought)

            try:
                args = parse_action_input(raw_input)
            except ValueError as exc:
                scratchpad += (
                    f"Thought: {thought}\nAction: {name}\nAction Input: {raw_input}\n"
                    f"Observation: {tool_error('bad_action_input', f'Invalid Action Input: {exc}', hint='Return one valid JSON object.')}\n"
                )
                continue

            tool = self.tools_by_name.get(name) or self.tools_by_name.get(name.lower())
            if name in self.deferred_names and not self._is_visible(name):
                hint = f'Call tools_search with "select:{name}" first, then call deferred_tool_call.'
                scratchpad += (
                    f"Thought: {thought}\nAction: {name}\nAction Input: {raw_input}\n"
                    f"Observation: {tool_error('tool_not_loaded', f'Tool {name} is not loaded.', hint=hint)}\n"
                )
                continue
            if tool is not None and not self._is_visible(tool.name):
                scratchpad += (
                    f"Thought: {thought}\nAction: {name}\nAction Input: {raw_input}\n"
                    f"Observation: {tool_error('tool_unavailable_this_turn', f'Tool {name} cannot run in this turn.', hint='Choose another available tool.')}\n"
                )
                continue
            if tool is None:
                available = ", ".join(sorted(self._visible_names())) or "(none)"
                scratchpad += (
                    f"Thought: {thought}\nAction: {name}\nAction Input: {raw_input}\n"
                    f"Observation: {tool_error('unknown_tool', f'Unknown tool: {name}.', hint=f'Choose one of: {available}')}\n"
                )
                continue

            exec_tool = tool
            exec_name = tool.name
            exec_args = args
            if name == DEFERRED_TOOL_CALL_NAME:
                inner_name = str(args.get("name") or "").strip()
                inner_input = args.get("input")
                if inner_input is None:
                    inner_input = {}
                if not inner_name:
                    scratchpad += self._observation(thought, name, raw_input, tool_error("empty_tool_name", "name must not be empty."))
                    continue
                if not isinstance(inner_input, dict):
                    scratchpad += self._observation(thought, name, raw_input, tool_error("bad_deferred_input", "input must be a JSON object."))
                    continue
                if inner_name not in self.deferred_names:
                    scratchpad += self._observation(
                        thought, name, raw_input,
                        tool_error("not_a_deferred_tool", f"{inner_name} is not a deferred tool."),
                    )
                    continue
                active = self.activation.snapshot() if self.activation is not None else frozenset()
                if inner_name not in active:
                    scratchpad += self._observation(
                        thought, name, raw_input,
                        tool_error(
                            "schema_not_loaded",
                            f"The schema of {inner_name} has not been loaded in this session.",
                            hint=f'Call tools_search with "select:{inner_name}" first.',
                        ),
                    )
                    continue
                resolved = self.tools_by_name.get(inner_name)
                if resolved is None:
                    scratchpad += self._observation(
                        thought, name, raw_input,
                        tool_error("tool_unavailable", f"{inner_name} is not available."),
                    )
                    continue
                exec_tool, exec_name, exec_args = resolved, inner_name, inner_input

            tool_calls += 1
            if tool_calls > self.max_tool_calls:
                final = (
                    "I stopped because the tool-call limit was reached. "
                    "Please narrow the request or continue in a new turn."
                )
                yield from self._finish(messages, final, emit)
                return

            call_id = f"call_{_step}_{uuid4().hex[:6]}"
            call = {"name": name, "args": args, "id": call_id}
            messages.append(AIMessage(content=thought, tool_calls=[call]))
            if emit:
                yield ("values", {"messages": list(messages)})

            blocked = self._gate_tool(exec_name, exec_args, call_id)
            if blocked is not None:
                observation = message_text(blocked)
                messages.append(
                    ToolMessage(
                        content=observation,
                        tool_call_id=call_id,
                        name=name,
                        status="error",
                    )
                )
            else:
                observation = self._execute_tool(exec_tool, exec_args)
                messages.append(
                    ToolMessage(
                        content=observation,
                        tool_call_id=call_id,
                        name=name,
                    )
                )
            if emit:
                yield ("values", {"messages": list(messages)})

            scratchpad += (
                f"Thought: {thought}\n"
                f"Action: {name}\n"
                f"Action Input: {json.dumps(args, ensure_ascii=False)}\n"
                f"Observation: {observation}\n"
            )

        final = (
            "I stopped because the model-call limit was reached. "
            "Please narrow the request or continue in a new turn."
        )
        yield from self._finish(messages, final, emit)

    # ---- loop helpers ----
    @staticmethod
    def _observation(thought: str, name: str, raw_input: str, error: str) -> str:
        return (
            f"Thought: {thought}\nAction: {name}\nAction Input: {raw_input}\n"
            f"Observation: {error}\n"
        )

    def _finish(
        self,
        messages: list[BaseMessage],
        final: str,
        emit: bool,
    ) -> Iterator[tuple[str, Any]]:
        """Append the final answer and emit the terminal events."""
        messages.append(AIMessage(content=final))
        if emit:
            yield ("messages", (AIMessageChunk(content=final), None))
        yield ("values", {"messages": list(messages)})

    def _protocol_failure(self) -> str:
        return (
            "I stopped because the model kept breaking the ReAct format. "
            "Please retry; if it persists, switch AGENT_ENGINE to tool_calling."
        )


    # ---- model ----
    def _ask_model(self, prompt: str):
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=prompt),
        ]
        last_error: Exception | None = None
        for attempt in range(self.model_max_retries + 1):
            try:
                return self._invoke_model(messages)
            except Exception as exc:
                last_error = exc
                if attempt < self.model_max_retries:
                    logger.warning("ReAct model retry %s/%s: %s", attempt + 1, self.model_max_retries, exc)
                    continue
                break
        raise last_error or RuntimeError("model call failed")

    def _invoke_model(self, messages: list[BaseMessage]):
        """Call the model with ReAct stop sequences when it supports them.

        Stopping at ``\\nObservation:`` prevents the model from writing the next
        step itself. Test doubles and a few providers only accept ``invoke``
        without extra kwargs, so fall back to the plain call once.
        """
        if self.admission is not None:
            with self.admission.slot(self.admission_cancel):
                return self._invoke_model_once(messages)
        return self._invoke_model_once(messages)

    def _invoke_model_once(self, messages: list[BaseMessage]):
        if not self.stop_sequences:
            return self.model.invoke(messages)
        try:
            return self.model.invoke(messages, stop=list(self.stop_sequences))
        except TypeError:
            logger.debug("model does not accept stop=; calling without stop sequences")
            return self.model.invoke(messages)

    @staticmethod
    def _text_of(response: Any) -> str:
        if isinstance(response, str):
            return response
        return message_text(response)

    # ---- tools ----
    def _gate_tool(self, name: str, args: dict, call_id: str) -> ToolMessage | None:
        gate = self.approval
        if gate is None:
            return None
        call = {"name": name, "args": args, "id": call_id}
        blocked = gate.policy.precheck(name, args)
        if blocked is not None:
            code, message, hint = blocked
            logger.warning("ReAct blocked tool %s: [%s] %s", name, code, message)
            return policy_blocked_message(call, code, message, hint)

        decision_result = gate.policy.evaluate(name, args)
        if decision_result.behavior == "allow":
            return None
        if decision_result.behavior == "deny":
            return policy_blocked_message(
                call,
                decision_result.code or "blocked_by_policy",
                decision_result.reason or f"`{name}` is blocked by the current permission rules.",
                "Do not retry the same call verbatim. Adjust the permission rules or mode first.",
            )

        target, summary, preview = describe_call(name, args)
        command = args.get("command") if isinstance(args, dict) else None
        risk = assess_command(str(command)) if command is not None else None
        request = ApprovalRequest(
            id=uuid4().hex[:12],
            tool=name,
            risk=gate.policy.risk_of(name),
            target=target,
            summary=summary,
            preview=preview,
            args=dict(args),
            reason=risk.reason if risk and risk.is_elevated else "",
            rule=risk.rule if risk and risk.is_elevated else "",
            steps=describe_steps(name, args),
        )
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
