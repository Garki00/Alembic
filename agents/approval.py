# agents/approval.py
"""工具审批：危险工具执行前先让人确认。

与主流 agent 一致的两级决策：

- **允许本次**：只放行这一次调用；
- **始终允许**：本会话内记住「工具 + 对象」（命令原文 / 文件路径），
  之后同类调用自动放行，不再打扰；
- **拒绝**：工具不执行，把失败写回给模型，让它换方案而不是重试。

哪些工具需要审批由权限模式与风险等级共同决定（见 :func:`permission_tools`）：

- ``read_only``：真正只读。写文件与执行命令在审批之前就被拒绝；
- ``approval_all``：所有工具调用都要审批；
- ``workspace_write``：工作区内写文件免审批，执行命令仍需审批；
- ``full_access``：不审批（但不可恢复的高危命令仍会被命令策略拒绝）。

审批之外还有一层命令策略（:mod:`tools.command_policy`）：不可恢复的操作
直接拒绝，可恢复但危险的操作会把原因带进审批卡片。

这里没有用 LangChain 自带的 ``HumanInTheLoopMiddleware``：它基于 LangGraph 的
``interrupt()``，需要有 checkpointer 并把一次对话拆成「暂停 + Command(resume)」，
与当前「一次 SSE 流跑完一轮」的结构不兼容。改用 ``wrap_tool_call`` 拦截，
审批结果同步返回，调用方可自由决定怎么问人（终端 / 浏览器）。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Callable, Literal, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from tools.command_policy import assess_command
from tools.errors import tool_error
from tools.registry import RISK_EXEC, RISK_READ, RISK_WRITE, TOOL_SPECS

logger = logging.getLogger(__name__)

Decision = Literal["once", "always", "deny"]
VALID_DECISIONS = ("once", "always", "deny")

PREVIEW_LIMIT = 800
# 「始终允许」的记忆前缀：读写同一文件共用 file:，删除/移动各自独立，
# 避免「允许修改 a.py」被误当成「允许删除 a.py」。
_KEY_PREFIX = {
    "write_file": "file",
    "edit_file": "file",
    "apply_patch": "patch",
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
    """一次待确认的工具调用，直接对应前端审批卡片要显示的内容。"""

    id: str
    tool: str
    risk: str
    target: str
    summary: str
    preview: str
    args: dict
    reason: str = ""
    rule: str = ""

    def as_event(self) -> dict:
        event = {
            "id": self.id,
            "tool": self.tool,
            "risk": self.risk,
            "target": self.target,
            "summary": self.summary,
            "preview": self.preview,
        }
        if self.reason:
            event["reason"] = self.reason
        if self.rule:
            event["rule"] = self.rule
        return event


def _clip(text: str, limit: int = PREVIEW_LIMIT) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def describe_call(tool: str, args: Mapping) -> tuple[str, str, str]:
    """把工具调用翻译成 (记忆用的 target, 一句话摘要, 内容预览)。"""
    args = dict(args or {})

    command = args.get("command")
    if command is not None:
        text = str(command).strip()
        return text, f"执行命令：{text}", _clip(text)

    source = args.get("source")
    destination = args.get("destination")
    if source is not None and destination is not None:
        target = f"{source} → {destination}"
        verb = _FILE_VERBS.get(tool, tool)
        return target, f"{verb}：{target}", _clip(json.dumps(args, ensure_ascii=False))

    process_id = args.get("process_id")
    if process_id is not None:
        return str(process_id), f"停止后台进程：{process_id}", _clip(json.dumps(args, ensure_ascii=False))

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


def gated_tools(enabled: set[str] | None = None) -> dict[str, str]:
    """按条目启用状态算出需要审批的具体工具名 → 风险等级。"""
    names = set(TOOL_SPECS) if enabled is None else set(enabled)
    gated: dict[str, str] = {}
    for name, spec in TOOL_SPECS.items():
        if name not in names:
            continue
        if spec.risk not in {RISK_WRITE, RISK_EXEC}:
            continue
        for tool_name in spec.tool_names:
            gated[tool_name] = spec.risk
    return gated


def tool_risks(enabled: set[str] | None = None) -> dict[str, str]:
    """所有已启用工具的具体工具名 → 风险等级（只读模式也用它判断读写）。"""
    names = set(TOOL_SPECS) if enabled is None else set(enabled)
    risks: dict[str, str] = {}
    for name, spec in TOOL_SPECS.items():
        if name not in names:
            continue
        for tool_name in spec.tool_names:
            risks[tool_name] = spec.risk
    return risks


def permission_tools(enabled: set[str] | None, mode: str) -> dict[str, str]:
    """权限模式 → 需要审批的工具。

    - ``read_only``：不审批，因为写/执行会在审批之前直接拒绝；只读工具放行；
    - ``approval_all``：所有已启用工具都审批；
    - ``workspace_write``：只对执行命令类工具审批，工作区内写文件免审批；
    - ``full_access``：完全不审批。
    """
    mode = (mode or "workspace_write").strip().lower()
    if mode in {"full_access", "read_only"}:
        return {}
    names = set(TOOL_SPECS) if enabled is None else set(enabled)
    if mode == "approval_all":
        allowed_risks = {RISK_READ, RISK_WRITE, RISK_EXEC}
    else:
        allowed_risks = {RISK_EXEC}
    gated: dict[str, str] = {}
    for name, spec in TOOL_SPECS.items():
        if name not in names or spec.risk not in allowed_risks:
            continue
        for tool_name in spec.tool_names:
            gated[tool_name] = spec.risk
    return gated


class ApprovalPolicy:
    """判断是否需要审批、是否被硬性拒绝，并保存「始终允许」的记忆。

    ``memo`` 由调用方持有（网页端按会话存、命令行存在进程内），
    这样跨轮次、跨 SSE 请求都能复用同一份授权。
    """

    def __init__(
        self,
        gated: Mapping[str, str],
        memo: dict[str, dict] | None = None,
        *,
        mode: str = "workspace_write",
        risks: Mapping[str, str] | None = None,
    ) -> None:
        self.gated = dict(gated)
        self.memo: dict[str, dict] = memo if memo is not None else {}
        self.mode = (mode or "workspace_write").strip().lower()
        self.risks = dict(risks or {})

    def needs_approval(self, tool: str) -> bool:
        return tool in self.gated

    def risk_of(self, tool: str) -> str:
        return self.risks.get(tool) or self.gated.get(tool) or "read"

    def precheck(self, tool: str, args: Mapping) -> tuple[str, str, str] | None:
        """审批前的硬性检查；返回 ``(错误码, 说明, 建议)`` 表示必须拦截。"""
        if self.mode == "read_only" and self.risk_of(tool) in {RISK_WRITE, RISK_EXEC}:
            return (
                "read_only",
                f"当前是只读模式，「{tool}」会修改文件或执行命令，已拒绝。",
                "切换到「全部审批」或更高权限后再操作，或改用只读工具查看现状。",
            )

        command = args.get("command") if isinstance(args, Mapping) else None
        if command is None:
            return None
        risk = assess_command(str(command))
        if risk.level != "hardline":
            return None
        return (
            "blocked_by_policy",
            f"命令被安全策略拒绝：{risk.reason}",
            f"规则：{risk.rule}。该操作不可恢复，无法通过审批放行。",
        )

    def key(self, tool: str, args: Mapping) -> str:
        """授权记忆的唯一键。

        写文件的两种工具共用 ``file:<路径>``：用户「始终允许修改 a.py」这个
        意图与用 write_file 还是 edit_file 无关，拆成两条只会重复打扰。
        删除 / 移动 / 复制各自独立，避免「允许改」被扩大成「允许删」。
        """
        target, _, _ = describe_call(tool, args)
        prefix = _KEY_PREFIX.get(tool, tool)
        return f"{prefix}:{target}"

    def is_allowed(self, tool: str, args: Mapping) -> bool:
        return self.needs_approval(tool) and self.key(tool, args) in self.memo

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
    """把策略与「怎么问人」绑在一起，交给运行时装进中间件。"""

    policy: ApprovalPolicy
    ask: Callable[[ApprovalRequest], Decision]


def denied_tool_message(call: Mapping) -> ToolMessage:
    name = str(call.get("name") or "")
    return ToolMessage(
        content=tool_error(
            "approval_denied",
            f"用户拒绝了这次「{name}」调用，工具没有执行。",
            hint="不要原样重试。若确有必要，先解释理由再尝试；也可以改用只读方式确认现状。",
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
    """在工具真正执行前插入策略检查与人工确认。"""

    def __init__(self, gate: ApprovalGate) -> None:
        super().__init__()
        self.gate = gate

    # ---- 决策逻辑（同步 / 异步共用） ----
    def _decide(self, call: Mapping) -> ToolMessage | None:
        """返回 None 表示放行，返回 ToolMessage 表示拦截。"""
        name = str(call.get("name") or "")
        args = call.get("args") or {}

        blocked = self.gate.policy.precheck(name, args)
        if blocked is not None:
            code, message, hint = blocked
            logger.warning("blocked tool call %s: [%s] %s", name, code, message)
            return policy_blocked_message(call, code, message, hint)

        if not self.gate.policy.needs_approval(name):
            return None
        if self.gate.policy.is_allowed(name, args):
            return None

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
{preview}
  [y] 允许本次    [a] 始终允许（本会话）    [n] 拒绝
选择 [y/a/n]: """

_CHOICES = {
    "y": "once", "yes": "once", "1": "once",
    "a": "always", "always": "always", "2": "always",
    "n": "deny", "no": "deny", "3": "deny", "": "deny",
}


def console_asker(request: ApprovalRequest) -> Decision:
    """终端里提问。

    非交互场景（stdin 被管道接管 / Ctrl-C）一律按拒绝处理，
    避免脚本里悄悄放行危险操作。
    """
    preview = f"{request.preview}\n" if request.preview else ""
    try:
        answer = input(_PROMPT.format(summary=request.summary, preview=preview)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n[已拒绝]")
        return "deny"
    return _CHOICES.get(answer, "deny")  # type: ignore[return-value]


def gate_from(
    enabled: set[str],
    memo: dict[str, dict] | None = None,
    *,
    mode: str = "workspace_write",
) -> ApprovalGate:
    """按启用条目构造命令行用的审批开关。"""
    return ApprovalGate(
        policy=ApprovalPolicy(
            permission_tools(enabled, mode),
            memo,
            mode=mode,
            risks=tool_risks(enabled),
        ),
        ask=console_asker,
    )
