# tests/download_test.py
"""download_file tests: binary persistence, overwrite policy, and SSRF/path boundaries."""

import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.download import build_download_tools


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/file.bin":
            body = b"\x00\x01binary-data"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/big.bin":
            body = b"x" * 5000
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/file.bin")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"no")

    def log_message(self, *args):
        pass


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"download_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class DownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ctx = ToolContext(workspace=self.root, max_output_chars=5000)

    def tool(self, config=None):
        return build_download_tools(
            config or {"DOWNLOAD_ALLOW_PRIVATE": "true"},
            self.ctx,
        )[0]

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_downloads_binary(self):
        text = self.tool().invoke({"url": self.url("/file.bin"), "path": "data/file.bin"})
        self.assertIn("Downloaded:", text)
        self.assertEqual((self.root / "data" / "file.bin").read_bytes(), b"\x00\x01binary-data")

    def test_follows_redirect(self):
        text = self.tool().invoke({"url": self.url("/redirect"), "path": "r.bin"})
        self.assertIn("Downloaded:", text)
        self.assertTrue((self.root / "r.bin").exists())

    def test_refuses_overwrite_without_flag(self):
        target = self.root / "keep.bin"
        target.write_bytes(b"keep")
        text = self.tool().invoke({"url": self.url("/file.bin"), "path": "keep.bin"})
        self.assertTrue(text.startswith("Error: [destination_exists]"), text)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_overwrites_with_flag(self):
        target = self.root / "keep.bin"
        target.write_bytes(b"keep")
        text = self.tool().invoke(
            {"url": self.url("/file.bin"), "path": "keep.bin", "overwrite": True}
        )
        self.assertIn("Downloaded:", text)
        self.assertEqual(target.read_bytes(), b"\x00\x01binary-data")

    def test_respects_size_limit(self):
        text = self.tool({"DOWNLOAD_ALLOW_PRIVATE": "true", "DOWNLOAD_MAX_BYTES": "10000"}).invoke(
            {"url": self.url("/file.bin"), "path": "ok.bin"}
        )
        self.assertIn("Downloaded:", text)

    def test_private_host_blocked_by_default(self):
        text = self.tool({"DOWNLOAD_ALLOW_PRIVATE": "false"}).invoke(
            {"url": self.url("/file.bin"), "path": "blocked.bin"}
        )
        self.assertTrue(text.startswith("Error: [blocked_host]"), text)

    def test_path_escape(self):
        text = self.tool().invoke({"url": self.url("/file.bin"), "path": "../escape.bin"})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)


if __name__ == "__main__":
    unittest.main()
