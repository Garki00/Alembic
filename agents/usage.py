# agents/usage.py
"""Provider-reported token usage, with prompt-cache buckets kept separate.

Why this module exists
----------------------
Every request the agent makes is answered with a ``usage`` block that already
contains the prompt-cache accounting: how many prompt tokens the provider
served from its prefix cache and how many it had to process. That number is
the only honest measure of "did the cache work", and it cannot be derived
locally (the system prompt and tool schemas are tokenized by the provider).

Shapes handled
--------------
``langchain_deepseek`` maps DeepSeek's ``prompt_cache_hit_tokens`` to
``usage_metadata["input_token_details"]["cache_read"]`` and
``prompt_cache_miss_tokens`` stays inside ``input_tokens``.
``langchain_openai`` maps ``prompt_tokens_details.cached_tokens`` to the same
key. Anthropic-style providers report ``cache_creation_input_tokens`` in
``cache_creation`` and keep it *outside* ``input_tokens``.

Because those two conventions disagree, :func:`usage_from_message` decides with
the provider's own total:

* ``total == input + output``            -> cache buckets are *inside* ``input``
* ``total == input + output + read + write`` -> cache buckets are *outside*
* no total / no agreement                -> assume inside (DeepSeek & OpenAI)

``input_tokens`` on a :class:`TokenUsage` always means **uncached** prompt
tokens, so ``prompt_tokens == input + cache_read + cache_write`` holds for every
provider and a hit rate is a plain division.

Aggregation rules (mirroring how a turn is actually billed)
-----------------------------------------------------------
A turn is several model requests (tool loop, retries). :func:`fold_turn_usage`
sums the attempts, but an optional bucket (cache read/write, reasoning) is only
reported when **every** participating attempt reported it. A partly-reported
bucket would understate the hit rate, so it is dropped instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from langchain_core.messages import AIMessage, BaseMessage

__all__ = [
    "TokenUsage",
    "TurnUsage",
    "SessionUsage",
    "usage_from_message",
    "fold_turn_usage",
    "format_hit_rate",
]


def _count(value: Any) -> int | None:
    """Return a non-negative int, or ``None`` when the provider did not report it."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class TokenUsage:
    """One provider response's usage; ``input_tokens`` excludes cached tokens."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def prompt_tokens(self) -> int:
        """Everything the model had to read: fresh input plus both cache buckets."""
        return (
            self.input_tokens
            + (self.cache_read_tokens or 0)
            + (self.cache_write_tokens or 0)
        )

    @property
    def hit_rate(self) -> float | None:
        """Share of the prompt served from the prefix cache, or ``None`` if unknown."""
        prompt = self.prompt_tokens
        if self.cache_read_tokens is None or prompt <= 0:
            return None
        return self.cache_read_tokens / prompt

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "hit_rate": self.hit_rate,
        }

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        def merge(left: int | None, right: int | None) -> int | None:
            return None if left is None or right is None else left + right

        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=merge(self.cache_read_tokens, other.cache_read_tokens),
            cache_write_tokens=merge(self.cache_write_tokens, other.cache_write_tokens),
            reasoning_tokens=merge(self.reasoning_tokens, other.reasoning_tokens),
            total_tokens=merge(self.total_tokens, other.total_tokens),
        )


def usage_from_message(message: Any) -> TokenUsage | None:
    """Read one AI message's provider usage.

    Returns ``None`` for messages that never reached the provider (synthetic
    messages, test doubles) instead of inventing zeros: a zero would silently
    drag a hit rate down.
    """
    if not isinstance(message, BaseMessage):
        return None
    usage: Any = getattr(message, "usage_metadata", None)
    if not isinstance(usage, Mapping):
        # Some providers only expose the raw block in response_metadata.
        meta = getattr(message, "response_metadata", None)
        usage = _mapping(meta).get("token_usage") if isinstance(meta, Mapping) else None
    if not isinstance(usage, Mapping):
        return None

    raw_input = _count(usage.get("input_tokens"))
    total = _count(usage.get("total_tokens"))
    details = _mapping(usage.get("input_token_details"))
    output_details = _mapping(usage.get("output_token_details"))

    cache_read = _count(details.get("cache_read"))
    cache_write = _count(details.get("cache_creation"))
    # Some adapters flatten the buckets onto usage_metadata itself.
    if cache_read is None:
        cache_read = _count(usage.get("cache_read_tokens"))
    if cache_write is None:
        cache_write = _count(usage.get("cache_write_tokens"))

    if raw_input is None:
        if total is None:
            return None
        # Only a total: subtract what we know so prompt accounting stays additive.
        raw_input = max(0, total - (cache_read or 0) - (cache_write or 0))

    output = _count(usage.get("output_tokens")) or 0
    reasoning = _count(output_details.get("reasoning"))

    cache_total = (cache_read or 0) + (cache_write or 0)
    included = cache_total > 0
    if cache_total and total is not None:
        if total == raw_input + output:
            included = True
        elif total == raw_input + output + cache_total:
            included = False
    uncached = max(0, raw_input - cache_total) if included else raw_input

    return TokenUsage(
        input_tokens=uncached,
        output_tokens=output,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        total_tokens=total,
    )


@dataclass(frozen=True)
class TurnUsage:
    """Usage of one conversation turn, folded over every model request it made."""

    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int = 0

    @property
    def known(self) -> bool:
        return self.attempts > 0

    @property
    def prompt_tokens(self) -> int:
        return (
            self.input_tokens
            + (self.cache_read_tokens or 0)
            + (self.cache_write_tokens or 0)
        )

    @property
    def hit_rate(self) -> float | None:
        prompt = self.prompt_tokens
        if self.cache_read_tokens is None or prompt <= 0:
            return None
        return self.cache_read_tokens / prompt

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "hit_rate": self.hit_rate,
        }


def fold_turn_usage(messages: Sequence[BaseMessage] | Iterable[BaseMessage]) -> TurnUsage:
    """Fold every provider-backed AI message of one turn into a single :class:`TurnUsage`."""
    collected: list[TokenUsage] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        usage = usage_from_message(message)
        if usage is not None:
            collected.append(usage)
    if not collected:
        return TurnUsage()

    def total_of(getter) -> int | None:
        values = [getter(item) for item in collected]
        if any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None)

    return TurnUsage(
        attempts=len(collected),
        input_tokens=sum(item.input_tokens for item in collected),
        output_tokens=sum(item.output_tokens for item in collected),
        cache_read_tokens=total_of(lambda item: item.cache_read_tokens),
        cache_write_tokens=total_of(lambda item: item.cache_write_tokens),
        reasoning_tokens=total_of(lambda item: item.reasoning_tokens),
        total_tokens=sum(
            item.total_tokens
            if item.total_tokens is not None
            else item.prompt_tokens + item.output_tokens
            for item in collected
        ),
    )


@dataclass
class SessionUsage:
    """Cumulative usage of a session, persisted next to the message history."""

    turns: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    #: requests whose provider reported a cache bucket (older turns may not)
    cache_reported_requests: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def hit_rate(self) -> float | None:
        prompt = self.prompt_tokens
        if self.cache_reported_requests <= 0 or prompt <= 0:
            return None
        return self.cache_read_tokens / prompt

    def add_turn(self, turn: TurnUsage) -> None:
        if not turn.known:
            return
        self.turns += 1
        self.requests += turn.attempts
        self.input_tokens += turn.input_tokens
        self.output_tokens += turn.output_tokens
        self.reasoning_tokens += turn.reasoning_tokens or 0
        self.total_tokens += turn.total_tokens
        if turn.cache_read_tokens is not None:
            self.cache_read_tokens += turn.cache_read_tokens
            self.cache_write_tokens += turn.cache_write_tokens or 0
            self.cache_reported_requests += turn.attempts

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "prompt_tokens": self.prompt_tokens,
            "total_tokens": self.total_tokens,
            "hit_rate": self.hit_rate,
            "cache_reported_requests": self.cache_reported_requests,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "SessionUsage":
        data = payload if isinstance(payload, Mapping) else {}

        def number(key: str) -> int:
            value = data.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

        return cls(
            turns=number("turns"),
            requests=number("requests"),
            input_tokens=number("input_tokens"),
            output_tokens=number("output_tokens"),
            cache_read_tokens=number("cache_read_tokens"),
            cache_write_tokens=number("cache_write_tokens"),
            reasoning_tokens=number("reasoning_tokens"),
            total_tokens=number("total_tokens"),
            cache_reported_requests=number("cache_reported_requests"),
        )


def format_hit_rate(rate: float | None, digits: int = 1) -> str:
    """``0.9234`` -> ``"92.3%"``; unknown stays an empty string for the caller to omit."""
    if rate is None:
        return ""
    return f"{rate * 100:.{digits}f}%"
