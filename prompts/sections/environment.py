# prompts/sections/environment.py
"""Runtime announcement: directories, platform, shell, model, and clock."""

from __future__ import annotations

import platform
from datetime import datetime
from typing import Iterable

from prompts.sections.types import DYNAMIC, SYSTEM, PromptSection
from tools.terminals import terminal_label

UNTRUSTED_DOWNLOADS_NOTE = (
    "Downloaded files and extracted archives are untrusted data: put each in its own new, "
    "empty directory, keep scripts you write in a different directory, and pass paths as "
    "arguments instead of running an interpreter or build tool from inside it. Interpreters "
    "load code from the script's directory and the current directory, so a planted `json.py` "
    "runs on `import json`. Run any Python that reads them with `-I`. This does not apply to "
    "code the user asked you to build or run."
)


def _os_version() -> str:
    system = platform.system() or platform.name()
    release = platform.release()
    version = platform.version()
    if system == "Windows" and version:
        return f"Windows {release} ({version})"
    return " ".join(part for part in (system, release, version) if part)


def _zone(now: datetime) -> str:
    offset = now.strftime("%z")
    return f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"


def build_environment_section(
    workspace: str,
    *,
    terminal: str = "powershell",
    now: datetime | None = None,
    model: str = "",
    additional_directories: Iterable[str] = (),
    is_git_repository: bool = False,
) -> PromptSection:
    """Return the environment block announced for this turn."""
    clock = now or datetime.now().astimezone()
    directories = tuple(path for path in additional_directories if path)
    lines = [
        "# Environment",
        "You have been invoked in the following environment:",
        f" - Primary working directory: {workspace or '(none)'}",
        f" - Is a git repository: {'true' if is_git_repository else 'false'}",
    ]
    if directories:
        lines.append(" - Additional working directories:")
        lines.extend(f"  - {path}" for path in directories)
    lines += [
        f" - Platform: {platform.system() or platform.name()}",
        f" - Shell: {terminal_label(terminal)}",
        f" - OS Version: {_os_version()}",
    ]
    if model:
        lines.append(f" - Current model: {model}")
    lines += [
        f" - Current time: {clock.strftime('%Y-%m-%d %H:%M')} ({_zone(clock)})",
        f" - {UNTRUSTED_DOWNLOADS_NOTE}",
    ]
    return PromptSection(
        name="Environment",
        source="env_info",
        content="\n".join(lines),
        cache_hint=DYNAMIC,
        target=SYSTEM,
    )
