# web/questions.py
"""Question relay for the web frontend: tool threads block for an answer and the browser submits it over HTTP.

The structure mirrors :mod:`web.approvals`:

- A tool call invokes :meth:`QuestionBroker.ask`, registers a pending entry,
  emits a ``question`` SSE event, and blocks on ``threading.Event``.
- The browser submits ``POST /api/questions/{id}``, which calls
  :meth:`QuestionRegistry.resolve`, stores the answer, and wakes the tool thread.
- On timeout or stream disconnect, the method returns ``None`` and the tool
  produces a ``question_timeout`` / ``question_cancelled`` error instead of
  pretending the user answered.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from tools.questions import Question


@dataclass
class PendingQuestion:
    """A question awaiting an answer."""

    question: Question
    event: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None


class QuestionRegistry:
    """Global pending table mapping ``question_id`` to its waiter so HTTP endpoints can deliver answers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingQuestion] = {}

    def register(self, question: Question) -> PendingQuestion:
        pending = PendingQuestion(question=question)
        with self._lock:
            self._pending[question.id] = pending
        return pending

    def resolve(self, question_id: str, answer: str) -> bool:
        with self._lock:
            pending = self._pending.pop(question_id, None)
        if pending is None:
            return False
        pending.answer = answer
        pending.event.set()
        return True

    def cancel(self, question_id: str) -> None:
        """Finish as unanswered; used when the stream disconnects."""
        with self._lock:
            pending = self._pending.pop(question_id, None)
        if pending is None:
            return
        pending.answer = None
        pending.event.set()

    def discard(self, question_id: str) -> None:
        with self._lock:
            self._pending.pop(question_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)


class QuestionBroker:
    """Question channel for a single conversation stream."""

    def __init__(
        self,
        *,
        registry: QuestionRegistry,
        emit: Callable[[str, dict], None],
        timeout: float = 300.0,
    ) -> None:
        self.registry = registry
        self.emit = emit
        self.timeout = max(1.0, float(timeout))
        self._ids: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False

    def ask(self, question: Question) -> str | None:
        """Block for an answer; return None on timeout, close, or any error."""
        with self._lock:
            if self._closed:
                return None
            pending = self.registry.register(question)
            self._ids.add(question.id)

        self.emit("question", question.as_event())

        if not pending.event.wait(self.timeout):
            self.registry.discard(question.id)
            with self._lock:
                self._ids.discard(question.id)
            self.emit("question_expired", {"id": question.id, "question": question.question})
            return None

        with self._lock:
            self._ids.discard(question.id)
        return pending.answer

    def close(self) -> None:
        """Cancel all pending waits when the stream disconnects so tool threads do not hang."""
        with self._lock:
            self._closed = True
            pending_ids = list(self._ids)
            self._ids.clear()
        for question_id in pending_ids:
            self.registry.cancel(question_id)
