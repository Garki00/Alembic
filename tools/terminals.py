# tools/terminals.py
"""Terminal adapter: run a command through the user's preferred shell.

Supported terminals:

- ``powershell``: Windows PowerShell / PowerShell 7 (default)
- ``cmd``: ``cmd.exe``
- ``gitbash``: the Bash shipped with Git for Windows
- ``wsl``: Bash inside the default WSL distribution

Commands are always launched as an argument list (``shell=False``), and the
command text is passed as a single argument to the target terminal, so Python does
not add another shell layer that would re-parse it. WSL cannot use a Windows path
directly as its working directory, so ``D:\\work`` is mapped to ``/mnt/d/work``
before ``cd``.
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
    """The preferred terminal does not exist or cannot start."""


@dataclass(frozen=True)
class TerminalInfo:
    value: str
    label: str
    available: bool
    detail: str = ""


@dataclass(frozen=True)
class TerminalInvocation:
    """Full description of one terminal invocation; when ``shell`` is True, ``argv`` is a command string."""

    argv: list[str] | str
    cwd: str | None
    shell: bool = False


def normalize_terminal(value: str | None) -> str:
    """Normalize user input or stored configuration into a supported terminal ID."""
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
    # Only then check PATH; exclude the Windows WSL launcher, which is not Git Bash.
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
        raise TerminalUnavailable(f"Cannot find the {TERMINAL_LABELS[terminal]} executable.")
    return terminal, path


def available_terminals() -> list[TerminalInfo]:
    """List the four terminals and whether each is available on this machine (for the settings and onboarding pages)."""
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
    """Convert a Windows absolute path into a Linux path under WSL's default automount."""
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
    """Build one terminal invocation.

    ``cmd`` uses ``shell=True`` because Windows ``CreateProcess`` argument escaping
    (``list2cmdline``) and cmd.exe quoting rules disagree: passing a quoted command
    as a list argument adds backslash escapes, and cmd then cannot find the
    executable. Giving cmd the raw command text matches what the user would type in
    the terminal. All other terminals use argument lists.

    Raises:
        TerminalUnavailable: The preferred terminal is missing.
    """
    text = str(command or "")
    resolved, executable = _resolve(terminal)
    workdir = str(cwd) if cwd else None

    if resolved == "powershell":
        # In PowerShell an executable path starting with a quote must use the call
        # operator, otherwise it is treated as a string expression. The model often
        # writes "C:\...\python.exe" -c ..., so prepend &.
        script = text.lstrip()
        if script[:1] in {'"', "'"} and not script.startswith("&"):
            script = "& " + script
        # A native program's exit code does not automatically become
        # powershell.exe's exit code; wrap the script in a block and exit
        # explicitly so test/build failures are visible to the tool.
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
        # See the docstring: cmd needs the raw command line, not one passed
        # through list2cmdline.
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
    # Non-Windows: bash/sh
    return TerminalInvocation([executable, "-c", text], workdir)
