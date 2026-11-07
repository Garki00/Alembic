# web/server.py
"""网页工作区后端：会话、模型设置、工具设置、工作区与流式对话。

启动：
    .venv\\Scripts\\python.exe -m uvicorn web.server:app --port 8000
"""

from __future__ import annotations

import json
import os
import queue
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.approval import (
    VALID_DECISIONS,
    ApprovalGate,
    ApprovalPolicy,
    gated_tools,
)
from agents.context import ChatSession, SessionStore, message_text
from config.settings import settings
from config.store import (
    AppPaths,
    ModelStore,
    PreferencesStore,
    ToolStore,
    WorkspaceStore,
)
from tools.processes import ProcessManager
from tools.todo import TodoStore
from web.approvals import ApprovalBroker, ApprovalRegistry
from web.auth import install_auth
from web.questions import QuestionBroker, QuestionRegistry
from web.runtime import build_runtime

# 前后端分离后，后端只提供 /api；页面由 frontend/ 的 Vite 应用提供。


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class ModelPayload(BaseModel):
    id: str | None = None
    name: str = ""
    provider: str = "deepseek"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.0


class FetchModelsPayload(BaseModel):
    base_url: str = ""
    api_key: str = ""
    provider: str = "deepseek"


class ToolPayload(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    clear: list[str] | None = None


class ChatPayload(BaseModel):
    session_id: str = "default"
    message: str
    workspace_id: str | None = None
    workspace: str | None = None  # 旧字段，仅用于兼容


class AddWorkspacePayload(BaseModel):
    path: str
    name: str = ""


class RenameWorkspacePayload(BaseModel):
    name: str = ""


class SessionPayload(BaseModel):
    id: str | None = None
    workspace_id: str = ""


class ApprovalPayload(BaseModel):
    decision: str = "deny"  # once | always | deny


class AnswerPayload(BaseModel):
    answer: str = ""


class PreferencesPayload(BaseModel):
    transcript_view: str | None = None
    collapse_timing: str | None = None
    developer_tools: bool | None = None


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _session_title(session_store: SessionStore, session_id: str) -> str:
    messages, _ = session_store.load(session_id)
    for message in messages:
        if getattr(message, "type", "") == "human":
            title = message_text(message).strip().replace("\n", " ")
            if title:
                return title[:40]
    return "新会话"


def _session_summary(session_store: SessionStore, meta) -> dict[str, Any]:
    return {
        "id": meta.session_id,
        "title": _session_title(session_store, meta.session_id),
        "turns": meta.turns,
        "messages": meta.message_count,
        "updated_at": meta.updated_at,
        "compactions": meta.compactions,
        "workspace_id": meta.workspace_id,
    }


def _serialize_messages(messages: Iterable[Any]) -> list[dict[str, Any]]:
    role_map = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}
    result: list[dict[str, Any]] = []
    for message in messages:
        role = role_map.get(getattr(message, "type", ""), getattr(message, "type", "unknown"))
        item: dict[str, Any] = {"role": role, "content": message_text(message)}
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


