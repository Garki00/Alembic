# agents/context.py
"""会话上下文：多轮历史、本地持久化与上下文压缩。

设计要点：

- ``ChatSession`` 持有当前会话的全部消息，每轮把它整体交给 Agent，
  因此多轮对话天然带上下文；
- 历史按会话 ID 保存成 ``memory/<session>.json``，进程重启后可以继续聊；
- 上下文 token 数优先取模型 API 返回的 ``usage``（与计费口径一致，包含系统
  提示词与工具 schema），拿不到时才用本地字符估算兜底；
- 超过阈值（窗口 × 比例 − 预留，或消息条数）时，把较早的对话交给模型压缩成
  一段中文摘要，只保留最近的若干条消息，避免上下文无限增长；
- 两次自动压缩之间至少间隔若干轮，且单条工具输出有 token 预算，防止“压完
  立刻再压”和大输出撑爆窗口；
- 压缩失败（例如摘要请求出错）时降级为“丢弃更早消息 + 明确提示”，
  保证对话仍然可用。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    get_buffer_string,
    messages_from_dict,
    messages_to_dict,
)

from config.settings import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SESSION_ID_UNSAFE = re.compile(r"[^0-9A-Za-z_.\-]+")

#: 压缩摘要消息的固定前缀，方便提示词与调试时识别
SUMMARY_PREFIX = "[Conversation summary]"

_SUMMARY_PROMPT = """Summarize this conversation for continuation.
Keep only: user goals, confirmed facts and decisions, changed files, verification
results, and unresolved next steps. Omit greetings, repetition, raw tool output,
and secrets. Use the conversation language. Output bullets only.

Conversation:
{messages}
"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def message_text(message: Any) -> str:
    """取出消息的纯文本内容（兼容 str 与 content blocks 两种形式）。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content or "")


def reasoning_text(message: Any) -> str:
    """提取供应商返回的可见推理文本，兼容常见 OpenAI/DeepSeek 结构。"""
    parts: list[str] = []

    def append(value: Any) -> None:
        if isinstance(value, str):
            if value:
                parts.append(value)
            return
        if isinstance(value, list):
            for item in value:
                append(item)
            return
        if not isinstance(value, dict):
            return
        block_type = str(value.get("type") or "").lower()
        if block_type in {"reasoning", "reasoning_content", "thinking", "summary_text"}:
            for key in ("text", "content", "reasoning", "reasoning_content", "summary"):
                append(value.get(key))
        elif "summary" in value:
            append(value.get("summary"))

    for container_name in ("additional_kwargs", "response_metadata"):
        container = getattr(message, container_name, None)
        if isinstance(container, dict):
            for key in ("reasoning_content", "reasoning", "thinking", "reasoning_text"):
                append(container.get(key))

    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            append(block)

    unique = [part for part in dict.fromkeys(parts) if part.strip()]
    if len(unique) == 1:
        return unique[0]
    return "\n\n".join(part.strip() for part in unique)


def _approx_tokens(text: str) -> int:
    """粗略估算 token 数：中日韩字符按 1 个算，其余按 4 字符约 1 个 token。"""
    if not text:
        return 0
    cjk = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff":
            cjk += 1
    other = len(text) - cjk
    return cjk + max(0, other // 4)


def estimate_tokens(messages: Sequence[BaseMessage]) -> int:
    """本地近似估算（仅在拿不到 API 用量时的兜底）。

    按字符分布粗算：中日韩字符 1 个 token，其他约 4 字符 1 个 token，再加
    每条消息的框架开销。它不含系统提示词和工具 schema，误差可能有 ±30%，
    所以只作兜底，正常情况下用 :func:`context_tokens` 的 API 用量结果。
    """
    total = 0
    for message in messages:
        text = message_text(message)
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            text += " " + json.dumps(tool_calls, ensure_ascii=False)
        total += _approx_tokens(text) + 4  # 每条消息的框架开销
    return total


def _usage_tokens(message: AIMessage) -> int | None:
    """取 AI 消息里 provider 返回的用量（含系统提示、工具 schema 与回复）。"""
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int) and total > 0:
        return total
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    if input_tokens or output_tokens:
        return int(input_tokens) + int(output_tokens)
    return None


def context_tokens(messages: Sequence[BaseMessage]) -> tuple[int, str]:
    """估算当前上下文的 token 数，返回 ``(tokens, 来源)``。

    优先用 provider 上报的用量（``usage_metadata.total_tokens``）：它是服务端
    按真实 tokenizer 算出来的，包含系统提示词、工具 schema 与本次回复；
    再把它之后的增量消息（工具结果、新提问）用本地估算补上。
    没有用量信息时退回 :func:`estimate_tokens`。
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, AIMessage):
            continue
        reported = _usage_tokens(message)
        if reported is None:
            continue
        tail = messages[index + 1 :]
        return reported + estimate_tokens(tail), "provider"
    return estimate_tokens(messages), "estimate"


