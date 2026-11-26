# tools/git.py
"""Git tools: ``git_status`` / ``git_diff`` / ``git_commit``.

Git is invoked with argument lists (never shell strings) inside the current
workspace. ``git_commit`` carries ``exec`` risk and goes through approval before
running.

``git_commit`` commits only **already staged** changes: when ``files`` is non-empty
those paths are ``git add``-ed first; otherwise the changes must already be staged.
It never runs ``git add -A`` automatically.
"""

from __future__ import annotations

import subprocess
from typing import Sequence

from langchain.tools import tool

from tools.context import ToolContext
from tools.env import build_child_env
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, resolve_in_workspace
from tools.spec import RISK_EXEC, ToolSpec, single_tool


class GitError(ValueError):
    """Git invocation failed; carries a machine-readable error code."""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def _run_git(ctx: ToolContext, args: Sequence[str], *, timeout: int = 30) -> tuple[int, str, str]:
    """Run git in the workspace; return ``(exit code, stdout, stderr)``."""
    if ctx.workspace is None:
        raise GitError(
            "no_workspace",
            "No workspace is available, so Git operations cannot run.",
            hint="Select a workspace directory in the top bar first.",
        )
    command = ["git", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=str(ctx.workspace),
            capture_output=True,
            timeout=timeout,
            env=build_child_env({
                "GIT_TERMINAL_PROMPT": "0",
                "PYTHONIOENCODING": "utf-8",
            }),
        )
    except FileNotFoundError as exc:
        raise GitError(
            "git_missing",
            "The git command was not found on this system.",
            hint="Install Git and make sure it is on PATH.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            "git_timeout",
            f"The Git command did not finish within {timeout}s.",
            hint="The repository may be large or waiting for input; try running it once manually in a terminal.",
        ) from exc
    except OSError as exc:
        raise GitError("git_failed", f"Cannot start Git: {exc}.") from exc

    return (
        int(completed.returncode),
        _decode(completed.stdout),
        _decode(completed.stderr),
    )


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _ensure_repo(ctx: ToolContext) -> None:
    code, out, _err = _run_git(ctx, ["rev-parse", "--is-inside-work-tree"])
    if code != 0 or out.strip().lower() != "true":
        raise GitError(
            "not_a_git_repo",
            "The current workspace is not a Git repository.",
            hint="Select a Git repository under Settings → Workspace, or run git init in a terminal first.",
        )


def _status_lines(ctx: ToolContext) -> tuple[str, list[str]]:
    _ensure_repo(ctx)
    code, out, err = _run_git(ctx, ["status", "--porcelain=v1", "-b", "--", "."])
    if code != 0:
        raise GitError(
            "git_status_failed",
            f"git status failed: {err.strip() or out.strip() or 'unknown error'}",
        )
    lines = out.splitlines()
    branch = ""
    changes: list[str] = []
    for line in lines:
        if line.startswith("## "):
            branch = line[3:].strip()
        elif line.strip():
            changes.append(line)
    return branch, changes


def _status_label(code: str) -> str:
    if code == "??":
        return "Untracked"
    if "R" in code:
        return "Renamed"
    if "D" in code:
        return "Deleted"
    if "A" in code:
        return "Added"
    if "M" in code:
        return "Modified"
    if "U" in code:
        return "Conflict"
    return "Changed"


