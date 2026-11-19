# tests/preferences_test.py
"""Persistence and API for general preferences (work step display, collapse timing, and code work view)."""

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
                "theme": "light",
                "transcript_view": "detailed",
                "collapse_timing": "completion",
                "permission_mode": "workspace_write",
                "terminal": "powershell",
                "link_target": "sidebar",
                "developer_tools": False,
                "compact_keep_first_turn": False,
            },
        )

    def test_theme_accepts_dark_and_system(self):
        self.assertEqual(self.store.update({"theme": "dark"})["theme"], "dark")
        self.assertEqual(self.store.update({"theme": "system"})["theme"], "system")
        with self.assertRaises(ValueError):
            self.store.update({"theme": "neon"})

    def test_legacy_read_only_migrates_to_approval_all(self):
        (self.tmp / "preferences.json").write_text(
            json.dumps({"permission_mode": "read_only"}), encoding="utf-8"
        )
        self.assertEqual(self.store.get()["permission_mode"], "approval_all")

    def test_new_read_only_is_kept(self):
        self.store.update({"permission_mode": "read_only"})
        self.assertEqual(self.store.get()["permission_mode"], "read_only")

    def test_terminal_and_link_target(self):
        updated = self.store.update({"terminal": "gitbash", "link_target": "browser"})
        self.assertEqual(updated["terminal"], "gitbash")
        self.assertEqual(updated["link_target"], "browser")
        reopened = PreferencesStore(self.tmp / "preferences.json")
        self.assertEqual(reopened.get()["terminal"], "gitbash")
        self.assertEqual(reopened.get()["link_target"], "browser")

    def test_invalid_terminal_and_link_target_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({"terminal": "fish"})
        with self.assertRaises(ValueError):
            self.store.update({"link_target": "iframe"})

    def test_update_and_persist(self):
        updated = self.store.update(
            {
                "transcript_view": "verbose",
                "collapse_timing": "next_input",
                "permission_mode": "full_access",
                "developer_tools": True,
            }
        )
        self.assertEqual(updated["transcript_view"], "verbose")
        self.assertEqual(updated["permission_mode"], "full_access")
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
        with self.assertRaises(ValueError):
            self.store.update({"permission_mode": "root"})


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
        self.assertEqual(data["preferences"]["permission_mode"], "workspace_write")
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
