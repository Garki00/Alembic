# web/process_events.py
"""Server-sent events for background process state changes.

The process view polls for details, but completion must reach the browser even
when the chat turn is already closed. This hub keeps one queue per connected
SSE client; the process manager publishes lifecycle events, and the endpoint
forwards them as ``process_started`` / ``process_completed``.
"""

from __future__ import annotations

import queue
import threading
from typing import Iterator


class ProcessEventHub:
    """Fan-out queue for process events.

    Event payloads are plain dictionaries because they are serialized directly
    into SSE frames. Subscribers are removed in ``finally`` when the browser
    disconnects.
    """

    def __init__(self, *, max_queue: int = 200) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._max_queue = max(1, int(max_queue))

    def publish(self, kind: str, payload: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait((kind, dict(payload)))
            except queue.Full:
                # A slow browser must not block process startup/completion.
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
