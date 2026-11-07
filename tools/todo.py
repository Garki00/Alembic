# tools/todo.py
"""任务清单工具：让多步任务的过程可见、可恢复。

状态存放在会话级目录（``<memory>/todos/<session>.json``），与对话历史分开：
``SessionStore.list_sessions`` 会扫描 memory 根目录下的 ``*.json``，
放进子目录可以避免把 todo 文件误认成会话。

工具本身是只读风险：它不改工作区文件，只改本应用自己的会话状态。
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
    """校验并规范化模型传来的任务数组。

    Raises:
        ValueError: 结构不合法；调用方转成 ``Error: [bad_todos]``。
    """
    if not isinstance(raw, list):
        raise ValueError("todos 必须是数组，每项包含 content 与 status。")
    if len(raw) > MAX_TODOS:
        raise ValueError(f"任务最多 {MAX_TODOS} 条，请合并或删减。")

    items: list[dict] = []
    in_progress = 0
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 项不是对象，应形如 {{\"content\": \"...\", \"status\": \"pending\"}}。")
        content = str(item.get("content") or item.get("text") or "").strip()
        if not content:
            raise ValueError(f"第 {index} 项缺少 content。")
        status_raw = str(item.get("status") or "pending").strip().lower()
        status = _STATUS_ALIASES.get(status_raw)
        if status is None:
            raise ValueError(
                f"第 {index} 项的 status={status_raw!r} 不合法，只能是 pending / in_progress / completed。"
            )
        if status == "in_progress":
            in_progress += 1
            if in_progress > 1:
                raise ValueError("同一时间只能有一条 in_progress，请把其他任务标回 pending。")
        items.append({"content": content, "status": status})
    return items


def render_todos(items: list[dict]) -> str:
    total = len(items)
    done = sum(1 for item in items if item.get("status") == "completed")
    lines = [f"任务清单：{done}/{total} 完成"]
    if not total:
        lines.append("(空) 当前没有待办任务。")
    for index, item in enumerate(items, 1):
        mark = _STATUS_MARK.get(str(item.get("status")), "[ ]")
        lines.append(f"{mark} {index}. {item.get('content', '')}")
    return "\n".join(lines)


class TodoStore:
    """按会话读写任务清单；``directory=None`` 时只在进程内保存。"""

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
        except OSError as exc:  # 磁盘只读不应影响本轮对话
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
    """构造任务清单工具。"""

    @tool
    def todo_write(todos: list[dict]) -> str:
        """整体更新本会话的任务清单（传空数组表示清空）。

        多步任务开始时先列计划；每完成一步就更新状态，让用户看到进度。
        同一时间最多一条 in_progress，完成的任务标 completed 而不是删除。

        Args:
            todos: 任务数组，每项形如
                ``{"content": "读取配置", "status": "pending"}``；
                status 只能是 pending / in_progress / completed。

        Returns:
            更新后的清单与完成计数。
        """
        try:
            items = normalize_todos(todos)
        except ValueError as exc:
            return tool_error("bad_todos", str(exc), hint="请修正后整体重传，不要只传变化的那一条。")

        store = ctx.todo_store or _FALLBACK
        store.save(ctx.session_id, items)
        ctx.emit_event("todo", {"items": items})
        return render_todos(items)

    return [todo_write]
