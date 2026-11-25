# agents/approval.py
"""Tool approval: rules and capabilities decide, a human confirms the rest.

- **Allow once**: let only this call through;
- **Always allow**: remember the pair (tool + target: raw command / file path) for
  the session, so later calls of the same kind are approved automatically;
- **Deny**: do not run the tool; report the failure back to the model so it picks
  another approach instead of retrying.

The decision itself lives in :mod:`agents.permission_service`: user/project rules
(deny → ask → allow) are evaluated first, then the mode (``plan`` / ``build`` /
``edit`` / ``yolo``) and the tool's capability row decide whether an approval is
needed. A session grant remembered here can skip an approval produced by the mode,
but never an explicit ask rule, a deny rule, or the plan gate.

A command policy layer (:mod:`tools.command_policy`) sits outside approval:
irreversible operations are rejected outright, while recoverable but dangerous
ones carry their reason into the approval card.

Approval is enforced through ``wrap_tool_call`` instead of LangChain's
``HumanInTheLoopMiddleware``, so a decision is returned synchronously and the
caller decides how to ask a human (terminal / browser).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, replace
from typing import Callable, Literal, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from tools.command_brief import command_steps, describe_command
from tools.command_policy import assess_command
from tools.errors import tool_error
from tools.registry import TOOL_SPECS, capability_for, entry_for_tool

from agents.permission_service import (
    ALLOW,
    ASK,
    DENY,
    PermissionConfig,
    PermissionDecision,
    evaluate,
)
from config.permission_rules import PermissionRuleset

logger = logging.getLogger(__name__)

#: Permission modes, ordered from strictest to loosest. Subagent profiles may only
#: narrow the parent mode, so the order is also the restriction rank.
PERMISSION_MODES = ("plan", "build", "edit", "yolo")
DEFAULT_PERMISSION_MODE = "build"
#: Names written by older preference files and profiles; accepted on read only.
LEGACY_PERMISSION_MODES = {
    "read_only": "plan",
    "approval_all": "build",
    "workspace_write": "build",
    "full_access": "yolo",
}

Decision = Literal["once", "always", "deny"]
VALID_DECISIONS = ("once", "always", "deny")

PREVIEW_LIMIT = 800
#: 命令原文要尽量完整地给用户看（审批卡片的原文区可滚动），只防极端长度。
COMMAND_PREVIEW_LIMIT = 8000
# 「始终允许」的记忆前缀：读写同一文件共用 file:，删除/移动各自独立，
# 避免「允许修改 a.py」被误当成「允许删除 a.py」。
_KEY_PREFIX = {
    "write_file": "file",
    "edit_file": "file",
    "apply_patch": "patch",
    "batch_edit": "patch",
    "delete_file": "delete",
    "move_file": "move",
    "copy_file": "copy",
    "make_dir": "mkdir",
    "download_file": "download",
}
_FILE_VERBS = {
    "delete_file": "删除",
    "move_file": "移动",
    "copy_file": "复制",
    "make_dir": "新建目录",
}


@dataclass(frozen=True)
class ApprovalRequest:
    """One tool call awaiting confirmation; mirrors what the frontend approval card shows.

    ``summary`` is a one-line human-readable statement of what will happen and
    ``preview`` is the raw command / content; the card states the intent first and
    expands the raw text on demand. ``steps`` splits a command into steps so a
    step-by-step confirmation is easier to judge.
    """

    id: str
    tool: str
    risk: str
    target: str
    summary: str
    preview: str
    args: dict
    reason: str = ""
    rule: str = ""
    steps: tuple[str, ...] = ()

    def as_event(self) -> dict:
        event = {
            "id": self.id,
            "tool": self.tool,
            "risk": self.risk,
            "target": self.target,
            "summary": self.summary,
            "preview": self.preview,
        }
        if len(self.steps) > 1:
            event["steps"] = list(self.steps)
        if self.reason:
            event["reason"] = self.reason
        if self.rule:
            event["rule"] = self.rule
        return event


def _clip(text: str, limit: int = PREVIEW_LIMIT) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _readable_hint(args: Mapping) -> str:
    """The model's own one-line description; returns an empty string when missing or unusable.

    Having the model fill ``description`` on a ``run_command`` call lets the approval
    card show a goal-level statement instead of making the user read the raw command.
    """
    command = " ".join(str(args.get("command") or "").split()).lower()
    for key in ("description", "purpose", "intent"):
        value = args.get(key)
        if not isinstance(value, str):
            continue
        text = " ".join(value.split())
        if not text or text.lower() == command:
            continue
        return text if len(text) <= 240 else text[:240] + "…"
    return ""


def describe_call(tool: str, args: Mapping) -> tuple[str, str, str]:
    """Translate a tool call into (memory target, one-line summary, content preview)."""
    args = dict(args or {})

    command = args.get("command")
    if command is not None:
        text = str(command).strip()
        # 优先用模型自写的意图说明；没有就按命令结构翻译成中文。
        summary = _readable_hint(args) or describe_command(text)
        if not summary:
            program = text.split(None, 1)[0] if text else "shell"
            summary = f"执行一条 {_clip(program, 32)} 命令，原文见下方"
        return text, summary, _clip(text, COMMAND_PREVIEW_LIMIT)

    source = args.get("source")
    destination = args.get("destination")
    if source is not None and destination is not None:
        target = f"{source} → {destination}"
        verb = _FILE_VERBS.get(tool, tool)
        return target, f"{verb}：{target}", _clip(json.dumps(args, ensure_ascii=False))

    process_id = args.get("process_id")
    if process_id is not None:
        return str(process_id), f"停止后台进程：{process_id}", _clip(json.dumps(args, ensure_ascii=False))

    if tool == "batch_edit":
        edits = args.get("edits") or []
        if not isinstance(edits, list):
            edits = []
        paths = []
        for item in edits:
            if not isinstance(item, Mapping):
                continue
            path = str(item.get("path") or "").strip()
            if path and path not in paths:
                paths.append(path)
        target = "、".join(paths) if paths else "多个文件"
        preview = "\n".join(
            f"- {item.get('path')}: {str(item.get('old_string') or '')[:120]} → {str(item.get('new_string') or '')[:120]}"
            for item in edits
            if isinstance(item, Mapping)
        )
        return target, f"原子编辑：{len(paths)} 个文件 / {len(edits)} 处", _clip(preview)

    if tool == "apply_patch":
        patch = str(args.get("patch") or "")
        names: list[str] = []
        try:
            from tools.patch import parse_unified_diff

            names = [item.display_path for item in parse_unified_diff(patch)]
        except Exception:
            names = []
        target = "、".join(names) if names else "补丁"
        return target, f"应用补丁：{len(names) or '?'} 个文件", _clip(patch)

    if tool == "download_file":
        url = str(args.get("url") or "")
        path = str(args.get("path") or "(自动命名)")
        return path, f"下载文件：{path}", _clip(f"{url}\n→ {path}")

    if tool == "git_commit":
        message = str(args.get("message") or "")
        files = args.get("files") or []
        target = ", ".join(str(item) for item in files) if files else "已暂存改动"
        return target, f"提交 Git：{message}", _clip(message)

    path = args.get("path")
    if tool == "write_file":
        content = str(args.get("content") or "")
        return str(path), f"写入文件：{path}（{len(content)} 字符）", _clip(content)
    if tool == "edit_file":
        old = str(args.get("old_string") or "")
        new = str(args.get("new_string") or "")
        return str(path), f"修改文件：{path}（替换 {len(old)} 字符）", _clip(f"- {old}\n+ {new}")
    if tool in _FILE_VERBS and path is not None:
        verb = _FILE_VERBS[tool]
        return str(path), f"{verb}：{path}", _clip(f"{verb} {path}")
    if path is not None:
        return str(path), f"{tool}：{path}", _clip(json.dumps(args, ensure_ascii=False))

    dumped = json.dumps(args, ensure_ascii=False, sort_keys=True)
    return dumped, f"调用 {tool}", _clip(dumped)


def describe_steps(tool: str, args: Mapping) -> tuple[str, ...]:
    """Per-step description for command tools; non-command tools return an empty tuple.

    Display only: when the rules do not recognize a command, approval still proceeds
    normally instead of being allowed or denied because of it.
    """
    command = (args or {}).get("command")
    if command is None:
        return ()
    return tuple(command_steps(str(command)))


def normalize_permission_mode(value: object, default: str = DEFAULT_PERMISSION_MODE) -> str:
    """Normalize a stored or requested mode; legacy names map to their current equivalent."""
    mode = str(value or "").strip().lower()
    mode = LEGACY_PERMISSION_MODES.get(mode, mode)
    return mode if mode in PERMISSION_MODES else default


class ApprovalPolicy:
    """Evaluate rules and capabilities, remember session grants, and block hardline commands.

    ``memo`` is owned by the caller (per session on the web side, in-process for the
    CLI), so the same grants are reused across turns and SSE requests. ``rules`` are
    the user's allow/ask/deny rules; ``project_rules`` are workspace rules that can
    only restrict.
    """

    def __init__(
        self,
        *,
        mode: str = DEFAULT_PERMISSION_MODE,
        memo: dict[str, dict] | None = None,
        rules: PermissionRuleset | None = None,
        project_rules: PermissionRuleset | None = None,
        config: PermissionConfig | None = None,
        prompts_enabled: bool = True,
        shell: str = "",
    ) -> None:
        self.memo: dict[str, dict] = memo if memo is not None else {}
        self.mode = normalize_permission_mode(mode)
        self.rules = rules or PermissionRuleset()
        self.project_rules = project_rules or PermissionRuleset()
        self.config = config or PermissionConfig()
        #: Shell dialect used to classify commands as read-only (POSIX shells only).
        self.shell = str(shell or "")
        # ``APPROVAL_ENABLED=false`` removes the human channel. Mode/capability asks then
        # pass (there is nobody to ask), but an explicit ask rule fails closed instead of
        # silently turning into an allow.
        self.prompts_enabled = prompts_enabled

    def capability(self, tool: str) -> dict:
        return capability_for(tool_name=tool)

    def risk_of(self, tool: str) -> str:
        """Legacy read/write/exec risk used by the approval card."""
        entry = entry_for_tool(tool)
        spec = TOOL_SPECS.get(entry) if entry else None
        if spec is not None:
            return spec.risk
        capability = self.capability(tool)
        if capability.get("read_only"):
            return "read"
        if capability.get("side_effect_scope") in {"system", "git"}:
            return "exec"
        return "write"

    def evaluate(self, tool: str, args: Mapping) -> PermissionDecision:
        """Full decision including remembered session grants (but never overriding rules/plan)."""
        decision = evaluate(
            tool,
            args,
            mode=self.mode,
            rules=self.rules,
            project_rules=self.project_rules,
            config=self.config,
            shell=self.shell,
        )
        if decision.behavior == ASK:
            if decision.session_grant_allowed and self.is_allowed(tool, args):
                return replace(
                    decision,
                    behavior=ALLOW,
                    rule_id="rule.session_allow",
                    reason=f"{tool} was allowed for this session",
                )
            if not self.prompts_enabled:
                if decision.session_grant_allowed:
                    return replace(
                        decision,
                        behavior=ALLOW,
                        rule_id="mode.approval_disabled",
                        reason="Approval prompts are disabled for this runtime",
                    )
                return replace(
                    decision,
                    behavior=DENY,
                    code="denied_by_rule",
                    reason=f"{tool} requires approval, but approval prompts are disabled",
                )
        return decision

    def needs_approval(self, tool: str, args: Mapping | None = None) -> bool:
        """Whether this call would ask a human (after session grants)."""
        return self.evaluate(tool, args or {}).behavior == ASK

    def precheck(self, tool: str, args: Mapping) -> tuple[str, str, str] | None:
        """Hard check before approval; a returned ``(code, message, hint)`` means the call must be blocked."""
        command = args.get("command") if isinstance(args, Mapping) else None
        if command is None:
            return None
        risk = assess_command(str(command))
        if risk.level != "hardline":
            return None
        return (
            "blocked_by_policy",
            f"Command rejected by the security policy: {risk.reason_en}",
            f"Rule: {risk.rule}. This operation is irreversible and cannot be released by approval.",
        )

    def key(self, tool: str, args: Mapping) -> str:
        """Unique key of an approval grant.

        Both file-writing tools share ``file:<path>``: "always allow editing a.py" does
        not depend on write_file vs edit_file, and splitting it would ask twice. Delete /
        move / copy stay separate so "allow editing" never widens into "allow deleting".
        """
        target, _, _ = describe_call(tool, args)
        prefix = _KEY_PREFIX.get(tool, tool)
        return f"{prefix}:{target}"

    def is_allowed(self, tool: str, args: Mapping) -> bool:
        return self.key(tool, args) in self.memo

    def remember(self, tool: str, args: Mapping) -> str:
        key = self.key(tool, args)
        target, summary, _ = describe_call(tool, args)
        self.memo[key] = {
            "key": key,
            "tool": tool,
            "risk": self.risk_of(tool),
            "target": target,
            "summary": summary,
        }
        return key

    def items(self) -> list[dict]:
        return list(self.memo.values())

    def clear(self) -> int:
        count = len(self.memo)
        self.memo.clear()
        return count


@dataclass(frozen=True)
class ApprovalGate:
    """Bind a policy to the way a human is asked; the runtime installs it as middleware."""

    policy: ApprovalPolicy
    ask: Callable[[ApprovalRequest], Decision]


def denied_tool_message(call: Mapping) -> ToolMessage:
    name = str(call.get("name") or "")
    return ToolMessage(
        content=tool_error(
            "approval_denied",
            f"The user denied this `{name}` call, so the tool did not run.",
            hint="Do not retry the same call verbatim. If it is truly necessary, explain why first and try again; you may also confirm the current state with read-only tools.",
        ),
        tool_call_id=call.get("id"),
        name=name,
        status="error",
    )


def policy_blocked_message(call: Mapping, code: str, message: str, hint: str = "") -> ToolMessage:
    name = str(call.get("name") or "")
    return ToolMessage(
        content=tool_error(code, message, hint=hint),
        tool_call_id=call.get("id"),
        name=name,
        status="error",
    )


class ApprovalMiddleware(AgentMiddleware):
    """Insert policy checks and human confirmation before a tool actually runs."""

    def __init__(self, gate: ApprovalGate) -> None:
        super().__init__()
        self.gate = gate

    # ---- 决策逻辑（同步 / 异步共用） ----
    def _decide(self, call: Mapping) -> ToolMessage | None:
        """Return None to let the call through, or a ToolMessage to block it."""
        name = str(call.get("name") or "")
        args = call.get("args") or {}

        blocked = self.gate.policy.precheck(name, args)
        if blocked is not None:
            code, message, hint = blocked
            logger.warning("blocked tool call %s: [%s] %s", name, code, message)
            return policy_blocked_message(call, code, message, hint)

        decision = self.gate.policy.evaluate(name, args)
        if decision.behavior == ALLOW:
            return None
        if decision.behavior == DENY:
            code = decision.code or "blocked_by_policy"
            logger.warning("denied tool call %s: [%s] %s", name, decision.rule_id, decision.reason)
            return policy_blocked_message(
                call,
                code,
                decision.reason or f"`{name}` is blocked by the current permission rules.",
                "Do not retry the same call verbatim. Adjust the permission rules or mode first.",
            )

        target, summary, preview = describe_call(name, args)
        command = args.get("command") if isinstance(args, Mapping) else None
        risk = assess_command(str(command)) if command is not None else None
        request = ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            tool=name,
            risk=self.gate.policy.risk_of(name),
            target=target,
            summary=summary,
            preview=preview,
            args=dict(args),
            reason=risk.reason if risk and risk.is_elevated else "",
            rule=risk.rule if risk and risk.is_elevated else "",
            steps=describe_steps(name, args),
        )
        if request.reason:
            logger.info("elevated tool call %s [%s]: %s", name, request.rule, request.reason)
        try:
            decision = self.gate.ask(request)
        except Exception:  # 提问通道本身出错时按拒绝处理（失败关闭）
            logger.exception("审批请求失败，按拒绝处理")
            return denied_tool_message(call)

        if decision == "deny":
            return denied_tool_message(call)
        if decision == "always":
            self.gate.policy.remember(name, args)
        return None

    def wrap_tool_call(self, request, handler):
        blocked = self._decide(request.tool_call or {})
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._decide(request.tool_call or {})
        if blocked is not None:
            return blocked
        return await handler(request)


# ---------------------------------------------------------------------------
# 命令行提问方式
# ---------------------------------------------------------------------------
_PROMPT = """\
需要确认：{summary}
{details}  [y] 允许本次    [a] 始终允许（本会话）    [n] 拒绝
选择 [y/a/n]: """

_CHOICES = {
    "y": "once", "yes": "once", "1": "once",
    "a": "always", "always": "always", "2": "always",
    "n": "deny", "no": "deny", "3": "deny", "": "deny",
}

_COMMAND_TOOLS = {"run_command", "start_process"}


def _console_details(request: ApprovalRequest) -> str:
    """Add the step list and the raw content for the terminal; the web card does its own layout."""
    lines: list[str] = []
    if len(request.steps) > 1:
        lines.extend(f"  · {step}" for step in request.steps)
    if request.preview:
        label = "原始命令" if request.tool in _COMMAND_TOOLS else "详细内容"
        lines.append(f"  {label}：{request.preview}")
    return "\n".join(lines) + "\n" if lines else ""


def console_asker(request: ApprovalRequest) -> Decision:
    """Ask on the terminal.

    Non-interactive situations (stdin piped, Ctrl-C) are always treated as a denial so
    a script never silently approves a dangerous operation.
    """
    try:
        answer = input(
            _PROMPT.format(summary=request.summary, details=_console_details(request))
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n[已拒绝]")
        return "deny"
    return _CHOICES.get(answer, "deny")  # type: ignore[return-value]


def gate_from(
    memo: dict[str, dict] | None = None,
    *,
    mode: str = DEFAULT_PERMISSION_MODE,
    rules: PermissionRuleset | None = None,
    project_rules: PermissionRuleset | None = None,
) -> ApprovalGate:
    """Build the CLI approval gate."""
    return ApprovalGate(
        policy=ApprovalPolicy(
            mode=mode,
            memo=memo,
            rules=rules,
            project_rules=project_rules,
        ),
        ask=console_asker,
    )
