# tools/sandbox/config.py
"""Persistent sandbox configuration (network mode and legacy proxy settings)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from config.settings import settings

DEFAULT_DOMAINS = (
    "registry.npmjs.org",
    "registry.npmmirror.com",
    "pypi.org",
    "files.pythonhosted.org",
    "github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "gitee.com",
)

#: Domains shipped before Gitee was added; used to migrate untouched old defaults.
_LEGACY_DEFAULT_DOMAINS = frozenset(DEFAULT_DOMAINS) - {"gitee.com"}
_DEFAULT_VERSION = 2


class SandboxConfigStore:
    """Read and write ``data/sandbox.json``; stores policy only, never account credentials."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or (Path(settings.data_dir) / "sandbox.json")).expanduser()
        self._lock = threading.RLock()
        self._cached: dict[str, Any] | None = None

    def _default(self) -> dict[str, Any]:
        return {
            "version": _DEFAULT_VERSION,
            "network": "allow",
            "allowed_domains": list(DEFAULT_DOMAINS),
            "allowed_ports": [443, 80],
            "proxy_port": 47831,
        }

    def get(self) -> dict[str, Any]:
        with self._lock:
            if self._cached is not None:
                return json.loads(json.dumps(self._cached))
            payload = self._default()
            migrated = False
            try:
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict):
                stored_network = str(data.get("network") or "").strip().lower()
                if stored_network == "offline":
                    payload["network"] = "offline"
                elif stored_network in {"allow", "allowlist"}:
                    payload["network"] = "allow"
                domains = data.get("allowed_domains")
                if isinstance(domains, list):
                    payload["allowed_domains"] = [
                        str(item).strip().lower()
                        for item in domains
                        if str(item).strip()
                    ]
                ports = data.get("allowed_ports")
                if isinstance(ports, list):
                    parsed = []
                    for item in ports:
                        try:
                            port = int(item)
                        except (TypeError, ValueError):
                            continue
                        if 1 <= port <= 65535:
                            parsed.append(port)
                    if parsed:
                        payload["allowed_ports"] = parsed
                try:
                    port = int(data.get("proxy_port") or payload["proxy_port"])
                except (TypeError, ValueError):
                    port = payload["proxy_port"]
                if 1 <= port <= 65535:
                    payload["proxy_port"] = port
                try:
                    version = int(data.get("version", 1))
                except (TypeError, ValueError):
                    version = 1
                if version < _DEFAULT_VERSION:
                    if set(payload["allowed_domains"]) == _LEGACY_DEFAULT_DOMAINS:
                        payload["allowed_domains"].append("gitee.com")
                        migrated = True
                    payload["version"] = _DEFAULT_VERSION
            if migrated:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._cached = payload
            return json.loads(json.dumps(payload))

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.get()
            if "network" in patch:
                network = str(patch["network"]).strip().lower()
                if network in {"allow", "allowlist"}:
                    current["network"] = "allow"
                elif network == "offline":
                    current["network"] = "offline"
                else:
                    raise ValueError("network 只能是 offline / allow")
            if "allowed_domains" in patch:
                domains = patch["allowed_domains"]
                if not isinstance(domains, list):
                    raise ValueError("allowed_domains 必须是数组")
                current["allowed_domains"] = [
                    str(item).strip().lower() for item in domains if str(item).strip()
                ]
            if "allowed_ports" in patch:
                ports = []
                for item in patch["allowed_ports"] or []:
                    try:
                        port = int(item)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= port <= 65535:
                        ports.append(port)
                if not ports:
                    raise ValueError("至少需要一个允许端口")
                current["allowed_ports"] = ports
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            self._cached = current
            return json.loads(json.dumps(current))
