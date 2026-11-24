# agents/subagents/manager.py
"""Run and supervise subagents.

The manager owns the task table, child sessions, cancellation, background
delivery, completion notifications, and the event stream consumed by the UI. A
bound :class:`SubagentService` is created for each parent turn; tools call it and
never touch the manager's internals.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from langchain_core.messages import ToolMessage

from agents.approval import ApprovalGate, ApprovalPolicy, permission_tools, tool_risks
from agents.subagents.profiles import (
    DEFAULT_SUBAGENT_TYPE,
    AgentProfile,
    find_profile,
    load_profiles,
)
from agents.subagents.prompts import (
    format_background_launch,
    format_completed_output,
    format_failed_output,
    format_task_notification,
)
from agents.subagents.runtime import (
    SubagentBuildError,
    build_child_agent,
    build_child_session,
    build_child_system_prompt,
    build_child_tools,
    resolve_model_config,
)
from config.settings import settings
from tools.context import SessionRef, ToolContext
from tools.errors import tool_error

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "stopped"}
_NOTIFICATION_LIMIT = 20


class SubagentCancelled(Exception):
    """Internal signal: the task was stopped before producing a report."""


@dataclass
class LaunchContext:
    """Everything one parent turn contributes to a child run."""

    session_id: str
    workspace: Path
    workspace_id: str = ""
    model: Any = None
    model_cfg: Any = None
    model_factory: Callable[[Any], Any] | None = None
    enabled_tools: set[str] = field(default_factory=set)
    visible_tools: set[str] = field(default_factory=set)
    tool_configs: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    approval: ApprovalGate | None = None
    asker: Callable | None = None
    emit: Callable[[str, dict], None] | None = None
    terminal: str = "powershell"
    surface: str = "web"
    engine: str | None = None
    activation: Any = None
    sandbox: Any = None
    sandbox_policy: Any = None
    sandbox_config: Mapping[str, Any] = field(default_factory=dict)
    cancel_event: Any = None
    vision: Callable[[str, str], str] | None = None
    todo_store: Any = None
    process_manager: Any = None


@dataclass
class SubagentTask:
    """One child execution, foreground or background."""

    id: str
    parent_session_id: str
    agent_type: str
    description: str
    prompt: str
    child_session_id: str
    background: bool = False
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    report: str = ""
    error: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    tool_uses: int = 0
    duration_ms: int = 0
    output_file: str = ""
    model: str = ""
    allowed_tools: tuple[str, ...] = ()
    notified: bool = False
    detached: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    pending_messages: list[str] = field(default_factory=list)
    session: Any = None
    profile: AgentProfile | None = None
    emitter: Callable[[str, dict], None] | None = None
    turn_ask: Callable | None = None
    thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    resumed: bool = False

    def snapshot(self) -> dict[str, Any]:
        """Serializable status used by the HTTP API."""
        return {
            "id": self.id,
            "parent_session_id": self.parent_session_id,
            "agent_type": self.agent_type,
            "description": self.description,
            "prompt": self.prompt,
            "child_session_id": self.child_session_id,
            "status": self.status,
            "background": self.background,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "report": self.report,
            "error": self.error,
            "usage": self.usage,
            "tool_uses": self.tool_uses,
            "duration_ms": self.duration_ms,
            "output_file": self.output_file,
            "model": self.model,
            "allowed_tools": list(self.allowed_tools),
            "notified": self.notified,
        }


class _CombinedCancel:
    """Cancellation view over the task flag and the parent turn flag."""

    def __init__(self, task: SubagentTask, parent: Any = None) -> None:
        self._task = task
        self._parent = parent

    def is_set(self) -> bool:
        return self._task.cancel.is_set() or bool(
            self._parent is not None and self._parent.is_set()
        )

    def set(self) -> None:
        self._task.cancel.set()


class SubagentService:
    """Turn-scoped facade the ``agent`` and ``send_message`` tools call."""

    def __init__(self, manager: "SubagentManager", ctx: LaunchContext) -> None:
        self._manager = manager
        self._ctx = ctx

    def run(
        self,
        *,
        description: str,
        prompt: str,
        agent_type: str = "",
        run_in_background: bool = False,
        tool_call_id: str = "",
    ) -> str:
        """Launch one child agent and return the model-visible tool result."""
        return self._manager.launch(
            self._ctx,
            description=description,
            prompt=prompt,
            agent_type=agent_type,
            run_in_background=run_in_background,
            tool_call_id=tool_call_id,
        )

    def send(
        self,
        *,
        agent_id: str,
        message: str,
        summary: str = "",
        tool_call_id: str = "",
    ) -> str:
        """Continue an existing child with its context intact."""
        return self._manager.send_message(
            self._ctx,
            agent_id=agent_id,
            message=message,
            summary=summary,
            tool_call_id=tool_call_id,
        )

    def list_tasks(self) -> list[dict[str, Any]]:
        """Return tasks owned by the current parent session."""
        return self._manager.list_tasks(self._ctx.session_id)

    def profiles(self) -> list[AgentProfile]:
        """Return the profile set visible to this turn."""
        return load_profiles(
            user_dir=self._manager.profiles_dir,
            workspace=self._ctx.workspace,
        )


class SubagentManager:
    """Own the subagent task table and child lifecycle."""

    def __init__(
        self,
        *,
        hub: Any = None,
        approval_registry: Any = None,
        profiles_dir: str | Path | None = None,
        store_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        max_concurrent: int | None = None,
        auto_background_seconds: float | None = None,
        session_factory: Callable[[SubagentTask, LaunchContext, AgentProfile], Any] | None = None,
    ) -> None:
        self.hub = hub
        self.approval_registry = approval_registry
        self.profiles_dir = Path(profiles_dir or settings.subagent_profiles_dir)
        self.store_dir = Path(store_dir or settings.subagent_store_dir)
        self.output_dir = Path(
            output_dir or Path(settings.app_home) / "subagents"
        )
        self.max_concurrent = int(max_concurrent or settings.max_concurrent_agents)
        self.auto_background_seconds = float(
            settings.subagent_auto_background
            if auto_background_seconds is None
            else auto_background_seconds
        )
        # Tests inject a fake session; production builds a real child runtime.
        self._session_factory = session_factory
        self._tasks: dict[str, SubagentTask] = {}
        self._notifications: dict[str, list[tuple[str, str]]] = {}
        self._lock = threading.Lock()

    # -- binding ---------------------------------------------------------

    def bind(self, ctx: LaunchContext) -> SubagentService:
        """Create the per-turn service object injected into the tool context."""
        return SubagentService(self, ctx)

    # -- query -----------------------------------------------------------

    def get(self, agent_id: str) -> SubagentTask | None:
        with self._lock:
            task = self._tasks.get(agent_id)
        if task is not None:
            return task
        return self._load_task(agent_id)

    def list_tasks(self, session_id: str) -> list[dict[str, Any]]:
        self._load_session_tasks(session_id)
        with self._lock:
            tasks = [
                task for task in self._tasks.values()
                if task.parent_session_id == session_id
            ]
        tasks.sort(key=lambda task: task.started_at)
        return [task.snapshot() for task in tasks]

    def load_messages(self, task: SubagentTask) -> list[Any]:
        """Return the child transcript, live when present and from disk after a restart."""
        if task.session is not None:
            return list(getattr(task.session, "display_messages", []) or [])
        from agents.context import SessionStore

        store = SessionStore(self.store_dir / _safe_slug(task.parent_session_id))
        return store.load_display(task.child_session_id)

    def _tasks_dir(self, session_id: str) -> Path:
        return self.store_dir / _safe_slug(session_id) / "tasks"

    def _record_path(self, task: SubagentTask) -> Path:
        return self._tasks_dir(task.parent_session_id) / f"{task.id}.json"

    def _persist(self, task: SubagentTask) -> None:
        """Write the task record so the panel and transcript survive a restart."""
        try:
            payload = {
                **task.snapshot(),
                "prompt": task.prompt,
                "saved_at": time.time(),
            }
            target = self._record_path(task)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, target)
        except OSError as exc:
            logger.warning("写入子代理记录失败 %s：%s", task.id, exc)

    def _load_session_tasks(self, session_id: str) -> None:
        directory = self._tasks_dir(session_id)
        if not directory.is_dir():
            return
        with self._lock:
            known = set(self._tasks)
        for path in directory.glob("*.json"):
            if path.stem in known:
                continue
            task = self._read_record(path)
            if task is None or task.parent_session_id != session_id:
                continue
            with self._lock:
                self._tasks.setdefault(task.id, task)
                known.add(task.id)

    def _load_task(self, agent_id: str) -> SubagentTask | None:
        if not self.store_dir.is_dir():
            return None
        matches = list(self.store_dir.glob(f"*/tasks/{agent_id}.json"))
        if not matches:
            return None
        task = self._read_record(matches[0])
        if task is None:
            return None
        with self._lock:
            self._tasks.setdefault(task.id, task)
        return task

    def _read_record(self, path: Path) -> SubagentTask | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("读取子代理记录失败 %s：%s", path, exc)
            return None
        if not isinstance(payload, dict) or not payload.get("id"):
            return None
        status = str(payload.get("status") or "completed")
        error = str(payload.get("error") or "")
        if status == "running":
            # A record left as running means the host stopped before finishing.
            status = "stopped"
            error = error or "The host restarted before this agent finished."
        task = SubagentTask(
            id=str(payload["id"]),
            parent_session_id=str(payload.get("parent_session_id") or ""),
            agent_type=str(payload.get("agent_type") or ""),
            description=str(payload.get("description") or ""),
            prompt=str(payload.get("prompt") or ""),
            child_session_id=str(payload.get("child_session_id") or payload["id"]),
            background=bool(payload.get("background")),
            status=status,
            started_at=float(payload.get("started_at") or 0),
            finished_at=payload.get("finished_at"),
            report=str(payload.get("report") or ""),
            error=error,
            usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
            tool_uses=int(payload.get("tool_uses") or 0),
            duration_ms=int(payload.get("duration_ms") or 0),
            output_file=str(payload.get("output_file") or ""),
            model=str(payload.get("model") or ""),
            allowed_tools=tuple(payload.get("allowed_tools") or ()),
            notified=bool(payload.get("notified")),
            detached=True,
        )
        task.done.set()
        return task

    def running_count(self, session_id: str | None = None) -> int:
        with self._lock:
            tasks = list(self._tasks.values())
        return sum(
            1
            for task in tasks
            if task.status == "running" and (session_id is None or task.parent_session_id == session_id)
        )

    def drain_notifications(self, session_id: str) -> list[str]:
        """Take pending background completion texts for one parent session."""
        with self._lock:
            items = self._notifications.pop(session_id, [])
            for task_id, _text in items:
                task = self._tasks.get(task_id)
                if task is not None:
                    task.notified = True
        return [text for _task_id, text in items]

    def has_notification(self, session_id: str, agent_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(agent_id)
            return bool(task and task.parent_session_id == session_id and task.notified)

    def stop(self, agent_id: str, *, initiator: str = "user") -> bool:
        """Request cancellation; returns False when the task is unknown or terminal."""
        task = self.get(agent_id)
        if task is None or task.status in TERMINAL_STATUSES:
            return False
        task.cancel.set()
        self._emit(
            task,
            "subagent_progress",
            {"phase": "stopping", "text": f"stopped by {initiator}"},
        )
        return True

    # -- launch ----------------------------------------------------------

    def launch(
        self,
        ctx: LaunchContext,
        *,
        description: str,
        prompt: str,
        agent_type: str = "",
        run_in_background: bool = False,
        tool_call_id: str = "",
    ) -> str:
        """Resolve the profile, start the child, and await the foreground result."""
        if not settings.subagents_enabled:
            return tool_error("subagents_disabled", "Subagents are disabled by configuration.")
        cleaned_prompt = str(prompt or "").strip()
        if not cleaned_prompt:
            return tool_error("subagent_empty_prompt", "The subagent task prompt is empty.")
        profiles = load_profiles(user_dir=self.profiles_dir, workspace=ctx.workspace)
        profile = find_profile(profiles, agent_type)
        if profile is None:
            names = ", ".join(item.name for item in profiles)
            return tool_error(
                "subagent_unknown_type",
                f"Unknown subagent type {agent_type!r}. Available types: {names}.",
            )
        task = self._new_task(
            ctx,
            profile=profile,
            description=str(description or profile.name).strip() or profile.name,
            prompt=cleaned_prompt,
            background=bool(run_in_background or profile.background),
            tool_call_id=tool_call_id,
        )
        with self._lock:
            running = sum(1 for item in self._tasks.values() if item.status == "running")
            if running >= self.max_concurrent:
                self._tasks.pop(task.id, None)
                return tool_error(
                    "subagent_busy",
                    f"Too many running subagents (limit {self.max_concurrent}); "
                    "wait for one to finish or run this task in the parent agent.",
                )
            self._tasks[task.id] = task
        self._start(task, ctx, prompt=task.prompt)
        if task.background:
            return format_background_launch(
                agent_id=task.id,
                output_file=self._output_path(task),
                can_read_output=bool({"read_file", "run_command"} & set(ctx.enabled_tools)),
            )
        return self._await(task, ctx)

    def send_message(
        self,
        ctx: LaunchContext,
        *,
        agent_id: str,
        message: str,
        summary: str = "",
        tool_call_id: str = "",
    ) -> str:
        """Queue a message for a running child, or resume a finished one."""
        task = self.get(str(agent_id or "").strip())
        if task is None or task.parent_session_id != ctx.session_id:
            return tool_error(
                "subagent_unknown",
                f"Unknown subagent {agent_id!r} for this session.",
            )
        text = str(message or "").strip()
        if not text:
            return tool_error("subagent_empty_message", "The message is empty.")
        if task.status == "running":
            with task.lock:
                task.pending_messages.append(text)
            return (
                f"Message queued for agent {task.id}. It will be delivered after the "
                "current step; results arrive with the agent's report or completion "
                "notification."
            )
        if task.session is None:
            return tool_error(
                "subagent_unavailable",
                f"Agent {task.id} has no resumable session.",
            )
        # Resume the existing child session with its history intact.
        task.status = "running"
        task.error = ""
        task.finished_at = None
        task.done = threading.Event()
        task.detached = bool(task.background)
        task.resumed = True
        self._persist(task)
        self._emit(
            task,
            "subagent_started",
            {
                "agent_type": task.agent_type,
                "description": task.description,
                "background": task.background,
                "child_session_id": task.child_session_id,
                "model": task.model,
                "allowed_tools": list(task.allowed_tools),
                "prompt": text,
                "resumed": True,
                "tool_call_id": tool_call_id,
            },
        )
        self._start(task, ctx, prompt=text, resume=True)
        if task.background:
            return format_background_launch(
                agent_id=task.id,
                output_file=self._output_path(task),
                can_read_output=bool({"read_file", "run_command"} & set(ctx.enabled_tools)),
            )
        return self._await(task, ctx)

    # -- task lifecycle --------------------------------------------------

    def _new_task(
        self,
        ctx: LaunchContext,
        *,
        profile: AgentProfile,
        description: str,
        prompt: str,
        background: bool,
        tool_call_id: str,
    ) -> SubagentTask:
        agent_id = f"subagent-{uuid4().hex[:8]}"
        task = SubagentTask(
            id=agent_id,
            parent_session_id=ctx.session_id,
            agent_type=profile.name,
            description=description,
            prompt=prompt,
            child_session_id=agent_id,
            background=background,
            profile=profile,
            emitter=ctx.emit,
            turn_ask=(ctx.approval.ask if ctx.approval is not None else None),
            detached=background,
        )
        task.allowed_tools = profile.child_tools(ctx.visible_tools or ctx.enabled_tools)
        return task

    def _start(
        self,
        task: SubagentTask,
        ctx: LaunchContext,
        *,
        prompt: str,
        resume: bool = False,
    ) -> None:
        thread = threading.Thread(
            target=self._run,
            args=(task, ctx, prompt, resume),
            name=f"subagent-{task.id}",
            daemon=True,
        )
        task.thread = thread
        thread.start()

    def _await(self, task: SubagentTask, ctx: LaunchContext) -> str:
        """Wait for a foreground child, honoring parent cancellation and auto-background."""
        deadline = (
            time.monotonic() + self.auto_background_seconds
            if self.auto_background_seconds > 0
            else None
        )
        while not task.done.wait(0.12):
            if ctx.cancel_event is not None and ctx.cancel_event.is_set():
                task.cancel.set()
                task.done.wait(5)
                return tool_error(
                    "subagent_cancelled",
                    "The parent turn was cancelled; the subagent was stopped.",
                )
            if deadline is not None and time.monotonic() >= deadline:
                task.background = True
                task.detached = True
                self._persist(task)
                self._emit(
                    task,
                    "subagent_backgrounded",
                    {
                        "description": task.description,
                        "agent_type": task.agent_type,
                        "status": "running",
                    },
                )
                return format_background_launch(
                    agent_id=task.id,
                    output_file=self._output_path(task),
                    can_read_output=bool(
                        {"read_file", "run_command"} & set(ctx.enabled_tools)
                    ),
                )
        if task.status == "stopped":
            return tool_error(
                "subagent_stopped",
                "The subagent was stopped before it returned a report.",
            )
        if task.status == "failed":
            return format_failed_output(
                error=task.error or "unknown error",
                report=task.report,
                agent_id=task.id,
                usage=task.usage,
                tool_uses=task.tool_uses,
                duration_ms=task.duration_ms,
            )
        return format_completed_output(
            report=task.report,
            agent_id=task.id,
            usage=task.usage,
            tool_uses=task.tool_uses,
            duration_ms=task.duration_ms,
        )

    def _run(
        self,
        task: SubagentTask,
        ctx: LaunchContext,
        prompt: str,
        resume: bool,
    ) -> None:
        started = time.monotonic()
        try:
            if not resume:
                self._persist(task)
                self._prepare(task, ctx)
                self._emit(
                    task,
                    "subagent_started",
                    {
                        "agent_type": task.agent_type,
                        "description": task.description,
                        "background": task.background,
                        "child_session_id": task.child_session_id,
                        "model": task.model,
                        "allowed_tools": list(task.allowed_tools),
                        "prompt": task.prompt,
                        "tool_call_id": "",
                    },
                )
            self._run_prompt(task, prompt)
            if not task.cancel.is_set():
                task.status = "completed"
        except SubagentCancelled:
            task.status = "stopped"
        except SubagentBuildError as exc:
            task.status = "failed"
            task.error = f"[{exc.code}] {exc}"
        except Exception as exc:  # keep the parent turn alive on any child failure
            task.status = "failed"
            task.error = str(exc) or exc.__class__.__name__
            logger.warning("子代理 %s 失败：%s", task.id, exc, exc_info=True)
        finally:
            task.duration_ms = int((time.monotonic() - started) * 1000)
            task.finished_at = time.time()
            # Emit the terminal event before releasing waiters so a foreground
            # caller observes the same ordering as the event stream.
            try:
                self._finish(task)
            finally:
                task.done.set()

    def _prepare(self, task: SubagentTask, ctx: LaunchContext) -> None:
        profile = task.profile or find_profile(
            load_profiles(user_dir=self.profiles_dir, workspace=ctx.workspace),
            DEFAULT_SUBAGENT_TYPE,
        )
        if profile is None:
            raise SubagentBuildError("subagent_unknown_type", "No usable subagent profile.")
        tool_names = profile.child_tools(ctx.visible_tools or ctx.enabled_tools)
        if not tool_names:
            raise SubagentBuildError(
                "subagent_no_tools",
                f"Profile {profile.name!r} exposes no enabled tools in this session.",
            )
        task.allowed_tools = tool_names

        if self._session_factory is not None:
            task.session = self._session_factory(task, ctx, profile)
            task.model = _model_label(ctx.model_cfg)
            return

        from tools.catalog import ToolActivation, entry_names_for_tools
        from tools.registry import TOOL_SPECS

        enabled_entries = {
            entry
            for entry in set(ctx.enabled_tools) | entry_names_for_tools(set(tool_names))
            if entry in TOOL_SPECS
        }

        child_cfg = resolve_model_config(ctx.model_cfg, profile.model)
        if child_cfg is ctx.model_cfg or child_cfg == ctx.model_cfg:
            child_model = ctx.model
            child_vision = ctx.vision
        elif ctx.model_factory is not None:
            child_model = ctx.model_factory(child_cfg)
            child_vision = _vision_for(ctx, child_model, child_cfg)
        else:
            child_model = ctx.model
            child_vision = ctx.vision

        child_ctx = ToolContext(
            workspace=ctx.workspace,
            max_output_chars=settings.max_tool_output_chars,
            command_timeout=float(settings.command_timeout),
            terminal=ctx.terminal,
            activation=ToolActivation(),
            enabled_tool_names=frozenset(tool_names),
            session=SessionRef(task.child_session_id),
            asker=self._child_asker(task, ctx),
            todo_store=ctx.todo_store,
            process_manager=ctx.process_manager,
            emit=lambda kind, data: self._child_emit(task, kind, data),
            cancel_event=_CombinedCancel(task, ctx.cancel_event),
            sandbox=ctx.sandbox,
            sandbox_policy=ctx.sandbox_policy,
            vision=child_vision,
            subagents=None,
        )
        tools = build_child_tools(
            enabled_entries=enabled_entries,
            tool_configs=ctx.tool_configs,
            tool_names=tool_names,
            tool_ctx=child_ctx,
        )
        child_mode = profile.restrict_mode(_parent_mode(ctx))
        gate = self._child_gate(task, ctx, child_mode, tool_names)
        project_context = None if profile.inject_context else ""
        system_prompt = build_child_system_prompt(
            profile_prompt=profile.prompt,
            workspace=str(ctx.workspace),
            tool_names=tool_names,
            terminal=ctx.terminal,
            surface=ctx.surface,
            engine=(ctx.engine or settings.agent_engine),
            project_context=project_context,
        )
        agent, summarizer = build_child_agent(
            model=child_model,
            tools=tools,
            system_prompt=system_prompt,
            approval=gate,
            engine=ctx.engine,
        )
        store_dir = self.store_dir / _safe_slug(ctx.session_id)
        task.session = build_child_session(
            agent=agent,
            summarizer=summarizer,
            child_session_id=task.child_session_id,
            store_dir=store_dir,
            workspace_id=ctx.workspace_id,
            workspace_path=str(ctx.workspace),
            model_cfg=child_cfg,
        )
        task.model = _model_label(child_cfg)

    def _child_gate(
        self,
        task: SubagentTask,
        ctx: LaunchContext,
        mode: str,
        tool_names: tuple[str, ...],
    ) -> ApprovalGate | None:
        if ctx.approval is None:
            return None
        from tools.catalog import entry_names_for_tools
        from tools.registry import TOOL_SPECS

        # ``enabled_tools`` normally holds entry names, but callers may pass
        # concrete tool names; map both to registry entries before building risks.
        mapped = entry_names_for_tools(set(tool_names))
        entries = {
            entry
            for entry in set(ctx.enabled_tools) | mapped
            if entry in TOOL_SPECS
            and set(TOOL_SPECS[entry].tool_names) & set(tool_names)
        }
        parent_policy = ctx.approval.policy
        policy = ApprovalPolicy(
            permission_tools(entries, mode),
            parent_policy.memo,
            mode=mode,
            risks=tool_risks(entries),
        )
        return ApprovalGate(
            policy=policy,
            ask=lambda request: self._ask(task, request),
        )

    def _ask(self, task: SubagentTask, request: Any) -> str:
        """Route approvals to the open turn, or to the persistent channel when detached."""
        if not task.detached and task.turn_ask is not None:
            try:
                return task.turn_ask(request)
            except Exception as exc:  # a broken relay must deny, never allow
                logger.warning("子代理审批通道失败：%s", exc)
                return "deny"
        return self._ask_persistent(task, request)

    def _ask_persistent(self, task: SubagentTask, request: Any) -> str:
        if self.approval_registry is None:
            return "deny"
        pending = self.approval_registry.register(request)
        self._emit(
            task,
            "approval",
            {
                **request.as_event(),
                "session_id": task.parent_session_id,
                "agent_id": task.id,
            },
        )
        if not pending.event.wait(settings.approval_timeout):
            self.approval_registry.discard(request.id)
            return "deny"
        return pending.decision or "deny"

    def _child_asker(self, task: SubagentTask, ctx: LaunchContext):
        def ask(question):
            if task.detached or ctx.asker is None:
                return None
            return ctx.asker(question)

        return ask

    def _run_prompt(self, task: SubagentTask, prompt: str) -> None:
        """Run one or more child turns, draining queued messages between them."""
        message = prompt
        while True:
            if task.cancel.is_set():
                raise SubagentCancelled()
            session = task.session
            if session is None:
                raise SubagentBuildError("subagent_unavailable", "Child session is missing.")
            result = None
            for kind, data in session.ask_stream(message):
                if task.cancel.is_set():
                    raise SubagentCancelled()
                self._mirror(task, kind, data)
                if kind == "done":
                    result = data
            if result is None:
                raise SubagentBuildError("subagent_no_result", "Child produced no result.")
            task.report = result.text
            task.tool_uses += sum(
                1 for item in result.new_messages if isinstance(item, ToolMessage)
            )
            self._merge_usage(task, result.usage_dict())
            with task.lock:
                message = task.pending_messages.pop(0) if task.pending_messages else ""
            if not message:
                break

    def _mirror(self, task: SubagentTask, kind: str, data: Any) -> None:
        """Translate one child event into a bounded progress event."""
        if kind == "delta":
            text = data if isinstance(data, str) else str((data or {}).get("text") or "")
            if text:
                # Token chunks only feed the sidebar live view; the parent chat
                # already renders them through its own tool result.
                self._emit_hub(task, "subagent_progress", {"phase": "text", "text": text})
            return
        if kind == "thought":
            if isinstance(data, dict):
                text = str(data.get("text") or "")
                append = bool(data.get("append"))
                thought_id = str(data.get("id") or "")
            else:
                text = str(data)
                append = False
                thought_id = ""
            if not text:
                return
            payload: dict[str, Any] = {
                "phase": "thinking",
                "text": text[:4000],
                "append": append,
            }
            if thought_id:
                payload["id"] = thought_id
            self._emit(task, "subagent_progress", payload)
            return
        if kind == "tool":
            payload = data if isinstance(data, dict) else {}
            self._emit(
                task,
                "subagent_progress",
                {
                    "phase": "tool",
                    "id": str(payload.get("id") or ""),
                    "name": str(payload.get("name") or "tool"),
                    "args": payload.get("args") or {},
                },
            )
            return
        if kind == "tool_result":
            payload = data if isinstance(data, dict) else {}
            content = str(payload.get("content") or "")
            self._emit(
                task,
                "subagent_progress",
                {
                    "phase": "tool_result",
                    "id": str(payload.get("id") or ""),
                    "name": str(payload.get("name") or "tool"),
                    "content": content[:4000],
                    "error": content.startswith("Error:"),
                },
            )
            return
        if kind == "compacted":
            self._emit(task, "subagent_progress", {"phase": "compacted", "text": "context compacted"})

    def _child_emit(self, task: SubagentTask, kind: str, data: dict) -> None:
        """Keep child-internal side channels inside the subagent.

        Live command output is useful progress; todo updates and other host
        events belong to the child session and must not touch the parent UI.
        """
        if kind != "tool_output":
            return
        text = str(data.get("text") or "")
        if not text:
            return
        self._emit(
            task,
            "subagent_progress",
            {
                "phase": "output",
                "name": str(data.get("name") or "run_command"),
                "text": text[-4000:],
            },
        )

    def _merge_usage(self, task: SubagentTask, usage: Mapping[str, Any]) -> None:
        total = task.usage or {}
        for key in (
            "attempts",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "prompt_tokens",
        ):
            total[key] = int(total.get(key) or 0) + int(usage.get(key) or 0)
        for key in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
            value = usage.get(key)
            if value is None:
                continue
            total[key] = int(total.get(key) or 0) + int(value)
        task.usage = total

    def _finish(self, task: SubagentTask) -> None:
        try:
            task.output_file = self._write_output(task)
        except OSError as exc:
            logger.warning("写入子代理输出失败 %s：%s", task.id, exc)
        self._persist(task)
        summary = _status_summary(task)
        self._emit(
            task,
            "subagent_completed",
            {
                "status": task.status,
                "report": task.report,
                "error": task.error,
                "usage": task.usage,
                "tool_uses": task.tool_uses,
                "duration_ms": task.duration_ms,
                "output_file": task.output_file or self._output_path(task),
                "background": task.background,
                "summary": summary,
            },
        )
        if task.background:
            task.notified = False
            text = format_task_notification(
                agent_id=task.id,
                description=task.description,
                status=task.status,
                summary=summary,
                output_file=task.output_file or self._output_path(task),
                report=task.report,
                error=task.error,
            )
            with self._lock:
                queue = self._notifications.setdefault(task.parent_session_id, [])
                queue.append((task.id, text))
                del queue[:-_NOTIFICATION_LIMIT]
        logger.info(
            "子代理结束 id=%s status=%s tool_uses=%s duration_ms=%s",
            task.id,
            task.status,
            task.tool_uses,
            task.duration_ms,
        )

    def _emit(self, task: SubagentTask, kind: str, payload: dict[str, Any]) -> None:
        event = {
            "session_id": task.parent_session_id,
            "agent_id": task.id,
            "agent_type": task.agent_type,
            "description": task.description,
            **payload,
        }
        if task.emitter is not None:
            try:
                task.emitter(kind, event)
            except Exception:  # a closed stream must not break the child
                pass
        if self.hub is not None:
            try:
                self.hub.publish(kind, event)
            except Exception:  # pragma: no cover - hub failures are non-fatal
                pass

    def _emit_hub(self, task: SubagentTask, kind: str, payload: dict[str, Any]) -> None:
        """Publish only to the out-of-turn hub, skipping the parent chat stream."""
        if self.hub is None:
            return
        event = {
            "session_id": task.parent_session_id,
            "agent_id": task.id,
            "agent_type": task.agent_type,
            "description": task.description,
            **payload,
        }
        try:
            self.hub.publish(kind, event)
        except Exception:  # pragma: no cover - hub failures are non-fatal
            pass

    def _output_path(self, task: SubagentTask) -> str:
        return str(self.output_dir / _safe_slug(task.parent_session_id) / f"{task.id}.txt")

    def _write_output(self, task: SubagentTask) -> str:
        target = Path(self._output_path(task))
        target.parent.mkdir(parents=True, exist_ok=True)
        body = task.report.strip() or task.error.strip() or "(no output)"
        target.write_text(body + "\n", encoding="utf-8")
        meta = {
            "id": task.id,
            "agent_type": task.agent_type,
            "description": task.description,
            "prompt": task.prompt,
            "status": task.status,
            "background": task.background,
            "child_session_id": task.child_session_id,
            "parent_session_id": task.parent_session_id,
            "model": task.model,
            "allowed_tools": list(task.allowed_tools),
            "tool_uses": task.tool_uses,
            "duration_ms": task.duration_ms,
            "usage": task.usage,
            "report": task.report,
            "error": task.error,
            "output_file": task.output_file,
            "notified": task.notified,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
        }
        target.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(target)


def _parent_mode(ctx: LaunchContext) -> str:
    if ctx.approval is None:
        return "full_access"
    return str(getattr(ctx.approval.policy, "mode", "workspace_write"))


def _vision_for(ctx: LaunchContext, model: Any, cfg: Any):
    """Give the child its own vision channel when it uses a different model."""
    if not getattr(cfg, "supports_images", False):
        return None
    try:
        from tools.images import vision_from_model

        return vision_from_model(model)
    except Exception:  # pragma: no cover - vision is optional
        return None


def _model_label(model_cfg: Any) -> str:
    if model_cfg is None:
        return ""
    provider = str(getattr(model_cfg, "provider_id", "") or getattr(model_cfg, "provider", ""))
    model = str(getattr(model_cfg, "model", "") or "")
    return f"{provider}/{model}" if provider else model


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(value))
    return cleaned.strip("-")[:80] or "session"


def _status_summary(task: SubagentTask) -> str:
    if task.status == "completed":
        return f'Agent "{task.description}" finished'
    if task.status == "failed":
        reason = (task.error or "unknown error").strip() or "unknown error"
        return f'Agent "{task.description}" failed: {reason}'
    if task.status == "stopped":
        return f'Agent "{task.description}" was stopped'
    return f'Agent "{task.description}" {task.status}'
