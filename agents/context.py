# agents/context.py
"""Session context: multi-turn history, local persistence, and cache-aware compaction.

- ``ChatSession`` holds every message of the current session and hands the whole list
  to the agent each turn, so a multi-turn conversation carries context naturally;
- history is saved per session ID as ``memory/<session>.json``, so a restarted process
  can continue the conversation;
- the context token count prefers the ``usage`` reported by the model API (the same
  basis as billing, including the system prompt and tool schemas) and falls back to a
  local character estimate only when it is unavailable;
- every turn also folds the provider's cache buckets into a per-turn and a per-session
  total (:mod:`agents.usage`), so the cache hit rate is measurable instead of guessed;
- above the threshold (window × ratio − reserve, or a message count) earlier turns are
  replaced by one checkpoint message. The summary request replays the conversation's
  own prefix (system prompt, tool declarations, the exact shadowed messages) so it is
  served from the provider's warm cache, and the retained tail is never rewritten;
- oversized tool results are pruned locally before a summary is requested, and only
  newly produced messages are truncated, so a stable prefix stays byte-identical;
- a provider-confirmed context overflow compacts the head and retries the request once;
- when summarization fails (for example a failed summary request) it degrades to
  dropping older messages with an explicit note, keeping the conversation usable.
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

from agents.compaction import (
    COMPACTION_INSTRUCTION,
    COMPACTION_INSTRUCTION_KEEP_FIRST,
    COMPACTION_PREAMBLE,
    TOOL_OUTPUT_TRUNCATED,
    checkpoint_message,
    first_turn_end,
    is_checkpoint_text,
    is_context_overflow_error,
    prune_tool_messages,
    select_shadow_start,
    select_shadow_start_by_budget,
)
from agents.usage import SessionUsage, TurnUsage, fold_turn_usage
from config.settings import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SESSION_ID_UNSAFE = re.compile(r"[^0-9A-Za-z_.\-]+")

#: 压缩检查点的固定前缀；用它识别摘要消息（旧会话里的旧标记也仍能识别）
SUMMARY_PREFIX = COMPACTION_PREAMBLE

#: 仅用于没有前缀回放能力的旧摘要器（例如测试替身或外部脚本）
_SUMMARY_PROMPT = """Summarize this conversation for continuation.
Keep only: user goals, confirmed facts and decisions, changed files, verification
results, and unresolved next steps. Omit greetings, repetition, raw tool output,
and secrets. Output bullets only.

Conversation:
{messages}
"""

_TITLE_PROMPT = """Write a short title for this conversation.
Rules: at most 6 words or 24 characters; same language as the conversation; no quotes,
no trailing punctuation, no explanations. Output the title only.

User: {user}
Assistant: {assistant}
Title:"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_ms() -> int:
    """Epoch milliseconds for per-message timestamps."""
    return int(datetime.now().timestamp() * 1000)


def message_text(message: Any) -> str:
    """Return the plain text of a message (supports both ``str`` and content blocks)."""
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


def thought_key(text: str) -> str:
    """Deduplication fingerprint for reasoning: ignores provider-side whitespace differences."""
    return re.sub(r"\s+", "", text or "")


