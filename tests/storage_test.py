# tests/storage_test.py
"""用户级数据目录与一次性迁移。"""

import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from config.settings import resolve_app_home, resolve_default_workspace
from config.storage import ensure_storage


def make_tempdir(prefix: str = "storage_") -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}{uuid4().hex[:12]}"
    path.mkdir()
    return path


class ResolveAppHomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_explicit_override_wins(self):
        with patch.dict("os.environ", {"SMART_AGENT_HOME": str(self.tmp)}):
            self.assertEqual(resolve_app_home(), self.tmp.resolve())

    def test_windows_default_is_appdata(self):
        if sys.platform != "win32":
            self.skipTest("仅在 Windows 上验证 APPDATA 约定")
        with patch.dict("os.environ", {"SMART_AGENT_HOME": "", "APPDATA": str(self.tmp)}):
            self.assertEqual(resolve_app_home(), (self.tmp / "SmartAgent").resolve())

    def test_default_workspace_follows_env(self):
        with patch.dict("os.environ", {"WORKSPACE_DIR": str(self.tmp)}):
            self.assertEqual(resolve_default_workspace(), self.tmp.resolve())


class EnsureStorageTest(unittest.TestCase):
    def setUp(self):
        self.root = make_tempdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.legacy = self.root / "repo"
        self.home = self.root / "home"
        (self.legacy / "data").mkdir(parents=True)
        (self.legacy / "data" / "tools.json").write_text('{"tools": {}}', encoding="utf-8")
        (self.legacy / "memory").mkdir()
        (self.legacy / "memory" / "chat.json").write_text('{"messages": []}', encoding="utf-8")

    def test_copies_legacy_data_once(self):
        result = ensure_storage(home=self.home, legacy=self.legacy)
        self.assertEqual(sorted(result["migrated"]), ["data", "memory"])
        self.assertTrue((self.home / "data" / "tools.json").exists())
        self.assertTrue((self.home / "memory" / "chat.json").exists())
        # 原目录保留，可回退
        self.assertTrue((self.legacy / "data" / "tools.json").exists())

    def test_does_not_overwrite_existing_data(self):
        (self.home / "data").mkdir(parents=True)
        (self.home / "data" / "tools.json").write_text('{"keep": true}', encoding="utf-8")

        result = ensure_storage(home=self.home, legacy=self.legacy)
        self.assertEqual(result["migrated"], ["memory"])
        self.assertEqual(
            (self.home / "data" / "tools.json").read_text(encoding="utf-8"), '{"keep": true}'
        )

    def test_second_run_is_noop(self):
        ensure_storage(home=self.home, legacy=self.legacy)
        again = ensure_storage(home=self.home, legacy=self.legacy)
        self.assertEqual(again["migrated"], [])

    def test_same_directory_skips_migration(self):
        result = ensure_storage(home=self.legacy, legacy=self.legacy)
        self.assertEqual(result["migrated"], [])

    def test_missing_legacy_is_fine(self):
        empty = self.root / "empty-repo"
        empty.mkdir()
        result = ensure_storage(home=self.home, legacy=empty)
        self.assertEqual(result["migrated"], [])
        self.assertTrue(self.home.is_dir())

    def test_empty_target_directory_is_replaced(self):
        (self.home / "data").mkdir(parents=True)  # 空目录
        result = ensure_storage(home=self.home, legacy=self.legacy)
        self.assertIn("data", result["migrated"])
        self.assertTrue((self.home / "data" / "tools.json").exists())


if __name__ == "__main__":
    unittest.main()
