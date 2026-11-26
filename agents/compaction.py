# agents/compaction.py
"""Cache-preserving context compaction.

The problem with the old strategy
---------------------------------
It summarized by sending a brand-new single-string prompt to the model. That
request shared nothing with the conversation, so the provider had to process
the whole transcript as fresh input; the replacement that came back then
rewrote the head of the history, so the *next* real request also started from
scratch. Two cold requests for every compaction.

The strategy here
-----------------
1. **The summarizer replays the live prefix.** The auxiliary request is built
   as ``system prompt + tools + the exact messages being compacted + one
   trailing instruction``. Those first bytes are identical to the conversation's
   own last request, so the provider serves them from its warm prefix cache and
   only the instruction and the summary are fresh tokens.
2. **The replacement is framed, not paraphrased.** The summary becomes one
   checkpoint message (preamble + ``<compacted-summary>`` block) placed at the
   head of the retained tail. Everything after it stays byte-identical, so the
   next request reuses the cache from the checkpoint onward.
3. **A model-free path runs first.** Oversized tool results inside the region
   are pruned (truncated with an explicit marker) without any model call. When
   pruning alone brings the context back under the threshold, no request is
   made and the history keeps its structure.
4. **A summary that does not shrink the source is rejected.** Replacing a span
   with something longer would both cost tokens and destroy a cacheable prefix
   for nothing, so the original history is kept.
5. **Context-overflow errors recover once.** If the provider rejects a request
   for exceeding the window, the head is compacted aggressively and the request
   is retried, instead of failing the turn.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)

__all__ = [
    "COMPACTION_PREAMBLE",
    "COMPACTION_INSTRUCTION",
    "COMPACTION_INSTRUCTION_KEEP_FIRST",
    "CHECKPOINT_OPEN",
    "CHECKPOINT_CLOSE",
    "LEGACY_SUMMARY_PREFIX",
    "PrefixSummarizer",
    "checkpoint_message",
    "is_checkpoint_text",
    "is_checkpoint_message",
    "first_turn_end",
    "select_shadow_start",
    "select_shadow_start_by_budget",
    "prune_tool_messages",
    "estimate_messages_tokens",
    "is_context_overflow_error",
]

#: Long-standing marker used by older sessions and by the ReAct renderer.
LEGACY_SUMMARY_PREFIX = "[Conversation summary]"

#: First line of every checkpoint message (also its detection marker).
COMPACTION_PREAMBLE = (
    "This is an automatically generated checkpoint condensing an earlier span of the "
    "conversation to free up context. Treat the captured context as established "
    "background and build on it without restating it. Continue the task directly from "
    "the messages that follow, without acknowledging this checkpoint."
)

CHECKPOINT_OPEN = "<compacted-summary>"
CHECKPOINT_CLOSE = "</compacted-summary>"

#: Trailing user instruction of the auxiliary request. Frozen text: a changed
#: instruction is a changed request, which would cost the warm prefix.
COMPACTION_INSTRUCTION = """You are now acting as a compaction engine for this AI coding assistant. Condense the conversation ABOVE into a structured checkpoint that lets another model resume the work with no loss of essential context.

Output EXACTLY the Markdown structure below: keep every section, in order. Use terse bullets, not prose paragraphs. Write "(none)" for an empty section — never drop a section.

