# tests/fetch_test.py
"""Web page fetching tool tests: local HTTP server, redirects, SSRF, and error contracts."""

import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.fetch import _validate_target, build_fetch_tools


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server 的命名约定
        if self.path == "/html":
            body = (
                "<html><head><title>测试页面</title><style>p{color:red}</style></head>"
                "<body><h1>标题</h1><p>正文 &amp; 更多</p><script>alert(1)</script></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"a": 1, "b": "x"}')
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/html")
            self.end_headers()
        elif self.path == "/binary":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")

    def log_message(self, *args):  # 安静
        pass


class FetchTest(unittest.TestCase):
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
        self.root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
        self.root.mkdir(parents=True, exist_ok=True)
        self.workdir = self.root / f"fetch_{uuid4().hex[:10]}"
        self.workdir.mkdir()
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.ctx = ToolContext(workspace=self.workdir, max_output_chars=5000)

    def tools(self, config=None):
        settings = {"FETCH_ALLOW_PRIVATE": "true"} if config is None else config
        return {t.name: t for t in build_fetch_tools(settings, self.ctx)}

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_html_is_converted_to_readable_text(self):
        text = self.tools()["fetch_url"].invoke({"url": self.url("/html")})
        self.assertIn("Status: 200", text)
        self.assertIn("Title: 测试页面", text)
        self.assertIn("标题", text)
        self.assertIn("正文 & 更多", text)
        self.assertNotIn("alert(1)", text)
        self.assertIn("untrusted", text)

    def test_json_is_pretty_printed(self):
        text = self.tools()["fetch_url"].invoke({"url": self.url("/json")})
        self.assertIn('"a": 1', text)
        self.assertIn('"b": "x"', text)

    def test_redirect_is_followed_and_validated(self):
        text = self.tools()["fetch_url"].invoke({"url": self.url("/redirect")})
        self.assertIn("Title: 测试页面", text)

    def test_error_status_is_reported(self):
        text = self.tools()["fetch_url"].invoke({"url": self.url("/nope")})
        self.assertTrue(text.startswith("Error: [http_error]"), text)
        self.assertIn("404", text)

    def test_binary_content_is_rejected(self):
        text = self.tools()["fetch_url"].invoke({"url": self.url("/binary")})
        self.assertTrue(text.startswith("Error: [unsupported_content]"), text)

    def test_private_host_is_blocked_by_default(self):
        text = self.tools({})["fetch_url"].invoke({"url": self.url("/html")})
        self.assertTrue(text.startswith("Error: [blocked_host]"), text)

    def test_link_local_ip_is_blocked(self):
        with self.assertRaises(Exception) as caught:
            _validate_target("http://169.254.169.254/latest/meta-data", allow_private=False)
        self.assertEqual(getattr(caught.exception, "code", ""), "blocked_host")

    def test_invalid_scheme_and_missing_url(self):
        text = self.tools()["fetch_url"].invoke({"url": "file:///etc/passwd"})
        self.assertTrue(text.startswith("Error: [invalid_url]"), text)

        empty = self.tools()["fetch_url"].invoke({"url": "   "})
        self.assertTrue(empty.startswith("Error: [empty_url]"), empty)


if __name__ == "__main__":
    unittest.main()
