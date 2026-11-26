# tools/sandbox/base.py
"""Command sandbox interfaces.

Production command execution must go through :class:`SandboxRunner`. The
runner is responsible for creating the child process, applying filesystem and
network policy, and returning a process handle with inherited restrictions.
There is deliberately no "run without sandbox" method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


class SandboxError(RuntimeError):
    """Sandbox setup or launch failure; carries a stable machine code."""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class SandboxPolicy:
    """Filesystem and network policy for one command."""

    workspace: Path
    session_id: str = "default"
    network: str = "allow"  # offline | allow
    allowed_domains: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = (443, 80)
    extra_read_roots: tuple[Path, ...] = ()
    temp_dir: Path | None = None
    proxy_url: str = ""

    def normalized_network(self) -> str:
        value = (self.network or "allow").strip().lower()
        # ``allowlist`` was the pre-1.0 online mode. Keep accepting it so old
        # configuration files still load, but it now means open network access.
        if value in {"allow", "allowlist"}:
            return "allow"
        return "offline"


@dataclass
class SandboxResult:
    """Completed command result."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0
    cancelled: bool = False
    timed_out: bool = False
    violation: str = ""


@dataclass
class SandboxStatus:
    """Result of the startup/health check shown in the UI."""

    ready: bool
    backend: str
    platform: str
    detail: str = ""
    network_modes: tuple[str, ...] = ("offline", "allow")
    allowed_domains: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "backend": self.backend,
            "platform": self.platform,
            "detail": self.detail,
            "network_modes": list(self.network_modes),
            "allowed_domains": list(self.allowed_domains),
            "reasons": list(self.reasons),
        }


class SandboxProcess(Protocol):
    """Minimal process interface used by shell and the process manager."""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self, timeout: float = 5.0) -> None: ...

    def close(self) -> None: ...


class SandboxRunner(Protocol):
    """Platform backend contract."""

    def status(self) -> SandboxStatus: ...

    def prepare_workspace(self, policy: SandboxPolicy) -> None: ...

    def spawn(
        self,
        *,
        command: str,
        cwd: Path,
        env: Mapping[str, str],
        policy: SandboxPolicy,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
        stdin: bool = False,
        tty: bool = False,
        shell: bool = False,
    ) -> SandboxProcess: ...

    def close(self) -> None: ...


def sandbox_unavailable(message: str, *, hint: str = "") -> SandboxError:
    return SandboxError("sandbox_unavailable", message, hint=hint)
