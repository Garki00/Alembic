"""API mode selection: protocol resolution and provider persistence."""

from __future__ import annotations

import json
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from config.store import ModelStore
from web.runtime import resolve_api_mode, resolve_base_url


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"api_mode_{uuid4().hex[:12]}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class ApiModeResolutionTest(unittest.TestCase):
    def test_explicit_mode_wins(self):
        self.assertEqual(resolve_api_mode("deepseek", "anthropic"), "anthropic")
        self.assertEqual(resolve_api_mode("anthropic", "openai"), "openai")
        self.assertEqual(resolve_api_mode("openai", "anthropic"), "anthropic")

    def test_default_mode_follows_provider(self):
        self.assertEqual(resolve_api_mode("anthropic", ""), "anthropic")
        self.assertEqual(resolve_api_mode("claude", ""), "anthropic")
        self.assertEqual(resolve_api_mode("deepseek", ""), "openai")
        self.assertEqual(resolve_api_mode("custom", ""), "openai")

    def test_anthropic_mode_swaps_the_default_base_url(self):
        self.assertEqual(
            resolve_base_url("deepseek", "anthropic", "https://api.deepseek.com"),
            "https://api.deepseek.com/anthropic",
        )
        self.assertEqual(
            resolve_base_url("anthropic", "anthropic", "https://api.anthropic.com"),
            "https://api.anthropic.com",
        )

    def test_custom_base_url_is_never_rewritten(self):
        self.assertEqual(
            resolve_base_url("deepseek", "anthropic", "https://proxy.test/anthropic"),
            "https://proxy.test/anthropic",
        )

    def test_openai_mode_on_anthropic_uses_the_v1_endpoint(self):
        self.assertEqual(
            resolve_base_url("anthropic", "openai", "https://api.anthropic.com"),
            "https://api.anthropic.com/v1",
        )


class ApiModeStoreTest(unittest.TestCase):
    def test_api_mode_round_trips(self):
        with workspace_tempdir() as tmp:
            store = ModelStore(tmp / "models.json")
            saved = store.upsert_provider(
                {
                    "provider": "deepseek",
                    "name": "DS",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-test",
                    "api_mode": "anthropic",
                    "models": [{"model": "deepseek-chat", "thinking": "high"}],
                }
            )
            self.assertEqual(saved.api_mode, "anthropic")
            self.assertEqual(saved.public()["api_mode"], "anthropic")
            self.assertEqual(store.active().api_mode, "anthropic")

    def test_invalid_mode_is_stored_as_default(self):
        with workspace_tempdir() as tmp:
            store = ModelStore(tmp / "models.json")
            saved = store.upsert_provider(
                {
                    "provider": "custom",
                    "name": "X",
                    "base_url": "https://example.test/v1",
                    "api_key": "sk-test",
                    "api_mode": "grpc",
                    "models": [{"model": "m", "thinking": "high"}],
                }
            )
            self.assertEqual(saved.api_mode, "")
            raw = json.loads((tmp / "models.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["providers"][0]["api_mode"], "")


if __name__ == "__main__":
    unittest.main()