def build_git_tools(ctx: ToolContext, only: set[str] | None = None) -> list:
    """Build the Git tool group; ``only`` splits it by registry entry."""

    @tool
    def git_status() -> str:
        """Show the current branch and working tree changes (equivalent to git status --short).

        Useful after edits to confirm which files changed; use git_diff when the
        actual content matters.

        Returns:
            The branch, number of changes, and per-file status; returns ``Error:``
            when the workspace is not a Git repository.
        """
        try:
            branch, changes = _status_lines(ctx)
        except GitError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if not changes:
            return f"Branch: {branch or '(unknown)'}\nWorking tree clean; no uncommitted changes."
        lines = [
            f"Branch: {branch or '(unknown)'}",
            f"Changes: {len(changes)} files",
            "",
        ]
        for line in changes:
            code = line[:2]
            path = line[3:].strip() if len(line) > 3 else ""
            lines.append(f"{_status_label(code)}  {path}  [{code.strip()}]")
        return truncate("\n".join(lines), ctx.max_output_chars)

    @tool
    def git_diff(path: str = "", staged: bool = False, context: int = 3) -> str:
        """Show the diff of the working tree or the staging area.

        Useful for reviewing changes before committing; pass path to limit the
        output to one file.

        Args:
            path: File or directory path relative to the workspace; empty means all changes.
            staged: When True, show staged changes (git diff --cached).
            context: Context lines kept per hunk (0-10), default 3.

        Returns:
            Standard unified diff text; a note when there are no changes.
        """
        try:
            _ensure_repo(ctx)
        except GitError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)

        args = ["diff", "--no-color", f"--unified={max(0, min(int(context or 3), 10))}"]
        if staged:
            args.append("--cached")
        if (path or "").strip():
            try:
                target = resolve_in_workspace(ctx.workspace, path)
            except WorkspacePathError as exc:
                return tool_error(exc.code, exc.message, hint=exc.hint)
            relative = target.relative_to(ctx.workspace).as_posix() if ctx.workspace else path
            args += ["--", relative]
        else:
            args += ["--", "."]

        code, out, err = _run_git(ctx, args)
        if code != 0:
            return tool_error(
                "git_diff_failed",
                f"git diff failed: {err.strip() or out.strip() or 'unknown error'}.",
            )
        if not out.strip():
            where = "staged area" if staged else "working tree"
            return f"No changes to show in the {where}."
        label = "staged" if staged else "working tree"
        header = f"Git diff ({label}{' · ' + path if path else ''})"
        return truncate(f"{header}\n{out.rstrip()}", ctx.max_output_chars)

    @tool
    def git_commit(message: str, files: list[str] | None = None) -> str:
        """Commit staged changes; when files is passed, those paths are staged first.

        It never runs ``git add -A`` automatically: either list the paths to commit
        in files, or let the user/another tool stage them first. Running git_status
        / git_diff before committing is recommended.

        Args:
            message: Commit message (must not be empty).
            files: List of relative paths to commit; empty means commit only what is already staged.

        Returns:
            The commit hash and summary; returns ``Error:`` when nothing is staged,
            the commit fails, or the workspace is not a repository.
        """
        text = (message or "").strip()
        if not text:
            return tool_error("empty_message", "The commit message must not be empty.")
        try:
            _ensure_repo(ctx)
            if isinstance(files, str):
                files = [files]
            if files:
                paths: list[str] = []
                for raw in files:
                    try:
                        target = resolve_in_workspace(ctx.workspace, raw)
                    except WorkspacePathError as exc:
                        return tool_error(exc.code, exc.message, hint=exc.hint)
                    paths.append(
                        target.relative_to(ctx.workspace).as_posix() if ctx.workspace else raw
                    )
                code, out, err = _run_git(ctx, ["add", "--", *paths])
                if code != 0:
                    return tool_error(
                        "git_add_failed",
                        f"git add failed: {err.strip() or out.strip() or 'unknown error'}.",
                    )
            code, out, err = _run_git(ctx, ["diff", "--cached", "--quiet"])
            if code == 0:
                return tool_error(
                    "nothing_staged",
                    "Nothing is staged, so there is nothing to commit.",
                    hint="Specify the paths to commit via files, or git add first and retry.",
                )
            if code not in (0, 1):
                return tool_error(
                    "git_diff_failed",
                    f"Cannot inspect the staging area: {err.strip() or out.strip() or 'unknown error'}.",
                )
            code, out, err = _run_git(ctx, ["commit", "-m", text])
            if code != 0:
                return tool_error(
                    "git_commit_failed",
                    f"git commit failed: {err.strip() or out.strip() or 'unknown error'}.",
                )
            summary = out.strip().splitlines()
            head = summary[0] if summary else ""
            return f"Committed: {head}\nMessage: {text}"
        except GitError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)

    tools = [git_status, git_diff, git_commit]
    if only is None:
        return tools
    return [tool for tool in tools if tool.name in only]


TOOL_ENTRIES: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="git_status", label="Git 状态", description="查看工作区状态",
        tool_names=("git_status",),
        builder=single_tool(lambda _config, ctx: build_git_tools(ctx), "git_status"),
        defer=True,
        search_hint="git status branch working tree 分支 工作区状态",
    ),
    ToolSpec(
        name="git_diff", label="Git 差异", description="查看文件改动",
        tool_names=("git_diff",),
        builder=single_tool(lambda _config, ctx: build_git_tools(ctx), "git_diff"),
        defer=True,
        search_hint="git diff changes 差异 改动",
    ),
    ToolSpec(
        name="git_commit",
        label="Git 提交",
        description="暂存并提交指定文件",
        risk=RISK_EXEC,
        tool_names=("git_commit",),
        builder=single_tool(lambda _config, ctx: build_git_tools(ctx), "git_commit"),
        capability={
            "read_only": False,
            "destructive": False,
            "side_effect_scope": "git",
            "risk_level": "medium",
            "needs_approval": True,
            "permission_name": "git",
        },
        defer=True,
        search_hint="git commit stage 提交 暂存",
    ),
)
