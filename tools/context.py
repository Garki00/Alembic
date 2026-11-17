# tools/context.py
"""Tool runtime context: workspace boundary, output budget, and shared objects.

Defines the context shared by file and command tools. It lives in its own module
so registry, file tools, and command tools can depend on it without importing
each other.

These shared objects are injected by the caller (CLI / Web):

- ``session``: reference to the current session. The CLI changes
  ``session_id`` in place when switching sessions, so already assembled tools
  do not need to be rebuilt.
- ``asker``: question channel for ``ask_user_question`` (SSE on the web,
  terminal on the CLI).
- ``todo_store``: persistent storage for the task list.
- ``process_manager``: background process manager, shared per workspace.
- ``emit``: callback that pushes tool-internal events (such as todo updates) to
  the frontend; may be None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # Type annotations only; avoids runtime circular imports
    from threading import Event

    from tools.catalog import ToolActivation
    from tools.processes import ProcessManager
    from tools.questions import Asker
    from tools.sandbox.base import SandboxPolicy, SandboxRunner
    from tools.todo import TodoStore


@dataclass
class SessionRef:
    """Mutable session reference; tools read ``session_id`` only when called."""

    session_id: str = "default"


@dataclass(frozen=True)
class ToolContext:
    """Environment parameters shared by all tools in one run.

    Attributes:
        workspace: Root directory for file and command tools; ``None`` means no
            workspace, and such tools return an error instead of falling back to
            "any path is writable".
        max_output_chars: Character limit for a single tool output; excess is
            truncated with a marker.
        command_timeout: Default command timeout in seconds.
        session: Reference to the current session, used by tools such as todo
            that persist per session.
        asker: Ask the user and wait for an answer; when ``None``, question tools
            return an error.
        todo_store: Task list storage; when ``None``, an in-process store is used.
        process_manager: Background process manager; when ``None``, a default
            instance is created per workspace.
        emit: Tool event outlet used by the web UI to push SSE events such as ``todo``.
        vision: Vision channel used by ``read_image`` to hand an image to the
            current model for understanding.
        terminal: Preferred terminal for command tools:
            powershell / cmd / gitbash / wsl.
        activation: Extension tool activation state for the current session;
            when ``None``, extension tools cannot be loaded.
        enabled_tool_names: Concrete tool names enabled in user settings, used by
            tools_search for filtering.
        cancel_event: Cancellation signal set when generation stops; foreground
            commands poll it and terminate the process tree.
        sandbox: Command sandbox backend; when ``None``, command tools must fail
            closed and cannot execute directly.
        sandbox_policy: Sandbox policy for this run (workspace, network, temp directory).
    """

    workspace: Path | None = None
    max_output_chars: int = 20000
    command_timeout: float = 60.0
    terminal: str = "powershell"
    activation: "ToolActivation | None" = None
    enabled_tool_names: frozenset[str] = frozenset()
    session: SessionRef = field(default_factory=SessionRef)
    asker: Asker | None = None
    todo_store: TodoStore | None = None
    process_manager: ProcessManager | None = None
    emit: Callable[[str, dict[str, Any]], None] | None = None
    cancel_event: "Event | None" = None
    sandbox: "SandboxRunner | None" = None
    sandbox_policy: "SandboxPolicy | None" = None
    #: Vision channel: ``(data_url, prompt) -> text description``. Injected by the
    #: runtime with the current model; when ``None``, ``read_image`` returns
    #: ``vision_unavailable``.
    vision: Callable[[str, str], str] | None = None

    @property
    def session_id(self) -> str:
        """Shortcut for the current session ID."""
        return self.session.session_id or "default"

    def emit_event(self, kind: str, data: dict[str, Any]) -> None:
        """Safely emit an event; silently ignored when there is no frontend (CLI)."""
        if self.emit is None:
            return
        try:
            self.emit(kind, data)
        except Exception:  # A frontend channel error must not fail the tool
            pass