def reasoning_text(message: Any) -> str:
    """Extract visible reasoning text returned by the provider, covering common OpenAI/DeepSeek shapes."""
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
    """Rough token estimate: one per CJK character, about one per four other characters."""
    if not text:
        return 0
    cjk = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff":
            cjk += 1
    other = len(text) - cjk
    return cjk + max(0, other // 4)


def estimate_tokens(messages: Sequence[BaseMessage]) -> int:
    """Local approximation (fallback used only when API usage is unavailable).

    Counts roughly by character mix: one token per CJK character, about one token per
    four other characters, plus per-message framing overhead. It excludes the system
    prompt and tool schemas, so the error can reach ±30%; use it only as a fallback,
    normally :func:`context_tokens` returns the API usage.
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
    """Return the provider-reported usage from an AI message (system prompt, tool schemas, and reply)."""
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
    """Estimate the current context tokens and return ``(tokens, source)``.

    Prefers the usage reported by the provider (``usage_metadata.total_tokens``): it is
    computed server-side with the real tokenizer and covers the system prompt, tool
    schemas, and the current reply; messages after it (tool results, new questions) are
    added from the local estimate. Falls back to :func:`estimate_tokens` when no usage
    information is available.
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


def cap_tool_outputs(
    messages: Sequence[BaseMessage],
    max_tokens: int,
) -> list[BaseMessage]:
    """Truncate tool output that exceeds the budget before it enters history (similar to Codex's tool_output_token_limit).

    Long logs and large file contents fill the window quickly, so output is cut to a token
    budget and marked explicitly; the model can call the tool again when it needs details.
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
    """Return the last AI reply that has body text."""
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
    """Turn a session ID into a safe file name to prevent path traversal."""
    raw = (session_id or "default").strip() or "default"
    slug = _SESSION_ID_UNSAFE.sub("_", raw).strip("._-")[:64] or "default"
    if slug != raw:
        import hashlib

        slug += "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return slug


def title_from_text(text: str, limit: int = 40) -> str:
    """Derive a short session title from the first user message."""
    value = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    value = re.sub(r"\[Attached files\].*?\[/Attached files\]", " ", value, flags=re.S)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" #>-")
    return value[:limit]


@dataclass
class SessionMeta:
    """Session metadata (also written to the JSON file for the session list)."""

    session_id: str
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    turns: int = 0
    compactions: int = 0
    message_count: int = 0
    last_compaction_turn: int = 0
    workspace_id: str = ""
    #: 会话所属工作区的目录。工作区配置丢失（例如重置配置）时用它把工作区恢复回来，
    #: 否则会话会挂在一个已经不存在的工作区上：列表里看不见，却又能被打开。
    workspace_path: str = ""
    title: str = ""
    pinned: bool = False
    archived: bool = False
    #: 累计用量（含缓存读/写桶），持久化以便刷新页面后仍能看到命中率
    usage: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """Persist session history as JSON files."""

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
                workspace_path=str(payload.get("workspace_path", "")),
                title=str(payload.get("title", "")),
                pinned=bool(payload.get("pinned", False)),
                archived=bool(payload.get("archived", False)),
                usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
            )
            return messages, meta
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("读取会话 %s 失败，按新会话处理：%s", session_id, exc)
            return [], SessionMeta(session_id=session_id)

    def load_display(self, session_id: str) -> list[BaseMessage]:
        """Full user-visible text; falls back to the model messages for older sessions."""
        path = self.path(session_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("读取会话原文 %s 失败：%s", session_id, exc)
            return []
        raw = payload.get("display_messages")
        if not isinstance(raw, list):
            raw = payload.get("messages", [])
        try:
            return messages_from_dict(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("解析会话原文 %s 失败：%s", session_id, exc)
            return []

    def save(
        self,
        session_id: str,
        messages: Sequence[BaseMessage],
        meta: SessionMeta,
        display_messages: Sequence[BaseMessage] | None = None,
    ) -> bool:
        visible = list(display_messages if display_messages is not None else messages)
        payload = {
            "session_id": meta.session_id,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
            "turns": meta.turns,
            "compactions": meta.compactions,
            "message_count": len(visible),
            "last_compaction_turn": meta.last_compaction_turn,
            "workspace_id": meta.workspace_id,
            "workspace_path": meta.workspace_path,
            "title": meta.title,
            "pinned": meta.pinned,
            "archived": meta.archived,
            "usage": meta.usage or {},
            "messages": messages_to_dict(list(messages)),
            # 用户可见的完整原文；模型上下文可以独立压缩，不能覆盖这一份。
            "display_messages": messages_to_dict(visible),
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

    def update_workspace(
        self,
        session_id: str,
        workspace_id: str,
        workspace_path: str = "",
    ) -> bool:
        """Rewrite only the workspace fields of a stored session.

        Recovery uses this to re-attach a session to the workspace that now owns
        its directory, without touching the message history.
        """
        path = self.path(session_id)
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("修复会话 %s 的工作区失败：%s", session_id, exc)
            return False
        payload["workspace_id"] = workspace_id
        if workspace_path:
            payload["workspace_path"] = workspace_path
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError as exc:
            logger.warning("写入会话 %s 的工作区失败：%s", session_id, exc)
            return False

    def duplicate(self, session_id: str, new_id: str, title: str) -> SessionMeta | None:
        """Copy one session (messages, display history, workspace) into a new id."""
        source = self.path(session_id)
        if not source.exists():
            return None
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("复制会话 %s 失败：%s", session_id, exc)
            return None
        now = _now()
        payload.update({
            "session_id": new_id,
            "title": title,
            "pinned": False,
            "archived": False,
            "created_at": now,
            "updated_at": now,
        })
        target = self.path(new_id)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            logger.warning("复制会话 %s 失败：%s", new_id, exc)
            return None
        _messages, meta = self.load(new_id)
        return meta

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
                    workspace_path=str(payload.get("workspace_path", "")),
                    title=str(payload.get("title", "")),
                    pinned=bool(payload.get("pinned", False)),
                    archived=bool(payload.get("archived", False)),
                )
            )
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions


# ---------------------------------------------------------------------------
# 上下文压缩
# ---------------------------------------------------------------------------
def _summarize(summarizer, messages: Sequence[BaseMessage], prefix: Sequence[BaseMessage] = ()) -> str:
    """Ask the summarizer for a checkpoint body.

    A :class:`~agents.compaction.PrefixSummarizer` replays the live prefix
    (system prompt, tool declarations, the preserved head, the exact shadowed
    messages) so the auxiliary request is served from the provider's warm cache.
    Anything else keeps the legacy single-string prompt.
    """
    if getattr(summarizer, "replays_prefix", False):
        instruction = COMPACTION_INSTRUCTION_KEEP_FIRST if prefix else COMPACTION_INSTRUCTION
        try:
            return summarizer.summarize(
                list(messages), prefix=list(prefix), instruction=instruction
            )
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            return summarizer.summarize(list(messages))
    prompt = _SUMMARY_PROMPT.format(messages=get_buffer_string(list(messages)))
    response = summarizer.invoke(prompt)
    text = message_text(response).strip()
    if not text:
        raise ValueError("摘要模型返回了空内容")
    return text


def _generate_title(model, messages: Sequence[BaseMessage]) -> str:
    """Ask the model for a short title from the first user/assistant exchange."""
    user = ""
    assistant = ""
    for message in messages:
        kind = getattr(message, "type", "")
        if kind == "human" and not user:
            user = message_text(message).strip()
        elif kind == "ai" and not assistant:
            text = message_text(message).strip()
            if text:
                assistant = text
        if user and assistant:
            break
    if not user:
        return ""
    prompt = _TITLE_PROMPT.format(user=user[:600], assistant=assistant[:600])
    response = model.invoke(prompt)
    text = message_text(response).strip()
    if not text:
        return ""
    line = next((item.strip() for item in text.splitlines() if item.strip()), "")
    line = re.sub(r"^(title|标题)\s*[:：]\s*", "", line, flags=re.I)
    line = line.strip(" \"'`“”‘’")
    line = re.sub(r"\s+", " ", line).strip(" .。!！?？")
    return line[:30]


def compact_messages(
    messages: Sequence[BaseMessage],
    summarizer,
    keep_recent: int = 8,
    *,
    retain_tokens: int | None = None,
    preserved: int = 0,
    start: int | None = None,
) -> tuple[list[BaseMessage], str | None]:
    """Replace the oldest span with one checkpoint message.

    ``preserved`` messages at the head are kept byte-for-byte (the optional
    first exchange); ``retain_tokens`` bounds the tail by context volume instead
    of message count. The retained tail is returned unchanged and in order, so
    the next request reuses its cache from the checkpoint onwards; a summary
    that would not shrink the span is rejected and the original history is kept.

    Returns:
        ``(new message list, summary text)``. ``(original, None)`` when there is
        nothing to compact, the summary is empty, or it does not shrink.
    """
    preserved = max(0, min(preserved, len(messages)))
    if start is None:
        if retain_tokens and retain_tokens > 0:
            start = select_shadow_start_by_budget(
                messages, retain_tokens, min_start=preserved
            )
        else:
            start = select_shadow_start(messages, keep_recent, min_start=preserved)
    if start <= preserved:
        return list(messages), None

    head = list(messages[:preserved])
    older = list(messages[preserved:start])
    recent = list(messages[start:])
    if not older:
        return list(messages), None

    try:
        summary = _summarize(summarizer, older, prefix=head)
    except Exception as exc:  # 摘要失败不应中断对话
        logger.warning("上下文摘要失败，降级为省略更早消息：%s", exc)
        note = HumanMessage(
            content=f"({len(older)} earlier messages were omitted because summarization failed; ask again if you need those details.)"
        )
        return [*head, note, *recent], None

    checkpoint = checkpoint_message(summary)
    if estimate_tokens([checkpoint]) >= estimate_tokens(older):
        # 摘要没有缩小上下文：替换只会多花 token 并打断缓存，保持原样。
        logger.warning("摘要未缩小上下文（%s 条消息），保留原历史", len(older))
        return list(messages), None
    return [*head, checkpoint, *recent], summary


# ---------------------------------------------------------------------------
# 上下文预算
# ---------------------------------------------------------------------------
def context_budget(
    window: int,
    output_reserve: int | None = None,
    *,
    trigger_ratio: float | None = None,
    headroom: int | None = None,
    retain: int | None = None,
) -> tuple[int, int]:
    """Return ``(trigger_tokens, retain_tokens)`` for a context window.

    The trigger is the smaller of a window share and what remains after the
    reply reserve and a headroom that scales with the window. Raising the floor
    to a share of the usable window keeps the trigger away from zero and away
    from negative values on small windows, while the window share keeps large
    windows from compacting early. The retain budget is a share of the usable
    window, bounded by half the trigger so a compaction can still shrink.
    """
    total = max(256, int(window or settings.model_context_window))
    reserve = int(
        output_reserve
        if output_reserve is not None
        else settings.compact_output_reserve_tokens
    )
    # A reply cannot reserve more than half the window; without this clamp a
    # large default reserve would erase a small window's budget entirely.
    reserve = min(max(0, reserve), total // 2)
    usable = max(128, total - reserve)
    if headroom is not None:
        room = max(64, int(headroom))
    else:
        room = max(
            64,
            min(settings.compact_headroom_tokens, int(usable * settings.compact_headroom_ratio)),
        )
    ratio = trigger_ratio or settings.compact_trigger_ratio
    candidate = min(int(total * ratio), usable - room)
    floor = max(64, int(usable * settings.compact_min_trigger_ratio))
    threshold = min(candidate if candidate > floor else floor, usable)
    keep = int(retain if retain is not None else usable * settings.compact_retain_ratio)
    keep = max(settings.compact_min_retain_tokens, keep)
    keep = min(keep, max(1, threshold // 2))
    return max(64, threshold), max(1, keep)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@dataclass
class TurnResult:
    """Result of one question/answer turn."""

    text: str
    new_messages: list[BaseMessage]
    context_tokens: int
    compacted: bool = False
    token_source: str = "estimate"
    #: 本轮所有模型请求折叠后的用量（含缓存读/写桶）
    usage: TurnUsage = field(default_factory=TurnUsage)

    def usage_dict(self) -> dict[str, Any]:
        return self.usage.to_dict()


#: 两次自动压缩之间至少间隔的轮数，避免压缩后立刻二次触发
MIN_TURNS_BETWEEN_COMPACTIONS = 2


class ChatSession:
    """Multi-turn session with persistence and automatic compaction."""

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
        max_output_tokens: int | None = None,
        compact_headroom_tokens: int | None = None,
        compact_retain_tokens: int | None = None,
        compact_reserve_tokens: int | None = None,
        trigger_ratio: float | None = None,
        max_tool_output_tokens: int | None = None,
        keep_first_turn: bool = False,
        workspace_id: str = "",
        workspace_path: str = "",
        hydrate: bool = True,
    ) -> None:
        self.agent = agent
        self.session_id = session_id or settings.session_id
        self.store = store or SessionStore()
        self.summarizer = summarizer

        self.model_context_window = model_context_window or settings.model_context_window
        self.max_output_tokens = int(
            max_output_tokens or settings.compact_output_reserve_tokens
        )
        # ``compact_reserve_tokens`` is the legacy name of the headroom override.
        headroom = compact_headroom_tokens or compact_reserve_tokens
        threshold, retain = self._context_budget(
            trigger_ratio=trigger_ratio, headroom=headroom, retain=compact_retain_tokens
        )
        self.compact_headroom_tokens = headroom or self._headroom_tokens()
        self.max_context_tokens = (
            max_context_tokens or settings.max_context_tokens or threshold
        )
        # The retained tail must stay below the effective trigger, including an
        # explicitly configured one, otherwise compaction could never shrink.
        # An explicit ``keep_recent`` opts into message-count retention instead
        # of the token budget (used by tests and by callers that want a fixed
        # message window).
        self.compact_retain_tokens = 0 if keep_recent is not None else max(
            1, min(retain, max(1, int(self.max_context_tokens) // 2))
        )
        # Message-count pressure is opt-in only (tests / explicit overrides);
        # production sessions are bounded by tokens alone.
        self.max_context_messages = max_context_messages
        self.keep_recent = keep_recent or settings.keep_recent_messages
        self.max_tool_output_tokens = (
            max_tool_output_tokens or settings.max_tool_output_tokens
        )
        self.keep_first_turn = bool(keep_first_turn)

        self.messages: list[BaseMessage] = []
        self.display_messages: list[BaseMessage] = []
        self.meta = SessionMeta(session_id=self.session_id)
        if hydrate:
            self.messages, self.meta = self.store.load(self.session_id)
            self.display_messages = self.store.load_display(self.session_id)
            if not self.display_messages:
                self.display_messages = list(self.messages)
        self.session_usage = SessionUsage.from_dict(self.meta.usage)
        # 工作区归属以调用方为准（会话文件里的旧值可能为空）
        if workspace_id:
            self.meta.workspace_id = workspace_id
        if workspace_path:
            self.meta.workspace_path = workspace_path

    # ---- 查询 ----
    def _headroom_tokens(self) -> int:
        """Spare room below the trigger: one reply plus one summary request.

        Scales with the usable window and is capped, so a small window keeps a
        usable threshold instead of subtracting a fixed large reserve.
        """
        usable = max(1024, int(self.model_context_window) - int(self.max_output_tokens))
        scaled = int(usable * settings.compact_headroom_ratio)
        return max(64, min(settings.compact_headroom_tokens, scaled))

    def _context_budget(
        self,
        *,
        trigger_ratio: float | None = None,
        headroom: int | None = None,
        retain: int | None = None,
    ) -> tuple[int, int]:
        """Return ``(trigger_tokens, retain_tokens)`` for this session."""
        return context_budget(
            self.model_context_window,
            self.max_output_tokens,
            trigger_ratio=trigger_ratio,
            headroom=headroom,
            retain=retain,
        )

    def __len__(self) -> int:
        return len(self.messages)

    def usage(self) -> tuple[int, str]:
        """Current context ``(tokens, source)``."""
        return context_tokens(self.messages)

    def needs_compaction(self) -> bool:
        tokens, _source = self.usage()
        if tokens > self.max_context_tokens:
            return True
        # Message-count pressure is only active when a caller sets an explicit
        # bound; production sessions are measured by tokens alone.
        return bool(
            self.max_context_messages
            and len(self.messages) > self.max_context_messages
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
            "max_messages": self.max_context_messages or 0,
            "model_context_window": self.model_context_window,
            "output_reserve_tokens": self.max_output_tokens,
            "headroom_tokens": self.compact_headroom_tokens,
            "retain_tokens": self.compact_retain_tokens,
            "keep_first_turn": self.keep_first_turn,
            "updated_at": self.meta.updated_at,
            "usage": self.session_usage.to_dict(),
        }

    def recent(self, limit: int = 10) -> list[BaseMessage]:
        """Recent user-visible text; compaction only affects the model context."""
        source = self.display_messages or self.messages
        return source[-limit:] if limit > 0 else list(source)

    # ---- 主流程 ----
    def ask(self, question: str) -> TurnResult:
        """Send one question and return the reply plus the messages added this turn."""
        compacted = self._begin_turn(question)
        history = list(self.messages)
        messages: list[BaseMessage] | None = None
        retried = False
        while True:
            try:
                messages = self._invoke(history)
                break
            except BaseException as exc:
                # 提供方确认溢出：压缩头部后重发一次（非流式路径同样适用）
                if (
                    isinstance(exc, Exception)
                    and not retried
                    and settings.overflow_recovery
                    and is_context_overflow_error(exc)
                    and self._recover_from_overflow()
                ):
                    retried = True
                    history = list(self.messages)
                    continue
                # 本轮失败：撤销刚追加的提问，保持历史一致
                self.messages = history[:-1]
                if self.display_messages:
                    self.display_messages.pop()
                raise
        return self._finish_turn(history, messages or history, compacted)

    def ask_stream(self, question: str, *, internal: bool = False):
        """Stream one question, yielding events in order:

        - ``("compacted", None)``: the context was compacted before this turn started;
        - ``("thought", text)``: intermediate ReAct reasoning (work-step preview, not part
          of the final answer);
        - ``("delta", text)``: incremental model output;
        - ``("tool", {"id", "name", "args"})``: the model started a tool call;
        - ``("tool_result", {"id", "name", "content"})``: a tool result;
        - ``("done", TurnResult)``: the turn is finished (history and disk already written).

        ``internal`` marks system-triggered continuations such as a finished background
        task: the message still enters the model context, but the frontend may hide it
        from the transcript instead of treating it as user input.
        """
        compacted = self._begin_turn(question, internal=internal)
        if compacted:
            yield ("compacted", None)

        # 提供方确认上下文溢出时：压缩头部后重发同一个请求（只重试一次）。
        # 仅当还没有向界面吐出任何内容时才重试，否则会重复输出。
        max_attempts = 2 if settings.overflow_recovery else 1
        attempt = 0
        while True:
            history = list(self.messages)
            state: dict[str, Any] = {"emitted": False, "messages": None}
            try:
                yield from self._stream_attempt(history, state)
                break
            except BaseException as exc:
                recovered = (
                    isinstance(exc, Exception)
                    and attempt + 1 < max_attempts
                    and not state["emitted"]
                    and is_context_overflow_error(exc)
                    and self._recover_from_overflow()
                )
                if recovered:
                    attempt += 1
                    continue
                self.messages = history[:-1]
                if self.display_messages:
                    self.display_messages.pop()
                raise

        result = self._finish_turn(history, state["messages"] or history, compacted)
        yield ("done", result)

    def _stream_attempt(self, history: list[BaseMessage], state: dict[str, Any]):
        """Consume one streamed attempt, recording emitted output in ``state``."""
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
        stream = getattr(self.agent, "stream", None)
        if stream is None:  # 不支持流式的 Agent（例如测试替身）
            messages = self._invoke(history)
            state["messages"] = messages
            return
        for mode, payload in stream(
            {"messages": history}, stream_mode=["messages", "values"]
        ):
            if mode == "thought":
                if payload:
                    state["emitted"] = True
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
                        state["emitted"] = True
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
                        state["emitted"] = True
                        yield ("delta", delta)
                continue
            if mode != "values" or not isinstance(payload, dict):
                continue
            current = list(payload.get("messages") or [])
            messages = current
            state["messages"] = current
            for message in current[len(history):]:
                if isinstance(message, ToolMessage):
                    result_id = str(
                        getattr(message, "tool_call_id", "")
                        or f"{getattr(message, 'name', '')}:{len(emitted_results)}"
                    )
                    if result_id in emitted_results:
                        continue
                    emitted_results.add(result_id)
                    state["emitted"] = True
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
                    # Providers may normalize spaces/newlines between
                    # the two, so compare whitespace-insensitive keys.
                    native_key = thought_key(native_reasoning)
                    already_streamed = thought_key(streamed_reasoning).endswith(native_key)
                    if already_streamed:
                        emitted_thoughts.add(native_key)
                    elif native_key not in emitted_thoughts:
                        emitted_thoughts.add(native_key)
                        state["emitted"] = True
                        yield ("thought", native_reasoning)
                if derive_step_thoughts and calls:
                    thought_text = message_text(message).strip()
                    thought_text_key = thought_key(thought_text)
                    if thought_text_key and thought_text_key not in emitted_thoughts:
                        emitted_thoughts.add(thought_text_key)
                        state["emitted"] = True
                        yield ("thought", thought_text)
                for call in calls:
                    key = str(call.get("id") or call.get("name"))
                    if key in emitted_tools:
                        continue
                    emitted_tools.add(key)
                    state["emitted"] = True
                    yield (
                        "tool",
                        {
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "args": call.get("args") or {},
                        },
                    )

    def compact(
        self,
        keep_recent: int | None = None,
        *,
        force: bool = False,
        retain_tokens: int | None = None,
    ) -> bool:
        """Condense the oldest span of the history; return whether anything changed.

        Every step is shaped by prefix caching:

        0. when the session keeps its first exchange, that head is copied
           verbatim and never summarized; the auxiliary request replays it too;
        1. the retained tail starts at a user message and never splits a tool
           call from its result; its size comes from the token budget;
        2. oversized tool results inside the shadow are pruned locally first
           (no model call) and the run stops early if that is enough;
        3. otherwise a prefix-replaying summarizer replaces the shadow with one
           checkpoint message, leaving the tail byte-identical;
        4. a summary that does not shrink the span is rejected.
        """
        if self.summarizer is None:
            raise RuntimeError("当前会话没有配置摘要模型，无法压缩。")
        keep = keep_recent if keep_recent is not None else self.keep_recent
        preserved = self._preserved_count()
        start = self._shadow_start(preserved, keep, retain_tokens)
        if start <= preserved and force:
            # A manual compaction should shrink something even when the whole
            # history still fits the retain budget.
            start = select_shadow_start(self.messages, keep, min_start=preserved)
        if start <= preserved:
            return False

        head = list(self.messages[:preserved])
        shadow = list(self.messages[preserved:start])
        tail = list(self.messages[start:])

        # 头部只剩一条检查点时，再摘要一次不会带来新信息：跳过，省下一次调用，
        # 也避免把同一个检查点在每次触发时都重写一遍。
        if len(shadow) == 1 and is_checkpoint_text(message_text(shadow[0])):
            return False

        pruned, pruned_count = prune_tool_messages(shadow, self.max_tool_output_tokens)
        if pruned_count and not force:
            candidate = [*head, *pruned, *tail]
            if not self._over_threshold(candidate):
                self.messages = candidate
                self._record_compaction()
                logger.info("压缩：修剪了 %s 条超长工具输出，无需摘要调用", pruned_count)
                return True

        messages, summary = compact_messages(
            self.messages,
            self.summarizer,
            keep,
            retain_tokens=self.compact_retain_tokens,
            preserved=preserved,
            start=start,
        )
        if summary is None:
            # 摘要失败或未缩小：至少把已经完成的修剪落地，避免白跑一趟。
            if pruned_count:
                self.messages = [*head, *pruned, *tail]
                self._record_compaction()
                return True
            return False
        self.messages = messages
        self._record_compaction()
        return True

    def _preserved_count(self) -> int:
        """Messages kept verbatim at the head (the first exchange, when enabled)."""
        if not self.keep_first_turn:
            return 0
        end = first_turn_end(self.messages)
        # Only preserve a head that still leaves a span to condense.
        return end if 0 < end < len(self.messages) - 1 else 0

    def _shadow_start(self, preserved: int, keep: int, retain_tokens: int | None = None) -> int:
        """Start of the retained tail, measured by tokens."""
        retain = retain_tokens if retain_tokens is not None else self.compact_retain_tokens
        if retain:
            return select_shadow_start_by_budget(
                self.messages, retain, min_start=preserved
            )
        return select_shadow_start(self.messages, keep, min_start=preserved)

    def reset(self) -> None:
        """Clear the current session (also deletes the history file on disk)."""
        self.messages = []
        self.display_messages = []
        self.meta = SessionMeta(session_id=self.session_id)
        self.session_usage = SessionUsage()
        self.store.delete(self.session_id)

    # ---- 内部实现 ----
    def _begin_turn(self, question: str, *, internal: bool = False) -> bool:
        text = (question or "").strip()
        if not text:
            raise ValueError("问题不能为空")
        message = HumanMessage(content=text)
        if internal:
            message.additional_kwargs["alembic_internal"] = True
        # 记录发送时间：前端用它显示消息时间与「用时」。
        message.additional_kwargs["alembic_time"] = _now_ms()
        self.messages.append(message)
        self.display_messages.append(message)
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
        # 只截断本轮新增的工具输出：改写已经发出去的历史消息，会让提供方的
        # 前缀缓存从被改写的位置起全部失效，还会在每轮重复截断同一条消息。
        new_messages = messages[len(history):] if len(messages) >= len(history) else []
        new_messages = cap_tool_outputs(new_messages, self.max_tool_output_tokens)
        # 本轮产生的消息补一个完成时间，前端据此显示「已完成，用时…」和结束时间。
        finish_ms = _now_ms()
        for message in new_messages:
            kwargs = getattr(message, "additional_kwargs", None)
            if isinstance(kwargs, dict) and "alembic_time" not in kwargs:
                kwargs["alembic_time"] = finish_ms
        self.messages = [*history, *new_messages]
        if new_messages:
            # 模型上下文可能刚被压缩，但用户可见的原文始终按增量追加。
            self.display_messages.extend(new_messages)

        turn_usage = fold_turn_usage(new_messages)
        if turn_usage.known:
            self.session_usage.add_turn(turn_usage)
            self.meta.usage = self.session_usage.to_dict()

        self.meta.turns += 1
        self.meta.updated_at = _now()
        if self.meta.turns == 1 and not self.meta.title:
            self.meta.title = self._first_turn_title()
        self._persist()
        tokens, source = self.usage()
        return TurnResult(
            text=_final_text(messages) or "（模型没有返回内容）",
            new_messages=new_messages,
            context_tokens=tokens,
            compacted=compacted,
            token_source=source,
            usage=turn_usage,
        )

    def _first_turn_title(self) -> str:
        question = next(
            (
                message_text(message)
                for message in self.messages
                if getattr(message, "type", "") == "human"
            ),
            "",
        )
        fallback = title_from_text(question)
        if self.summarizer is None:
            return fallback
        # 标题是一次冷启动的小请求，用摘要器背后的原始模型；前缀回放摘要器
        # 本身没有 invoke()，不该为了标题重建一遍会话前缀。
        model = getattr(self.summarizer, "model", self.summarizer)
        if not hasattr(model, "invoke"):
            return fallback
        try:
            return _generate_title(model, self.messages) or fallback
        except Exception as exc:  # 标题失败不影响本轮对话
            logger.warning("生成对话标题失败，回退首条内容：%s", exc)
            return fallback

    def _maybe_compact(self) -> bool:
        if self.summarizer is None or not self.needs_compaction():
            return False
        # 刚压缩过就再压一次通常是徒劳的：摘要本身也会占上下文
        if self.meta.turns - self.meta.last_compaction_turn < MIN_TURNS_BETWEEN_COMPACTIONS:
            return False
        compacted = False
        keep = self.keep_recent
        retain = self.compact_retain_tokens
        for index in range(1 + max(0, settings.compaction_retries)):
            try:
                changed = self.compact(
                    keep_recent=keep, force=index > 0, retain_tokens=retain
                )
            except Exception as exc:  # 压缩失败不影响本轮对话
                logger.warning("自动压缩失败，本轮继续使用完整历史：%s", exc)
                return compacted
            compacted = compacted or changed
            if not changed or not self.needs_compaction():
                break
            # Still above the trigger: shrink both the retained budget and the
            # message-based fallback so the pressure really drops.
            keep = max(1, keep // 2)
            if retain:
                retain = max(settings.compact_min_retain_tokens, retain // 2)
        return compacted

    def _recover_from_overflow(self) -> bool:
        """Compact the head aggressively after a provider-confirmed overflow."""
        if self.summarizer is None:
            return False
        keep = max(1, min(self.keep_recent, settings.overflow_retain_messages))
        try:
            changed = self.compact(keep_recent=keep, force=True)
        except Exception as exc:
            logger.warning("上下文溢出后压缩失败：%s", exc)
            return False
        if changed:
            logger.info("上下文溢出：已压缩头部，重发同一请求")
        return changed

    def _over_threshold(self, messages: Sequence[BaseMessage]) -> bool:
        tokens, _source = context_tokens(messages)
        if tokens > self.max_context_tokens:
            return True
        return bool(
            self.max_context_messages and len(messages) > self.max_context_messages
        )

    def _record_compaction(self) -> None:
        self.meta.compactions += 1
        self.meta.last_compaction_turn = self.meta.turns
        self.meta.updated_at = _now()
        self._persist()

    def _persist(self) -> None:
        self.store.save(
            self.session_id,
            self.messages,
            self.meta,
            display_messages=self.display_messages or self.messages,
        )
