# tools/processes.py
"""Background process tools: start, list, read logs, wait, and stop.

``start_process`` returns a process ID immediately and writes output to a log
file; use it for dev servers, watch builds, and other long-running tasks.
``read_process_logs`` and ``wait_process`` inspect that log on demand, while
``run_command`` captures a complete result in one call.

Safety boundaries:

- ``start_process`` / ``stop_process`` carry exec risk and are subject to
  registry approval.
- Processes are addressed only by IDs created by this manager; arbitrary PIDs
  are not accepted.
- Stopping targets the process tree (Windows ``taskkill /T``, POSIX process
  groups) so child processes are not left behind.
"""

from __future__ import annotations

import atexit
import locale
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from langchain.tools import tool

from tools.context import ToolContext
from tools.env import build_child_env
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace
from tools.sandbox.base import SandboxError, SandboxPolicy, SandboxProcess
from tools.terminals import TERMINAL_CHOICES, build_invocation, terminal_label

_IS_WINDOWS = os.name == "nt"
_MAX_LOG_BYTES = 1_000_000


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", locale.getpreferredencoding(False), "gbk"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


@dataclass
class ProcessRecord:
    """A process started by this manager."""

    id: str
    command: str
    cwd: Path
    process: SandboxProcess
    log_path: Path
    terminal: str = "powershell"
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    stopped_at: str = ""
    ended_at: str = ""
    session_id: str = ""
    workspace: str = ""
    description: str = ""
    notify_on_exit: bool = True
    stop_requested: bool = False
    started_monotonic: float = field(default_factory=time.monotonic)
    ended_monotonic: float | None = None
    sandboxed: bool = True
    sandbox_backend: str = "windows-local-user"
    network: str = "offline"
    log_threads: list[threading.Thread] = field(default_factory=list, repr=False)

    @property
    def pid(self) -> int:
        return int(self.process.pid or 0)

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return self.process.poll()

    @property
    def duration_seconds(self) -> float:
        end = self.ended_monotonic if self.ended_monotonic is not None else time.monotonic()
        return max(0.0, end - self.started_monotonic)

    @property
    def status(self) -> str:
        if self.running:
            return "running"
        if self.stop_requested:
            return "stopped"
        return "completed" if self.exit_code == 0 else "failed"

    def public(self) -> dict:
        """Structure used by the process view; excludes the Popen object."""
        return {
            "id": self.id,
            "command": self.command,
            "cwd": str(self.cwd),
            "workspace": self.workspace,
            "session_id": self.session_id,
            "description": self.description,
            "terminal": self.terminal,
            "terminal_label": terminal_label(self.terminal),
            "status": self.status,
            "running": self.running,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "notify": self.notify_on_exit,
            "log_path": str(self.log_path),
            "pid": self.pid,
            "sandboxed": self.sandboxed,
            "sandbox_backend": self.sandbox_backend,
            "network": self.network,
        }


class _LogPump(threading.Thread):
    """Copy one process stream into the task log file."""

    def __init__(self, stream, path: Path) -> None:
        super().__init__(daemon=True, name=f"alembic-log-{path.stem}")
        self.stream = stream
        self.path = path

    def run(self) -> None:
        try:
            handle = self.path.open("ab", buffering=0)
        except OSError:
            return
        try:
            while True:
                chunk = self.stream.read(8192)
                if not chunk:
                    break
                try:
                    handle.write(chunk)
                except OSError:
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass


