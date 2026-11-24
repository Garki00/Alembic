# web/server.py
"""Web workspace backend: sessions, model settings, tool settings, workspaces, and streaming chat.

Startup:
    .venv\\Scripts\\python.exe -m uvicorn web.server:app --port 8000
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import threading
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.approval import (
    VALID_DECISIONS,
    ApprovalGate,
    ApprovalPolicy,
    permission_tools,
    tool_risks,
)
from agents.context import (
    ChatSession,
    SessionMeta,
    SessionStore,
    context_budget,
    context_tokens,
    message_text,
    reasoning_text,
)
from agents.usage import SessionUsage
from config.settings import settings
from tools.paths import (
    WorkspacePathError,
    display_path,
    resolve_in_workspace,
    resolve_readable_path,
)
from config.store import (
    AppPaths,
    ModelStore,
    PreferencesStore,
    ToolStore,
    WorkspaceStore,
)
from tools.catalog import ToolActivation
from tools.processes import ProcessManager
from tools.sandbox import create_sandbox
from tools.sandbox.config import SandboxConfigStore
from tools.terminals import available_terminals
from tools.todo import TodoStore
from web.approvals import ApprovalBroker, ApprovalRegistry
from web.auth import install_auth
from web.event_hub import EventHub
from web.process_events import ProcessEventHub
from web.questions import QuestionBroker, QuestionRegistry

from agents.subagents.manager import SubagentManager

# 前后端分离后，后端只提供 /api；页面由 frontend/ 的 Vite 应用提供。


def _default_runtime_factory(*args, **kwargs):
    """Import the LangChain runtime lazily so backend startup stays light."""
    from web.runtime import build_runtime

    return build_runtime(*args, **kwargs)


def _pick_directory(initial: str, title: str) -> dict:
    """Open the system folder picker and return the chosen directory."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # tkinter is optional in some Python builds
        raise HTTPException(501, "当前环境无法打开系统文件选择器") from exc
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            selected = filedialog.askdirectory(
                initialdir=initial or None,
                mustexist=True,
                title=title,
            )
        finally:
            root.destroy()
    except Exception as exc:
        raise HTTPException(500, f"无法打开系统文件选择器：{exc}") from exc
    path = str(selected or "").strip()
    return {"path": path, "cancelled": not bool(path)}


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class ProviderModelPayload(BaseModel):
    id: str | None = None
    model: str = ""
    name: str = ""
    thinking: str = "high"
    thinking_levels: list[str] | None = None
    temperature: float = 0.0
    supports_reasoning: bool | None = None
    supports_images: bool | None = None
    context_length: int = 0
    max_output_tokens: int = 0


class ProviderPayload(BaseModel):
    id: str | None = None
    provider: str = "custom"
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    #: ``openai`` or ``anthropic``; empty keeps the provider's default protocol.
    api_mode: str = ""
    models: list[ProviderModelPayload] | None = None


class ActivateModelPayload(BaseModel):
    model_id: str = ""


class ThinkingPayload(BaseModel):
    thinking: str = "max"


class FetchModelsPayload(BaseModel):
    provider_id: str = ""
    provider: str = "custom"
    base_url: str = ""
    api_key: str = ""


class ToolPayload(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    clear: list[str] | None = None


class ChatPayload(BaseModel):
    session_id: str | None = "default"
    message: str
    workspace_id: str | None = None
    workspace: str | None = None  # 旧字段，仅用于兼容
    attachments: list[str] = []
    internal: bool = False
    process_id: str = ""
    subagent_id: str = ""


class AddWorkspacePayload(BaseModel):
    path: str
    name: str = ""


class RenameWorkspacePayload(BaseModel):
    name: str = ""


class SessionPayload(BaseModel):
    id: str | None = None
    workspace_id: str = ""


class SessionPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class FolderPickPayload(BaseModel):
    initial: str = ""


class ApprovalPayload(BaseModel):
    decision: str = "deny"  # once | always | deny


class AnswerPayload(BaseModel):
    answer: str = ""


class PreferencesPayload(BaseModel):
    theme: str | None = None
    transcript_view: str | None = None
    collapse_timing: str | None = None
    permission_mode: str | None = None
    terminal: str | None = None
    link_target: str | None = None
    developer_tools: bool | None = None
    compact_keep_first_turn: bool | None = None


class SandboxConfigPayload(BaseModel):
    network: str | None = None
    allowed_domains: list[str] | None = None
    allowed_ports: list[int] | None = None


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _clean_title(text: str) -> str:
    """Trim a first message into a readable one-line session title."""
    text = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    text = re.sub(r"\[Attached files\].*?\[/Attached files\]", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" #>-")
    return text[:40]


def _session_title(session_store: SessionStore, session_id: str, meta=None) -> str:
    stored = str(getattr(meta, "title", "") or "").strip()
    if stored:
        return stored
    messages, _ = session_store.load(session_id)
    for message in messages:
        if getattr(message, "type", "") == "human":
            raw = message_text(message)
            title = _clean_title(raw) or re.sub(r"\s+", " ", raw).strip()[:40]
            if title:
                return title
    return "新对话"


def _restore_workspaces_from_sessions(session_store: SessionStore, workspace_store) -> int:
    """Re-attach sessions that point at a workspace the config no longer has.

    Resetting the config directory leaves the session history in place while the
    workspace list disappears. Such sessions would be invisible in the sidebar yet
    still openable, so each one is recovered from the path recorded on the session:

    - a directory that already has a workspace now owns the session, so the session
      is re-pointed at it (the workspace may have been re-added with a new id);
    - otherwise the workspace is re-created with the original id, and the session
      attaches itself again.

    Sessions without a recorded path cannot be recovered and stay untouched.

    Returns the number of sessions that were re-attached or restored.
    """
    known = {workspace.id: workspace for workspace in workspace_store.list()}
    by_path = {str(workspace.path): workspace for workspace in workspace_store.list()}
    previous_active = workspace_store.active_id
    recovered = 0
    for meta in session_store.list_sessions():
        if not meta.workspace_id or meta.workspace_id in known:
            continue
        path = str(getattr(meta, "workspace_path", "") or "").strip()
        if not path or not Path(path).is_dir():
            continue
        owner = by_path.get(path)
        if owner is not None:
            # 同一个目录已经有工作区（重置后又重新添加过）：把会话并过去
            if session_store.update_workspace(meta.session_id, owner.id, path):
                recovered += 1
            continue
        try:
            workspace = workspace_store.add(path, workspace_id=meta.workspace_id)
        except ValueError:
            continue
        known[workspace.id] = workspace
        by_path[workspace.path] = workspace
        recovered += 1
    # 恢复工作区不应该顺手改变用户当前选中的工作区
    if previous_active and workspace_store.active_id != previous_active:
        workspace_store.set_active(previous_active)
    return recovered


def _session_summary(session_store: SessionStore, meta) -> dict[str, Any]:
    return {
        "id": meta.session_id,
        "title": _session_title(session_store, meta.session_id, meta),
        "turns": meta.turns,
        "messages": meta.message_count,
        "updated_at": meta.updated_at,
        "compactions": meta.compactions,
        "workspace_id": meta.workspace_id,
        "pinned": bool(getattr(meta, "pinned", False)),
        "archived": bool(getattr(meta, "archived", False)),
    }


def _serialize_messages(messages: Iterable[Any]) -> list[dict[str, Any]]:
    role_map = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}
    result: list[dict[str, Any]] = []
    for message in messages:
        role = role_map.get(getattr(message, "type", ""), getattr(message, "type", "unknown"))
        item: dict[str, Any] = {"role": role, "content": message_text(message)}
        kwargs = getattr(message, "additional_kwargs", None)
        if isinstance(kwargs, dict):
            if kwargs.get("alembic_internal"):
                item["internal"] = True
            time_ms = kwargs.get("alembic_time")
            if isinstance(time_ms, int):
                item["time"] = time_ms
        reasoning = reasoning_text(message)
        if reasoning:
            item["reasoning"] = reasoning
        calls = getattr(message, "tool_calls", None)
        if calls:
            item["tool_calls"] = [
                {"id": call.get("id"), "name": call.get("name"), "args": call.get("args") or {}}
                for call in calls
            ]
        if role == "tool":
            item["tool_name"] = getattr(message, "name", None) or ""
            item["tool_call_id"] = getattr(message, "tool_call_id", None) or ""
        result.append(item)
    return result


