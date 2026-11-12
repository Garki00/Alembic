# tools/shell.py
"""命令执行工具：让 agent 能真正跑起来验证自己的工作（测试、构建、脚本）。

风险等级最高，因此：

- 在工具注册表里默认开启，但执行前必须经过审批；只读模式直接拒绝；
- 工作目录被限制在工作区内（``cwd`` 同样走越界检查）；
- 支持 PowerShell / CMD / Git Bash / WSL，默认跟随「设置 → 通用 → 终端偏好」；
- 子进程只继承白名单环境变量，模型密钥不会被命令读到；
- 强制超时，避免一条挂起的命令拖死整轮对话；
- stdout / stderr 实时读取并推送 ``tool_output`` 事件，前端可以边跑边看；
- 停止生成时通过 ``cancel_event`` 结束整个进程树。

注意：这不是操作系统级沙盒。命令与后端进程属于同一个 OS 用户，真正限制
它能做什么的是命令策略（高危命令直接拒绝）与用户审批，而不是本模块。
"""

from __future__ import annotations

import locale
import os
import subprocess
import threading
import time

from langchain.tools import tool

from tools.context import ToolContext
from tools.env import build_child_env
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace
from tools.terminals import (
    TERMINAL_CHOICES,
    TerminalUnavailable,
    build_invocation,
    terminal_label,
)
from tools.winjob import WindowsJob

_MAX_TIMEOUT = 600
_MAX_CAPTURE_BYTES = 2_000_000
_EMIT_INTERVAL = 0.08


