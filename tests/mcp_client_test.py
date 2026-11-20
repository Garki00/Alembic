"""Real SDK transport integration tests for the synchronous MCP bridge."""
import sys
import socket
import subprocess
import time
import tempfile
import threading
import unittest
from pathlib import Path
from extensions.mcp import MCPClient, MCPError

SERVER = '''
import asyncio
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("integration")
@mcp.tool()
def add(a: int, b: int) -> dict[str, int]:
    return {"sum": a+b}
@mcp.tool()
async def slow() -> str:
    await asyncio.sleep(5)
    return "done"
@mcp.resource("test://hello")
def hello() -> str:
    return "resource body"
@mcp.prompt()
def greeting(name: str) -> str:
    return "Hello " + name
mcp.run()
'''

class MCPIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.script = Path(self.temp.name) / "server.py"
        self.script.write_text(SERVER, encoding="utf-8")
        self.server = {"command":sys.executable, "args":[str(self.script)], "timeout":10}
        self.client = MCPClient()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_real_stdio_session(self):
        found = self.client.discover(self.server)
        self.assertEqual({t["name"] for t in found["tools"]}, {"add", "slow"})
        self.assertEqual(found["resources"][0]["uri"], "test://hello")
        self.assertEqual(found["prompts"][0]["name"], "greeting")
        result = self.client.call_tool(self.server, "add", {"a":2,"b":3})
        self.assertEqual(result["structuredContent"], {"sum":5})
        self.assertFalse(result["isError"])
        self.assertIn("resource body", str(self.client.read_resource(self.server, "test://hello")))
        self.assertIn("Hello Ada", str(self.client.get_prompt(self.server, "greeting", {"name":"Ada"})))
        self.assertEqual(len(self.client._workers), 1)
        workers = list(self.client._workers.values())
        self.client.close()
        self.assertTrue(all(not w.thread.is_alive() for w in workers))

    def test_cancel_then_reuse(self):
        self.client.discover(self.server)
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set)
        timer.start()
        try:
            with self.assertRaisesRegex(MCPError, "cancelled"):
                self.client.call_tool(self.server, "slow", {}, cancel)
        finally:
            timer.cancel()
        result = self.client.call_tool(self.server, "add", {"a":1,"b":1})
        self.assertEqual(result["structuredContent"], {"sum":2})

    def test_http_transports(self):
        for transport, sdk_transport, endpoint in (("streamable_http", "streamable-http", "/mcp"), ("sse", "sse", "/sse")):
            with self.subTest(transport=transport):
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                script = Path(self.temp.name) / (transport + ".py")
                script.write_text(SERVER.replace('FastMCP("integration")', f'FastMCP("integration", host="127.0.0.1", port={port})').replace('mcp.run()', f'mcp.run(transport="{sdk_transport}")'), encoding="utf-8")
                process = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                                break
                        except OSError:
                            if time.monotonic() > deadline:
                                self.fail("MCP test server failed to listen")
                            time.sleep(.05)
                    server = {"transport":transport, "url":f"http://127.0.0.1:{port}{endpoint}", "timeout":5}
                    self.assertEqual(len(self.client.discover(server)["tools"]), 2)
                    self.assertEqual(self.client.call_tool(server, "add", {"a":3,"b":4})["structuredContent"], {"sum":7})
                    self.client.close()
                finally:
                    process.terminate()
                    process.wait(timeout=5)

    def test_reconcile_preserves_and_retires_connections(self):
        self.client.discover(self.server)
        original = next(iter(self.client._workers.values()))
        self.client.reconcile([{**self.server, "enabled":True, "name":"Display name changed"}])
        self.assertIs(next(iter(self.client._workers.values())), original)
        self.assertTrue(original.thread.is_alive())
        self.client.reconcile([{**self.server, "enabled":False}])
        self.assertFalse(original.thread.is_alive())
        self.assertFalse(self.client._workers)
        self.client.discover(self.server)
        changed = next(iter(self.client._workers.values()))
        self.client.reconcile([{**self.server, "timeout":9}])
        self.assertFalse(changed.thread.is_alive())
        self.client.discover(self.server)
        deleted = next(iter(self.client._workers.values()))
        self.client.reconcile([])
        self.assertFalse(deleted.thread.is_alive())
        self.assertFalse(self.client._workers)

    def test_disabled_no_process(self):
        with self.assertRaisesRegex(MCPError, "disabled"):
            self.client.discover({**self.server, "enabled":False})
        self.assertFalse(self.client._workers)

    def test_errors_do_not_expose_credentials(self):
        with self.assertRaises(MCPError) as caught:
            self.client.discover({"command":"does-not-exist-secret-token", "timeout":1})
        self.assertNotIn("secret-token", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
