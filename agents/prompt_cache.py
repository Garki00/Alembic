"""Cache policy resolved from the concrete model id.

Two kinds of prompt caching exist:

* **Explicit** — the request carries breakpoint markers and the provider caches
  up to them (Anthropic ``cache_control``). The marker count is limited.
* **Implicit** — the provider matches the longest stable prefix on its own and
  reports the hit in ``usage``; nothing has to be sent.

The policy is resolved from the *wire* model id, never from the display name or
the provider template: one provider can host models with different behaviour,
and users can point a custom base URL at any compatible model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CachePolicy",
    "cache_policy",
    "uses_explicit_cache",
    "plan_breakpoints",
    "EXPLICIT",
    "IMPLICIT",
    "NONE",
]

EXPLICIT = "explicit"
IMPLICIT = "implicit"
NONE = "none"


@dataclass(frozen=True)
class CachePolicy:
    """How one model caches its prompt prefix."""

    mode: str = IMPLICIT
    #: Maximum number of breakpoints an explicit-cache request may carry.
    breakpoints: int = 0
    #: Prefix length below which the provider does not cache at all.
    min_prefix_tokens: int = 0

    @property
    def explicit(self) -> bool:
        return self.mode == EXPLICIT


#: ``(pattern, mode, breakpoints, min_prefix_tokens)``, first match wins.
#: Patterns are anchored at the start of the normalized model id.
_KNOWN_MODELS: tuple[tuple[str, str, int, int], ...] = (
    # Anthropic Claude: explicit ephemeral breakpoints (4 by default).
    (r"^claude", EXPLICIT, 4, 1024),
    # OpenAI: automatic prefix caching with a 1024-token minimum.
    (r"^(gpt-|o[1-9]|chatgpt|ft:gpt)", IMPLICIT, 0, 1024),
    # DeepSeek: automatic context caching on 64-token chunks.
    (r"^deepseek", IMPLICIT, 0, 64),
    # xAI Grok: automatic prefix cache.
    (r"^grok", IMPLICIT, 0, 128),
    # Google Gemini: implicit caching on 2.5+ models.
    (r"^(gemini|models/gemini)", IMPLICIT, 0, 1024),
    # Moonshot Kimi: automatic context cache.
    (r"^(kimi|moonshot)", IMPLICIT, 0, 64),
    # Zhipu GLM: automatic context cache.
    (r"^(glm|chatglm|zhipu)", IMPLICIT, 0, 64),
    # Alibaba Qwen/DashScope: automatic context cache.
    (r"^(qwen|qwq|qwen-)", IMPLICIT, 0, 64),
    # Volcengine Ark: prefix cache on endpoint ids and doubao models.
    (r"^(doubao|ep-|volc)", IMPLICIT, 0, 64),
    # MiniMax: automatic context cache.
    (r"^(minimax|abab|MiniMax)", IMPLICIT, 0, 64),
)

_NORMALIZE = re.compile(r"\s+")


def cache_policy(model: str | None) -> CachePolicy:
    """Return the cache policy for a wire model id.

    Unknown models fall back to implicit caching: a stable prefix costs nothing
    extra, and a custom endpoint may still cache on its own.
    """
    name = _NORMALIZE.sub("", str(model or "")).strip()
    if not name:
        return CachePolicy(IMPLICIT, 0, 0)
    lowered = name.lower()
    for pattern, mode, breakpoints, minimum in _KNOWN_MODELS:
        if re.search(pattern, lowered):
            return CachePolicy(mode, breakpoints, minimum)
    return CachePolicy(IMPLICIT, 0, 64)


def uses_explicit_cache(model: str | None) -> bool:
    """Whether requests for this model must carry explicit breakpoint markers."""
    return cache_policy(model).explicit


def plan_breakpoints(
    policy: CachePolicy,
    *,
    has_system: bool = True,
    has_first_turn: bool = False,
) -> tuple[str, ...]:
    """Return the logical cache anchors to mark, at most ``policy.breakpoints``.

    ``"prefix"`` marks the end of the system prompt and tool declarations,
    ``"first_turn"`` the end of the preserved first exchange and ``"tail"`` the
    end of the current request. Only explicit-cache models use this plan;
    implicit caches ignore it.
    """
    if not policy.explicit or policy.breakpoints <= 0:
        return ()
    anchors: list[str] = []
    if has_system:
        anchors.append("prefix")
    if has_first_turn:
        anchors.append("first_turn")
    anchors.append("tail")
    # The oldest anchors survive when the provider allows fewer markers: they
    # cover the widest reused prefix.
    return tuple(anchors[: policy.breakpoints])
