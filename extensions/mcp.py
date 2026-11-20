"""Persistent synchronous MCP client backed by the official asynchronous SDK."""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import queue
import threading
import time
from contextlib import AsyncExitStack
from datetime import timedelta
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client


class MCPError(RuntimeError):
    """A sanitized connection, protocol, cancellation, or configuration error."""


def _dump(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _config(server):
    if server.get("enabled") is False:
        raise MCPError("MCP server is disabled.")
    config = {k: server[k] for k in ("transport", "command", "args", "env", "cwd", "url", "headers", "bearer_token", "timeout", "oauth") if k in server}
    config.setdefault("transport", "stdio")
    try:
        config["timeout"] = float(config.get("timeout", 30))
        if not 0 < config["timeout"] <= 600:
            raise ValueError()
    except (ValueError, TypeError):
        raise MCPError("MCP timeout must be between 0 and 600 seconds.") from None
    if config["transport"] == "stdio":
        if not isinstance(config.get("command"), str) or not config["command"].strip():
            raise MCPError("MCP command is required.")
        if not isinstance(config.get("args", []), list) or not all(isinstance(s, str) for s in config.get("args", [])):
            raise MCPError("MCP arguments must be strings.")
    elif config["transport"] in {"sse", "streamable_http"}:
        url = urlsplit(config.get("url", ""))
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise MCPError("MCP URL must be HTTP(S) without embedded credentials.")
    else:
        raise MCPError("Unsupported MCP transport.")
    for key in ("env", "headers"):
        values = config.get(key, {})
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise MCPError(f"MCP {key} must map strings to strings.")
    return config


class _OAuthStorage:
    """Application-scoped token storage; never returned in discovery output."""
    def __init__(self, path):
        from pathlib import Path
        self.path = Path(path)

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save(self, key, value):
        data = self._read()
        data[key] = _dump(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        value = self._read().get("tokens")
        return OAuthToken.model_validate(value) if value else None

    async def set_tokens(self, value):
        self._save("tokens", value)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        value = self._read().get("client")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, value):
        self._save("client", value)


async def _oauth(config, stack, data_dir):
    """Use the SDK's OAuth discovery, PKCE, state validation and refresh flow."""
    import webbrowser
    from pathlib import Path
    from urllib.parse import parse_qs
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    options = config["oauth"]
    redirect_uri = options.get("redirect_uri", "http://127.0.0.1:47832/callback")
    redirect = urlsplit(redirect_uri)
    if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or not redirect.port:
        raise MCPError("OAuth callback must use an explicit localhost IPv4 port.")
    callback = None
    expected_state = None
    listener = None

    async def receive(reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            target = urlsplit(line.decode("ascii").split(" ")[1])
            query = parse_qs(target.query)
            state = query.get("state", [None])[0]
            valid = (target.path == redirect.path and len(query.get("code", [])) == 1
                     and callback is not None and not callback.done()
                     and expected_state is not None and state == expected_state)
            if valid:
                callback.set_result((query["code"][0], state))
            body = b"Authorization received. You may close this window." if valid else b"Invalid authorization callback."
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        except (IndexError, ValueError, UnicodeError, asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def close_listener():
        nonlocal listener
        if listener is not None:
            listener.close()
            await listener.wait_closed()
            listener = None

    stack.push_async_callback(close_listener)

    async def redirect_handler(url):
        nonlocal listener, callback, expected_state
        await close_listener()
        if callback is not None and not callback.done():
            callback.cancel()
        callback = asyncio.get_running_loop().create_future()
        expected_state = parse_qs(urlsplit(url).query).get("state", [None])[0]
        listener = await asyncio.start_server(receive, "127.0.0.1", redirect.port)
        try:
            if not await asyncio.to_thread(webbrowser.open, url):
                raise MCPError("Could not open the OAuth authorization browser.")
        except BaseException:
            await close_listener()
            raise

    async def callback_handler():
        if callback is None:
            raise MCPError("OAuth authorization has not started.")
        try:
            return await asyncio.wait_for(asyncio.shield(callback), config["timeout"])
        finally:
            await close_listener()

    identity_data = {"url": config["url"], "redirect_uri": redirect_uri,
                     "scope": options.get("scope"), "client_name": "Alembic"}
    identity = hashlib.sha256(json.dumps(identity_data, sort_keys=True).encode()).hexdigest()
    token_path = Path(data_dir) / "mcp-tokens" / (identity + ".json")
    return OAuthClientProvider(server_url=config["url"],
        client_metadata=OAuthClientMetadata(redirect_uris=[redirect_uri], client_name="Alembic", token_endpoint_auth_method="none", scope=options.get("scope")),
        storage=_OAuthStorage(token_path), redirect_handler=redirect_handler,
        callback_handler=callback_handler, timeout=config["timeout"])


class _Worker:
    def __init__(self, config, data_dir):
        self.config = config
        self.data_dir = data_dir
        self.requests = queue.Queue()
        self.ready = concurrent.futures.Future()
        self.closed = threading.Event()
        self.loop = None
        self.owner = None
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="mcp-client", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException:
            if not self.ready.done():
                self.ready.set_exception(MCPError("MCP connection failed; check server configuration and credentials."))
        finally:
            self.closed.set()
            while True:
                try:
                    item = self.requests.get_nowait()
                except queue.Empty:
                    break
                if item is not None and not item[3].done():
                    item[3].set_exception(MCPError("MCP connection closed."))

    async def _serve(self):
        cfg = self.config
        self.loop = asyncio.get_running_loop()
        self.owner = asyncio.current_task()
        if self.stopping.is_set():
            return
        async with AsyncExitStack() as stack:
            if cfg["transport"] == "stdio":
                from tools.env import build_child_env
                env = build_child_env()
                # Explicit server credentials are scoped to this installed server.
                for key, value in cfg.get("env", {}).items():
                    if key.upper().startswith(("LD_", "DYLD_", "PYTHON")) or key.upper() in {"NODE_OPTIONS", "NODE_PATH"}:
                        raise MCPError("MCP interpreter injection environment variables are not allowed.")
                    env[key] = value
                error_log = stack.enter_context(open(os.devnull, "w"))
                streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                    command=cfg["command"], args=cfg.get("args", []), env=env, cwd=cfg.get("cwd")), errlog=error_log))
            else:
                headers = dict(cfg.get("headers", {}))
                if cfg.get("bearer_token"):
                    headers["Authorization"] = "Bearer " + cfg["bearer_token"]
                transport = sse_client if cfg["transport"] == "sse" else streamablehttp_client
                auth = await _oauth(cfg, stack, self.data_dir) if cfg.get("oauth") is not None else None
                streams = await stack.enter_async_context(transport(cfg["url"], headers=headers, timeout=cfg["timeout"], sse_read_timeout=cfg["timeout"], auth=auth))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=cfg["timeout"])))
            initialized = await session.initialize()
            self.ready.set_result(True)
            while True:
                try:
                    request = self.requests.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.02)
                    continue
                if request is None:
                    return
                operation, arguments, cancel, future = request
                if future.cancelled():
                    continue
                task = asyncio.create_task(self._dispatch(session, initialized, operation, arguments))
                started = time.monotonic()
                try:
                    while not task.done():
                        if future.cancelled() or (cancel is not None and cancel.is_set()):
                            raise MCPError("MCP request cancelled.")
                        if time.monotonic() - started > cfg["timeout"]:
                            raise MCPError("MCP request timed out.")
                        await asyncio.wait({task}, timeout=0.05)
                    result = task.result()
                    if not future.done():
                        future.set_result(result)
                except BaseException as exc:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    if not future.done():
                        future.set_exception(MCPError(str(exc)) if isinstance(exc, MCPError) else MCPError("MCP request failed; check server availability and arguments."))

    async def _dispatch(self, session, initialized, operation, args):
        if operation == "discover":
            result = {"tools": [], "resources": [], "prompts": [], "resourceTemplates": [], "capabilities": _dump(initialized.capabilities)}
            for capability, method, field in (("tools", "list_tools", "tools"), ("resources", "list_resources", "resources"), ("resources", "list_resource_templates", "resourceTemplates"), ("prompts", "list_prompts", "prompts")):
                if getattr(initialized.capabilities, capability, None) is None:
                    continue
                cursor, seen = None, set()
                for _ in range(1000):
                    page = _dump(await getattr(session, method)(cursor=cursor))
                    result[field].extend(page.get(field, []))
                    cursor = page.get("nextCursor")
                    if cursor is None:
                        break
                    if cursor in seen:
                        raise MCPError("MCP server repeated a pagination cursor.")
                    seen.add(cursor)
                else:
                    raise MCPError("MCP discovery exceeded the pagination limit.")
            return result
        if operation == "call_tool":
            return _dump(await session.call_tool(args[0], arguments=args[1]))
        if operation == "read_resource":
            return _dump(await session.read_resource(args[0]))
        if operation == "get_prompt":
            return _dump(await session.get_prompt(args[0], arguments=args[1]))
        raise MCPError("Unknown MCP operation.")

    def invoke(self, operation, args, cancel):
        deadline = time.monotonic() + self.config["timeout"]
        while not self.ready.done():
            if cancel is not None and cancel.is_set():
                raise MCPError("MCP request cancelled.")
            if time.monotonic() >= deadline:
                self.stop()
                raise MCPError("MCP connection timed out.")
            try:
                self.ready.result(timeout=0.05)
            except concurrent.futures.TimeoutError:
                pass
        self.ready.result()
        if self.closed.is_set():
            raise MCPError("MCP connection closed.")
        future = concurrent.futures.Future()
        self.requests.put((operation, args, cancel, future))
        while True:
            try:
                return future.result(timeout=0.05)
            except concurrent.futures.TimeoutError:
                if cancel is not None and cancel.is_set():
                    future.cancel()
                    raise MCPError("MCP request cancelled.")
                if time.monotonic() >= deadline:
                    future.cancel()
                    raise MCPError("MCP request timed out.")
                if self.closed.is_set():
                    raise MCPError("MCP connection closed.")

    def stop(self):
        self.stopping.set()
        self.requests.put(None)
        if self.loop is not None and self.owner is not None and not self.closed.is_set():
            try:
                self.loop.call_soon_threadsafe(self.owner.cancel)
            except RuntimeError:
                pass


