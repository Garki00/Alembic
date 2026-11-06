# web/approvals.py
"""网页端的审批中继。

工具中间件在「某个工具线程」里同步等待人做决定，而决定来自另一个 HTTP 请求，
所以这里用「注册表 + Event」把两边接起来：

- 中间件调用 :meth:`ApprovalBroker.ask` → 登记一条待办、往 SSE 队列推一个
  ``approval`` 事件，然后阻塞在 ``threading.Event`` 上；
- 浏览器点按钮 → ``POST /api/approvals/{id}`` → :meth:`ApprovalRegistry.resolve`
  写入决定并唤醒该线程；
- 没人回答（超时）或流断了 → 按拒绝处理，工具不执行。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from agents.approval import VALID_DECISIONS, ApprovalRequest, Decision


@dataclass
class PendingApproval:
    """一条等待回执的审批。"""

    request: ApprovalRequest
    event: threading.Event = field(default_factory=threading.Event)
    decision: str = "deny"


class ApprovalRegistry:
    """全局待办表：``request_id`` → 等待者，供 HTTP 端点投递决策。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}

    def register(self, request: ApprovalRequest) -> PendingApproval:
        pending = PendingApproval(request=request)
        with self._lock:
            self._pending[request.id] = pending
        return pending

    def resolve(self, request_id: str, decision: str) -> bool:
        """投递决策；请求不存在或已过期时返回 False。"""
        if decision not in VALID_DECISIONS:
            return False
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
        pending.decision = decision
        pending.event.set()
        return True

    def discard(self, request_id: str) -> None:
        with self._lock:
            self._pending.pop(request_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)


class ApprovalBroker:
    """单个对话流的审批通道。

    Args:
        registry: 全局待办表，HTTP 端点据此找到等待者。
        emit: 把 ``(事件名, 数据)`` 推给 SSE 队列的回调。
        timeout: 等待人回答的秒数，超时按拒绝处理。
    """

    def __init__(
        self,
        *,
        registry: ApprovalRegistry,
        emit: Callable[[str, dict], None],
        timeout: float = 300.0,
    ) -> None:
        self.registry = registry
        self.emit = emit
        self.timeout = max(1.0, float(timeout))
        self._ids: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False

    def ask(self, request: ApprovalRequest) -> Decision:
        """阻塞等待决策；异常路径一律返回 deny，绝不默认放行。"""
        with self._lock:
            if self._closed:
                return "deny"
            pending = self.registry.register(request)
            self._ids.add(request.id)

        self.emit("approval", request.as_event())

        if not pending.event.wait(self.timeout):
            self.registry.discard(request.id)
            with self._lock:
                self._ids.discard(request.id)
            self.emit("approval_expired", {"id": request.id, "tool": request.tool})
            return "deny"

        with self._lock:
            self._ids.discard(request.id)
        return pending.decision  # type: ignore[return-value]

    def close(self) -> None:
        """流结束时清掉没被回答的请求，别把工具线程永远挂在那里。"""
        with self._lock:
            self._closed = True
            pending_ids = list(self._ids)
            self._ids.clear()
        for request_id in pending_ids:
            self.registry.resolve(request_id, "deny")
