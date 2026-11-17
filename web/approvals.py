# web/approvals.py
"""Approval relay for the web frontend.

A tool middleware blocks synchronously in a tool thread waiting for a user
decision, while the decision arrives through a separate HTTP request. A registry
plus ``threading.Event`` connects the two sides:

- The middleware calls :meth:`ApprovalBroker.ask`, registers a pending entry,
  emits an ``approval`` event to the SSE queue, and blocks on the event.
- The browser submits ``POST /api/approvals/{id}``, which calls
  :meth:`ApprovalRegistry.resolve`, stores the decision, and wakes the thread.
- A timeout or a closed stream is treated as a denial, so the tool does not run.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from agents.approval import VALID_DECISIONS, ApprovalRequest, Decision


@dataclass
class PendingApproval:
    """An approval awaiting a decision."""

    request: ApprovalRequest
    event: threading.Event = field(default_factory=threading.Event)
    decision: str = "deny"


class ApprovalRegistry:
    """Global pending table mapping ``request_id`` to its waiter so HTTP endpoints can deliver decisions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}

    def register(self, request: ApprovalRequest) -> PendingApproval:
        pending = PendingApproval(request=request)
        with self._lock:
            self._pending[request.id] = pending
        return pending

    def resolve(self, request_id: str, decision: str) -> bool:
        """Deliver a decision; return False when the request is unknown or expired."""
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
    """Approval channel for a single conversation stream.

    Args:
        registry: Global pending table used by HTTP endpoints to find the waiter.
        emit: Callback that pushes ``(event_name, data)`` to the SSE queue.
        timeout: Seconds to wait for a decision; a timeout is treated as a denial.
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
        """Block until a decision arrives; all error paths return deny rather than allowing the tool."""
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
        """Resolve unanswered requests as denials when the stream ends so tool threads do not hang."""
        with self._lock:
            self._closed = True
            pending_ids = list(self._ids)
            self._ids.clear()
        for request_id in pending_ids:
            self.registry.resolve(request_id, "deny")
