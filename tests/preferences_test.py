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
                "transcript_view": "standard",
                "collapse_timing": "completion",
                "permission_mode": "build",
                "terminal": "powershell",
                "link_target": "sidebar",
                "developer_tools": False,
                "compact_keep_first_turn": False,
                "subagent_models": {},
                "custom_system_prompt": "",
            },
        )

    def test_theme_accepts_dark_and_system(self):
        self.assertEqual(self.store.update({"theme": "dark"})["theme"], "dark")
        self.assertEqual(self.store.update({"theme": "system"})["theme"], "system")
        with self.assertRaises(ValueError):
            self.store.update({"theme": "neon"})

    def test_custom_system_prompt_round_trip(self):
        text = "# Identity\nYou are a release engineer."
        updated = self.store.update({"custom_system_prompt": text})
        self.assertEqual(updated["custom_system_prompt"], text)
        self.assertEqual(
            PreferencesStore(self.tmp / "preferences.json").get()["custom_system_prompt"],
            text,
        )
        self.assertEqual(self.store.update({"custom_system_prompt": ""})["custom_system_prompt"], "")

    def test_custom_system_prompt_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            self.store.update({"custom_system_prompt": 3})
        with self.assertRaises(ValueError):
            self.store.update({"custom_system_prompt": "x" * 20_001})
        cleaned = self.store.update({"custom_system_prompt": "a\x00b"})
        self.assertEqual(cleaned["custom_system_prompt"], "ab")

    def test_legacy_v1_read_only_migrates_to_build(self):
        # v1 的 read_only 语义是「所有工具都要审批」，对应现在的 build。
        (self.tmp / "preferences.json").write_text(
            json.dumps({"permission_mode": "read_only"}), encoding="utf-8"
        )
        self.assertEqual(self.store.get()["permission_mode"], "build")

    def test_legacy_v2_modes_migrate_to_current_ones(self):
        cases = {
            "read_only": "plan",
            "approval_all": "build",
            "workspace_write": "build",
            "full_access": "yolo",
        }
        for legacy, expected in cases.items():
            (self.tmp / "preferences.json").write_text(
                json.dumps({"version": 2, "permission_mode": legacy}), encoding="utf-8"
            )
            # 每次都用新实例读取：PreferencesStore 会缓存已加载的文件内容。
            store = PreferencesStore(self.tmp / "preferences.json")
            self.assertEqual(store.get()["permission_mode"], expected, legacy)

    def test_new_modes_are_kept(self):
        for mode in ("plan", "build", "edit", "yolo"):
            self.store.update({"permission_mode": mode})
            self.assertEqual(self.store.get()["permission_mode"], mode)

    def test_legacy_alias_is_accepted_on_update(self):
        updated = self.store.update({"permission_mode": "workspace_write"})
        self.assertEqual(updated["permission_mode"], "build")

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
                "permission_mode": "yolo",
                "developer_tools": True,
            }
        )
        self.assertEqual(updated["transcript_view"], "verbose")
        self.assertEqual(updated["permission_mode"], "yolo")
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

    def test_subagent_models_keep_known_types_only(self):
        updated = self.store.update(
            {
                "subagent_models": {
                    "general-purpose": "provider-1/model-1",
                    "Explore": "",
                    "unknown": "provider-1/model-2",
                }
            }
        )
        self.assertEqual(updated["subagent_models"], {"general-purpose": "provider-1/model-1"})
        reopened = PreferencesStore(self.tmp / "preferences.json")
        self.assertEqual(reopened.get()["subagent_models"], {"general-purpose": "provider-1/model-1"})
        self.assertEqual(self.store.update({"subagent_models": {}})["subagent_models"], {})

    def test_invalid_subagent_models_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({"subagent_models": ["general-purpose"]})

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
        self.assertEqual(data["preferences"]["transcript_view"], "standard")
        self.assertEqual(data["preferences"]["collapse_timing"], "completion")
        self.assertEqual(data["preferences"]["permission_mode"], "build")
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

    def test_subagent_models_round_trip(self):
        response = self.client.put(
            "/api/preferences",
            json={"subagent_models": {"general-purpose": "provider-1/model-1"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["preferences"]["subagent_models"],
            {"general-purpose": "provider-1/model-1"},
        )

    def test_custom_system_prompt_round_trip(self):
        text = "# Identity\nYou are a release engineer."
        response = self.client.put("/api/preferences", json={"custom_system_prompt": text})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["preferences"]["custom_system_prompt"], text)
        self.assertEqual(
            self.client.get("/api/preferences").json()["preferences"]["custom_system_prompt"],
            text,
        )

    def test_custom_system_prompt_too_long_returns_400(self):
        response = self.client.put(
            "/api/preferences", json={"custom_system_prompt": "x" * 20_001}
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
