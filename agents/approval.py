# agents/approval.py
"""工具审批：危险工具执行前先让人确认。

与主流 agent 一致的两级决策：

- **允许本次**：只放行这一次调用；
- **始终允许**：本会话内记住「工具 + 对象」（命令原文 / 文件路径），
  之后同类调用自动放行，不再打扰；
- **拒绝**：工具不执行，把失败写回给模型，让它换方案而不是重试。

哪些工具需要审批由风险等级决定（见 ``tools.registry`` 的 ``risk``）：
``write``（改文件）与 ``exec``（执行命令）必须确认，``read`` 直接执行。

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

from tools.errors import tool_error
from tools.registry import RISK_EXEC, RISK_READ, RISK_WRITE, TOOL_SPECS

logger = logging.getLogger(__name__)

Decision = Literal["once", "always", "deny"]
VALID_DECISIONS = ("once", "always", "deny")

_PREVIEW_LIMIT = 800
# 「始终允许」的记忆前缀：读写同一文件共用 file:，删除/移动各自独立，
# 避免「允许修改 a.py」被误当成「允许删除 a.py」。
_KEY_PREFIX = {
    "write_file": "file",
    "edit_file": "file",
    "delete_file": "delete",
    "move_file": "move",
    "copy_file": "copy",
    "make_dir": "mkdir",
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

    def as_event(self) -> dict:
        return {
            "id": self.id,
            "tool": self.tool,
            "risk": self.risk,
            "target": self.target,
            "summary": self.summary,
            "preview": self.preview,
        }


def _clip(text: str, limit: int = _PREVIEW_LIMIT) -> str:
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


def permission_tools(enabled: set[str] | None, mode: str) -> dict[str, str]:
    """权限模式 → 需要审批的工具。

    - ``read_only``：所有工具都要人确认（用户明确要求最严格模式）；
    - ``workspace_write``：默认；只对执行命令类工具审批，工作区内写文件免审批；
    - ``full_access``：完全不审批。
    """
    if mode == "full_access":
        return {}
    names = set(TOOL_SPECS) if enabled is None else set(enabled)
    allowed_risks = {RISK_EXEC} if mode == "workspace_write" else {RISK_READ, RISK_WRITE, RISK_EXEC}
    gated: dict[str, str] = {}
    for name, spec in TOOL_SPECS.items():
        if name not in names or spec.risk not in allowed_risks:
            continue
        for tool_name in spec.tool_names:
            gated[tool_name] = spec.risk
    return gated


class ApprovalPolicy:
    """判断是否需要审批，并保存「始终允许」的记忆。

    ``memo`` 由调用方持有（网页端按会话存、命令行存在进程内），
    这样跨轮次、跨 SSE 请求都能复用同一份授权。
    """

    def __init__(self, gated: Mapping[str, str], memo: dict[str, dict] | None = None) -> None:
        self.gated = dict(gated)
        self.memo: dict[str, dict] = memo if memo is not None else {}

    def needs_approval(self, tool: str) -> bool:
        return tool in self.gated

    def risk_of(self, tool: str) -> str:
        return self.gated.get(tool, "read")

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


class ApprovalMiddleware(AgentMiddleware):
    """在工具真正执行前插入一道人工确认。"""

    def __init__(self, gate: ApprovalGate) -> None:
        super().__init__()
        self.gate = gate

    # ---- 决策逻辑（同步 / 异步共用） ----
    def _decide(self, call: Mapping) -> ToolMessage | None:
        """返回 None 表示放行，返回 ToolMessage 表示拦截。"""
        name = str(call.get("name") or "")
        args = call.get("args") or {}

        if not self.gate.policy.needs_approval(name):
            return None
        if self.gate.policy.is_allowed(name, args):
            return None

        target, summary, preview = describe_call(name, args)
        request = ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            tool=name,
            risk=self.gate.policy.risk_of(name),
            target=target,
            summary=summary,
            preview=preview,
            args=dict(args),
        )
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


def gate_from(enabled: set[str], memo: dict[str, dict] | None = None) -> ApprovalGate:
    """按启用条目构造命令行用的审批开关。"""
    return ApprovalGate(policy=ApprovalPolicy(gated_tools(enabled), memo), ask=console_asker)
