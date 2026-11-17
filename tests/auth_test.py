# tests/auth_test.py
"""Access tokens for the desktop shell: the API requires the correct token."""

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from web.auth import COOKIE_NAME
from web.server import create_app


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"auth_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class AuthEnabledTest(unittest.TestCase):
    TOKEN = "test-token-1234"

    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = create_app(
            data_dir=self.tmp / "data",
            memory_dir=self.tmp / "memory",
            auth_token=self.TOKEN,
        )
        self.client = TestClient(self.app)

    def test_health_needs_no_token(self):
        """The shell uses this for liveness checks; it must be allowed and must not leak configuration."""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("tools", response.text)

    def test_api_requires_token(self):
        response = self.client.get("/api/bootstrap")
        self.assertEqual(response.status_code, 401)

    def test_static_requires_token(self):
        """Pages and the API are both behind the token; static assets are no longer served by the backend."""
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self.client.get("/static/app.js").status_code, 401)

    def test_cors_preflight_skips_token(self):
        """Browser preflight carries no Authorization header and must be allowed, or cross-origin requests cannot be sent at all."""
        response = self.client.options(
            "/api/chat",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"], "http://127.0.0.1:5173"
        )

    def test_url_token_sets_cookie_and_redirects(self):
        response = self.client.get(f"/?token={self.TOKEN}", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        cookie = response.headers.get("set-cookie", "")
        self.assertIn(COOKIE_NAME, cookie)
        self.assertIn("HttpOnly", cookie)

        # cookie 落到客户端后，API 可用
        self.assertEqual(self.client.get("/api/bootstrap").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_bearer_header_works(self):
        response = self.client.get(
            "/api/bootstrap", headers={"Authorization": f"Bearer {self.TOKEN}"}
        )
        self.assertEqual(response.status_code, 200)

    def test_wrong_token_rejected(self):
        self.assertEqual(self.client.get("/?token=nope", follow_redirects=False).status_code, 401)
        self.assertEqual(
            self.client.get("/api/bootstrap", headers={"Authorization": "Bearer nope"}).status_code,
            401,
        )


class AuthDisabledTest(unittest.TestCase):
    def test_no_token_means_open_access(self):
        """Manual `python -m web` sets no token and stays directly usable as before."""
        tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        app = create_app(data_dir=tmp / "data", memory_dir=tmp / "memory")
        client = TestClient(app)
        self.assertEqual(client.get("/api/bootstrap").status_code, 200)
        self.assertEqual(client.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
