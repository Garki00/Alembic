# agents/subagents/admission.py
"""Per-parent-session admission queue for subagent model requests.

Every model request made by a child agent takes one slot from the parent
session's queue. Requests beyond the concurrency limit wait in FIFO order, so a
burst of parallel subagents is serialized instead of multiplying the provider
request rate. Waiting is cancellation-aware.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from langchain.agents.middleware import AgentMiddleware


class SubagentCancelledError(Exception):
    """Raised while waiting for an admission slot when the task was cancelled."""


@dataclass
class _Waiter:
    granted: bool = False


class ModelRequestAdmission:
    """FIFO admission queue shared by all child agents of one parent session."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._condition = threading.Condition()
        self._active = 0
        self._waiters: deque[_Waiter] = deque()

    @contextmanager
    def slot(self, cancel: threading.Event | None = None) -> Iterator[None]:
        """Acquire one slot in FIFO order, waiting in short intervals for cancellation."""
        if cancel is not None and cancel.is_set():
            raise SubagentCancelledError("Subagent cancelled before a model request started.")
        waiter: _Waiter | None = None
        with self._condition:
            if self._active < self.limit and not self._waiters:
                self._active += 1
            else:
                waiter = _Waiter()
                self._waiters.append(waiter)
                while not waiter.granted:
                    if cancel is not None and cancel.is_set():
                        try:
                            self._waiters.remove(waiter)
                        except ValueError:
                            pass
                        self._condition.notify_all()
                        raise SubagentCancelledError(
                            "Subagent cancelled while waiting for a model slot."
                        )
                    self._condition.wait(0.1)
                if cancel is not None and cancel.is_set():
                    self._release_locked()
                    raise SubagentCancelledError(
                        "Subagent cancelled while waiting for a model slot."
                    )
        try:
            yield
        finally:
            with self._condition:
                self._release_locked()

    def _release_locked(self) -> None:
        """Hand the slot to the next waiter, or give it back to the pool."""
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.granted:
                continue
            waiter.granted = True
            self._condition.notify_all()
            return
        self._active = max(0, self._active - 1)

    def snapshot(self) -> dict[str, int]:
        """Return current occupancy for diagnostics."""
        with self._condition:
            return {
                "limit": self.limit,
                "active": self._active,
                "waiting": sum(1 for waiter in self._waiters if not waiter.granted),
            }

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        """Block until no request is active; used by tests and shutdown paths."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._condition:
                if self._active == 0 and not self._waiters:
                    return True
            time.sleep(0.01)
        return False


class ModelAdmissionMiddleware(AgentMiddleware):
    """Hold one admission slot for the duration of each model request."""

    def __init__(
        self,
        admission: ModelRequestAdmission,
        cancel: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self.admission = admission
        self.cancel = cancel

    def wrap_model_call(self, request, handler):
        with self.admission.slot(self.cancel):
            return handler(request)

    async def awrap_model_call(self, request, handler):
        with self.admission.slot(self.cancel):
            return await handler(request)
