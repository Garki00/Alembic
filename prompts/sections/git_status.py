# prompts/sections/git_status.py
"""Repository snapshot at the start of a conversation.

The snapshot is collected once and cached for a short window, then rendered as
the ``# gitStatus`` block. It is deliberately a snapshot: the prompt says so, and
the model is expected to re-check with the Git tools before acting on it.

Git is invoked with fixed argument lists (never a shell string), a short
timeout, and the same whitelisted child environment the Git tools use.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from prompts.sections.types import DYNAMIC, SYSTEM, PromptSection
from tools.env import build_child_env

#: A conversation is treated as started once; the snapshot stays stable for this
#: window even when the agent is rebuilt for a later turn.
SNAPSHOT_TTL_SECONDS = 600.0
_COMMAND_TIMEOUT = 3.0

_CACHE: dict[str, tuple[float, "GitSnapshot"]] = {}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class GitSnapshot:
    """Facts shown in the gitStatus block."""

    is_repo: bool = False
    branch: str = ""
    main_branch: str = ""
    user: str = ""
    status: str = ""
    recent_commits: tuple[str, ...] = ()


def find_git_dir(workspace: str | Path | None) -> Path | None:
    """Return the nearest ``.git`` entry without running Git."""
    if not workspace:
        return None
    try:
        current = Path(workspace).expanduser().resolve()
    except OSError:
        return None
    for _ in range(12):
        candidate = current / ".git"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _git(workspace: Path, args: Sequence[str]) -> tuple[int, str, str]:
    env = build_child_env({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(workspace),
            capture_output=True,
            timeout=_COMMAND_TIMEOUT,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, "", ""
    return (
        int(completed.returncode),
        completed.stdout.decode("utf-8", errors="replace").strip(),
        completed.stderr.decode("utf-8", errors="replace").strip(),
    )


def _main_branch(workspace: Path) -> str:
    code, out, _err = _git(workspace, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if code == 0 and out:
        return out.split("/", 1)[-1]
    code, out, _err = _git(workspace, ["config", "--get", "init.defaultBranch"])
    if code == 0 and out:
        return out
    for name in ("main", "master"):
        code, _out, _err = _git(workspace, ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"])
        if code == 0:
            return name
    return ""


def _user(workspace: Path) -> str:
    _code, name, _err = _git(workspace, ["config", "--get", "user.name"])
    _code, email, _err = _git(workspace, ["config", "--get", "user.email"])
    if name and email:
        return f"{name} <{email}>"
    return name or email


def _status(workspace: Path) -> tuple[bool, str, str]:
    """Return ``(ok, branch, porcelain status)``; the branch line is stripped."""
    code, out, _err = _git(workspace, ["status", "--porcelain=v2", "--branch"])
    if code != 0:
        return False, "", ""
    branch = ""
    lines: list[str] = []
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head "):].strip()
            continue
        if line.startswith("#"):
            continue
        lines.append(_format_status_line(line))
    return True, branch, "\n".join(lines)


def _format_status_line(line: str) -> str:
    """Turn one porcelain v2 line into a short, readable status line."""
    parts = line.split(" ")
    if line.startswith("1 "):
        # 1 XY sub mH mI mW hH hI path
        xy = parts[1] if len(parts) > 1 else "??"
        path = " ".join(parts[8:]) if len(parts) > 8 else ""
        return f"{xy} {path}".strip()
    if line.startswith("2 "):
        xy = parts[1] if len(parts) > 1 else "??"
        path = " ".join(parts[9:]) if len(parts) > 9 else ""
        return f"{xy} {path}".strip()
    if line.startswith("u "):
        xy = parts[1] if len(parts) > 1 else "UU"
        path = " ".join(parts[10:]) if len(parts) > 10 else ""
        return f"{xy} {path}".strip()
    if line.startswith("? "):
        return f"?? {line[2:]}"
    if line.startswith("! "):
        return f"!! {line[2:]}"
    return line


def _recent_commits(workspace: Path) -> tuple[str, ...]:
    code, out, _err = _git(workspace, ["log", "-n", "5", "--pretty=format:%h %s"])
    if code != 0 or not out:
        return ()
    return tuple(line for line in out.splitlines() if line)


def collect_git_snapshot(workspace: str | Path | None) -> GitSnapshot:
    """Collect the repository facts once per workspace, cached for a session."""
    if not workspace or find_git_dir(workspace) is None:
        return GitSnapshot()
    try:
        root = Path(workspace).expanduser().resolve()
    except OSError:
        return GitSnapshot()
    key = str(root).lower() if os.name == "nt" else str(root)
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and now - cached[0] < SNAPSHOT_TTL_SECONDS:
            return cached[1]
    ok, branch, status = _status(root)
    if not ok:
        return GitSnapshot()
    snapshot = GitSnapshot(
        is_repo=True,
        branch=branch,
        main_branch=_main_branch(root),
        user=_user(root),
        status=status,
        recent_commits=_recent_commits(root),
    )
    with _LOCK:
        _CACHE[key] = (now, snapshot)
    return snapshot


def clear_git_snapshot_cache() -> None:
    """Drop cached snapshots; used by tests and after a workspace switch."""
    with _LOCK:
        _CACHE.clear()


def build_git_status_section(snapshot: GitSnapshot | None) -> PromptSection | None:
    """Render the gitStatus block, or ``None`` when the workspace is not a repo."""
    if snapshot is None or not snapshot.is_repo:
        return None
    lines = [
        "# gitStatus",
        "This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.",
        "",
    ]
    if snapshot.branch:
        lines += [f"Current branch: {snapshot.branch}", ""]
    if snapshot.main_branch:
        lines += [f"Main branch (you will usually use this for PRs): {snapshot.main_branch}", ""]
    if snapshot.user:
        lines += [f"Git user: {snapshot.user}", ""]
    lines += ["Status:", snapshot.status or "(clean)", ""]
    lines += ["Recent commits:"]
    lines += [f"  {commit}" for commit in snapshot.recent_commits] or ["  (none)"]
    return PromptSection(
        name="Git Status",
        source="session_context",
        content="\n".join(lines),
        cache_hint=DYNAMIC,
        target=SYSTEM,
    )
