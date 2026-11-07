# tests/store_test.py
"""配置持久化的迁移与工作区 CRUD 测试（不经过 HTTP）。"""

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from config.store import ModelStore, ToolStore, WorkspaceStore


def make_dir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"store_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class ToolsMigrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_dir()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "tools.json"

    def write(self, payload: dict) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_v1_migration_enables_run_command_and_keeps_config(self):
        """老配置里 run_command=false 只是当时的默认值，迁移时打开它。"""
        self.write({
            "tools": {
                "run_command": {"enabled": False, "config": {}},
                "get_weather": {"enabled": True, "config": {"QWEATHER_KEY_ID": "kid-1"}},
            }
        })
        ToolStore(self.path).ensure_saved()

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 3)
        self.assertTrue(saved["tools"]["run_command"]["enabled"])
        self.assertFalse(saved["tools"]["get_weather"]["enabled"])
        self.assertEqual(saved["tools"]["get_weather"]["config"]["QWEATHER_KEY_ID"], "kid-1")
        # 新增条目也要补齐
        self.assertIn("file_read", saved["tools"])

    def test_v2_respects_user_choice(self):
        """已经迁移过之后，用户手动关掉 run_command 不会被强行打开。"""
        self.write({
            "version": 2,
            "tools": {"run_command": {"enabled": False, "config": {}}},
        })
        ToolStore(self.path).ensure_saved()

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(saved["tools"]["run_command"]["enabled"])

    def test_seed_uses_current_defaults(self):
        store = ToolStore(self.path)
        store.ensure_saved()
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(saved["tools"]["run_command"]["enabled"])
        self.assertFalse(saved["tools"]["get_weather"]["enabled"])
        self.assertEqual(saved["version"], 3)


class ModelStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_dir()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "models.json"

    def test_seed_is_empty_without_complete_env_config(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""}):
            store = ModelStore(self.path)
            self.assertEqual(store.list(), [])
            self.assertTrue(store.needs_setup())

    def test_seed_uses_env_only_when_key_and_model_exist(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test", "MODEL_NAME": "deepseek-chat"}):
            store = ModelStore(self.path)
            models = store.list()
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0].model, "deepseek-chat")
            self.assertFalse(store.needs_setup())

    def test_needs_setup_requires_model_id(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""}):
            store = ModelStore(self.path)
            store.upsert({
                "name": "半配置",
                "provider": "custom",
                "model": "",
                "base_url": "https://example.test/v1",
                "api_key": "sk-test",
            })
            self.assertTrue(store.needs_setup())


class WorkspaceStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_dir()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "workspace.json"
        self.a = self.dir / "alpha"
        self.b = self.dir / "beta"
        self.a.mkdir()
        self.b.mkdir()

    def test_seed_has_default_workspace(self):
        store = WorkspaceStore(self.path, default=self.a)
        state = store.state()
        self.assertEqual(len(state["workspaces"]), 1)
        self.assertEqual(Path(state["workspaces"][0]["path"]), self.a.resolve())
        self.assertEqual(state["active_id"], state["workspaces"][0]["id"])

    def test_legacy_format_is_migrated(self):
        """旧格式 current + recent 迁移成命名工作区列表。"""
        self.path.write_text(json.dumps({
            "current": str(self.a),
            "recent": [str(self.a), str(self.b), "Z:/missing"],
        }), encoding="utf-8")

        store = WorkspaceStore(self.path, default=self.a)
        state = store.state()
        self.assertEqual(len(state["workspaces"]), 2)  # 不存在的目录被丢掉
        self.assertEqual(store.active().path, str(self.a.resolve()))

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 2)
        self.assertEqual([item["name"] for item in saved["workspaces"]], ["alpha", "beta"])

    def test_add_rename_activate_remove(self):
        store = WorkspaceStore(self.path, default=self.a)

        workspace = store.add(str(self.b), "项目二")
        self.assertEqual(workspace.name, "项目二")
        self.assertEqual(store.active_id, workspace.id)

        self.assertTrue(store.rename(workspace.id, "  改名了  "))
        self.assertEqual(store.get(workspace.id).name, "改名了")

        self.assertTrue(store.set_active("ws-default"))
        self.assertEqual(store.active().id, "ws-default")

        self.assertTrue(store.remove(workspace.id))
        self.assertIsNone(store.get(workspace.id))
        self.assertFalse(store.remove(workspace.id))

    def test_add_rejects_missing_and_duplicate(self):
        store = WorkspaceStore(self.path, default=self.a)
        with self.assertRaises(ValueError):
            store.add(str(self.a))  # 默认工作区就是 alpha
        with self.assertRaises(ValueError):
            store.add("Z:/nope")

    def test_rename_rejects_empty(self):
        store = WorkspaceStore(self.path, default=self.a)
        self.assertFalse(store.rename("ws-default", "   "))


if __name__ == "__main__":
    unittest.main()
