# tools/processes.py
"""后台进程工具：启动、查看、读日志、停止。

``run_command`` 是一次性抓完整输出，跑 dev server / 长任务时不适合：
它会把整轮对话卡到超时。这里的 ``start_process`` 立即返回进程 ID，
输出落到日志文件，由 ``read_process_logs`` 按需读取。

安全边界：

- ``start_process`` / ``stop_process`` 属于 exec 风险，注册表会把它们
  纳入审批；
- 进程只能通过本管理器启动的 ID 操作，不接受任意 PID；
- 停止时按进程树处理（Windows ``taskkill /T``，POSIX 进程组），
  避免只杀掉 shell 而留下子进程。
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
from uuid import uuid4

from langchain.tools import tool

from tools.context import ToolContext
from tools.env import build_child_env
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace
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
    """一个由本管理器启动的进程。"""

    id: str
    command: str
    cwd: Path
    popen: subprocess.Popen
    log_path: Path
    terminal: str = "powershell"
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    stopped_at: str = ""

    @property
    def pid(self) -> int:
        return int(self.popen.pid or 0)

    @property
    def running(self) -> bool:
        return self.popen.poll() is None

    @property
    def exit_code(self) -> int | None:
        return self.popen.poll()


class ProcessManager:
    """进程表与日志目录；一个应用实例共享一份。"""

    def __init__(self, log_dir: str | Path) -> None:
        self.log_dir = Path(log_dir).expanduser()
        self._records: dict[str, ProcessRecord] = {}
        self._lock = threading.Lock()
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        _MANAGERS.append(self)

    # ---- 生命周期 ----
    def start(
        self,
        command: str,
        cwd: Path,
        workspace: Path | None,
        terminal: str = "powershell",
    ) -> ProcessRecord:
        text = (command or "").strip()
        if not text:
            raise ValueError("command 不能为空")
        chosen = (terminal or "powershell").strip().lower()
        if chosen not in TERMINAL_CHOICES:
            raise ValueError(f"不支持的终端：{terminal}")
        with self._lock:
            running = sum(1 for record in self._records.values() if record.running)
        if running >= 10:
            raise ValueError("同时运行的后台进程过多（上限 10），请先停止不用的进程")
        process_id = f"proc-{uuid4().hex[:8]}"
        log_path = self.log_dir / f"{process_id}.log"

        invocation = build_invocation(chosen, text, cwd)
        env = build_child_env({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        kwargs: dict = {
            "cwd": invocation.cwd,
            "shell": invocation.shell,
            "stdin": subprocess.DEVNULL,
            "stdout": None,
            "stderr": subprocess.STDOUT,
            "env": env,
        }
        if _IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        try:
            handle = log_path.open("wb")
        except OSError as exc:
            raise OSError(f"无法创建日志文件 {log_path}：{exc}") from exc
        try:
            kwargs["stdout"] = handle
            popen = subprocess.Popen(invocation.argv, **kwargs)
        except OSError:
            handle.close()
            raise
        finally:
            try:
                handle.close()
            except OSError:
                pass

        record = ProcessRecord(
            id=process_id,
            command=text,
            cwd=cwd,
            popen=popen,
            log_path=log_path,
            terminal=chosen,
        )
        with self._lock:
            self._records[process_id] = record
            self._prune_locked()
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
            text = f"…[日志超过 {_MAX_LOG_BYTES // 1000} KB，只读取尾部]…\n{text}"
        lines = text.splitlines()
        limit = max(10, min(int(tail or 200), 5000))
        if len(lines) > limit:
            lines = [f"…[日志共 {len(lines)} 行，只显示最后 {limit} 行]…", *lines[-limit:]]
        return record, "\n".join(lines)

    def stop(self, process_id: str, timeout: float = 5.0) -> ProcessRecord | None:
        record = self.get(process_id)
        if record is None:
            return None
        if not record.running:
            return record

        pid = record.pid
        try:
            if _IS_WINDOWS:
                # /T 连子进程一起结束；/F 避免 GUI/控制台进程忽略 terminate
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    env=build_child_env(),
                )
            else:
                import signal

                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
        except (OSError, subprocess.SubprocessError):
            pass

        try:
            record.popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                record.popen.kill()
                record.popen.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        record.stopped_at = datetime.now().isoformat(timespec="seconds")
        return record

    def cleanup(self) -> None:
        for record in self.list():
            if record.running:
                self.stop(record.id, timeout=2)

    def _prune_locked(self) -> None:
        """最多保留 100 条记录，优先删已退出的旧记录。"""
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
    """按工作区取共享管理器（没有注入时使用）。"""
    from config.settings import settings

    key = str(workspace) if workspace else "__none__"
    with _SHARED_LOCK:
        manager = _SHARED.get(key)
        if manager is None:
            manager = ProcessManager(Path(settings.app_home) / "logs" / "processes")
            _SHARED[key] = manager
        return manager


def build_process_tools(ctx: ToolContext, only: set[str] | None = None) -> list:
    """构造后台进程工具组；``only`` 用于按注册表条目拆分工具。"""
    manager = ctx.process_manager or get_process_manager(ctx.workspace)

    @tool
    def start_process(
        command: str,
        cwd: str = "",
        terminal: str = "",
        description: str = "",
    ) -> str:
        """在后台启动一条命令，立即返回进程 ID；输出写入日志文件。

        适合 dev server、watch 构建、长时间脚本。需要看结果时用
        read_process_logs；不需要时用 stop_process 结束，避免留下后台进程。

        Args:
            command: 要执行的命令，例如 "npm run dev"。
            cwd: 相对工作区的执行目录，默认工作区根目录。
            terminal: 临时指定 powershell / cmd / gitbash / wsl；
                留空时使用「设置 → 通用」里的终端偏好。
            description: 用一句白话说明这个后台进程要做什么
                （例如「启动前端开发服务器，监听 5173 端口」）。
                审批卡片用它作为主标题，请勿重复命令原文或罗列参数。

        Returns:
            进程 ID、PID 与日志位置；启动失败时返回 ``Error:``。
        """
        text = (command or "").strip()
        if not text:
            return tool_error("empty_command", "command 不能为空。")
        try:
            workdir = resolve_in_workspace(ctx.workspace, cwd) if cwd.strip() else ctx.workspace
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if workdir is None:
            return tool_error("no_workspace", "当前没有可用工作区，无法启动进程。")
        if not workdir.is_dir():
            return tool_error("not_a_directory", f"执行目录不存在：{cwd}。")

        chosen = (terminal or ctx.terminal or "powershell").strip().lower()
        if chosen not in TERMINAL_CHOICES:
            return tool_error(
                "unknown_terminal",
                f"不支持的终端：{terminal}。",
                hint="可选 powershell / cmd / gitbash / wsl。",
            )
        try:
            record = manager.start(text, workdir, ctx.workspace, terminal=chosen)
        except (OSError, ValueError) as exc:
            return tool_error("spawn_failed", f"无法启动进程：{exc}。")

        return (
            f"已启动后台进程：{record.id}\n"
            f"PID：{record.pid}\n"
            f"命令：{record.command}\n"
            f"终端：{terminal_label(record.terminal)}\n"
            f"目录：{display_path(ctx.workspace, workdir)}\n"
            f"日志：{record.log_path}\n"
            "用 read_process_logs 查看输出，用 stop_process 结束。"
        )

    @tool
    def list_processes() -> str:
        """列出本应用启动过的后台进程及其运行状态。"""
        records = manager.list()
        if not records:
            return "当前没有后台进程。"
        lines = [f"后台进程（{len(records)} 个）："]
        for record in records:
            status = f"运行中 (PID {record.pid})" if record.running else f"已退出 (code {record.exit_code})"
            lines.append(f"- {record.id} · {status} · {record.command}")
            lines.append(f"  启动：{record.started_at}，日志：{record.log_path.name}")
        return truncate("\n".join(lines), ctx.max_output_chars)

    @tool
    def read_process_logs(process_id: str, tail: int = 200) -> str:
        """读取后台进程的日志尾部。

        Args:
            process_id: start_process 返回的进程 ID。
            tail: 最多返回最后多少行（10-5000），默认 200。

        Returns:
            进程状态、命令与日志内容；ID 不存在时返回 ``Error:``。
        """
        record, text = manager.read_log(process_id, tail)
        if record is None:
            return tool_error(
                "unknown_process",
                f"找不到进程 {process_id!r}。",
                hint="先用 list_processes 查看现有进程 ID。",
            )
        status = f"运行中 (PID {record.pid})" if record.running else f"已退出 (code {record.exit_code})"
        header = [f"进程：{record.id} · {status}", f"命令：{record.command}", "--- 日志 ---"]
        body = text.rstrip() or "(暂无输出)"
        return truncate("\n".join([*header, body]), ctx.max_output_chars, keep_tail=True)

    @tool
    def wait_process(process_id: str, timeout: int = 30, pattern: str = "", tail: int = 60) -> str:
        """等待后台进程退出，或等待日志中出现某个模式。

        适合启动 dev server 后等「Listening on ...」、跑构建后等结束，
        避免模型反复调用 read_process_logs 轮询。

        Args:
            process_id: start_process 返回的进程 ID。
            timeout: 最长等待秒数（1-120），默认 30。
            pattern: 可选的正则；日志中出现匹配行时立即返回。
            tail: 返回最后多少行日志（10-500），默认 60。

        Returns:
            进程状态、命中的日志行与日志尾部。等待超时不算工具错误，
            会明确说明进程仍在运行。
        """
        record = manager.get(process_id)
        if record is None:
            return tool_error(
                "unknown_process",
                f"找不到进程 {process_id!r}。",
                hint="先用 list_processes 查看现有进程 ID。",
            )
        regex = None
        if (pattern or "").strip():
            import re

            try:
                regex = re.compile(pattern)
            except re.error as exc:
                return tool_error(
                    "bad_pattern",
                    f"正则表达式无法编译：{exc}。",
                    hint="请检查括号、方括号是否配对，或留空 pattern 只等进程退出。",
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

        status = f"运行中 (PID {record.pid})" if record.running else f"已退出 (code {record.exit_code})"
        header = [f"进程：{record.id} · {status}"]
        if matched:
            header.append(f"命中：{matched.strip()}")
        elif regex is not None:
            header.append(f"未命中模式：{pattern}")
        if timed_out:
            header.append(f"等待超时：进程在 {limit}s 内没有退出，也没有命中模式。")

        text = log_text().replace("\r\n", "\n")
        lines = text.rstrip("\n").splitlines()
        keep = max(10, min(int(tail or 60), 500))
        if len(lines) > keep:
            lines = [f"…[日志共 {len(lines)} 行，只显示最后 {keep} 行]…", *lines[-keep:]]
        header.append("--- 日志 ---")
        header.append("\n".join(lines) if lines else "(暂无输出)")
        return truncate("\n".join(header), ctx.max_output_chars, keep_tail=True)

    @tool
    def stop_process(process_id: str) -> str:
        """停止一个由本应用启动的后台进程（会结束其子进程）。

        Args:
            process_id: start_process 返回的进程 ID。

        Returns:
            停止结果与退出码；ID 不存在时返回 ``Error:``。
        """
        record = manager.get(process_id)
        if record is None:
            return tool_error(
                "unknown_process",
                f"找不到进程 {process_id!r}。",
                hint="先用 list_processes 查看现有进程 ID。",
            )
        if not record.running:
            return f"进程 {record.id} 已经退出（code {record.exit_code}），无需停止。"
        stopped = manager.stop(record.id)
        code = stopped.exit_code if stopped else None
        return f"已停止进程：{record.id}（退出码 {code}）。"

    tools = [start_process, list_processes, read_process_logs, wait_process, stop_process]
    if only is None:
        return tools
    return [tool for tool in tools if tool.name in only]
