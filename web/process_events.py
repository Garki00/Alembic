# web/process_events.py
"""Server-sent events for background process state changes.

The process view polls for details, but completion must reach the browser even
when the chat turn is already closed. This hub keeps one queue per connected
SSE client; the process manager publishes lifecycle events, and the endpoint
forwards them as ``process_started`` / ``process_completed``.
"""

from __future__ import annotations

from web.event_hub import EventHub

#: Background process events use the shared fan-out hub implementation.
ProcessEventHub = EventHub

__all__ = ["ProcessEventHub"]
