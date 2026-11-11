# tools/git.py
"""Git 工具：``git_status`` / ``git_diff`` / ``git_commit``。

不通过 shell 拼命令，而是用参数列表调用 ``git``，避免命令注入。所有命令都限制在
当前工作区；``git_commit`` 属于 ``exec`` 风险，执行前会走审批。

``git_commit`` 只提交**已经暂存**的改动：``files`` 非空时先 ``git add`` 这些路径，
为空时要求用户/模型先自行暂存。它不会静默 ``git add -A``，避免把无关文件带进提交。
"""

from __future__ import annotations

import subprocess
from typing import Sequence

from langchain.tools import tool

from tools.context import ToolContext
from tools.env import build_child_env
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, resolve_in_workspace


class GitError(ValueError):
    """Git 调用失败；带机读错误码。"""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def _run_git(ctx: ToolContext, args: Sequence[str], *, timeout: int = 30) -> tuple[int, str, str]:
    """在工作区执行 git；返回 ``(退出码, stdout, stderr)``。"""
    if ctx.workspace is None:
        raise GitError(
            "no_workspace",
            "当前没有可用工作区，无法执行 Git 操作。",
            hint="请先在顶栏选择一个工作区目录。",
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
            "系统里找不到 git 命令。",
            hint="请先安装 Git，并确认它已加入 PATH。",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            "git_timeout",
            f"Git 命令在 {timeout}s 内没有结束。",
            hint="仓库可能过大或正在等待输入；可以先在终端里手动执行一次。",
        ) from exc
    except OSError as exc:
        raise GitError("git_failed", f"无法启动 Git：{exc}。") from exc

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
            "当前工作区不是 Git 仓库。",
            hint="在设置 → 工作区里选择一个 Git 仓库，或先在终端执行 git init。",
        )


def _status_lines(ctx: ToolContext) -> tuple[str, list[str]]:
    _ensure_repo(ctx)
    code, out, err = _run_git(ctx, ["status", "--porcelain=v1", "-b", "--", "."])
    if code != 0:
        raise GitError(
            "git_status_failed",
            f"git status 失败：{err.strip() or out.strip() or '未知错误'}",
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
        return "未跟踪"
    if "R" in code:
        return "重命名"
    if "D" in code:
        return "删除"
    if "A" in code:
        return "新增"
    if "M" in code:
        return "修改"
    if "U" in code:
        return "冲突"
    return "变更"


def build_git_tools(ctx: ToolContext, only: set[str] | None = None) -> list:
    """构造 Git 工具组；``only`` 用于按注册表条目拆分。"""

    @tool
    def git_status() -> str:
        """查看当前分支与工作区改动（相当于 git status --short）。

        适合在修改后确认改了哪些文件；需要看具体内容时用 git_diff。

        Returns:
            分支、改动数量与逐文件状态；不是 Git 仓库时返回 ``Error:``。
        """
        try:
            branch, changes = _status_lines(ctx)
        except GitError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if not changes:
            return f"分支：{branch or '(未知)'}\n工作区干净，没有未提交的改动。"
        lines = [
            f"分支：{branch or '(未知)'}",
            f"改动：{len(changes)} 个文件",
            "",
        ]
        for line in changes:
            code = line[:2]
            path = line[3:].strip() if len(line) > 3 else ""
            lines.append(f"{_status_label(code)}  {path}  [{code.strip()}]")
        return truncate("\n".join(lines), ctx.max_output_chars)

    @tool
    def git_diff(path: str = "", staged: bool = False, context: int = 3) -> str:
        """查看工作区或暂存区的 diff。

        适合在提交前检查改动内容；想只看某个文件时传 path。

        Args:
            path: 相对工作区的文件/目录路径；留空表示全部改动。
            staged: True 时查看已暂存（git diff --cached）的改动。
            context: 每个改动块保留的上下文行数（0-10），默认 3。

        Returns:
            标准 unified diff 文本；没有改动时给出说明。
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
                f"git diff 失败：{err.strip() or out.strip() or '未知错误'}。",
            )
        if not out.strip():
            where = "暂存区" if staged else "工作区"
            return f"{where}没有可显示的改动。"
        label = "暂存区" if staged else "工作区"
        header = f"Git diff（{label}{' · ' + path if path else ''}）"
        return truncate(f"{header}\n{out.rstrip()}", ctx.max_output_chars)

    @tool
    def git_commit(message: str, files: list[str] | None = None) -> str:
        """提交已暂存的改动；传 files 时会先暂存这些路径。

        不会自动 ``git add -A``：要么在 files 里明确列出要提交的路径，要么先
        让用户/其他工具暂存。提交前建议先 git_status / git_diff 检查。

        Args:
            message: 提交信息（不能为空）。
            files: 要提交的相对路径列表；留空表示只提交已经暂存的改动。

        Returns:
            提交哈希与摘要；没有暂存内容、提交失败或不是仓库时返回 ``Error:``。
        """
        text = (message or "").strip()
        if not text:
            return tool_error("empty_message", "提交信息不能为空。")
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
                        f"git add 失败：{err.strip() or out.strip() or '未知错误'}。",
                    )
            code, out, err = _run_git(ctx, ["diff", "--cached", "--quiet"])
            if code == 0:
                return tool_error(
                    "nothing_staged",
                    "暂存区没有改动，无法提交。",
                    hint="先用 files 指定要提交的路径，或先 git add 再重试。",
                )
            if code not in (0, 1):
                return tool_error(
                    "git_diff_failed",
                    f"无法检查暂存区：{err.strip() or out.strip() or '未知错误'}。",
                )
            code, out, err = _run_git(ctx, ["commit", "-m", text])
            if code != 0:
                return tool_error(
                    "git_commit_failed",
                    f"git commit 失败：{err.strip() or out.strip() or '未知错误'}。",
                )
            summary = out.strip().splitlines()
            head = summary[0] if summary else ""
            return f"已提交：{head}\n提交信息：{text}"
        except GitError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)

    tools = [git_status, git_diff, git_commit]
    if only is None:
        return tools
    return [tool for tool in tools if tool.name in only]
