# tools/sandbox/proxy.py
"""Loopback HTTP CONNECT proxy used for proxy environment injection.

In ``allow`` mode the proxy is an unfiltered pass-through: the child receives
``HTTP_PROXY``/``HTTPS_PROXY`` but is not firewalled, so a command can ignore
the proxy and connect directly. The domain matcher remains for tests and
backward-compatible callers; production online mode always passes
``allow_all=True``.
"""

from __future__ import annotations

import http.client
import ipaddress
import select
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

MAX_HEADER = 64 * 1024


def domain_allowed(host: str, allowed: list[str]) -> bool:
    """Match exact host, ``*.suffix`` wildcard, and optional ``:port``."""
    value = (host or "").strip().lower().rstrip(".")
    if not value:
        return False
    for pattern in allowed:
        item = pattern.strip().lower().rstrip(".")
        if not item:
            continue
        if ":" in item:
            name, port = item.rsplit(":", 1)
            if value == item or value == f"{name}:{port}":
                return True
        if item.startswith("*."):
            suffix = item[1:]
            if value.endswith(suffix) and value != suffix[1:]:
                return True
        elif value == item:
            return True
    return False


def is_private_host(host: str) -> bool:
    """Reject literal loopback/private/link-local addresses."""
    text = (host or "").strip().strip("[]")
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    )


class _ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address,
        handler,
        *,
        allowed: list[str],
        ports: set[int],
        allow_all: bool = False,
    ):
        self.allowed = allowed
        self.allowed_ports = ports
        self.allow_all = allow_all
        super().__init__(address, handler)


class _ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            first = self.rfile.readline(MAX_HEADER)
            if not first:
                return
            method, target, version = first.decode("latin-1").strip().split(" ", 2)
            headers = []
            while True:
                line = self.rfile.readline(MAX_HEADER)
                if line in (b"\r\n", b"\n", b""):
                    break
                headers.append(line)
            if method.upper() == "CONNECT":
                self._connect(target)
            else:
                self._http(method, target, version, headers)
        except (OSError, ValueError, UnicodeError):
            return

    def _connect(self, target: str) -> None:
        host, _, port_text = target.rpartition(":")
        if not host:
            host, port_text = target, "443"
        try:
            port = int(port_text)
        except ValueError:
            self._deny(403, "bad port")
            return
        if not self._allowed(host, port):
            self._deny(403, f"domain not allowed: {host}")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=15)
        except OSError:
            self._deny(502, f"cannot connect: {host}")
            return
        try:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            sockets = [self.connection, upstream]
            while True:
                readable, _, _ = select.select(sockets, [], [], 30)
                if not readable:
                    continue
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    other = upstream if sock is self.connection else self.connection
                    other.sendall(data)
        finally:
            upstream.close()

    def _http(self, method: str, target: str, version: str, headers: list[bytes]) -> None:
        parsed = urlsplit(target if "://" in target else f"http://{target}")
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not self._allowed(host, port):
            self._deny(403, f"domain not allowed: {host}")
            return
        body = b""
        length = 0
        for raw in headers:
            name, _, value = raw.decode("latin-1").partition(":")
            if name.strip().lower() == "content-length":
                try:
                    length = max(0, min(int(value.strip()), 20_000_000))
                except ValueError:
                    length = 0
        if length:
            body = self.rfile.read(length)
        try:
            if parsed.scheme == "https":
                conn = http.client.HTTPSConnection(host, port, timeout=20)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=20)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            conn.request(method, path, body=body, headers={
                key.decode("latin-1").split(":", 1)[0]: value.decode("latin-1").strip()
                for key, _, value in (line.partition(b":") for line in headers)
                if key and key.decode("latin-1").lower() not in {"proxy-connection", "host"}
            })
            response = conn.getresponse()
            payload = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() in {"transfer-encoding", "connection", "content-length"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception:
            self._deny(502, f"proxy request failed: {host}")

    def _allowed(self, host: str, port: int) -> bool:
        server = self.server
        assert isinstance(server, _ProxyServer)
        if server.allow_all:
            return True
        if port not in server.allowed_ports:
            return False
        if is_private_host(host) and not domain_allowed(host, server.allowed):
            return False
        return (
            domain_allowed(host, server.allowed)
            or domain_allowed(f"{host}:{port}", server.allowed)
        )

    def _deny(self, status: int, message: str) -> None:
        body = message.encode("utf-8", errors="replace")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SandboxProxy:
    """Owns the loopback proxy lifetime."""

    def __init__(
        self,
        port: int,
        allowed: list[str],
        ports: set[int],
        *,
        allow_all: bool = False,
    ) -> None:
        self.port = int(port)
        self.allowed = list(allowed)
        self.ports = set(ports)
        self.allow_all = allow_all
        self._server: _ProxyServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self._server is not None:
            return
        server = _ProxyServer(
            ("127.0.0.1", self.port),
            _ProxyHandler,
            allowed=self.allowed,
            ports=self.ports,
            allow_all=self.allow_all,
        )
        thread = threading.Thread(target=server.serve_forever, name="alembic-sandbox-proxy", daemon=True)
        thread.start()
        self._server = server
        self._thread = thread

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._thread = None