TOOL_OUTPUT_TRUNCATED = "…[工具输出已截断，原始约 {tokens} tokens]"


def cap_tool_outputs(
    messages: Sequence[BaseMessage],
    max_tokens: int,
) -> list[BaseMessage]:
    """把超过预算的工具输出截断后再写入历史（类似 Codex 的 tool_output_token_limit）。

    长日志、大文件内容整段塞进上下文会迅速撑满窗口，这里按 token 预算截断，
    并留下明确标记，后续需要细节时让模型重新调用工具读取。
    """
    if max_tokens <= 0:
        return list(messages)

    capped: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            text = message_text(message)
            tokens = _approx_tokens(text)
            if tokens > max_tokens:
                # 按估算值换算成字符预算，留出标记的空间
                keep = max_tokens * 4
                marker = TOOL_OUTPUT_TRUNCATED.format(tokens=tokens)
                message = message.model_copy(update={"content": f"{text[:keep]}\n{marker}"})
        capped.append(message)
    return capped


def _final_text(messages: Sequence[BaseMessage]) -> str:
    """取最后一条有正文的 AI 回复。"""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = message_text(message).strip()
            if text:
                return text
    return ""


# ---------------------------------------------------------------------------
# 历史持久化
# ---------------------------------------------------------------------------
def session_slug(session_id: str) -> str:
    """把会话 ID 变成安全的文件名，防止路径穿越。"""
    raw = (session_id or "default").strip() or "default"
    slug = _SESSION_ID_UNSAFE.sub("_", raw).strip("._-")[:64] or "default"
    if slug != raw:
        import hashlib

        slug += "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return slug


@dataclass
class SessionMeta:
    """会话元信息（同时写入 json，供 /sessions 展示）。"""

    session_id: str
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    turns: int = 0
    compactions: int = 0
    message_count: int = 0
    last_compaction_turn: int = 0
    workspace_id: str = ""