class MCPClient:
    """Reuse SDK sessions; each session's context managers belong to one worker task."""
    def __init__(self, data_dir=None):
        from pathlib import Path
        if data_dir is None:
            from config.settings import settings
            data_dir = settings.data_dir
        self.data_dir = Path(data_dir).expanduser().resolve()
        self._workers = {}
        self._lock = threading.Lock()

    def _invoke(self, server, operation, arguments=(), cancel_event=None):
        config = _config(server)
        key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        with self._lock:
            worker = self._workers.get(key)
            if worker is None or worker.closed.is_set():
                worker = self._workers[key] = _Worker(config, self.data_dir)
        return worker.invoke(operation, arguments, cancel_event)

    def discover(self, server):
        return self._invoke(server, "discover")

    def call_tool(self, server, name, args, cancel_event=None):
        return self._invoke(server, "call_tool", (name, args), cancel_event)

    def read_resource(self, server, uri, cancel_event=None):
        return self._invoke(server, "read_resource", (uri,), cancel_event)

    def get_prompt(self, server, name, args=None, cancel_event=None):
        return self._invoke(server, "get_prompt", (name, args), cancel_event)

    @staticmethod
    def _close_workers(workers):
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.thread.join(timeout=5)

    def reconcile(self, servers):
        """Retire obsolete sessions while keeping unchanged enabled connections."""
        retained = set()
        for server in servers:
            if server.get("enabled") is False:
                continue
            try:
                config = _config(server)
                retained.add(hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())
            except (MCPError, TypeError, ValueError):
                continue
        with self._lock:
            retired = [self._workers.pop(key) for key in list(self._workers) if key not in retained]
        self._close_workers(retired)

    def close(self):
        with self._lock:
            workers, self._workers = list(self._workers.values()), {}
        self._close_workers(workers)
