# web/event_hub.py
"""Fan-out queues for server-sent events that outlive one chat turn.

Each connected browser gets one queue. Publishers write plain dictionaries that
are serialized directly into SSE frames; a slow subscriber never blocks the
producer.
"""

from __future__ import annotations

import queue
import threading
from typing import Iterator


class EventHub:
    """Thread-safe fan-out queue with per-subscriber backpressure isolation."""

    def __init__(self, *, max_queue: int = 200) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._max_queue = max(1, int(max_queue))

    def publish(self, kind: str, payload: dict) -> None:
        """Deliver one event to every current subscriber."""
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait((kind, dict(payload)))
            except queue.Full:
                # A slow browser must not block completion or task startup.
                pass

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def listen(self) -> Iterator[tuple[str, dict] | None]:
        """Yield events until the caller stops; ``None`` is a heartbeat marker."""
        subscriber = self.subscribe()
        try:
            yield None
            while True:
                try:
                    yield subscriber.get(timeout=15)
                except queue.Empty:
                    yield None
        finally:
            self.unsubscribe(subscriber)
