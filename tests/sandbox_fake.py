# tests/sandbox_fake.py
"""Test-only sandbox that runs commands as the test process.

Production uses the native backend; this fake is injected explicitly by tests
so the offline suite does not need administrator setup.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping

from tools.sandbox.base import SandboxPolicy, SandboxStatus


class _FakeProcess:
    def __init__(self, popen: subprocess.Popen) -> None:
        self._popen = popen
        self.stdout = popen.stdout
        self.stderr = popen.stderr
        self._forced = False

    @property
    def pid(self) -> int:
        return int(self._popen.pid)

    def poll(self) -> int | None:
        return 1 if self._forced else self._popen.poll()

    def wait(self, timeout: float | None = None) -> int:
        if self._forced:
            return 1
        return int(self._popen.wait(timeout=timeout))

    def terminate(self, timeout: float = 5.0) -> None:
        if self._forced or self._popen.poll() is not None:
            return
        self._forced = True
        try:
            self._popen.kill()
        except OSError:
            pass
        try:
            self._popen.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def close(self) -> None:
        # The fake deliberately leaves pipe objects to the test process; the
        # production backend closes its handles in the job-aware wrapper.
        return None


class FakeSandbox:
    def __init__(self) -> None:
        self.closed = False

    def status(self) -> SandboxStatus:
        return SandboxStatus(True, "fake", "test", "test sandbox", ("offline", "allowlist"))

    def prepare_workspace(self, policy: SandboxPolicy) -> None:
        return None

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
    ) -> _FakeProcess:
        popen = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=dict(env),
            shell=shell,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
            start_new_session=os.name != "nt",
        )
        try:
            if popen.stdin is not None:
                popen.stdin.close()
        except (OSError, ValueError):
            pass
        return _FakeProcess(popen)

    def close(self) -> None:
        self.closed = True
