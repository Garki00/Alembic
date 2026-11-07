# tests/preferences_test.py
"""通用设置（工作步骤展示 / 收起时机 / 代码工作视图）的持久化与接口。"""

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from config.store import PreferencesStore
from web.server import create_app


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"prefs_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class PreferencesStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = PreferencesStore(self.tmp / "preferences.json")

    def test_defaults(self):
        self.assertEqual(
            self.store.get(),
            {
                "transcript_view": "detailed",
                "collapse_timing": "completion",
                "developer_tools": False,
            },
        )

    def test_update_and_persist(self):
        updated = self.store.update(
            {
                "transcript_view": "verbose",
                "collapse_timing": "next_input",
                "developer_tools": True,
            }
        )
        self.assertEqual(updated["transcript_view"], "verbose")
        self.assertTrue(updated["developer_tools"])

        reopened = PreferencesStore(self.tmp / "preferences.json")
        self.assertEqual(reopened.get(), updated)
        saved = json.loads((self.tmp / "preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["collapse_timing"], "next_input")

    def test_partial_update_keeps_other_values(self):
        self.store.update({"transcript_view": "compact"})
        updated = self.store.update({"developer_tools": True})
        self.assertEqual(updated["transcript_view"], "compact")
        self.assertEqual(updated["collapse_timing"], "completion")

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({"transcript_view": "huge"})
        with self.assertRaises(ValueError):
            self.store.update({"collapse_timing": "later"})


class PreferencesApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = create_app(data_dir=self.tmp / "data", memory_dir=self.tmp / "memory")
        self.client = TestClient(self.app)

    def test_bootstrap_contains_preferences(self):
        data = self.client.get("/api/bootstrap").json()
        self.assertEqual(data["preferences"]["transcript_view"], "detailed")
        self.assertEqual(data["preferences"]["collapse_timing"], "completion")
        self.assertFalse(data["preferences"]["developer_tools"])

    def test_get_and_put(self):
        updated = self.client.put(
            "/api/preferences",
            json={"transcript_view": "compact", "developer_tools": True},
        )
        self.assertEqual(updated.status_code, 200)
        preferences = updated.json()["preferences"]
        self.assertEqual(preferences["transcript_view"], "compact")
        self.assertTrue(preferences["developer_tools"])
        self.assertEqual(
            self.client.get("/api/preferences").json()["preferences"],
            preferences,
        )

    def test_invalid_value_returns_400(self):
        response = self.client.put("/api/preferences", json={"transcript_view": "huge"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
