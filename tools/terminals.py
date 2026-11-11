# tools/terminals.py
"""终端适配：把一条命令交给用户偏好的 shell 执行。

支持的终端：

- ``powershell``：Windows PowerShell / PowerShell 7（默认）
- ``cmd``：``cmd.exe``
- ``gitbash``：Git for Windows 自带的 Bash
- ``wsl``：默认 WSL 发行版里的 Bash

命令始终以参数列表启动（``shell=False``），命令原文作为单个参数传给目标
终端，避免 Python 再拼一层 shell 造成二次解析。WSL 无法直接使用 Windows
路径作为工作目录，因此会把 ``D:\\work`` 映射成 ``/mnt/d/work`` 再 ``cd``。
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

TERMINAL_CHOICES: tuple[str, ...] = ("powershell", "cmd", "gitbash", "wsl")

TERMINAL_LABELS: dict[str, str] = {
    "powershell": "PowerShell",
    "cmd": "CMD",
    "gitbash": "Git Bash",
    "wsl": "WSL",
}

_ALIASES = {
    "ps": "powershell",
    "pwsh": "powershell",
    "powershell.exe": "powershell",
    "powershell5": "powershell",
    "cmd.exe": "cmd",
    "command": "cmd",
    "bash": "gitbash",
    "git-bash": "gitbash",
    "git_bash": "gitbash",
    "wsl.exe": "wsl",
    "wsl2": "wsl",
}


class TerminalUnavailable(ValueError):
    """偏好终端不存在或无法启动。"""


@dataclass(frozen=True)
class TerminalInfo:
    value: str
    label: str
    available: bool
    detail: str = ""


@dataclass(frozen=True)
class TerminalInvocation:
    """一次终端调用的完整描述；``shell`` 为 True 时 ``argv`` 是命令字符串。"""

    argv: list[str] | str
    cwd: str | None
    shell: bool = False


def normalize_terminal(value: str | None) -> str:
    """把用户输入/历史配置规范成受支持的终端 ID。"""
    text = str(value or "").strip().lower()
    if not text:
        return "powershell"
    text = _ALIASES.get(text, text)
    return text if text in TERMINAL_CHOICES else "powershell"


def terminal_label(value: str | None) -> str:
    return TERMINAL_LABELS.get(normalize_terminal(value), "PowerShell")


def _existing(candidates: list[str | Path | None]) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        text = str(candidate)
        if Path(text).is_file():
            return text
    return ""


def _which(name: str) -> str:
    found = shutil.which(name)
    return found or ""


def _powershell_path() -> str:
    candidates = [
        os.getenv("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe",
        _which("powershell.exe"),
        _which("pwsh.exe"),
    ]
    return _existing(candidates)


def _cmd_path() -> str:
    candidates = [
        os.getenv("COMSPEC", ""),
        os.getenv("SystemRoot", r"C:\Windows") + r"\System32\cmd.exe",
        _which("cmd.exe"),
    ]
    return _existing(candidates)


def _git_bash_path() -> str:
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    candidates = [
        os.getenv("GIT_BASH_PATH", ""),
        Path(program_files) / "Git" / "bin" / "bash.exe",
        Path(program_files) / "Git" / "usr" / "bin" / "bash.exe",
        Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
    ]
    git = _which("git.exe") or _which("git")
    if git:
        git_path = Path(git)
        candidates.extend([
            git_path.parent.parent / "bin" / "bash.exe",
            git_path.parent.parent / "usr" / "bin" / "bash.exe",
        ])
    found = _existing(candidates)
    if found:
        return found
    # 最后才看 PATH；排除 Windows 自带的 WSL 启动器，它不是 Git Bash。
    which = _which("bash.exe") or _which("bash")
    normalized = which.lower().replace("/", "\\")
    if normalized and "windows\\system32\\bash.exe" not in normalized:
        return which
    return ""


def _wsl_path() -> str:
    candidates = [
        os.getenv("SystemRoot", r"C:\Windows") + r"\System32\wsl.exe",
        _which("wsl.exe"),
        _which("wsl"),
    ]
    return _existing(candidates)


def _posix_bash_path() -> str:
    return _which("bash") or _which("sh") or "/bin/sh"


def _resolve(value: str | None) -> tuple[str, str]:
    terminal = normalize_terminal(value)
    if os.name != "nt":
        return "posix", _posix_bash_path()

    finders = {
        "powershell": _powershell_path,
        "cmd": _cmd_path,
        "gitbash": _git_bash_path,
        "wsl": _wsl_path,
    }
    path = finders[terminal]()
    if not path:
        raise TerminalUnavailable(f"找不到 {TERMINAL_LABELS[terminal]} 可执行文件。")
    return terminal, path


def available_terminals() -> list[TerminalInfo]:
    """列出四种终端及本机是否可用（供设置页与初始化页展示）。"""
    result: list[TerminalInfo] = []
    for terminal in TERMINAL_CHOICES:
        try:
            _, path = _resolve(terminal)
        except TerminalUnavailable:
            result.append(TerminalInfo(terminal, TERMINAL_LABELS[terminal], False, ""))
            continue
        detail = Path(path).name if path else ""
        result.append(TerminalInfo(terminal, TERMINAL_LABELS[terminal], True, detail))
    return result


def windows_to_wsl_path(path: str | Path) -> str:
    """把 Windows 绝对路径转成 WSL 默认自动挂载下的 Linux 路径。"""
    text = str(path)
    try:
        resolved = Path(text).resolve()
    except OSError:
        resolved = Path(text)
    drive = resolved.drive
    if len(drive) == 2 and drive.endswith(":"):
        rest = str(resolved)[len(drive):].lstrip("\\/").replace("\\", "/")
        return f"/mnt/{drive[0].lower()}/{rest}"
    return str(resolved).replace("\\", "/")


def build_invocation(
    terminal: str | None,
    command: str,
    cwd: str | Path | None,
) -> TerminalInvocation:
    """构造一次终端调用。

    ``cmd`` 用 ``shell=True`` 是因为 Windows 的 ``CreateProcess`` 参数转义
    （``list2cmdline``）与 cmd.exe 的引号规则不一致：把带引号的命令作为列表
    参数传入会被反斜杠转义，cmd 反而找不到可执行文件。直接交给 cmd 解析命令
    原文与用户在终端里输入一致。其他终端都用参数列表。

    Raises:
        TerminalUnavailable: 偏好终端缺失。
    """
    text = str(command or "")
    resolved, executable = _resolve(terminal)
    workdir = str(cwd) if cwd else None

    if resolved == "powershell":
        # PowerShell 里以引号开头的可执行文件必须用调用运算符，否则会被当成
        # 字符串表达式。模型常写 "C:\...\python.exe" -c ...，这里补一个 &。
        script = text.lstrip()
        if script[:1] in {'"', "'"} and not script.startswith("&"):
            script = "& " + script
        # 原生程序的退出码不会自动成为 powershell.exe 的退出码；
        # 包一层脚本块再显式 exit，测试/构建失败才能被工具看见。
        script = (
            "& {\n" + script + "\n}\n"
            "$ok = $?\n"
            "$code = $LASTEXITCODE\n"
            "if ($null -ne $code) { exit $code }\n"
            "if (-not $ok) { exit 1 }"
        )
        argv = [
            executable, "-NoLogo", "-NoProfile", "-NonInteractive",
            "-Command", script,
        ]
        return TerminalInvocation(argv, workdir)
    if resolved == "cmd":
        # 见 docstring：cmd 需要原始命令行，不能再经过 list2cmdline。
        return TerminalInvocation(text, workdir, shell=True)
    if resolved == "gitbash":
        return TerminalInvocation([executable, "--noprofile", "--norc", "-c", text], workdir)
    if resolved == "wsl":
        if workdir:
            linux_cwd = windows_to_wsl_path(workdir)
            script = f"cd -- {shlex.quote(linux_cwd)} && {text}"
        else:
            script = text
        return TerminalInvocation([executable, "-e", "bash", "-lc", script], None)
    # 非 Windows：bash/sh
    return TerminalInvocation([executable, "-c", text], workdir)