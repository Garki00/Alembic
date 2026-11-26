# tools/shell.py
"""Shell command execution tool that lets the agent run commands to verify its work.

Constraints:

- Enabled by default in the tool registry, but every execution requires approval;
  read-only mode rejects it outright.
- The working directory must stay inside the workspace (``cwd`` is checked too).
- Supports PowerShell / CMD / Git Bash / WSL and follows the configured terminal
  preference by default.
- Child processes inherit only whitelisted environment variables; model keys are
  not exposed to commands.
- Enforces a timeout to bound command runtime.
- Streams stdout / stderr and emits ``tool_output`` events while the command runs.
- Starts commands through the platform sandbox backend and terminates the whole
  process tree when generation is stopped.

Commands are never run as the backend process user. When no sandbox is available
the command must fail closed instead of falling back to direct host execution.
"""

from __future__ import annotations

import locale
import os
import subprocess
import threading
import time
from pathlib import Path

from langchain.tools import tool

from tools.context import ToolContext
from tools.env import build_child_env
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace
from tools.sandbox.base import SandboxError, sandbox_unavailable
from tools.spec import RISK_EXEC, ToolSpec
from tools.terminals import (
    TERMINAL_CHOICES,
    TerminalUnavailable,
    build_invocation,
    terminal_label,
)

_MAX_TIMEOUT = 600
_MAX_CAPTURE_BYTES = 2_000_000
_EMIT_INTERVAL = 0.08


def _decode(raw: bytes) -> str:
    """Decode child process output as UTF-8, then system encoding, then GBK, then replacement.

    Windows PowerShell / cmd often writes in the local code page (GBK on Simplified
    Chinese systems), which decodes to replacement characters as UTF-8.
    """
    if not raw:
        return ""
    candidates = ["utf-8", locale.getpreferredencoding(False), "gbk"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _block(title: str, text: str, max_chars: int) -> list[str]:
    body = text.rstrip("\n")
    if not body:
        body = "(empty)"
    body = truncate(body, max_chars, keep_tail=True)
    return [f"--- {title} ---", body]


class _OutputPump:
    """Read one pipe, accumulate output, and emit throttled live events."""

    def __init__(self, stream, name: str, ctx: ToolContext) -> None:
        self.stream = stream
        self.name = name
        self.ctx = ctx
        self.raw = bytearray()
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self._last_emit = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float = 2.0) -> None:
        self._thread.join(timeout)
        self._flush()

    def _run(self) -> None:
        while True:
            try:
                chunk = self.stream.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            if len(self.raw) < _MAX_CAPTURE_BYTES:
                room = _MAX_CAPTURE_BYTES - len(self.raw)
                self.raw.extend(chunk[:room])
            text = _decode(chunk)
            if text:
                with self._lock:
                    self._pending.append(text)
                self._maybe_flush()
        self._flush()

    def _maybe_flush(self) -> None:
        now = time.monotonic()
        if now - self._last_emit < _EMIT_INTERVAL:
            return
        self._flush()

    def _flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            text = "".join(self._pending)
            self._pending.clear()
            self._last_emit = time.monotonic()
        self.ctx.emit_event(
            "tool_output",
            {"name": "run_command", "stream": self.name, "text": text},
        )

    @property
    def text(self) -> str:
        return _decode(bytes(self.raw))


def _run_process(
    invocation,
    command_line: str,
    env: dict[str, str],
    limit: float,
    ctx: ToolContext,
) -> tuple[int | None, str, str, bool, bool, float]:
    """Start a command and read its output live; commands start only through the sandbox backend."""
    if ctx.sandbox is None or ctx.sandbox_policy is None:
        raise sandbox_unavailable(
            "No command sandbox is available; the command was not run.",
            hint="Run scripts/setup-sandbox.ps1 as administrator.",
        )

    started = time.monotonic()
    try:
        process = ctx.sandbox.spawn(
            command=command_line,
            cwd=Path(invocation.cwd or ctx.workspace or Path.cwd()),
            env=env,
            policy=ctx.sandbox_policy,
            tty=False,
            shell=bool(invocation.shell),
        )
    except SandboxError:
        raise
    except Exception as exc:
        raise SandboxError("sandbox_spawn_failed", f"Sandbox failed to start: {exc}") from exc

    stdout = _OutputPump(process.stdout, "stdout", ctx)
    stderr = _OutputPump(process.stderr, "stderr", ctx)
    stdout.start()
    stderr.start()
    try:
        cancelled = False
        timed_out = False
        while process.poll() is None:
            if ctx.cancel_event is not None and ctx.cancel_event.is_set():
                cancelled = True
                process.terminate()
                break
            if time.monotonic() - started > limit:
                timed_out = True
                process.terminate()
                break
            time.sleep(0.05)
        code = process.poll()
        stdout.join()
        stderr.join()
        return code, stdout.text, stderr.text, cancelled, timed_out, time.monotonic() - started
    finally:
        if process.poll() is None:
            process.terminate()
        process.close()