## Primary Request and Intent
- [the user's original and evolving goals; quote verbatim where the exact wording matters]

## Key Technical Concepts
- [technologies, frameworks, patterns, and conventions in play]

## Files and Code
- [exact path: why it matters, key changes or snippets]

## Errors and Fixes
- [error: how it was resolved, plus any related user feedback]

## Pending Jobs
- [explicitly requested work not yet completed]

## Current Work
- [precisely what was in progress at this checkpoint]

## Next Step
- [the single next action, directly in line with the most recent request, or "(none)"]

## Critical Context
- [decisions and their rationale, constraints, user preferences, open questions, data needed to continue]

Rules:
- Write concise engineering prose in the language the conversation mostly uses. Preserve exact file paths, commands, error strings, identifiers, numeric values, function signatures, and syntax fragments.
- Capture user feedback and explicit instructions faithfully, especially corrections.
- Do NOT mention this summarization request or that the context was compacted.
- Output only the checkpoint text: do not call any tool or take any other action.
- If the conversation already contains a <compacted-summary> block, it is a PRIOR checkpoint. Do not copy it forward verbatim: preserve still-true facts, drop stale ones, and merge newer information into a single consolidated summary under the same structure.
"""

#: Instruction variant used when the first exchange is preserved verbatim: the
#: checkpoint will be placed after it, so it must not be restated.
COMPACTION_INSTRUCTION_KEEP_FIRST = """You are now acting as a compaction engine for this AI coding assistant. The FIRST user/assistant exchange above is preserved verbatim in the live conversation and appears before this checkpoint, so do not restate it. Condense only the LATER conversation above into a structured checkpoint that lets another model resume the work with no loss of essential context.

Output EXACTLY the Markdown structure below: keep every section, in order. Use terse bullets, not prose paragraphs. Write "(none)" for an empty section — never drop a section.

## Primary Request and Intent
- [the user's goals from the condensed span; quote verbatim where the exact wording matters]

## Key Technical Concepts
- [technologies, frameworks, patterns, and conventions in play]

## Files and Code
- [exact path: why it matters, key changes or snippets]

## Errors and Fixes
- [error: how it was resolved, plus any related user feedback]

## Pending Jobs
- [explicitly requested work not yet completed]

## Current Work
- [precisely what was in progress at this checkpoint]

## Next Step
- [the single next action, directly in line with the most recent request, or "(none)"]

## Critical Context
- [decisions and their rationale, constraints, user preferences, open questions, data needed to continue]

Rules:
- Write concise engineering prose in the language the conversation mostly uses. Preserve exact file paths, commands, error strings, identifiers, numeric values, function signatures, and syntax fragments.
- Capture user feedback and explicit instructions faithfully, especially corrections.
- Do NOT mention this summarization request or that the context was compacted.
- Output only the checkpoint text: do not call any tool or take any other action.
- If the conversation already contains a <compacted-summary> block, it is a PRIOR checkpoint. Do not copy it forward verbatim: preserve still-true facts, drop stale ones, and merge newer information into a single consolidated summary under the same structure.
"""

#: Markers providers use for "this request does not fit the window".
_OVERFLOW_PATTERNS = (
    re.compile(r"context[ _-]?length", re.I),
    re.compile(r"maximum context", re.I),
    re.compile(r"context window", re.I),
    re.compile(r"prompt is too long", re.I),
    re.compile(r"input is too long", re.I),
    re.compile(r"too many tokens", re.I),
    re.compile(r"reduce the length of the (messages|prompt)", re.I),
    re.compile(r"request too large", re.I),
    re.compile(r"exceeds? the model'?s maximum", re.I),
)

#: Tool output truncation marker; shared with the ingestion-time cap in context.py.
TOOL_OUTPUT_TRUNCATED = "…[tool output truncated, original about {tokens} tokens]"

#: Prefix of the marker above, used to detect an already-pruned body.
TOOL_OUTPUT_MARK = "…[tool output truncated"


# ---------------------------------------------------------------------------
# 消息工具（避免与 agents.context 形成循环导入）
# ---------------------------------------------------------------------------
def _message_text(message: Any) -> str:
    from agents.context import message_text

    return message_text(message)


def estimate_messages_tokens(messages: Sequence[BaseMessage]) -> int:
    """Approximate token count of a message span (used only for shrink checks)."""
    from agents.context import estimate_tokens

    return estimate_tokens(messages)


def is_checkpoint_text(text: str) -> bool:
    """Whether a message body is a compaction checkpoint (new or legacy framing)."""
    head = (text or "").lstrip()
    return (
        head.startswith(COMPACTION_PREAMBLE[:40])
        or head.startswith(LEGACY_SUMMARY_PREFIX)
        or CHECKPOINT_OPEN in head[:400]
    )


def is_checkpoint_message(message: Any) -> bool:
    return is_checkpoint_text(_message_text(message))


def checkpoint_message(summary: str) -> HumanMessage:
    """Wrap a summary in the frozen checkpoint framing."""
    body = summary.strip()
    return HumanMessage(
        content=f"{COMPACTION_PREAMBLE}\n\n{CHECKPOINT_OPEN}\n{body}\n{CHECKPOINT_CLOSE}"
    )


# ---------------------------------------------------------------------------
# 前缀回放摘要器
# ---------------------------------------------------------------------------
@dataclass
class PrefixSummarizer:
    """Summarizer that replays the conversation's own prefix.

    ``tools`` must be the exact object list the agent binds for its requests and
    ``system_prompt`` the exact string it sends, otherwise the replay stops
    matching the cached prefix at the first differing byte.

    ``engine="react"`` replays the ReAct rendering instead: its requests carry a
    single rendered transcript, so the replay is that rendering restricted to the
    shadowed messages. It is a prefix of the live request only while the
    renderer's own truncation does not kick in; the tool-calling engine (the
    default) replays byte-for-byte.
    """

    model: Any
    system_prompt: str
    #: Exact tool declarations, or a callable returning them. A callable lets the
    #: summarizer bind the set the last live request actually used, which keeps
    #: the replayed tools block byte-identical when tool visibility changes.
    tools: Any = ()
    engine: str = "tool_calling"
    max_tokens: int | None = None

    #: Tells :class:`agents.context.ChatSession` to use the replay path.
    replays_prefix: bool = True

    def _resolve_tools(self) -> list[Any]:
        tools = self.tools
        if callable(tools):
            try:
                tools = tools()
            except Exception as exc:  # 工具快照失败时退化为不带工具回放
                logger.warning("摘要器无法读取当前工具声明：%s", exc)
                return []
        return list(tools or ())

    def _bind(self, tools: Sequence[Any] | None = None) -> Any:
        model = self.model
        resolved = self._resolve_tools() if tools is None else list(tools)
        if self.engine == "tool_calling" and resolved:
            try:
                model = model.bind_tools(resolved)
            except Exception as exc:  # 工具声明不可绑定时退化为纯文本摘要
                logger.warning("摘要器无法绑定工具声明，将不带工具回放：%s", exc)
        if self.max_tokens:
            try:
                model = model.bind(max_tokens=int(self.max_tokens))
            except Exception as exc:  # 某些提供方不接受该参数
                logger.debug("摘要器无法设置 max_tokens：%s", exc)
        return model

    def replay(
        self,
        shadow: Sequence[BaseMessage],
        prefix: Sequence[BaseMessage] = (),
        instruction: str = COMPACTION_INSTRUCTION,
    ) -> list[BaseMessage]:
        """Build the auxiliary request: live prefix first, instruction last.

        ``prefix`` is the part of the history kept verbatim (the preserved first
        exchange). It is replayed ahead of the condensed span so the request
        still starts with the same bytes as the live conversation.
        """
        if prefix and instruction == COMPACTION_INSTRUCTION:
            # A preserved head must not be restated in the checkpoint.
            instruction = COMPACTION_INSTRUCTION_KEEP_FIRST
        if self.engine == "react":
            # ReAct 的请求是「系统提示词 + 一段渲染后的文本」；回放同样形状，
            # 只是把渲染范围收窄到被遮蔽的历史，前面仍是逐字节相同的前缀。
            from agents.react import format_history

            body = format_history([*prefix, *shadow])
            text = f"{body}\n\n{instruction}" if body else instruction
            return [SystemMessage(content=self.system_prompt), HumanMessage(content=text)]
        return [
            SystemMessage(content=self.system_prompt),
            *prefix,
            *shadow,
            HumanMessage(content=instruction),
        ]

    def summarize(
        self,
        shadow: Sequence[BaseMessage],
        prefix: Sequence[BaseMessage] = (),
        instruction: str = COMPACTION_INSTRUCTION,
    ) -> str:
        """Return the checkpoint body; raise when the model produced no text."""
        response = self._bind().invoke(self.replay(shadow, prefix, instruction))
        text = _message_text(response).strip()
        if not text:
            calls = list(getattr(response, "tool_calls", None) or [])
            if calls:
                raise ValueError("摘要模型尝试调用工具而不是输出摘要")
            raise ValueError("摘要模型返回了空内容")
        return text


# ---------------------------------------------------------------------------
# 区域选择
# ---------------------------------------------------------------------------
def _is_human(message: Any) -> bool:
    return getattr(message, "type", "") == "human"


def first_turn_end(messages: Sequence[BaseMessage]) -> int:
    """Index just past the first complete user/assistant exchange.

    The first turn is the first user message plus the first assistant answer
    that closes it (an assistant message without tool calls). Returns ``0`` when
    the history does not contain a complete first turn yet, in which case
    nothing can be preserved.
    """
    started = False
    for index, message in enumerate(messages):
        kind = getattr(message, "type", "")
        if kind == "human":
            if started:
                return index
            started = True
            continue
        if started and kind == "ai" and not getattr(message, "tool_calls", None):
            if _message_text(message).strip():
                return index + 1
    return 0


def select_shadow_start(
    messages: Sequence[BaseMessage],
    keep_recent: int,
    *,
    prefer_human: bool = True,
    min_start: int = 0,
) -> int:
    """Index where the retained tail starts; everything before it is compacted.

    Three invariants:

    * a tool result is never separated from the assistant message that called it
      (the API rejects an orphan ``tool`` message);
    * when possible the tail starts at a user message, so the model resumes at a
      natural boundary instead of mid-exchange;
    * ``min_start`` (the preserved first exchange) is never crossed.
    """
    if not messages:
        return 0
    start = max(min_start, len(messages) - max(1, keep_recent))
    while start > min_start and isinstance(messages[start], ToolMessage):
        start -= 1
    if prefer_human and start > min_start:
        nxt = next(
            (
                index
                for index in range(start, len(messages))
                if _is_human(messages[index])
            ),
            None,
        )
        if nxt is not None and nxt > start:
            start = nxt
    return max(start, min_start)


def select_shadow_start_by_budget(
    messages: Sequence[BaseMessage],
    budget_tokens: int,
    *,
    prefer_human: bool = True,
    min_start: int = 0,
) -> int:
    """Index where the retained tail starts, measured in tokens.

    Walks backwards until the accumulated estimate exceeds ``budget_tokens``, so
    the retained tail is bounded by context volume rather than message count.
    The same boundary invariants as :func:`select_shadow_start` apply.
    """
    if not messages:
        return 0
    if budget_tokens <= 0:
        return select_shadow_start(messages, 1, prefer_human=prefer_human, min_start=min_start)

    total = 0
    start = len(messages)
    for index in range(len(messages) - 1, max(-1, min_start - 1), -1):
        tokens = estimate_messages_tokens([messages[index]])
        if total + tokens > budget_tokens and index < len(messages) - 1:
            start = index + 1
            break
        total += tokens
        start = index
    start = min(max(start, min_start), len(messages) - 1)
    while start > min_start and isinstance(messages[start], ToolMessage):
        start -= 1
    if prefer_human and start > min_start:
        nxt = next(
            (
                index
                for index in range(start, len(messages))
                if _is_human(messages[index])
            ),
            None,
        )
        if nxt is not None and nxt > start:
            start = nxt
    return max(start, min_start)


# ---------------------------------------------------------------------------
# 无模型的修剪
# ---------------------------------------------------------------------------
def prune_tool_messages(
    messages: Sequence[BaseMessage],
    max_tokens: int,
) -> tuple[list[BaseMessage], int]:
    """Truncate oversized tool bodies before they are either sent or summarized.

    Returns ``(messages, pruned_count)``. Already-short bodies are untouched, so
    calling this repeatedly is a no-op instead of shaving more bytes each time
    (repeated truncation would invalidate the cache again on every turn).
    """
    if max_tokens <= 0:
        return list(messages), 0
    pruned = 0
    result: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            # read_file carries its own byte/token guards and pagination notice.
            if getattr(message, "name", None) == "read_file":
                result.append(message)
                continue
            text = _message_text(message)
            if TOOL_OUTPUT_MARK in text:
                # 已经修剪过：再剪一次只会改动字节、白白打断前缀缓存。
                result.append(message)
                continue
            tokens = estimate_messages_tokens([message])
            if tokens > max_tokens:
                keep = max_tokens * 4
                marker = TOOL_OUTPUT_TRUNCATED.format(tokens=tokens)
                message = message.model_copy(
                    update={"content": f"{text[:keep]}\n{marker}"}
                )
                pruned += 1
        result.append(message)
    return result, pruned


# ---------------------------------------------------------------------------
# 溢出恢复
# ---------------------------------------------------------------------------
def is_context_overflow_error(exc: BaseException | str) -> bool:
    """Whether a provider failure means "the prompt does not fit the window"."""
    text = exc if isinstance(exc, str) else "".join(
        str(part) for part in (getattr(exc, "message", ""), exc)
    )
    return any(pattern.search(text or "") for pattern in _OVERFLOW_PATTERNS)
