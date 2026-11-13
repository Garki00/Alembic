# tools/sandbox/__init__.py
"""Sandbox backend selection.

Only the native Windows backend is considered production-ready. There is no
fallback that runs commands as the desktop user.
"""

from __future__ import annotations

import os

from tools.sandbox.base import (
    SandboxError,
    SandboxPolicy,
    SandboxProcess,
    SandboxResult,
    SandboxStatus,
    sandbox_unavailable,
)

__all__ = [
    "SandboxError",
    "SandboxPolicy",
    "SandboxProcess",
    "SandboxResult",
    "SandboxStatus",
    "create_sandbox",
    "sandbox_unavailable",
]


def create_sandbox(*, config=None):
    """Build the platform backend, or return None when unavailable."""
    if os.name == "nt":
        from tools.sandbox.windows import WindowsSandbox

        return WindowsSandbox(config=config)
    return None