def _new_session_id() -> str:
    return f"chat-{datetime.now():%Y%m%d-%H%M%S}"


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_app(
    data_dir: str | Path | None = None,
    memory_dir: str | Path | None = None,
    runtime_factory=build_runtime,
    auth_token: str | None = None,
) -> FastAPI:
    paths = AppPaths(data_dir)
    models = ModelStore(paths.models_file)
    tools = ToolStore(paths.tools_file)
    workspaces = WorkspaceStore(paths.workspace_file, default=settings.default_workspace)
    preferences = PreferencesStore(paths.preferences_file)
    sessions = SessionStore(memory_dir or settings.memory_dir)

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
    app.state.runtime_factory = runtime_factory
    app.state.locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
    # 审批：待回执表（跨请求）+ 每个会话已记住的「始终允许」
    app.state.approvals = ApprovalRegistry()
    app.state.approval_memo: dict[str, dict[str, dict]] = {}
    # 提问：与审批同构的中继，回答走 POST /api/questions/{id}
    app.state.questions = QuestionRegistry()
    # 任务清单与后台进程都放在应用级，跨轮次共享
    app.state.todos = TodoStore(sessions.directory / "todos")
    app.state.processes = ProcessManager(sessions.directory.parent / "logs" / "processes")

    def resolve_workspace(workspace_id: str | None, legacy_path: str | None) -> tuple[str, str]:
        """把请求里的工作区解析成 ``(id, 绝对路径)``。"""
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

    # ---- API 根与健康检查 ----
    @app.get("/")
    def index():
        """API 说明。页面请打开前端开发服务器（默认 http://127.0.0.1:5173）。"""
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
        return {"ok": True, "model": models.active_id, "time": datetime.now().isoformat(timespec="seconds")}

    # ---- 初始化数据 ----
    @app.get("/api/bootstrap")
    def bootstrap():
        # 首次访问时把默认模型/工具配置落盘，方便用户查看与备份
        models.ensure_saved()
        tools.ensure_saved()
        workspaces.ensure_saved()
        active = models.active()
        return {
            "models": [item.public() for item in models.list()],
            "active_id": models.active_id,
            "active_model": active.public() if active else None,
            "needs_setup": models.needs_setup(),
            "tools": tools.public(),
            "workspaces": workspaces.state()["workspaces"],
            "active_workspace_id": workspaces.active_id,
            "sessions": [_session_summary(sessions, meta) for meta in sessions.list_sessions()],
            "preferences": preferences.get(),
        }

    # ---- 模型 ----
    @app.get("/api/models")
    def list_models():
        return {"models": [item.public() for item in models.list()], "active_id": models.active_id}

    @app.post("/api/models")
    def save_model(payload: ModelPayload):
        if not payload.model and not payload.name:
            raise HTTPException(400, "请至少填写模型名称")
        saved = models.upsert(payload.model_dump())
        return {"model": saved.public(), "active_id": models.active_id}

    @app.delete("/api/models/{model_id}")
    def delete_model(model_id: str):
        if not models.delete(model_id):
            raise HTTPException(404, "模型不存在")
        return {"ok": True, "active_id": models.active_id}

    @app.post("/api/models/{model_id}/activate")
    def activate_model(model_id: str):
        if not models.set_active(model_id):
            raise HTTPException(404, "模型不存在")
        return {"ok": True, "active_id": model_id}

    @app.post("/api/models/fetch")
    def fetch_models(payload: FetchModelsPayload):
        """从上游拉取模型列表，并**全部**加入可选模型（已存在的跳过）。"""
        base_url = (payload.base_url.strip()).rstrip("/")
        if not base_url:
            raise HTTPException(400, "请填写 Base URL")
        api_key = payload.api_key.strip()
        if not api_key:
            active = models.active()
            api_key = active.api_key if active else ""
        if not api_key:
            raise HTTPException(400, "请先填写 API Key，或先配置一个可用模型")

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        url = base_url + "/models"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # 网络/密钥/格式问题统一提示
            raise HTTPException(400, f"获取模型列表失败：{exc}") from exc

        ids: set[str] = set()
        if isinstance(data, dict):
            items = data.get("data") or data.get("models") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]))
            elif isinstance(item, str):
                ids.add(item)

        existing = {(item.base_url.rstrip("/"), item.model) for item in models.list()}
        added: list[str] = []
        skipped: list[str] = []
        for model_id in sorted(ids):
            if (base_url, model_id) in existing:
                skipped.append(model_id)
                continue
            models.upsert(
                {
                    "name": model_id,
                    "provider": payload.provider,
                    "model": model_id,
                    "base_url": base_url,
                    "api_key": api_key,
                    "temperature": 0,
                }
            )
            added.append(model_id)
            existing.add((base_url, model_id))

        return {
            "base_url": base_url,
            "fetched": sorted(ids),
            "added": added,
            "skipped": skipped,
            "models": [item.public() for item in models.list()],
            "active_id": models.active_id,
        }

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
        return {
            "session": {
                "id": session_id,
                "title": "新对话",
                "turns": 0,
                "messages": 0,
                "updated_at": "",
                "compactions": 0,
                "workspace_id": payload.workspace_id.strip(),
            }
        }

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str):
        sessions.delete(session_id)
        app.state.todos.clear(session_id)
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/messages")
    def session_messages(session_id: str):
        messages, meta = sessions.load(session_id)
        return {
            "session_id": session_id,
            "messages": _serialize_messages(messages),
            "turns": meta.turns,
            "compactions": meta.compactions,
        }

    @app.get("/api/sessions/{session_id}/todos")
    def session_todos(session_id: str):
        """当前会话的任务清单（网页刷新/切换会话后恢复面板）。"""
        return {"session_id": session_id, "items": app.state.todos.load(session_id)}

    # ---- 对话（SSE 流式） ----
    @app.post("/api/chat")
    def chat(payload: ChatPayload):
        message = (payload.message or "").strip()
        if not message:
            raise HTTPException(400, "消息不能为空")

        model_cfg = models.active()
        if model_cfg is None:
            raise HTTPException(400, "还没有可用的模型，请先在设置里添加")

        session_id = (payload.session_id or "").strip() or "default"
        workspace_id, workspace = resolve_workspace(payload.workspace_id, payload.workspace)

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
        if settings.approval_enabled:
            memo = app.state.approval_memo.setdefault(session_id, {})
            gate = ApprovalGate(
                policy=ApprovalPolicy(gated_tools(tools.enabled_tools()), memo),
                ask=broker.ask,
            )

        try:
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
            )
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

        session = ChatSession(
            agent,
            session_id,
            store=sessions,
            summarizer=summarizer,
            workspace_id=workspace_id,
        )

        def run_turn() -> None:
            lock = app.state.locks[session_id]
            try:
                with lock:
                    for kind, data in session.ask_stream(message):
                        if kind == "delta":
                            events.put(("delta", {"text": data}))
                        elif kind == "thought":
                            events.put(("thought", {"text": data}))
                        elif kind == "tool":
                            events.put(("tool", data))
                        elif kind == "tool_result":
                            events.put(("tool_result", data))
                        elif kind == "compacted":
                            events.put(("compacted", {}))
                        elif kind == "done":
                            events.put(
                                (
                                    "done",
                                    {
                                        "session_id": session_id,
                                        "text": data.text,
                                        "tokens": data.context_tokens,
                                        "token_source": data.token_source,
                                        "messages": len(session.messages),
                                        "turns": session.meta.turns,
                                    },
                                )
                            )
            except Exception as exc:
                events.put(("error", {"message": str(exc)}))
            finally:
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
                question_broker.close()
                broker.close()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
        """查看某个会话里「始终允许」记住了哪些操作。"""
        sid = (session_id or "default").strip() or "default"
        items = list(app.state.approval_memo.get(sid, {}).values())
        return {"session_id": sid, "items": items}

    @app.delete("/api/approvals")
    def clear_approvals(session_id: str = ""):
        """撤销某个会话里所有「始终允许」。"""
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