def _decode(raw: bytes) -> str:
    """按 UTF-8 → 系统编码 → GBK → 替换的顺序解码子进程输出。

    Windows 的 PowerShell / cmd 常按本地代码页（简中为 GBK）输出，
    直接当 UTF-8 解码会得到一堆替换字符。
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
        body = "(空)"
    body = truncate(body, max_chars, keep_tail=True)
    return [f"--- {title} ---", body]


def _terminate_tree(popen: subprocess.Popen, job: WindowsJob | None = None) -> None:
    """结束命令及其子进程，避免只杀 shell 留下孤儿。"""
    if popen.poll() is not None:
        if job is not None:
            job.close()
        return
    if job is not None:
        job.terminate()
    elif os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(popen.pid), "/T", "/F"],
                capture_output=True,
                timeout=2,
                env=build_child_env(),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        import signal

        try:
            os.killpg(os.getpgid(popen.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        popen.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            popen.kill()
            popen.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if job is not None:
        job.close()


class _OutputPump:
    """读取一路管道，累积输出并按节流推送实时事件。"""

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
    env: dict[str, str],
    limit: float,
    ctx: ToolContext,
) -> tuple[int | None, str, str, bool, bool, float]:
    """启动命令并实时读取输出。

    Returns:
        ``(returncode, stdout, stderr, cancelled, timed_out, elapsed)``
    """
    kwargs: dict = {
        "cwd": invocation.cwd,
        "shell": invocation.shell,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": env,
        "bufsize": 0,
    }
    pty_master = None
    if os.name == "nt":
        kwargs["stdin"] = subprocess.PIPE
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # POSIX: give stdout/stdin a real PTY so progress bars and TTY-aware
        # tools behave like a normal terminal. stderr stays a pipe so the final
        # result can still separate stdout and stderr.
        import pty

        pty_master, slave = pty.openpty()
        kwargs["stdin"] = slave
        kwargs["stdout"] = slave
        kwargs["start_new_session"] = True

    started = time.monotonic()
    job = WindowsJob.create()
    try:
        popen = subprocess.Popen(invocation.argv, **kwargs)
    except Exception:
        if job is not None:
            job.close()
        raise
    if job is not None:
        if not job.assign(popen):
            job.close()
            job = None
    try:
        if pty_master is not None:
            try:
                os.close(slave)
            except OSError:
                pass
            stdout_stream = os.fdopen(pty_master, "rb", buffering=0)
            try:
                # Non-interactive calls should not wait forever for stdin.
                os.write(pty_master, b"\x04")
            except OSError:
                pass
        else:
            stdout_stream = popen.stdout
        if popen.stdin is not None:
            # 没有交互输入通道时立即发 EOF，避免命令一直等 stdin。
            try:
                popen.stdin.close()
            except (OSError, ValueError):
                pass
        stdout = _OutputPump(stdout_stream, "stdout", ctx)
        stderr = _OutputPump(popen.stderr, "stderr", ctx)
        stdout.start()
        stderr.start()

        cancelled = False
        timed_out = False
        while popen.poll() is None:
            if ctx.cancel_event is not None and ctx.cancel_event.is_set():
                cancelled = True
                _terminate_tree(popen, job)
                break
            if time.monotonic() - started > limit:
                timed_out = True
                _terminate_tree(popen, job)
                break
            time.sleep(0.05)
        code = popen.poll()
        stdout.join()
        stderr.join()
        return code, stdout.text, stderr.text, cancelled, timed_out, time.monotonic() - started
    finally:
        if popen.poll() is None:
            _terminate_tree(popen, job)
        elif job is not None:
            job.close()
        for stream in (popen.stdout, popen.stderr, popen.stdin):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        if pty_master is not None:
            try:
                os.close(pty_master)
            except OSError:
                pass


def build_shell_tools(ctx: ToolContext) -> list:
    """构造命令执行工具（终端偏好、环境白名单、实时输出、取消与超时）。"""

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
            return tool_error("empty_command", "command 不能为空。")
        if ctx.cancel_event is not None and ctx.cancel_event.is_set():
            return tool_error("cancelled", "本轮已停止，命令没有执行。")

        try:
            workdir = resolve_in_workspace(ctx.workspace, cwd) if cwd.strip() else ctx.workspace
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if workdir is None:
            return tool_error("no_workspace", "当前没有可用工作区，无法执行命令。")
        if not workdir.is_dir():
            return tool_error("not_a_directory", f"执行目录不存在：{cwd}。")

        limit = int(timeout or 0)
        limit = ctx.command_timeout if limit <= 0 else max(1, min(limit, _MAX_TIMEOUT))

        chosen = (terminal or ctx.terminal or "powershell").strip().lower()
        if chosen not in TERMINAL_CHOICES:
            return tool_error(
                "unknown_terminal",
                f"不支持的终端：{terminal}。",
                hint="可选 powershell / cmd / gitbash / wsl。",
            )
        try:
            invocation = build_invocation(chosen, text, workdir)
        except TerminalUnavailable as exc:
            return tool_error(
                "terminal_unavailable",
                str(exc),
                hint="请在「设置 → 通用 → 终端偏好」里换一个可用终端，或安装对应工具。",
            )

        env = build_child_env({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        try:
            code, stdout_text, stderr_text, cancelled, timed_out, elapsed = _run_process(
                invocation, env, float(limit), ctx
            )
        except OSError as exc:
            return tool_error(
                "spawn_failed",
                f"无法启动 {terminal_label(chosen)}：{exc}。",
                hint="请确认该终端可用，或在设置里换一个终端。",
            )

        if cancelled:
            partial = (stdout_text + stderr_text).strip()
            return tool_error(
                "cancelled",
                f"命令已随本轮停止而取消（终止进程树，耗时 {elapsed:.1f}s）。",
                hint=(
                    "重新执行前先确认目标是否仍然需要。"
                    + (f"\n已产生的输出：\n{truncate(partial, ctx.max_output_chars // 2, keep_tail=True)}" if partial else "")
                ),
            )
        if timed_out:
            partial = (stdout_text + stderr_text).strip()
            return tool_error(
                "timeout",
                f"命令在 {limit:g}s 内没有结束，已终止（耗时 {elapsed:.1f}s）。",
                hint=(
                    "可以换更快的命令、加超时参数，或先确认命令是否需要交互式输入。"
                    + (f"\n已产生的输出：\n{truncate(partial, ctx.max_output_chars // 2, keep_tail=True)}" if partial else "")
                ),
            )

        code = int(code or 0)
        status = "成功" if code == 0 else "失败"
        lines = [
            f"退出码：{code}（{status}）",
            f"终端：{terminal_label(chosen)}",
            f"工作目录：{display_path(ctx.workspace, workdir)}",
            f"耗时：{elapsed:.1f}s",
        ]
        budget = max(200, (ctx.max_output_chars - 200) // 2)
        lines += _block("stdout", stdout_text, budget)
        lines += _block("stderr", stderr_text, budget)
        if code != 0:
            lines.append("提示：命令以非零状态退出，请检查 stderr 或调整命令后重试。")
        return truncate("\n".join(lines), ctx.max_output_chars, keep_tail=True)

    return [run_command]
