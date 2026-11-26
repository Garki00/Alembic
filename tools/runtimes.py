"""Locate application-owned portable runtimes without downloads or host dependencies.

Provision with scripts/provision_runtimes.py before distribution. Runtime selection
only changes child PATH; all agent commands still require the configured sandbox.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def runtime_root() -> Path:
    """Return the fixed bundle location, never a workspace or environment override."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return base / "runtimes" / "win32-x64"


def runtime_paths(root: Path | None = None) -> list[Path]:
    """Return complete installed runtime directories for native Windows x64."""
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        return []
    root = runtime_root() if root is None else root
    try:
        if not (root / "installed.json").is_file():
            return []
        required = [root / "node" / "node.exe", root / "python" / "python.exe", root / "pnpm" / "pnpm.exe", root / "bin" / "pnpm.cmd"]
        if not all(path.is_file() for path in required):
            return []
    except OSError:
        # An inaccessible build artifact is indistinguishable from a missing
        # bundle here; fall back to the sanitized host PATH without failing the
        # command before the sandbox is reached.
        return []
    return [root / "bin", root / "node", root / "python", root / "python" / "Scripts"]


def add_runtime_path(env: dict[str, str]) -> dict[str, str]:
    """Prepend deterministic bundled tools to an already sanitized environment."""
    paths = runtime_paths()
    if paths:
        existing = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*(str(path) for path in paths), *([existing] if existing else [])])
    return env
