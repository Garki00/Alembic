"""Focused protocol pagination and OAuth storage checks."""
import asyncio
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, patch
from mcp.types import ListToolsResult, Tool
from mcp.shared.auth import OAuthToken
from extensions.mcp import MCPClient, MCPError, _Worker, _OAuthStorage, _oauth

class MCPProtocolTest(unittest.TestCase):
    def test_pagination_and_cycle(self):
        capabilities = SimpleNamespace(tools=True, resources=None, prompts=None)
        capabilities.model_dump = lambda **kwargs: {"tools": {}}
        initialized = SimpleNamespace(capabilities=capabilities)
        first = ListToolsResult(tools=[Tool(name="one", inputSchema={"type":"object"})], nextCursor="page2")
        last = ListToolsResult(tools=[Tool(name="two", inputSchema={"type":"object"})])
        session = SimpleNamespace(list_tools=AsyncMock(side_effect=[first,last]))
        worker = object.__new__(_Worker)
        found = asyncio.run(worker._dispatch(session, initialized, "discover", ()))
        self.assertEqual([t["name"] for t in found["tools"]], ["one","two"])
        session.list_tools = AsyncMock(side_effect=[first,first])
        with self.assertRaisesRegex(MCPError, "pagination cursor"):
            asyncio.run(worker._dispatch(session, initialized, "discover", ()))

    def test_oauth_storage_and_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            async def check():
                storage = _OAuthStorage(Path(directory) / "token.json")
                self.assertIsNone(await storage.get_tokens())
                await storage.set_tokens(OAuthToken(access_token="test-only", token_type="Bearer", refresh_token="refresh"))
                self.assertEqual((await storage.get_tokens()).refresh_token, "refresh")
                async with AsyncExitStack() as stack:
                    provider = await _oauth({"url":"https://example.invalid/mcp", "oauth":{}, "timeout":5}, stack, directory)
                    self.assertIsNotNone(provider)
                async with AsyncExitStack() as stack:
                    with self.assertRaises(MCPError):
                        await _oauth({"url":"https://example.invalid", "oauth":{"redirect_uri":"https://foreign.example/callback"}, "timeout":5}, stack, directory)
            asyncio.run(check())

    def test_instance_data_dir_and_identity_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = MCPClient(data_dir=Path(directory) / "custom")
            captured = []
            def provider(**kwargs):
                captured.append(kwargs)
                return kwargs
            async def check():
                with patch("mcp.client.auth.OAuthClientProvider", side_effect=provider):
                    for options in ({}, {"scope":"read"}, {"redirect_uri":"http://127.0.0.1:47833/callback"}):
                        async with AsyncExitStack() as stack:
                            value = await _oauth({"url":"https://example.invalid/mcp", "oauth":options, "timeout":5}, stack, client.data_dir)
                            await value["storage"].set_tokens(OAuthToken(access_token="local-test", token_type="Bearer"))
            asyncio.run(check())
            paths = {c["storage"].path for c in captured}
            self.assertEqual(len(paths), 3)
            self.assertTrue(all(p.parent == client.data_dir / "mcp-tokens" and p.exists() for p in paths))
            self.assertEqual(len(list(Path(directory).rglob("*.json"))), 3)

    def test_repeated_oauth_redirect_and_listener_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            async def check():
                async with AsyncExitStack() as stack:
                    with patch("mcp.client.auth.OAuthClientProvider", side_effect=lambda **kw: kw):
                        provider = await _oauth({"url":"https://example.invalid/mcp", "oauth":{"redirect_uri":f"http://127.0.0.1:{port}/callback"}, "timeout":2}, stack, directory)
                    for index in range(2):
                        with patch("webbrowser.open", return_value=True):
                            await provider["redirect_handler"](f"https://auth.invalid/authorize?state=state{index}")
                        reader, writer = await asyncio.open_connection("127.0.0.1", port)
                        writer.write(f"GET /callback?code=code{index}&state=state{index} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
                        await writer.drain()
                        self.assertIn(b"Authorization received", await reader.read())
                        writer.close()
                        await writer.wait_closed()
                        self.assertEqual(await provider["callback_handler"](), (f"code{index}", f"state{index}"))
                        with self.assertRaises(OSError):
                            await asyncio.open_connection("127.0.0.1", port)
                    with patch("webbrowser.open", return_value=True):
                        await provider["redirect_handler"]("https://auth.invalid/authorize?state=last")
                with self.assertRaises(OSError):
                    await asyncio.open_connection("127.0.0.1", port)
            asyncio.run(check())
