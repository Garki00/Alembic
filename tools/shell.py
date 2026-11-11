# tools/shell.py
"""命令执行工具：让 agent 能真正跑起来验证自己的工作（测试、构建、脚本）。

风险等级最高，因此：

- 在工具注册表里默认开启，但执行前必须经过审批；只读模式直接拒绝；
- 工作目录被限制在工作区内（``cwd`` 同样走越界检查）；
- 支持 PowerShell / CMD / Git Bash / WSL，默认跟随「设置 → 通用 → 终端偏好」；
- 子进程只继承白名单环境变量，模型密钥不会被命令读到；
- 强制超时，避免一条挂起的命令拖死整轮对话；
- stdout / stderr 分别截断，保留尾部（报错通常在最后）。

注意：这不是操作系统级沙盒。命令与后端进程属于同一个 OS 用户，真正限制
它能做什么的是命令策略（高危命令直接拒绝）与用户审批，而不是本模块。
"""

from __future__ import annotations

import locale
import subprocess
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

_MAX_TIMEOUT = 600


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


def build_shell_tools(ctx: ToolContext) -> list:
    """构造命令执行工具（终端偏好、环境白名单、强制超时）。"""

    @tool
    def run_command(
        command: str,
        cwd: str = "",
        timeout: int = 0,
        terminal: str = "",
        description: str = "",
    ) -> str:
        """在工作区内执行一条 shell 命令，返回退出码与输出。

        适合跑测试、构建、脚本、查看 git 状态等。命令不会被交互式接管，
        需要确认的操作请改用非交互参数（如 -y）。

        Args:
            command: 要执行的命令，例如 "python -m unittest discover -s tests"、
                "git status --short"、"dir"。
            cwd: 相对工作区的执行目录，默认工作区根目录。
            timeout: 超时秒数（1-600），0 表示使用默认值。
            terminal: 临时指定终端：powershell / cmd / gitbash / wsl；
                留空时使用「设置 → 通用」里的终端偏好。
            description: 用一句白话说明这条命令要做什么、会改动什么
                （例如「扫描当前目录的 .html 文件并检查页面里的本地链接」）。
                审批卡片用它作为主标题，请勿重复命令原文或罗列参数。

        Returns:
            首行是退出码（非零表示命令失败），随后是 stdout 与 stderr 两个区块。
        """
        text = (command or "").strip()
        if not text:
            return tool_error("empty_command", "command 不能为空。")

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

        started = time.monotonic()
        try:
            completed = subprocess.run(
                invocation.argv,
                shell=invocation.shell,
                cwd=invocation.cwd,
                capture_output=True,
                timeout=limit,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            partial = _decode(exc.stdout or b"") + _decode(exc.stderr or b"")
            return tool_error(
                "timeout",
                f"命令在 {limit:g}s 内没有结束，已终止（耗时 {elapsed:.1f}s）。",
                hint=(
                    "可以换更快的命令、加超时参数，或先确认命令是否需要交互式输入。"
                    + (f"\n已产生的输出：\n{truncate(partial, ctx.max_output_chars // 2, keep_tail=True)}" if partial else "")
                ),
            )
        except OSError as exc:
            return tool_error(
                "spawn_failed",
                f"无法启动 {terminal_label(chosen)}：{exc}。",
                hint="请确认该终端可用，或在设置里换一个终端。",
            )

        elapsed = time.monotonic() - started
        code = completed.returncode
        status = "成功" if code == 0 else "失败"
        lines = [
            f"退出码：{code}（{status}）",
            f"终端：{terminal_label(chosen)}",
            f"工作目录：{display_path(ctx.workspace, workdir)}",
            f"耗时：{elapsed:.1f}s",
        ]
        budget = max(200, (ctx.max_output_chars - 200) // 2)
        lines += _block("stdout", _decode(completed.stdout), budget)
        lines += _block("stderr", _decode(completed.stderr), budget)
        if code != 0:
            lines.append("提示：命令以非零状态退出，请检查 stderr 或调整命令后重试。")
        # 兜底再截一次：保留尾部，避免 stderr（报错）被整体切掉
        return truncate("\n".join(lines), ctx.max_output_chars, keep_tail=True)

    return [run_command]