def build_shell_tools(ctx: ToolContext) -> list:
    """Build command execution tools (terminal preference, env whitelist, live output, cancel, timeout)."""

    @tool
    def run_command(
        command: str,
        cwd: str = "",
        timeout: int = 0,
        terminal: str = "",
        description: str = "",
    ) -> str:
        """Run a shell command inside the workspace and stream its output.

        Use it for tests, builds, scripts, and Git. Output is streamed to the
        UI while the command runs; the final result still contains the exit
        code and the complete stdout/stderr sections. The process tree is
        terminated when generation is stopped.

        Args:
            command: Command to run, for example "python -m unittest discover -s tests".
            cwd: Working directory relative to the workspace; defaults to the workspace root.
            timeout: Timeout in seconds (1-600); 0 uses the configured default.
            terminal: Optional terminal override: powershell / cmd / gitbash / wsl.
            description: One plain sentence describing what the command does. It is shown
                as the approval card title.

        Returns:
            The exit code first, then stdout and stderr sections; returns an
            ``Error:`` string on timeout or cancellation.
        """
        text = (command or "").strip()
        if not text:
            return tool_error("empty_command", "command must not be empty.")
        if ctx.cancel_event is not None and ctx.cancel_event.is_set():
            return tool_error("cancelled", "This turn has been stopped; the command was not run.")

        try:
            workdir = resolve_in_workspace(ctx.workspace, cwd) if cwd.strip() else ctx.workspace
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if workdir is None:
            return tool_error("no_workspace", "No workspace is available; the command cannot run.")
        if not workdir.is_dir():
            return tool_error("not_a_directory", f"Working directory does not exist: {cwd}.")

        limit = int(timeout or 0)
        limit = ctx.command_timeout if limit <= 0 else max(1, min(limit, _MAX_TIMEOUT))

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
            invocation = build_invocation(chosen, text, workdir)
        except TerminalUnavailable as exc:
            return tool_error(
                "terminal_unavailable",
                str(exc),
                hint="Choose an available terminal under Settings → General → Terminal preference, or install the required tool.",
            )

        if invocation.shell:
            command_line = str(invocation.argv)
        elif os.name == "nt":
            command_line = subprocess.list2cmdline([str(item) for item in invocation.argv])
        else:
            import shlex

            command_line = shlex.join([str(item) for item in invocation.argv])
        env = build_child_env({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        try:
            code, stdout_text, stderr_text, cancelled, timed_out, elapsed = _run_process(
                invocation, command_line, env, float(limit), ctx
            )
        except SandboxError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        except OSError as exc:
            return tool_error(
                "spawn_failed",
                f"Could not start {terminal_label(chosen)}: {exc}.",
                hint="Confirm the terminal is available, or choose another terminal in settings.",
            )

        if cancelled:
            partial = (stdout_text + stderr_text).strip()
            return tool_error(
                "cancelled",
                f"Command cancelled because the turn stopped (process tree terminated, {elapsed:.1f}s elapsed).",
                hint=(
                    "Confirm the target is still needed before rerunning."
                    + (f"\nOutput produced so far:\n{truncate(partial, ctx.max_output_chars // 2, keep_tail=True)}" if partial else "")
                ),
            )
        if timed_out:
            partial = (stdout_text + stderr_text).strip()
            return tool_error(
                "timeout",
                f"Command did not finish within {limit:g}s and was terminated ({elapsed:.1f}s elapsed).",
                hint=(
                    "Use a faster command, increase the timeout, or check whether the command waits for interactive input."
                    + (f"\nOutput produced so far:\n{truncate(partial, ctx.max_output_chars // 2, keep_tail=True)}" if partial else "")
                ),
            )

        code = int(code or 0)
        status = "succeeded" if code == 0 else "failed"
        lines = [
            f"Exit code: {code} ({status})",
            f"Terminal: {terminal_label(chosen)}",
            f"Working directory: {display_path(ctx.workspace, workdir)}",
            f"Duration: {elapsed:.1f}s",
        ]
        budget = max(200, (ctx.max_output_chars - 200) // 2)
        lines += _block("stdout", stdout_text, budget)
        lines += _block("stderr", stderr_text, budget)
        if code != 0:
            lines.append("Hint: the command exited with a non-zero status; check stderr or adjust the command and retry.")
        return truncate("\n".join(lines), ctx.max_output_chars, keep_tail=True)

    return [run_command]


TOOL_ENTRIES: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="run_command",
        label="命令执行",
        description="执行 shell 命令（测试、构建、脚本、Git）",
        risk=RISK_EXEC,
        tool_names=("run_command",),
        builder=lambda _config, ctx: build_shell_tools(ctx),
        capability={
            "risk_level": "high",
            "destructive": True,
            "permission_name": "command",
        },
    ),
)