class SessionStore:
    """把会话历史保存成 JSON 文件。"""

    def __init__(self, directory: str | Path | None = None) -> None:
        path = Path(directory or settings.memory_dir).expanduser()
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        self.directory = path

    def path(self, session_id: str) -> Path:
        return self.directory / f"{session_slug(session_id)}.json"

    def load(self, session_id: str) -> tuple[list[BaseMessage], SessionMeta]:
        path = self.path(session_id)
        if not path.exists():
            return [], SessionMeta(session_id=session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            messages = messages_from_dict(payload.get("messages", []))
            meta = SessionMeta(
                session_id=payload.get("session_id", session_id),
                created_at=payload.get("created_at", _now()),
                updated_at=payload.get("updated_at", _now()),
                turns=int(payload.get("turns", 0)),
                compactions=int(payload.get("compactions", 0)),
                message_count=len(messages),
                last_compaction_turn=int(payload.get("last_compaction_turn", 0)),
                workspace_id=str(payload.get("workspace_id", "")),
            )
            return messages, meta
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("读取会话 %s 失败，按新会话处理：%s", session_id, exc)
            return [], SessionMeta(session_id=session_id)

    def save(self, session_id: str, messages: Sequence[BaseMessage], meta: SessionMeta) -> bool:
        payload = {
            "session_id": meta.session_id,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
            "turns": meta.turns,
            "compactions": meta.compactions,
            "message_count": len(messages),
            "last_compaction_turn": meta.last_compaction_turn,
            "workspace_id": meta.workspace_id,
            "messages": messages_to_dict(list(messages)),
        }
        path = self.path(session_id)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError as exc:  # 磁盘只读、权限不足等，不影响本轮对话
            logger.warning("保存会话 %s 失败：%s", session_id, exc)
            return False

    def delete(self, session_id: str) -> bool:
        path = self.path(session_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("删除会话 %s 失败：%s", session_id, exc)
            return False

    def list_sessions(self) -> list[SessionMeta]:
        if not self.directory.exists():
            return []
        sessions: list[SessionMeta] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            sessions.append(
                SessionMeta(
                    session_id=payload.get("session_id", path.stem),
                    created_at=payload.get("created_at", ""),
                    updated_at=payload.get("updated_at", ""),
                    turns=int(payload.get("turns", 0)),
                    compactions=int(payload.get("compactions", 0)),
                    message_count=int(payload.get("message_count", 0)),
                    workspace_id=str(payload.get("workspace_id", "")),
                )
            )
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions


# ---------------------------------------------------------------------------
# 上下文压缩
# ---------------------------------------------------------------------------
def _summarize(model, messages: Sequence[BaseMessage]) -> str:
    prompt = _SUMMARY_PROMPT.format(messages=get_buffer_string(list(messages)))
    response = model.invoke(prompt)
    text = message_text(response).strip()
    if not text:
        raise ValueError("摘要模型返回了空内容")
    return text


def _keep_start_index(messages: Sequence[BaseMessage], keep_recent: int) -> int:
    """计算保留区起点，避免把工具结果和触发它的 AI 消息拆开。"""
    start = max(0, len(messages) - max(1, keep_recent))
    while start > 0 and isinstance(messages[start], ToolMessage):
        start -= 1
    return start


def compact_messages(
    messages: Sequence[BaseMessage],
    summarizer,
    keep_recent: int = 8,
) -> tuple[list[BaseMessage], str | None]:
    """压缩历史消息。

    Returns:
        ``(新消息列表, 摘要文本)``。没有可压缩内容或摘要失败时返回
        ``(原列表, None)`` 或降级后的列表。
    """
    start = _keep_start_index(messages, keep_recent)
    if start <= 0:
        return list(messages), None

    older = list(messages[:start])
    recent = list(messages[start:])
    try:
        summary = _summarize(summarizer, older)
    except Exception as exc:  # 摘要失败不应中断对话
        logger.warning("上下文摘要失败，降级为省略更早消息：%s", exc)
        note = HumanMessage(
            content=f"（更早的 {len(older)} 条对话因压缩失败被省略，如需细节请重新询问。）"
        )
        return [note, *recent], None

    return [HumanMessage(content=f"{SUMMARY_PREFIX}\n{summary}"), *recent], summary


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@dataclass
class TurnResult:
    """一轮问答的结果。"""

    text: str
    new_messages: list[BaseMessage]
    context_tokens: int
    compacted: bool = False
    token_source: str = "estimate"


#: 两次自动压缩之间至少间隔的轮数，避免压缩后立刻二次触发
MIN_TURNS_BETWEEN_COMPACTIONS = 2


class ChatSession:
    """带持久化与自动压缩的多轮会话。"""

    def __init__(
        self,
        agent,
        session_id: str | None = None,
        *,
        store: SessionStore | None = None,
        summarizer=None,
        max_context_tokens: int | None = None,
        max_context_messages: int | None = None,
        keep_recent: int | None = None,
        model_context_window: int | None = None,
        compact_reserve_tokens: int | None = None,
        trigger_ratio: float | None = None,
        max_tool_output_tokens: int | None = None,
        workspace_id: str = "",
        hydrate: bool = True,
    ) -> None:
        self.agent = agent
        self.session_id = session_id or settings.session_id
        self.store = store or SessionStore()
        self.summarizer = summarizer

        self.model_context_window = model_context_window or settings.model_context_window
        self.compact_reserve_tokens = (
            compact_reserve_tokens or settings.compact_reserve_tokens
        )
        ratio = trigger_ratio or settings.compact_trigger_ratio
        # 压缩阈值 = 窗口 × 触发比例 − 预留（给回复和摘要请求本身留余量）
        self.max_context_tokens = (
            max_context_tokens
            or settings.max_context_tokens
            or max(1, int(self.model_context_window * ratio) - self.compact_reserve_tokens)
        )
        self.max_context_messages = max_context_messages or settings.max_context_messages
        self.keep_recent = keep_recent or settings.keep_recent_messages
        self.max_tool_output_tokens = (
            max_tool_output_tokens or settings.max_tool_output_tokens
        )

        self.messages: list[BaseMessage] = []
        self.meta = SessionMeta(session_id=self.session_id)
        if hydrate:
            self.messages, self.meta = self.store.load(self.session_id)
        # 工作区归属以调用方为准（会话文件里的旧值可能为空）
        if workspace_id:
            self.meta.workspace_id = workspace_id

    # ---- 查询 ----
    def __len__(self) -> int:
        return len(self.messages)

    def usage(self) -> tuple[int, str]:
        """当前上下文的 ``(token 数, 来源)``。"""
        return context_tokens(self.messages)

    def needs_compaction(self) -> bool:
        tokens, _source = self.usage()
        return (
            len(self.messages) > self.max_context_messages
            or tokens > self.max_context_tokens
        )

    def stats(self) -> dict[str, Any]:
        tokens, source = self.usage()
        return {
            "session_id": self.session_id,
            "turns": self.meta.turns,
            "messages": len(self.messages),
            "tokens": tokens,
            "token_source": source,
            "compactions": self.meta.compactions,
            "max_tokens": self.max_context_tokens,
            "max_messages": self.max_context_messages,
            "model_context_window": self.model_context_window,
            "reserve_tokens": self.compact_reserve_tokens,
            "updated_at": self.meta.updated_at,
        }

    def recent(self, limit: int = 10) -> list[BaseMessage]:
        return self.messages[-limit:] if limit > 0 else list(self.messages)

    # ---- 主流程 ----
    def ask(self, question: str) -> TurnResult:
        """发送一轮提问，返回回复与本轮新增的消息。"""
        compacted = self._begin_turn(question)
        history = list(self.messages)
        try:
            messages = self._invoke(history)
        except BaseException:
            # 本轮失败：撤销刚追加的提问，保持历史一致
            self.messages = history[:-1]
            raise
        return self._finish_turn(history, messages, compacted)

    def ask_stream(self, question: str, *, internal: bool = False):
        """流式提问，依次产出事件：

        - ``("compacted", None)``：本轮开始前压缩了上下文；
        - ``("thought", 文本)``：ReAct 的中间推理（工作步骤预览，不属于最终答案）；
        - ``("delta", 文本)``：模型输出的增量文本；
        - ``("tool", {"id", "name", "args"})``：模型发起了一次工具调用；
        - ``("tool_result", {"id", "name", "content"})``：工具返回的结果；
        - ``("done", TurnResult)``：本轮结束（已写入历史与磁盘）。

        ``internal`` 用于后台任务完成等系统触发的续跑：消息仍进入模型上下文，
        但前端可以在历史里隐藏它，不把它当成用户输入。
        """
        compacted = self._begin_turn(question, internal=internal)
        if compacted:
            yield ("compacted", None)

        history = list(self.messages)
        emitted_tools: set[str] = set()
        emitted_results: set[str] = set()
        emitted_thoughts: set[str] = set()
        # Reasoning chunks are accumulated in one string. Providers do not
        # always give streamed chunks a stable id, so a per-id buffer cannot
        # tell which AI message a value belongs to; the turn-level suffix check
        # below does.
        streamed_reasoning = ""
        fallback_reasoning_id = ""
        # ReAct emits its own ``thought`` events. Native tool-calling agents do
        # not, so derive them from AI messages that carry tool calls: that text
        # is the model's reasoning for the action, not part of the final answer.
        derive_step_thoughts = not getattr(self.agent, "emits_thought_events", False)
        messages: list[BaseMessage] | None = None
        try:
            stream = getattr(self.agent, "stream", None)
            if stream is None:  # 不支持流式的 Agent（例如测试替身）
                messages = self._invoke(history)
            else:
                for mode, payload in stream(
                    {"messages": history}, stream_mode=["messages", "values"]
                ):
                    if mode == "thought":
                        if payload:
                            yield (
                                "thought",
                                payload if isinstance(payload, dict) else str(payload),
                            )
                        continue
                    if mode == "messages":
                        chunk, _meta = payload
                        if isinstance(chunk, AIMessageChunk):
                            native_reasoning_delta = reasoning_text(chunk)
                            if native_reasoning_delta:
                                reasoning_id = (
                                    str(getattr(chunk, "id", "") or fallback_reasoning_id)
                                    or f"reasoning-{uuid4().hex[:12]}"
                                )
                                fallback_reasoning_id = reasoning_id
                                streamed_reasoning += native_reasoning_delta
                                yield (
                                    "thought",
                                    {
                                        "id": reasoning_id,
                                        "text": native_reasoning_delta,
                                        "append": True,
                                    },
                                )
                            delta = message_text(chunk)
                            if delta:
                                yield ("delta", delta)
                        continue
                    if mode != "values" or not isinstance(payload, dict):
                        continue
                    current = list(payload.get("messages") or [])
                    messages = current
                    for message in current[len(history):]:
                        if isinstance(message, ToolMessage):
                            result_id = str(
                                getattr(message, "tool_call_id", "")
                                or f"{getattr(message, 'name', '')}:{len(emitted_results)}"
                            )
                            if result_id in emitted_results:
                                continue
                            emitted_results.add(result_id)
                            yield (
                                "tool_result",
                                {
                                    "id": result_id,
                                    "name": getattr(message, "name", "") or "",
                                    "content": message_text(message),
                                },
                            )
                            continue
                        if not isinstance(message, AIMessage):
                            continue
                        calls = list(message.tool_calls or [])
                        native_reasoning = reasoning_text(message).strip()
                        if native_reasoning:
                            # The same reasoning often arrives twice: once as
                            # streamed chunks and once on the final AIMessage.
                            # If the accumulated stream ends with this text, the
                            # UI already has it; just remember it so a content
                            # thought with identical text is not emitted either.
                            already_streamed = streamed_reasoning.strip().endswith(native_reasoning)
                            if already_streamed:
                                emitted_thoughts.add(native_reasoning)
                            elif native_reasoning not in emitted_thoughts:
                                emitted_thoughts.add(native_reasoning)
                                yield ("thought", native_reasoning)
                        if derive_step_thoughts and calls:
                            thought_text = message_text(message).strip()
                            if thought_text and thought_text not in emitted_thoughts:
                                emitted_thoughts.add(thought_text)
                                yield ("thought", thought_text)
                        for call in calls:
                            key = str(call.get("id") or call.get("name"))
                            if key in emitted_tools:
                                continue
                            emitted_tools.add(key)
                            yield (
                                "tool",
                                {
                                    "id": call.get("id"),
                                    "name": call.get("name"),
                                    "args": call.get("args") or {},
                                },
                            )
        except BaseException:
            self.messages = history[:-1]
            raise

        result = self._finish_turn(history, messages or history, compacted)
        yield ("done", result)

    def compact(self, keep_recent: int | None = None) -> bool:
        """立即压缩上下文，返回是否发生了压缩。"""
        if self.summarizer is None:
            raise RuntimeError("当前会话没有配置摘要模型，无法压缩。")
        messages, _summary = compact_messages(
            self.messages,
            self.summarizer,
            keep_recent or self.keep_recent,
        )
        if messages == self.messages:
            return False
        self.messages = messages
        self.meta.compactions += 1
        self.meta.last_compaction_turn = self.meta.turns
        self.meta.updated_at = _now()
        self._persist()
        return True

    def reset(self) -> None:
        """清空当前会话（同时删除磁盘上的历史文件）。"""
        self.messages = []
        self.meta = SessionMeta(session_id=self.session_id)
        self.store.delete(self.session_id)

    # ---- 内部实现 ----
    def _begin_turn(self, question: str, *, internal: bool = False) -> bool:
        text = (question or "").strip()
        if not text:
            raise ValueError("问题不能为空")
        message = HumanMessage(content=text)
        if internal:
            message.additional_kwargs["alembic_internal"] = True
        self.messages.append(message)
        return self._maybe_compact()

    def _invoke(self, history: list[BaseMessage]) -> list[BaseMessage]:
        result = self.agent.invoke({"messages": history})
        messages = list(result.get("messages", [])) if isinstance(result, dict) else []
        return messages or history

    def _finish_turn(
        self,
        history: list[BaseMessage],
        messages: list[BaseMessage],
        compacted: bool,
    ) -> TurnResult:
        # 工具输出超预算的部分截断后再进历史，避免单次大输出撑爆窗口
        messages = cap_tool_outputs(messages, self.max_tool_output_tokens)
        self.messages = messages
        new_messages = messages[len(history):] if len(messages) >= len(history) else []

        self.meta.turns += 1
        self.meta.updated_at = _now()
        self._persist()
        tokens, source = self.usage()
        return TurnResult(
            text=_final_text(messages) or "（模型没有返回内容）",
            new_messages=new_messages,
            context_tokens=tokens,
            compacted=compacted,
            token_source=source,
        )

    def _maybe_compact(self) -> bool:
        if self.summarizer is None or not self.needs_compaction():
            return False
        # 刚压缩过就再压一次通常是徒劳的：摘要本身也会占上下文
        if self.meta.turns - self.meta.last_compaction_turn < MIN_TURNS_BETWEEN_COMPACTIONS:
            return False
        try:
            return self.compact()
        except Exception as exc:  # 压缩失败不影响本轮对话
            logger.warning("自动压缩失败，本轮继续使用完整历史：%s", exc)
            return False

    def _persist(self) -> None:
        self.store.save(self.session_id, self.messages, self.meta)