class ProcessManager:
    """Process table and log directory; one instance is shared per application instance."""

    def __init__(
        self,
        log_dir: str | Path,
        *,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.log_dir = Path(log_dir).expanduser()
        self._records: dict[str, ProcessRecord] = {}
        self._lock = threading.Lock()
        self._on_event = on_event
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        _MANAGERS.append(self)

    def _emit(self, kind: str, payload: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(kind, payload)
        except Exception:
            pass

    def _watch(self, record: ProcessRecord) -> None:
        """Wait for the process to exit and emit a completion event for the web UI and consumers."""
        try:
            record.process.wait()
        except Exception:
            pass
        for thread in record.log_threads:
            thread.join(timeout=2)
        with self._lock:
            if not record.ended_at:
                record.ended_at = datetime.now().isoformat(timespec="seconds")
                record.ended_monotonic = time.monotonic()
        record.process.close()
        payload = record.public()
        payload["log_tail"] = self._tail(record, 80)
        self._emit("process_completed", payload)

    def _tail(self, record: ProcessRecord, lines: int) -> str:
        _, text = self.read_log(record.id, lines)
        return text

    # ---- lifecycle ----
    def start(
        self,
        command: str,
        cwd: Path,
        workspace: Path | None,
        terminal: str = "powershell",
        *,
        session_id: str = "",
        description: str = "",
        notify: bool = True,
        sandbox=None,
        policy: SandboxPolicy | None = None,
    ) -> ProcessRecord:
        text = (command or "").strip()
        if not text:
            raise ValueError("command must not be empty")
        chosen = (terminal or "powershell").strip().lower()
        if chosen not in TERMINAL_CHOICES:
            raise ValueError(f"Unsupported terminal: {terminal}")
        if sandbox is None or policy is None:
            raise SandboxError(
                "sandbox_unavailable",
                "No command sandbox is available; the background task was not started.",
                hint="Run scripts/setup-sandbox.ps1 as administrator, then retry.",
            )
        with self._lock:
            running = sum(1 for record in self._records.values() if record.running)
        if running >= 10:
            raise ValueError("Too many background processes are running (limit 10); stop unused processes first")
        process_id = f"proc-{uuid4().hex[:8]}"
        log_path = self.log_dir / f"{process_id}.log"
        try:
            log_path.write_bytes(b"")
        except OSError as exc:
            raise OSError(f"Could not create log file {log_path}: {exc}") from exc

        invocation = build_invocation(chosen, text, cwd)
        env = build_child_env({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        if invocation.shell:
            command_line = str(invocation.argv)
        elif _IS_WINDOWS:
            command_line = subprocess.list2cmdline([str(item) for item in invocation.argv])
        else:
            import shlex

            command_line = shlex.join([str(item) for item in invocation.argv])
        try:
            process = sandbox.spawn(
                command=command_line,
                cwd=Path(invocation.cwd or cwd),
                env=env,
                policy=policy,
                tty=False,
                shell=bool(invocation.shell),
            )
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError("sandbox_spawn_failed", f"Sandbox failed to start: {exc}") from exc

        log_threads = [
            _LogPump(stream, log_path)
            for stream in (process.stdout, process.stderr)
            if stream is not None
        ]
        for thread in log_threads:
            thread.start()
        record = ProcessRecord(
            id=process_id,
            command=text,
            cwd=cwd,
            process=process,
            log_path=log_path,
            terminal=chosen,
            session_id=session_id,
            workspace=str(workspace or cwd),
            description=description,
            notify_on_exit=bool(notify),
            network=policy.normalized_network(),
            log_threads=log_threads,
        )
        with self._lock:
            self._records[process_id] = record
            self._prune_locked()
        self._emit("process_started", record.public())
        threading.Thread(target=self._watch, args=(record,), daemon=True).start()
        return record

    def list(self) -> list[ProcessRecord]:
        with self._lock:
            self._prune_locked()
            return list(self._records.values())

    def get(self, process_id: str) -> ProcessRecord | None:
        text = (process_id or "").strip()
        with self._lock:
            if text in self._records:
                return self._records[text]
            matches = [record for key, record in self._records.items() if key.startswith(text) and text]
            return matches[0] if len(matches) == 1 else None

    def read_log(self, process_id: str, tail: int = 200) -> tuple[ProcessRecord | None, str]:
        record = self.get(process_id)
        if record is None:
            return None, ""
        try:
            size = record.log_path.stat().st_size
            with record.log_path.open("rb") as handle:
                if size > _MAX_LOG_BYTES:
                    handle.seek(size - _MAX_LOG_BYTES)
                raw = handle.read()
        except OSError:
            return record, ""
        text = _decode(raw).replace("\r\n", "\n")
        if size > _MAX_LOG_BYTES:
            text = f"…[log exceeds {_MAX_LOG_BYTES // 1000} KB; only the tail was read]…\n{text}"
        lines = text.splitlines()
        limit = max(10, min(int(tail or 200), 5000))
        if len(lines) > limit:
            lines = [f"…[{len(lines)} log lines total; showing the last {limit}]…", *lines[-limit:]]
        return record, "\n".join(lines)

    def stop(self, process_id: str, timeout: float = 5.0) -> ProcessRecord | None:
        record = self.get(process_id)
        if record is None:
            return None
        if not record.running:
            return record

        # A user-requested stop suppresses the "background task completed" prompt
        # but still emits a status update.
        record.notify_on_exit = False
        record.stop_requested = True
        try:
            record.process.terminate(timeout=timeout)
        except Exception:
            pass
        record.stopped_at = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            if not record.ended_at:
                record.ended_at = record.stopped_at
                record.ended_monotonic = time.monotonic()
        return record

    def stop_session(self, session_id: str, timeout: float = 5.0) -> int:
        """Stop every background task running in a session."""
        sid = (session_id or "").strip()
        if not sid:
            return 0
        targets = [record for record in self.list() if record.running and record.session_id == sid]
        for record in targets:
            self.stop(record.id, timeout=timeout)
        return len(targets)

    def cleanup(self) -> None:
        for record in self.list():
            if record.running:
                self.stop(record.id, timeout=2)

    def _prune_locked(self) -> None:
        """Keep at most 100 records, removing old finished records first."""
        if len(self._records) <= 100:
            return
        finished = [key for key, record in self._records.items() if not record.running]
        for key in finished[: len(self._records) - 100]:
            self._records.pop(key, None)


_MANAGERS: list[ProcessManager] = []


@atexit.register
def _cleanup_all() -> None:
    for manager in list(_MANAGERS):
        try:
            manager.cleanup()
        except Exception:
            pass


_SHARED: dict[str, ProcessManager] = {}
_SHARED_LOCK = threading.Lock()


def get_process_manager(workspace: Path | None) -> ProcessManager:
    """Return the shared manager for a workspace (used when none is injected)."""
    from config.settings import settings

    key = str(workspace) if workspace else "__none__"
    with _SHARED_LOCK:
        manager = _SHARED.get(key)
        if manager is None:
            manager = ProcessManager(Path(settings.app_home) / "logs" / "processes")
            _SHARED[key] = manager
        return manager


def build_process_tools(ctx: ToolContext, only: set[str] | None = None) -> list:
    """Build the background process tool group; ``only`` splits tools by registry entry."""
    manager = ctx.process_manager or get_process_manager(ctx.workspace)

    @tool
    def start_process(
        command: str,
        cwd: str = "",
        terminal: str = "",
        description: str = "",
        notify: bool = True,
    ) -> str:
        """Start a command in the background and return immediately.

        Use this for dev servers, watch builds, and long-running scripts.
        Completion is delivered to the conversation as a background event when
        ``notify`` is true; the current turn does not need to wait. Set
        ``notify=False`` for servers that are expected to keep running.

        Args:
            command: The command to run, for example "npm run dev".
            cwd: Working directory relative to the workspace; defaults to the workspace root.
            terminal: Optional terminal override: powershell / cmd / gitbash / wsl.
            description: One plain sentence describing what the process does. It is shown in the approval card.
            notify: Whether to notify the conversation when the process exits.

        Returns:
            Process ID, PID, command, terminal, and log location; returns an
            ``Error:`` string when the process cannot be started.
        """
        text = (command or "").strip()
        if not text:
            return tool_error("empty_command", "command must not be empty.")
        try:
            workdir = resolve_in_workspace(ctx.workspace, cwd) if cwd.strip() else ctx.workspace
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if workdir is None:
            return tool_error("no_workspace", "No workspace is available; cannot start a process.")
        if not workdir.is_dir():
            return tool_error("not_a_directory", f"Working directory does not exist: {cwd}.")

        chosen = (terminal or ctx.terminal or "powershell").strip().lower()
        if chosen not in TERMINAL_CHOICES:
            return tool_error(
                "unknown_terminal",
                f"Unsupported terminal: {terminal}.",
                hint="Choose powershell / cmd / gitbash / wsl.",
            )
        if chosen == "wsl":
            return tool_error(
                "sandbox_unsupported",
                "WSL terminals are not supported under the Windows sandbox: WSL processes do not inherit the sandbox token.",
                hint="Use powershell / cmd / gitbash; use a separate Linux sandbox backend when WSL is required.",
            )
        try:
            record = manager.start(
                text,
                workdir,
                ctx.workspace,
                terminal=chosen,
                session_id=ctx.session_id,
                description=description,
                notify=notify,
                sandbox=ctx.sandbox,
                policy=ctx.sandbox_policy,
            )
        except SandboxError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        except (OSError, ValueError) as exc:
            return tool_error("spawn_failed", f"Could not start process: {exc}.")

        return (
            f"Started background process: {record.id}\n"
            f"PID: {record.pid}\n"
            f"Command: {record.command}\n"
            f"Terminal: {terminal_label(record.terminal)}\n"
            f"Directory: {display_path(ctx.workspace, workdir)}\n"
            f"Completion notification: {'on' if record.notify_on_exit else 'off'}\n"
            f"Log: {record.log_path}\n"
            "Use read_process_logs to view output and stop_process to stop it."
        )

    @tool
    def list_processes() -> str:
        """List background processes started by this application and their status."""
        records = manager.list()
        if not records:
            return "No background processes."
        lines = [f"Background processes ({len(records)}):"]
        for record in records:
            if record.running:
                status = f"running (PID {record.pid}, {record.duration_seconds:.1f}s)"
            elif record.status == "stopped":
                status = "stopped"
            else:
                status = f"exited (code {record.exit_code})"
            lines.append(f"- {record.id} · {status} · {record.command}")
            lines.append(f"  Started: {record.started_at}, log: {record.log_path.name}")
        return truncate("\n".join(lines), ctx.max_output_chars)

    @tool
    def read_process_logs(process_id: str, tail: int = 200) -> str:
        """Read the tail of a background process log.

        Args:
            process_id: Process ID returned by start_process.
            tail: Maximum number of trailing lines to return (10-5000); defaults to 200.

        Returns:
            Process status, command, and log content; returns ``Error:`` when the ID is unknown.
        """
        record, text = manager.read_log(process_id, tail)
        if record is None:
            return tool_error(
                "unknown_process",
                f"Process {process_id!r} not found.",
                hint="Use list_processes to see available process IDs.",
            )
        status = f"running (PID {record.pid})" if record.running else f"exited (code {record.exit_code})"
        header = [f"Process: {record.id} · {status}", f"Command: {record.command}", "--- log ---"]
        body = text.rstrip() or "(no output yet)"
        return truncate("\n".join([*header, body]), ctx.max_output_chars, keep_tail=True)

    @tool
    def wait_process(process_id: str, timeout: int = 30, pattern: str = "", tail: int = 60) -> str:
        """Wait for a background process to exit, or for a pattern to appear in its log.

        Useful after starting a dev server to wait for "Listening on ...", or
        after a build to wait for completion, instead of polling
        read_process_logs repeatedly.

        Args:
            process_id: Process ID returned by start_process.
            timeout: Maximum wait in seconds (1-120); defaults to 30.
            pattern: Optional regular expression; returns as soon as a matching log line appears.
            tail: Number of trailing log lines to return (10-500); defaults to 60.

        Returns:
            Process status, the matched log line, and the log tail. A wait timeout
            is not a tool error; the result states that the process is still running.
        """
        record = manager.get(process_id)
        if record is None:
            return tool_error(
                "unknown_process",
                f"Process {process_id!r} not found.",
                hint="Use list_processes to see available process IDs.",
            )
        regex = None
        if (pattern or "").strip():
            import re

            try:
                regex = re.compile(pattern)
            except re.error as exc:
                return tool_error(
                    "bad_pattern",
                    f"Could not compile regular expression: {exc}.",
                    hint="Check that parentheses and brackets are balanced, or leave pattern empty to wait only for exit.",
                )

        limit = max(1, min(int(timeout or 30), 120))
        deadline = time.monotonic() + limit
        matched = ""
        timed_out = False

        def log_text() -> str:
            try:
                size = record.log_path.stat().st_size
                with record.log_path.open("rb") as handle:
                    if size > _MAX_LOG_BYTES:
                        handle.seek(size - _MAX_LOG_BYTES)
                    return _decode(handle.read())
            except OSError:
                return ""

        text = log_text().replace("\r\n", "\n")
        if regex is not None:
            for line in text.splitlines():
                if regex.search(line):
                    matched = line
                    break
        while not matched and record.running and time.monotonic() < deadline:
            time.sleep(0.3)
            if regex is not None:
                text = log_text().replace("\r\n", "\n")
                for line in text.splitlines():
                    if regex.search(line):
                        matched = line
                        break
        if record.running and not matched and time.monotonic() >= deadline:
            timed_out = True

        status = f"running (PID {record.pid})" if record.running else f"exited (code {record.exit_code})"
        header = [f"Process: {record.id} · {status}"]
        if matched:
            header.append(f"Matched: {matched.strip()}")
        elif regex is not None:
            header.append(f"Pattern not matched: {pattern}")
        if timed_out:
            header.append(f"Wait timed out: the process did not exit or match the pattern within {limit}s.")

        text = log_text().replace("\r\n", "\n")
        lines = text.rstrip("\n").splitlines()
        keep = max(10, min(int(tail or 60), 500))
        if len(lines) > keep:
            lines = [f"…[{len(lines)} log lines total; showing the last {keep}]…", *lines[-keep:]]
        header.append("--- log ---")
        header.append("\n".join(lines) if lines else "(no output yet)")
        return truncate("\n".join(header), ctx.max_output_chars, keep_tail=True)

    @tool
    def stop_process(process_id: str) -> str:
        """Stop a background process started by this application, including its children.

        Args:
            process_id: Process ID returned by start_process.

        Returns:
            Stop result and exit code; returns ``Error:`` when the ID is unknown.
        """
        record = manager.get(process_id)
        if record is None:
            return tool_error(
                "unknown_process",
                f"Process {process_id!r} not found.",
                hint="Use list_processes to see available process IDs.",
            )
        if not record.running:
            return f"Process {record.id} has already exited (code {record.exit_code}); nothing to stop."
        stopped = manager.stop(record.id)
        code = stopped.exit_code if stopped else None
        return f"Stopped process: {record.id} (exit code {code})."

    tools = [start_process, list_processes, read_process_logs, wait_process, stop_process]
    if only is None:
        return tools
    return [tool for tool in tools if tool.name in only]
