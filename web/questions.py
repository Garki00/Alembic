# web/questions.py
"""网页端的提问中继：工具线程阻塞等回答，浏览器通过 HTTP 投递。

结构与 :mod:`web.approvals` 一致：

- 工具调用 :meth:`QuestionBroker.ask` → 登记、推 ``question`` SSE 事件、
  阻塞在 ``threading.Event``；
- 浏览器提交 → ``POST /api/questions/{id}`` → :meth:`QuestionRegistry.resolve`
  写入回答并唤醒工具线程；
- 超时或流断开 → 返回 ``None``，工具转成 ``question_timeout`` /
  ``question_cancelled`` 错误，绝不假装用户回答了。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from tools.questions import Question


@dataclass
class PendingQuestion:
    """一条等待回答的提问。"""

    question: Question
    event: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None


class QuestionRegistry:
    """全局待答表：``question_id`` → 等待者，供 HTTP 端点投递回答。"""

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
        """按「没有回答」收尾（流断开时用）。"""
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
    """单个对话流的提问通道。"""

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
        """阻塞等待回答；超时/关闭/异常一律返回 None。"""
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
        """流断开时取消所有等待，别把工具线程永远挂在那里。"""
        with self._lock:
            self._closed = True
            pending_ids = list(self._ids)
            self._ids.clear()
        for question_id in pending_ids:
            self.registry.cancel(question_id)
