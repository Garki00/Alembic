# tools/todo.py
"""Todo-list tool: track multi-step task progress across the session.

State is stored per session in ``<memory>/todos/<session>.json``, separate from the
conversation history so ``SessionStore.list_sessions`` does not mistake todo files
for sessions. The tool is read-only risk: it changes only this application's own
session state, never workspace files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path

from langchain.tools import tool

from tools.context import ToolContext
from tools.spec import ToolSpec
from tools.errors import tool_error

logger = logging.getLogger(__name__)

MAX_TODOS = 50
STATUSES = ("pending", "in_progress", "completed")
_STATUS_ALIASES = {
    "pending": "pending",
    "todo": "pending",
    "未开始": "pending",
    "待办": "pending",
    "in_progress": "in_progress",
    "in-progress": "in_progress",
    "doing": "in_progress",
    "active": "in_progress",
    "进行中": "in_progress",
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "已完成": "completed",
    "完成": "completed",
}
_STATUS_MARK = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
_UNSAFE = re.compile(r"[^0-9A-Za-z_.\-]+")


def _slug(session_id: str) -> str:
    raw = (session_id or "default").strip() or "default"
    slug = _UNSAFE.sub("_", raw).strip("._-")[:64] or "default"
    if slug != raw:
        slug += "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return slug


def normalize_todos(raw: list[dict]) -> list[dict]:
    """Validate and normalize the todo array sent by the model.

    Raises:
        ValueError: The structure is invalid; the caller converts it to ``Error: [bad_todos]``.
    """
    if not isinstance(raw, list):
        raise ValueError("todos must be an array; each item needs content and status.")
    if len(raw) > MAX_TODOS:
        raise ValueError(f"At most {MAX_TODOS} todos are allowed; merge or remove some.")

    items: list[dict] = []
    in_progress = 0
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Item {index} is not an object; expected something like {{\"content\": \"...\", \"status\": \"pending\"}}.")
        content = str(item.get("content") or item.get("text") or "").strip()
        if not content:
            raise ValueError(f"Item {index} is missing content.")
        status_raw = str(item.get("status") or "pending").strip().lower()
        status = _STATUS_ALIASES.get(status_raw)
        if status is None:
            raise ValueError(
                f"Item {index} has invalid status={status_raw!r}; only pending / in_progress / completed are allowed."
            )
        if status == "in_progress":
            in_progress += 1
            if in_progress > 1:
                raise ValueError("Only one todo may be in_progress at a time; mark the others back to pending.")
        items.append({"content": content, "status": status})
    return items


def render_todos(items: list[dict]) -> str:
    total = len(items)
    done = sum(1 for item in items if item.get("status") == "completed")
    lines = [f"Todo list: {done}/{total} completed"]
    if not total:
        lines.append("(empty) No todo items currently.")
    for index, item in enumerate(items, 1):
        mark = _STATUS_MARK.get(str(item.get("status")), "[ ]")
        lines.append(f"{mark} {index}. {item.get('content', '')}")
    return "\n".join(lines)


class TodoStore:
    """Read and write the todo list per session; with ``directory=None`` it is kept in-process only."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory).expanduser() if directory else None
        self._cache: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Path | None:
        if self.directory is None:
            return None
        return self.directory / f"{_slug(session_id)}.json"

    def load(self, session_id: str) -> list[dict]:
        sid = session_id or "default"
        with self._lock:
            cached = self._cache.get(sid)
            if cached is not None:
                return [dict(item) for item in cached]

        path = self._path(sid)
        items: list[dict] = []
        if path is not None and path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                raw = payload.get("items") if isinstance(payload, dict) else payload
                items = [
                    {"content": str(item.get("content", "")), "status": str(item.get("status", "pending"))}
                    for item in (raw or [])
                    if isinstance(item, dict) and str(item.get("content", "")).strip()
                ]
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("读取任务清单 %s 失败，按空清单处理：%s", sid, exc)
        with self._lock:
            self._cache[sid] = [dict(item) for item in items]
        return items

    def save(self, session_id: str, items: list[dict]) -> bool:
        sid = session_id or "default"
        snapshot = [dict(item) for item in items]
        with self._lock:
            self._cache[sid] = snapshot

        path = self._path(sid)
        if path is None:
            return True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps({"session_id": sid, "items": snapshot}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)
            return True
        except OSError as exc:  # a read-only disk must not break the current conversation
            logger.warning("保存任务清单 %s 失败：%s", sid, exc)
            return False

    def clear(self, session_id: str) -> None:
        sid = session_id or "default"
        with self._lock:
            self._cache.pop(sid, None)
        path = self._path(sid)
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("清除任务清单 %s 失败：%s", sid, exc)


_FALLBACK = TodoStore()


def build_todo_tools(ctx: ToolContext) -> list:
    """Build the todo-list tool."""

    @tool
    def todo_write(todos: list[dict]) -> str:
        """Replace this session's todo list as a whole (pass an empty array to clear it).

        List the plan when a multi-step task starts; update statuses as each step
        completes so the user can see progress. At most one todo may be
        in_progress; mark finished todos as completed instead of deleting them.

        Args:
            todos: Array of todos, each shaped like
                ``{"content": "Read the config", "status": "pending"}``;
                status must be pending / in_progress / completed.

        Returns:
            The updated list and the completed count.
        """
        try:
            items = normalize_todos(todos)
        except ValueError as exc:
            return tool_error("bad_todos", str(exc), hint="Fix the issues and resend the whole list, not just the changed item.")

        store = ctx.todo_store or _FALLBACK
        store.save(ctx.session_id, items)
        ctx.emit_event("todo", {"items": items})
        return render_todos(items)

    return [todo_write]


TOOL_ENTRIES: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="todo_write",
        label="任务清单",
        description="维护会话任务清单",
        tool_names=("todo_write",),
        builder=lambda _config, ctx: build_todo_tools(ctx),
        capability={
            "read_only": True,
            "side_effect_scope": "session",
            "risk_level": "low",
            "permission_name": "session",
        },
    ),
)