def _upstream_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _upstream_model_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Support /models responses with capability fields, such as OpenRouter; most providers omit them."""
    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
    context = _upstream_int(
        item.get("context_length")
        or item.get("context_window")
        or item.get("max_context_length")
        or architecture.get("context_length")
    )
    output = _upstream_int(
        item.get("max_output_tokens")
        or item.get("max_completion_tokens")
        or top_provider.get("max_completion_tokens")
    )
    modalities = (
        item.get("input_modalities")
        or architecture.get("input_modalities")
        or item.get("modalities")
        or []
    )
    if isinstance(modalities, str):
        modalities = [modalities]
    supports_images = any(
        "image" in str(value).lower() for value in modalities if value
    ) if modalities else None
    supported = item.get("supported_parameters") or []
    if isinstance(supported, str):
        supported = [supported]
    supported_text = " ".join(str(value).lower() for value in supported)
    supports_reasoning: bool | None = None
    if isinstance(item.get("supports_reasoning"), bool):
        supports_reasoning = item["supports_reasoning"]
    elif supported_text:
        supports_reasoning = "reasoning" in supported_text or "thinking" in supported_text
    result: dict[str, Any] = {}
    if context:
        result["context_length"] = context
    if output:
        result["max_output_tokens"] = output
    if supports_images is not None:
        result["supports_images"] = bool(supports_images)
    if supports_reasoning is not None:
        result["supports_reasoning"] = supports_reasoning
    return result


def _new_session_id() -> str:
    return f"chat-{datetime.now():%Y%m%d-%H%M%S}"


def _context_limit(model_cfg=None) -> int:
    """Context compaction trigger for the active model; prefer the model's own window."""
    if settings.max_context_tokens:
        return int(settings.max_context_tokens)
    window = int(getattr(model_cfg, "context_length", 0) or settings.model_context_window)
    output = int(getattr(model_cfg, "max_output_tokens", 0) or 0) or None
    trigger, _retain = context_budget(window, output)
    return trigger


def _safe_attachment_name(raw: str) -> str:
    """Keep only the file name, removing path components and control characters."""
    name = Path(str(raw or "")).name
    name = re.sub(r"[\x00-\x1f\x7f]+", "", name).strip(" .")
    name = name.replace("\\", "_").replace("/", "_")
    return name[:160] or "attachment"


