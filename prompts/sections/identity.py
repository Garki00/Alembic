# prompts/sections/identity.py
"""Agent identity: the default identity or the user's selected prompt.

The selected prompt replaces the identity and rules sections. The fixed prefix,
the interface contract, and the tool guidance stay in place, because they
describe the runtime contract rather than the agent's persona.
"""

from __future__ import annotations

from prompts.sections.types import STABLE, SYSTEM, PromptSection

DEFAULT_IDENTITY = (
    "You are an agent working with the user toward their goals, using your own "
    "judgment along the way."
)

SECURITY_NOTICE = (
    "IMPORTANT: Assist with authorized security testing, defensive security, CTF "
    "challenges, and educational contexts. Refuse requests for destructive techniques, "
    "DoS attacks, mass targeting, supply chain compromise, or detection evasion for "
    "malicious purposes. Dual-use security tools (C2 frameworks, credential testing, "
    "exploit development) require clear authorization context: pentesting engagements, "
    "CTF competitions, security research, or defensive use cases."
)


def build_identity_section(custom_prompt: str = "") -> PromptSection:
    """Return the default identity, or the user's prompt verbatim when set."""
    text = custom_prompt or ""
    if text.strip():
        return PromptSection(
            name="Custom System Prompt",
            source="custom_system_prompt",
            content=f"# Identity\n{text}",
            cache_hint=STABLE,
            target=SYSTEM,
        )
    return PromptSection(
        name="Agent Identity",
        source="identity",
        content=f"# Identity\n{DEFAULT_IDENTITY}\n\n{SECURITY_NOTICE}",
        cache_hint=STABLE,
        target=SYSTEM,
    )