def _attachment_kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
        return "image"
    if suffix in {".pdf", ".docx", ".xlsx", ".pptx"}:
        return "document"
    return "file"


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_app(
    data_dir: str | Path | None = None,
    memory_dir: str | Path | None = None,
    runtime_factory=None,
    auth_token: str | None = None,
) -> FastAPI:
    if runtime_factory is None:
        runtime_factory = _default_runtime_factory
    paths = AppPaths(data_dir)
    models = ModelStore(paths.models_file)
    tools = ToolStore(paths.tools_file)
    from extensions.manager import ExtensionManager
    extensions = ExtensionManager(paths.data_dir)
    workspaces = WorkspaceStore(paths.workspace_file, default=settings.default_workspace)
    preferences = PreferencesStore(paths.preferences_file)
    sessions = SessionStore(memory_dir or settings.memory_dir)
    sandbox_config = SandboxConfigStore(Path(paths.data_dir) / "sandbox.json")
    sandbox = create_sandbox(config=sandbox_config)

    app = FastAPI(title="ALEMBIC Workspace", version="0.1.0")
    # 桌面外壳会注入随机 token；不设则保持「本机直接打开就能用」的旧行为。
    # ALEMBIC_TOKEN 为主，SMART_AGENT_TOKEN 仅用于旧桌面外壳兼容。
    token = (
        auth_token
        if auth_token is not None
        else (
            os.getenv("ALEMBIC_TOKEN", "").strip()
            or os.getenv("SMART_AGENT_TOKEN", "").strip()
        )
    )
    if token:
        install_auth(app, token)
    # CORS 必须在 auth 之后加入：Starlette 后加入的中间件在最外层，
    # 这样 401 响应与 OPTIONS 预检也会带上 CORS 头。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.models = models
    app.state.tools = tools
    app.state.workspaces = workspaces
    app.state.preferences = preferences
    app.state.sessions = sessions
    app.state.sandbox = sandbox
    app.state.sandbox_config = sandbox_config
    app.state.runtime_factory = runtime_factory
    app.state.locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
    # 每个会话当前一轮的取消信号；停止生成时同时中断前台命令。
    app.state.cancels: dict[str, threading.Event] = {}
    # 后台任务完成事件：进程视图和自动续跑都订阅这一份。
    app.state.process_events = ProcessEventHub()
    # 审批：待回执表（跨请求）+ 每个会话已记住的「始终允许」
    app.state.approvals = ApprovalRegistry()
    app.state.approval_memo: dict[str, dict[str, dict]] = {}
    # 子代理：事件走独立 hub，任务表由应用级 manager 持有。
    app.state.subagent_events = EventHub()
    app.state.subagents = SubagentManager(
        hub=app.state.subagent_events,
        approval_registry=app.state.approvals,
        profiles_dir=paths.data_dir / "agents",
        store_dir=sessions.directory / "subagents",
        output_dir=Path(paths.data_dir) / "subagents",
    )
    # 同一个后台子代理只允许由一个标签页触发自动续跑。
    app.state.continued_subagents: set[str] = set()
    # 每个会话的扩展工具激活状态；基础工具常驻，扩展工具由 tools_search 加载。
    app.state.session_activations: dict[str, ToolActivation] = {}
    # 提问：与审批同构的中继，回答走 POST /api/questions/{id}
    app.state.questions = QuestionRegistry()
    # 任务清单与后台进程都放在应用级，跨轮次共享
    app.state.todos = TodoStore(sessions.directory / "todos")
    app.state.processes = ProcessManager(
        sessions.directory.parent / "logs" / "processes",
        on_event=app.state.process_events.publish,
    )
    # 同一个后台任务只允许由一个标签页触发自动续跑。
    app.state.continued_processes: set[str] = set()
    app.state.continued_lock = threading.Lock()

    def resolve_workspace(workspace_id: str | None, legacy_path: str | None) -> tuple[str, str]:
        """Resolve the request workspace into ``(id, absolute path)``."""
        workspace = workspaces.get(workspace_id) if workspace_id else None
        if workspace is None:
            workspace = workspaces.active()
        if workspace is not None:
            if not Path(workspace.path).is_dir():
                raise HTTPException(400, f"工作区目录不存在：{workspace.path}")
            return workspace.id, workspace.path
        if legacy_path and legacy_path.strip():
            return "", legacy_path.strip()
        raise HTTPException(400, "还没有工作区，请先在左侧添加一个")

    app.state.extensions = extensions

    @app.on_event("shutdown")
    def close_extensions():
        extensions.close()

    @app.get("/api/extensions")
    def extensions_get():
        return extensions.snapshot()

    @app.post("/api/extensions/refresh")
    def extensions_refresh():
        return extensions.refresh()

    @app.put("/api/extensions/directories")
    def extensions_directories(payload: dict):
        try:
            return extensions.set_directories(payload)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/extensions/mcp/{identity}")
    def extensions_mcp_put(identity: str, payload: dict):
        try:
            return extensions.put_server(identity, payload)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/extensions/mcp/{identity}")
    def extensions_mcp_delete(identity: str):
        try:
            return extensions.delete_server(identity)
        except KeyError as exc:
            raise HTTPException(404, "MCP server not found") from exc

    @app.patch("/api/extensions/skills/{identity}")
    def extensions_skill_patch(identity: str, payload: dict):
        if not isinstance(payload.get("enabled"), bool):
            raise HTTPException(400, "enabled must be a boolean")
        try:
            return extensions.set_skill(identity, payload["enabled"])
        except KeyError as exc:
            raise HTTPException(404, "Skill not found") from exc

    @app.post("/api/extensions/pick-directory")
    def extensions_pick_directory():
        return _pick_directory("", "选择插件目录")

    # ---- API 根与健康检查 ----
    @app.get("/")
    def index():
        """API description. For the page itself, open the frontend dev server (default http://127.0.0.1:5173)."""
        return {
            "name": "ALEMBIC API",
            "version": "0.1.0",
            "api": "/api",
            "health": "/api/health",
            "docs": "/docs",
            "frontend": settings.frontend_url,
        }

    @app.get("/api/health")
    def health():
        return {"ok": True, "model": models.active_model_id, "time": datetime.now().isoformat(timespec="seconds")}

    # ---- 沙盒 ----
    @app.get("/api/sandbox/status")
    def sandbox_status():
        status = app.state.sandbox.status() if app.state.sandbox is not None else None
        if status is None:
            return {
                "ready": False,
                "backend": "none",
                "platform": "unsupported",
                "detail": "当前平台没有原生沙盒后端。",
                "network_modes": [],
                "allowed_domains": [],
                "reasons": ["unsupported_platform"],
            }
        return status.public()

    @app.get("/api/sandbox/config")
    def sandbox_config_get():
        return {"config": sandbox_config.get()}

    @app.put("/api/sandbox/config")
    def sandbox_config_put(payload: SandboxConfigPayload):
        try:
            updated = sandbox_config.update(payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if app.state.sandbox is not None:
            app.state.sandbox.close()
        return {"config": updated}

    # ---- 后台任务视图 ----
    @app.get("/api/processes/events")
    def process_events():
        """Background task state event stream shared by the process view and automatic continuation."""

        def event_stream():
            try:
                yield ": connected\n\n"
                for item in app.state.process_events.listen():
                    if item is None:
                        yield ": ping\n\n"
                        continue
                    kind, data = item
                    yield _sse(kind, data)
            finally:
                pass

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/processes")
    def list_processes():
        records = sorted(
            app.state.processes.list(),
            key=lambda record: record.started_monotonic,
            reverse=True,
        )
        items = [record.public() for record in records]
        return {
            "processes": items,
            "running": sum(1 for item in items if item["running"]),
            "finished": sum(1 for item in items if not item["running"]),
        }

    @app.get("/api/processes/{process_id}")
    def process_detail(process_id: str, tail: int = 400):
        record = app.state.processes.get(process_id)
        if record is None:
            raise HTTPException(404, "后台任务不存在")
        _, text = app.state.processes.read_log(record.id, tail)
        return {"process": record.public(), "log": text}

    @app.post("/api/processes/{process_id}/stop")
    def stop_process(process_id: str):
        record = app.state.processes.get(process_id)
        if record is None:
            raise HTTPException(404, "后台任务不存在")
        stopped = app.state.processes.stop(record.id)
        return {"ok": True, "process": stopped.public() if stopped else None}

    # ---- 子代理视图 ----
    @app.get("/api/subagents/events")
    def subagent_events():
        """Subagent lifecycle stream: progress, completion, and background approvals."""

        def event_stream():
            yield ": connected\n\n"
            for item in app.state.subagent_events.listen():
                if item is None:
                    yield ": ping\n\n"
                    continue
                kind, data = item
                yield _sse(kind, data)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/subagents")
    def list_subagents(session_id: str = ""):
        sid = (session_id or "").strip()
        if not sid:
            raise HTTPException(400, "缺少 session_id")
        return {"subagents": app.state.subagents.list_tasks(sid)}

    @app.get("/api/subagents/{agent_id}")
    def subagent_detail(agent_id: str):
        task = app.state.subagents.get(agent_id)
        if task is None:
            raise HTTPException(404, "子代理不存在")
        payload = task.snapshot()
        payload["messages"] = _serialize_messages(
            app.state.subagents.load_messages(task)
        )
        return {"subagent": payload}

    @app.post("/api/subagents/{agent_id}/stop")
    def stop_subagent(agent_id: str):
        stopped = app.state.subagents.stop(agent_id, initiator="user")
        if not stopped:
            raise HTTPException(409, "子代理已结束或不存在")
        return {"ok": True, "subagent": app.state.subagents.get(agent_id).snapshot()}  # type: ignore[union-attr]

    # ---- 初始化数据 ----
    @app.get("/api/bootstrap")
    def bootstrap():
        # 仅「第一次启动」才把初始化向导推给前端：判定依据是供应商配置文件是否已存在。
        # 必须先取这个值，再调用 ensure_saved()，否则它会顺手把文件创建出来。
        first_run = not models.config_exists()
        # 首次访问时把默认模型/工具配置落盘，方便用户查看与备份
        models.ensure_saved()
        tools.ensure_saved()
        workspaces.ensure_saved()
        # 配置目录被重置后，会话仍在磁盘上：用会话里记录的工作区路径把工作区补回来，
        # 否则这些对话会挂在不存在的工作区上——列表里看不见，却又会被自动打开。
        _restore_workspaces_from_sessions(sessions, workspaces)
        active = models.active()
        return {
            "providers": [item.public() for item in models.providers()],
            "active_provider_id": models.active_provider_id,
            "active_model_id": models.active_model_id,
            "active_model": active.public() if active else None,
            "needs_setup": models.needs_setup(),
            "first_run": first_run,
            "tools": tools.public(),
            "workspaces": workspaces.state()["workspaces"],
            "active_workspace_id": workspaces.active_id,
            "sessions": [_session_summary(sessions, meta) for meta in sessions.list_sessions()],
            "preferences": preferences.get(),
            "sandbox": app.state.sandbox.status().public() if app.state.sandbox is not None else {
                "ready": False,
                "backend": "none",
                "platform": "unsupported",
                "detail": "当前平台没有原生沙盒后端。",
                "network_modes": [],
                "allowed_domains": [],
                "reasons": ["unsupported_platform"],
            },
            "sandbox_config": sandbox_config.get(),
            "terminals": [
                {
                    "value": item.value,
                    "label": item.label,
                    "available": item.available,
                    "detail": item.detail,
                }
                for item in available_terminals()
            ],
        }

    # ---- 供应商与模型 ----
    def _providers_payload() -> dict[str, Any]:
        active = models.active()
        return {
            "providers": [item.public() for item in models.providers()],
            "active_provider_id": models.active_provider_id,
            "active_model_id": models.active_model_id,
            "active_model": active.public() if active else None,
        }

    @app.get("/api/providers")
    def list_providers():
        return _providers_payload()

    @app.post("/api/providers")
    def save_provider(payload: ProviderPayload):
        existing = models.get_provider(payload.id) if payload.id else None
        if not payload.base_url.strip():
            raise HTTPException(400, "请填写 Base URL")
        if not payload.api_key.strip() and existing is None:
            raise HTTPException(400, "新建供应商需要填写 API Key")
        if payload.models is not None and not any(
            item.model.strip() for item in payload.models
        ):
            raise HTTPException(400, "请至少添加一个模型")
        if payload.models is None and existing is None:
            raise HTTPException(400, "请至少添加一个模型")
        saved = models.upsert_provider(payload.model_dump())
        return {"provider": saved.public(), **_providers_payload()}

    @app.delete("/api/providers/{provider_id}")
    def delete_provider(provider_id: str):
        if not models.delete_provider(provider_id):
            raise HTTPException(404, "供应商不存在")
        return {"ok": True, **_providers_payload()}

    @app.post("/api/providers/{provider_id}/activate")
    def activate_model(provider_id: str, payload: ActivateModelPayload):
        if not models.set_active(provider_id, payload.model_id):
            raise HTTPException(404, "供应商或模型不存在")
        return {"ok": True, **_providers_payload()}

    @app.patch("/api/providers/{provider_id}/models/{model_id}/thinking")
    def set_model_thinking(provider_id: str, model_id: str, payload: ThinkingPayload):
        if not models.set_thinking(provider_id, model_id, payload.thinking):
            raise HTTPException(404, "供应商、模型或推理等级不存在")
        return {"ok": True, **_providers_payload()}

    @app.post("/api/providers/fetch")
    def fetch_models(payload: FetchModelsPayload):
        """Fetch model IDs from the upstream provider; capable providers also return context, image, and reasoning metadata."""
        base_url = payload.base_url.strip().rstrip("/")
        api_key = payload.api_key.strip()
        if not api_key and payload.provider_id:
            provider = models.get_provider(payload.provider_id)
            api_key = provider.api_key if provider else ""
        if not base_url:
            raise HTTPException(400, "请填写 Base URL")
        if not api_key:
            raise HTTPException(400, "请先填写 API Key")

        headers = {"Authorization": f"Bearer {api_key}"}
        url = base_url + "/models"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # 网络/密钥/格式问题统一提示
            raise HTTPException(400, f"获取模型列表失败：{exc}") from exc

        ids: set[str] = set()
        metadata: dict[str, dict[str, Any]] = {}
        if isinstance(data, dict):
            items = data.get("data") or data.get("models") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                model_id = str(item["id"])
                ids.add(model_id)
                meta = _upstream_model_metadata(item)
                if meta:
                    metadata[model_id] = meta
            elif isinstance(item, str):
                ids.add(item)

        return {"base_url": base_url, "models": sorted(ids), "metadata": metadata}

    # ---- 工具 ----
    @app.get("/api/tools")
    def list_tools():
        return {"tools": tools.public()}

    @app.put("/api/tools/{tool_name}")
    def update_tool(tool_name: str, payload: ToolPayload):
        try:
            updated = tools.update(
                tool_name,
                enabled=payload.enabled,
                config=payload.config,
                clear=payload.clear,
            )
        except ValueError as exc:  # 字段类型校验（number / select）
            raise HTTPException(400, str(exc)) from exc
        if not updated:
            raise HTTPException(404, "工具不存在")
        return {"tools": tools.public()}

    # ---- 通用设置（界面偏好） ----
    @app.get("/api/preferences")
    def get_preferences():
        return {"preferences": preferences.get()}

    @app.put("/api/preferences")
    def update_preferences(payload: PreferencesPayload):
        try:
            updated = preferences.update(payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"preferences": updated}

    # ---- 工作区 ----
    @app.get("/api/workspaces")
    def list_workspaces():
        return workspaces.state()

    @app.post("/api/workspaces")
    def add_workspace(payload: AddWorkspacePayload):
        try:
            workspace = workspaces.add(payload.path, payload.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "workspace": workspace.public(),
            "workspaces": workspaces.state()["workspaces"],
            "active_id": workspaces.active_id,
        }

    @app.patch("/api/workspaces/{workspace_id}")
    def rename_workspace(workspace_id: str, payload: RenameWorkspacePayload):
        if not workspaces.rename(workspace_id, payload.name):
            raise HTTPException(400, "工作区不存在或名称无效")
        return {"workspaces": workspaces.state()["workspaces"]}

    @app.delete("/api/workspaces/{workspace_id}")
    def delete_workspace(workspace_id: str):
        if workspaces.get(workspace_id) is None:
            raise HTTPException(404, "工作区不存在")
        # 工作区删除时连带删除它的会话，避免留下无法归属的记录
        doomed = [
            meta.session_id
            for meta in sessions.list_sessions()
            if meta.workspace_id == workspace_id
        ]
        for session_id in doomed:
            sessions.delete(session_id)
        workspaces.remove(workspace_id)
        return {
            "ok": True,
            "removed_sessions": len(doomed),
            "workspaces": workspaces.state()["workspaces"],
            "active_id": workspaces.active_id,
        }

    @app.post("/api/workspaces/{workspace_id}/activate")
    def activate_workspace(workspace_id: str):
        if not workspaces.set_active(workspace_id):
            raise HTTPException(404, "工作区不存在")
        return {"ok": True, "active_id": workspace_id}

    @app.get("/api/workspaces/browse")
    def browse_workspace(path: str | None = None):
        try:
            return workspaces.browse(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/workspaces/pick")
    def pick_workspace_folder(payload: FolderPickPayload):
        """Open the system folder picker and return the chosen directory."""
        return _pick_directory(payload.initial, "选择工作区目录")

    # ---- 工作区文件浏览（右侧边栏） ----
    _SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".tmp", ".idea", ".alembic"}
    _UNSUPPORTED_SUFFIXES = {
        ".bmp", ".ico",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".7z", ".rar", ".gz", ".tar", ".exe", ".dll", ".so",
        ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".mov",
    }
    _IMAGE_SUFFIXES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    _IMAGE_LIMIT = 8_000_000

    def _workspace_root(workspace_id: str | None) -> Path:
        _, root = resolve_workspace(workspace_id, None)
        return Path(root)

    @app.get("/api/workspace/files")
    def list_workspace_files(path: str = "", workspace_id: str | None = None):
        root = _workspace_root(workspace_id)
        try:
            target = resolve_in_workspace(root, path or ".")
        except WorkspacePathError as exc:
            raise HTTPException(400, exc.message) from exc
        if not target.exists() or not target.is_dir():
            raise HTTPException(404, "目录不存在")
        entries = []
        try:
            children = sorted(
                target.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
        except OSError as exc:
            raise HTTPException(400, f"无法读取目录：{exc}") from exc
        for child in children:
            if child.is_dir() and child.name in _SKIP_DIRS:
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append({
                "name": child.name,
                "path": display_path(root, child),
                "type": "dir" if child.is_dir() else "file",
                "size": 0 if child.is_dir() else stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
            if len(entries) >= 500:
                break
        return {
            "path": display_path(root, target),
            "entries": entries,
            "truncated": len(entries) >= 500,
        }

    @app.get("/api/workspace/file")
    def read_workspace_file(path: str, workspace_id: str | None = None):
        try:
            root = _workspace_root(workspace_id)
        except HTTPException:
            # An absolute path outside the workspace needs no workspace at all.
            root = None
        try:
            # The side panel also previews paths outside the workspace (logs,
            # diffs). Credential files and the app data directory stay blocked.
            target = resolve_readable_path(path, root=root)
        except WorkspacePathError as exc:
            raise HTTPException(400, exc.message) from exc
        if not target.exists() or not target.is_file():
            raise HTTPException(404, "文件不存在")
        try:
            size = target.stat().st_size
            with target.open("rb") as handle:
                sample = handle.read(8192)
        except OSError as exc:
            raise HTTPException(400, f"无法读取文件：{exc}") from exc
        suffix = target.suffix.lower()
        binary = b"\x00" in sample or suffix in _UNSUPPORTED_SUFFIXES
        name = target.name
        markdown = suffix in {".md", ".markdown", ".mdx"} or name.lower().startswith("readme")
        image_mime = _IMAGE_SUFFIXES.get(suffix)
        if image_mime:
            if size > _IMAGE_LIMIT:
                return {
                    "path": display_path(root, target),
                    "name": name,
                    "size": size,
                    "supported": False,
                    "kind": "unsupported",
                    "content": "",
                    "truncated": False,
                    "reason": "图片过大，暂不支持预览",
                }
            try:
                payload = base64.b64encode(target.read_bytes()).decode("ascii")
            except OSError as exc:
                raise HTTPException(400, f"无法读取文件：{exc}") from exc
            return {
                "path": display_path(root, target),
                "name": name,
                "size": size,
                "supported": True,
                "kind": "image",
                "content": f"data:{image_mime};base64,{payload}",
                "truncated": False,
            }
        if binary:
            return {
                "path": display_path(root, target),
                "name": name,
                "size": size,
                "supported": False,
                "kind": "unsupported",
                "content": "",
                "truncated": False,
                "reason": "该格式暂不支持预览",
            }
        limit = 400_000
        try:
            with target.open("r", encoding="utf-8", errors="replace") as handle:
                content = handle.read(limit + 1)
        except OSError as exc:
            raise HTTPException(400, f"无法读取文件：{exc}") from exc
        truncated = len(content) > limit or size > 2_000_000
        return {
            "path": display_path(root, target),
            "name": name,
            "size": size,
            "supported": True,
            "kind": "markdown" if markdown else "text",
            "content": content[:limit],
            "truncated": truncated,
        }

    # ---- 会话 ----
    @app.get("/api/sessions")
    def list_sessions(workspace_id: str | None = None):
        items = [_session_summary(sessions, meta) for meta in sessions.list_sessions()]
        if workspace_id:
            items = [item for item in items if item["workspace_id"] == workspace_id]
        return {"sessions": items}

    @app.post("/api/sessions")
    def create_session(payload: SessionPayload):
        session_id = (payload.id or "").strip() or _new_session_id()
        sessions.delete(session_id)
        app.state.todos.clear(session_id)
        # 立即落一条空记录：第一轮对话还没结束就刷新页面时，会话不会凭空消失。
        # 标题留空，第一轮结束后仍会按老逻辑自动生成。
        meta = SessionMeta(
            session_id=session_id,
            workspace_id=payload.workspace_id.strip(),
        )
        sessions.save(session_id, [], meta)
        return {
            "session": {
                "id": session_id,
                "title": "新对话",
                "turns": 0,
                "messages": 0,
                "updated_at": meta.updated_at,
                "compactions": 0,
                "workspace_id": payload.workspace_id.strip(),
            }
        }

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str):
        sessions.delete(session_id)
        app.state.todos.clear(session_id)
        app.state.session_activations.pop(session_id, None)
        return {"ok": True}

    @app.patch("/api/sessions/{session_id}")
    def update_session(session_id: str, payload: SessionPatch):
        messages, meta = sessions.load(session_id)
        if payload.title is not None:
            meta.title = _clean_title(payload.title)[:80]
        if payload.pinned is not None:
            meta.pinned = bool(payload.pinned)
        if payload.archived is not None:
            meta.archived = bool(payload.archived)
        if not sessions.save(
            session_id,
            messages,
            meta,
            display_messages=sessions.load_display(session_id) or messages,
        ):
            raise HTTPException(500, "保存会话信息失败")
        return {"session": _session_summary(sessions, meta)}

    @app.post("/api/sessions/{session_id}/branch")
    def branch_session(session_id: str):
        _messages, meta = sessions.load(session_id)
        base = _session_title(sessions, session_id, meta).strip() or "新对话"
        pattern = re.compile(rf"^{re.escape(base)}\((\d+)\)$")
        used = 0
        for item in sessions.list_sessions():
            if item.workspace_id != meta.workspace_id:
                continue
            match = pattern.match(_session_title(sessions, item.session_id, item))
            if match:
                used = max(used, int(match.group(1)))
        title = f"{base}({used + 1})"
        new_id = f"{_new_session_id()}-{uuid.uuid4().hex[:4]}"
        created = sessions.duplicate(session_id, new_id, title)
        if created is None:
            raise HTTPException(404, "对话不存在或无法复制")
        return {"session": _session_summary(sessions, created)}

    @app.get("/api/sessions/{session_id}/messages")
    def session_messages(session_id: str):
        model_messages, meta = sessions.load(session_id)
        display_messages = sessions.load_display(session_id) or model_messages
        tokens, token_source = context_tokens(model_messages)
        return {
            "session_id": session_id,
            "messages": _serialize_messages(display_messages),
            "turns": meta.turns,
            "compactions": meta.compactions,
            "tokens": tokens,
            "token_source": token_source,
            "max_tokens": _context_limit(models.active()),
            "usage": SessionUsage.from_dict(meta.usage).to_dict(),
        }

    @app.post("/api/sessions/{session_id}/compact")
    def compact_session(session_id: str):
        """Compact the given session immediately without taking the conversation's session lock."""
        model_cfg = models.active()
        if model_cfg is None:
            raise HTTPException(400, "还没有可用的模型，请先在设置里添加")
        lock = app.state.locks[session_id]
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "当前会话正在生成，请先停止或等待本轮结束")
        try:
            messages, meta = sessions.load(session_id)
            if not messages:
                raise HTTPException(400, "当前会话还没有可压缩的历史")
            # 摘要器必须和真实请求共享系统提示词与工具声明，才能复用前缀缓存；
            # 因此这里走与对话相同的运行时装配，只丢弃随后不用的 agent。
            try:
                _workspace_id, workspace = resolve_workspace(meta.workspace_id, None)
            except HTTPException:
                workspace = str(Path(settings.default_workspace))
            prefs = preferences.get()
            try:
                _agent, summarizer = app.state.runtime_factory(
                    model_cfg,
                    tools.enabled_tools(),
                    workspace,
                    tool_configs=tools.configs(),
                    session_id=session_id,
                    terminal=str(prefs.get("terminal") or "powershell"),
                    activation=app.state.session_activations.setdefault(
                        session_id, ToolActivation()
                    ),
                    sandbox=app.state.sandbox,
                    sandbox_config=sandbox_config.get(),
                )
            except Exception as exc:
                raise HTTPException(400, str(exc)) from exc
            session = ChatSession(
                None,
                session_id,
                store=sessions,
                summarizer=summarizer,
                workspace_id=meta.workspace_id,
                model_context_window=model_cfg.context_length or None,
                max_output_tokens=model_cfg.max_output_tokens or None,
                keep_first_turn=bool(prefs.get("compact_keep_first_turn")),
                hydrate=True,
            )
            changed = session.compact(force=True)
            stats = session.stats()
            return {
                "compacted": changed,
                "tokens": stats["tokens"],
                "token_source": stats["token_source"],
                "max_tokens": stats["max_tokens"],
                "messages": len(session.messages),
                "turns": stats["turns"],
                "usage": stats["usage"],
            }
        finally:
            lock.release()

    # ---- 附件 ----
    _MAX_ATTACHMENT_BYTES = 20_000_000

    @app.post("/api/sessions/{session_id}/attachments")
    async def upload_attachment(
        session_id: str,
        request: Request,
        filename: str = "attachment",
        workspace_id: str | None = None,
    ):
        """Write a browser file into the hidden attachment directory inside the workspace and return its model-readable relative path."""
        _, root_text = resolve_workspace(workspace_id, None)
        root = Path(root_text)
        body = await request.body()
        if not body:
            raise HTTPException(400, "文件为空")
        if len(body) > _MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "附件不能超过 20 MB")
        name = _safe_attachment_name(filename)
        safe_session = re.sub(r"[^0-9A-Za-z_.-]+", "_", session_id or "default")[:64] or "default"
        folder = root / ".alembic" / "attachments" / safe_session
        try:
            folder.resolve().relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise HTTPException(400, "附件目录越界") from exc
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"无法创建附件目录：{exc}") from exc
        target = folder / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{name}"
        try:
            target.write_bytes(body)
        except OSError as exc:
            raise HTTPException(400, f"保存附件失败：{exc}") from exc
        return {
            "name": name,
            "path": display_path(root, target),
            "size": len(body),
            "kind": _attachment_kind(name),
        }

    @app.get("/api/sessions/{session_id}/todos")
    def session_todos(session_id: str):
        """Task list for the current session (restores the panel after a page refresh or session switch)."""
        return {"session_id": session_id, "items": app.state.todos.load(session_id)}

    # ---- 对话（SSE 流式） ----
    @app.post("/api/chat")
    def chat(payload: ChatPayload):
        message = (payload.message or "").strip()
        if not message and not payload.attachments and not payload.subagent_id:
            raise HTTPException(400, "消息不能为空")

        model_cfg = models.active()
        if model_cfg is None:
            raise HTTPException(400, "还没有可用的模型，请先在设置里添加")

        # 没有 session_id 时直接创建新会话：在工作区里直接输入发送即可开始对话。
        session_id = (payload.session_id or "").strip() or _new_session_id()
        workspace_id, workspace = resolve_workspace(payload.workspace_id, payload.workspace)

        if payload.attachments:
            if len(payload.attachments) > 10:
                raise HTTPException(400, "一次最多附加 10 个文件")
            lines = ["[Attached files]"]
            for raw in payload.attachments:
                try:
                    target = resolve_in_workspace(Path(workspace), raw)
                except WorkspacePathError as exc:
                    raise HTTPException(400, exc.message) from exc
                if not target.is_file():
                    raise HTTPException(400, f"附件不存在：{raw}")
                size = target.stat().st_size
                lines.append(
                    f"- {display_path(Path(workspace), target)} "
                    f"({_attachment_kind(target.name)}, {size} bytes)"
                )
            lines.append("[/Attached files]")
            message = "\n".join([*lines, "", message or "Please review the attached files."])

        if payload.process_id:
            with app.state.continued_lock:
                if payload.process_id in app.state.continued_processes:
                    raise HTTPException(409, "这个后台任务已经触发过续跑")
                app.state.continued_processes.add(payload.process_id)

        # 子代理的完成通知要进入父模型上下文；即使用户先发了新消息，也一并带上。
        pending_notifications = app.state.subagents.drain_notifications(session_id)

        if payload.subagent_id:
            with app.state.continued_lock:
                if payload.subagent_id in app.state.continued_subagents:
                    raise HTTPException(409, "这个子代理已经触发过续跑")
                app.state.continued_subagents.add(payload.subagent_id)
            if not any(payload.subagent_id in text for text in pending_notifications):
                raise HTTPException(409, "这个子代理没有待处理的完成通知")
            if not message:
                message = "A background subagent finished. Review its result and continue the task."
            payload.internal = True

        if pending_notifications:
            message = "\n\n".join([*pending_notifications, message])
            payload.internal = payload.internal or bool(payload.subagent_id)

        # 审批要在「流还在 yield」的时候等浏览器回执，所以把对话放进工作线程，
        # 主线程只负责从队列里取事件往外发；否则工具线程一阻塞，事件就发不出去。
        events: queue.Queue[tuple[str, dict] | None] = queue.Queue()

        def emit(kind: str, data: dict) -> None:
            events.put((kind, data))

        broker = ApprovalBroker(
            registry=app.state.approvals,
            emit=emit,
            timeout=settings.approval_timeout,
        )
        question_broker = QuestionBroker(
            registry=app.state.questions,
            emit=emit,
            timeout=settings.question_timeout,
        )
        gate: ApprovalGate | None = None
        prefs = preferences.get()
        permission_mode = str(prefs.get("permission_mode") or "workspace_write")
        terminal = str(prefs.get("terminal") or "powershell")
        gated = permission_tools(tools.enabled_tools(), permission_mode)
        if not settings.approval_enabled:
            # 关闭的只是「弹审批」，不是命令策略：高危命令仍然直接拒绝。
            gated = {}
        memo = app.state.approval_memo.setdefault(session_id, {})
        gate = ApprovalGate(
            policy=ApprovalPolicy(
                gated,
                memo,
                mode=permission_mode,
                risks=tool_risks(tools.enabled_tools()),
            ),
            ask=broker.ask,
        )

        try:
            history, _meta = sessions.load(session_id)
            recent_requests: list[str] = []
            for item in reversed(history):
                if getattr(item, "type", "") != "human":
                    continue
                text = message_text(item).strip()
                if text:
                    recent_requests.append(text)
                if len(recent_requests) == 2:
                    break
            request_text = "\n".join([*reversed(recent_requests), message])
            activation = app.state.session_activations.setdefault(
                session_id, ToolActivation()
            )
            cancel_event = threading.Event()
            app.state.cancels[session_id] = cancel_event
            agent, summarizer = app.state.runtime_factory(
                model_cfg,
                tools.enabled_tools(),
                workspace,
                tool_configs=tools.configs(),
                approval=gate,
                asker=question_broker.ask,
                session_id=session_id,
                todo_store=app.state.todos,
                process_manager=app.state.processes,
                emit=emit,
                request_text=request_text,
                terminal=terminal,
                activation=activation,
                cancel_event=cancel_event,
                sandbox=app.state.sandbox,
                sandbox_config=sandbox_config.get(),
                subagents=app.state.subagents,
                workspace_id=workspace_id,
            )
        except Exception as exc:
            if "cancel_event" in locals():
                app.state.cancels.pop(session_id, None)
            raise HTTPException(400, str(exc)) from exc

        session = ChatSession(
            agent,
            session_id,
            store=sessions,
            summarizer=summarizer,
            workspace_id=workspace_id,
            workspace_path=workspace,
            model_context_window=model_cfg.context_length or None,
            max_output_tokens=model_cfg.max_output_tokens or None,
            keep_first_turn=bool(prefs.get("compact_keep_first_turn")),
        )

        def run_turn() -> None:
            lock = app.state.locks[session_id]
            try:
                with lock:
                    for kind, data in session.ask_stream(message, internal=payload.internal):
                        if kind == "delta":
                            events.put(("delta", {"text": data}))
                        elif kind == "thought":
                            events.put((
                                "thought",
                                data if isinstance(data, dict) else {"text": data},
                            ))
                        elif kind == "tool":
                            events.put(("tool", data))
                        elif kind == "tool_result":
                            events.put(("tool_result", data))
                        elif kind == "compacted":
                            events.put(("compacted", {}))
                        elif kind == "done":
                            stats = session.stats()
                            events.put(
                                (
                                    "done",
                                    {
                                        "session_id": session_id,
                                        "text": data.text,
                                        "tokens": data.context_tokens,
                                        "token_source": data.token_source,
                                        "max_tokens": stats["max_tokens"],
                                        "messages": len(session.messages),
                                        "turns": session.meta.turns,
                                        # 本轮 + 会话累计用量，含提供方的缓存读/写桶
                                        "usage": data.usage_dict(),
                                        "session_usage": stats["usage"],
                                    },
                                )
                            )
            except Exception as exc:
                events.put(("error", {"message": str(exc)}))
            finally:
                if app.state.cancels.get(session_id) is cancel_event:
                    app.state.cancels.pop(session_id, None)
                question_broker.close()
                broker.close()
                events.put(None)  # 结束哨兵

        threading.Thread(target=run_turn, name=f"chat-{session_id}", daemon=True).start()

        def event_stream():
            try:
                yield _sse(
                    "meta",
                    {
                        "session_id": session_id,
                        "model_id": model_cfg.id,
                        "model": model_cfg.name,
                        "workspace": workspace,
                    },
                )
                while True:
                    item = events.get()
                    if item is None:
                        break
                    kind, data = item
                    yield _sse(kind, data)
            finally:
                # 客户端中途断开时，别让工具线程还挂在审批/提问上等 5 分钟
                cancel_event.set()
                question_broker.close()
                broker.close()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- 停止当前轮 ----
    @app.post("/api/sessions/{session_id}/cancel")
    def cancel_session(session_id: str, background: bool = True):
        """Stop generation: interrupt foreground commands in the current turn and, by default, stop this session's background tasks."""
        event = app.state.cancels.get(session_id)
        if event is not None:
            event.set()
        stopped = app.state.processes.stop_session(session_id) if background else 0
        return {
            "ok": True,
            "cancelled": event is not None,
            "stopped_processes": stopped,
        }

    # ---- 审批 ----
    @app.post("/api/approvals/{request_id}")
    def resolve_approval(request_id: str, payload: ApprovalPayload):
        decision = (payload.decision or "").strip().lower()
        if decision not in VALID_DECISIONS:
            raise HTTPException(400, "decision 只能是 once / always / deny")
        if not app.state.approvals.resolve(request_id, decision):
            raise HTTPException(404, "该审批请求不存在或已过期")
        return {"ok": True, "decision": decision}

    @app.get("/api/approvals")
    def list_approvals(session_id: str = ""):
        """List the operations remembered as "always allow" for a session."""
        sid = (session_id or "default").strip() or "default"
        items = list(app.state.approval_memo.get(sid, {}).values())
        return {"session_id": sid, "items": items}

    @app.delete("/api/approvals")
    def clear_approvals(session_id: str = ""):
        """Revoke all "always allow" entries for a session."""
        sid = (session_id or "default").strip() or "default"
        return {"ok": True, "cleared": len(app.state.approval_memo.pop(sid, {}) or {})}

    # ---- 提问 ----
    @app.post("/api/questions/{question_id}")
    def answer_question(question_id: str, payload: AnswerPayload):
        answer = payload.answer or ""
        if not app.state.questions.resolve(question_id, answer):
            raise HTTPException(404, "该提问不存在或已过期")
        return {"ok": True}

    return app


app = create_app()
