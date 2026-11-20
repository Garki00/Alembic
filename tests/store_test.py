# tests/store_test.py
"""Config persistence migration and workspace CRUD tests (without HTTP)."""

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
        """run_command=false in the old config was only the default at the time; migration turns it on."""
        self.write({
            "tools": {
                "run_command": {"enabled": False, "config": {}},
                "get_weather": {"enabled": True, "config": {"QWEATHER_KEY_ID": "kid-1"}},
            }
        })
        ToolStore(self.path).ensure_saved()

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 4)
        self.assertTrue(saved["tools"]["run_command"]["enabled"])
        self.assertFalse(saved["tools"]["get_weather"]["enabled"])
        self.assertEqual(saved["tools"]["get_weather"]["config"]["QWEATHER_KEY_ID"], "kid-1")
        # 新增条目也要补齐
        self.assertIn("read_file", saved["tools"])

    def test_v2_respects_user_choice(self):
        """After migration, a user who manually disables run_command is not forcibly re-enabled."""
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
        self.assertEqual(saved["version"], 4)


class ModelStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_dir()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "models.json"

    def test_seed_is_empty_without_complete_env_config(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""}):
            store = ModelStore(self.path)
            self.assertEqual(store.providers(), [])
            self.assertTrue(store.needs_setup())

    def test_seed_uses_env_only_when_key_and_model_exist(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test", "MODEL_NAME": "deepseek-chat"}):
            store = ModelStore(self.path)
            providers = store.providers()
            self.assertEqual(len(providers), 1)
            self.assertEqual(providers[0].models[0].model, "deepseek-chat")
            self.assertFalse(store.needs_setup())

    def test_needs_setup_requires_model_id(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""}):
            store = ModelStore(self.path)
            store.upsert_provider({
                "provider": "custom",
                "base_url": "https://example.test/v1",
                "api_key": "sk-test",
                "models": [{"model": ""}],
            })
            self.assertTrue(store.needs_setup())

    def test_provider_models_and_thinking_roundtrip(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""}):
            store = ModelStore(self.path)
            provider = store.upsert_provider({
                "provider": "openai",
                "name": "主力供应商",
                "base_url": "https://example.test/v1",
                "api_key": "sk-test",
                "models": [
                    {"model": "gpt-a", "thinking": "max"},
                    {"model": "gpt-b", "thinking": "bogus"},
                ],
            })
            self.assertEqual(
                [item.thinking for item in provider.models],
                ["max", "none"],
            )
            self.assertEqual(provider.models[0].thinking_levels, ["low", "high", "max"])
            self.assertEqual(
                provider.models[1].thinking_levels,
                ["none", "low", "high", "max"],
            )
            self.assertEqual(store.active_model_id, provider.models[0].id)
            self.assertEqual(store.active().thinking, "max")
            self.assertTrue(store.set_thinking(
                provider.id, provider.models[0].id, "low"
            ))
            self.assertEqual(store.active().thinking, "low")
            self.assertFalse(store.set_thinking(
                provider.id, provider.models[0].id, "bogus"
            ))

    def test_model_limits_default_to_known_maximum(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""}):
            store = ModelStore(self.path)
            provider = store.upsert_provider({
                "provider": "openai",
                "name": "OpenAI",
                "base_url": "https://example.test/v1",
                "api_key": "sk-test",
                "models": [{"model": "gpt-4.1", "thinking": "high"}],
            })
            model = provider.models[0]
            self.assertEqual(model.context_length, 1_048_576)
            self.assertEqual(model.max_output_tokens, 32_768)
            self.assertTrue(model.supports_images)
            active = store.active()
            self.assertEqual(active.context_length, 1_048_576)
            self.assertEqual(active.max_output_tokens, 32_768)
            self.assertTrue(active.supports_images)

            updated = store.upsert_provider({
                "id": provider.id,
                "provider": "openai",
                "base_url": "https://example.test/v1",
                "api_key": "sk-test",
                "models": [{
                    "id": model.id,
                    "model": "gpt-4.1",
                    "thinking": "high",
                    "supports_images": False,
                    "context_length": 32000,
                    "max_output_tokens": 4096,
                }],
            })
            self.assertFalse(updated.models[0].supports_images)
            self.assertEqual(store.active().context_length, 32000)
            self.assertEqual(store.active().max_output_tokens, 4096)

    def test_legacy_flat_models_are_migrated_to_one_provider(self):
        self.path.write_text(json.dumps({
            "models": [
                {"id": "a", "name": "A", "provider": "deepseek", "model": "m-a",
                 "base_url": "https://api.deepseek.com", "api_key": "sk", "temperature": 0},
                {"id": "b", "name": "B", "provider": "deepseek", "model": "m-b",
                 "base_url": "https://api.deepseek.com", "api_key": "sk", "temperature": 0},
            ],
            "active_id": "b",
        }), encoding="utf-8")
        store = ModelStore(self.path)
        providers = store.providers()
        self.assertEqual(len(providers), 1)
        self.assertEqual([item.model for item in providers[0].models], ["m-a", "m-b"])
        self.assertEqual(
            providers[0].models[0].thinking_levels,
            ["none", "low", "high", "max"],
        )
        self.assertEqual(store.active_model_id, "b")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("providers", saved)


class WorkspaceStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_dir()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "workspace.json"
        self.a = self.dir / "alpha"
        self.b = self.dir / "beta"
        self.a.mkdir()
        self.b.mkdir()

    def test_seed_has_no_workspace(self):
        """New installs do not add a workspace automatically; the user picks the directory."""
        store = WorkspaceStore(self.path, default=self.a)
        state = store.state()
        self.assertEqual(state["workspaces"], [])
        self.assertEqual(state["active_id"], "")

    def test_legacy_format_is_migrated(self):
        """Legacy current + recent format migrates into a named workspace list."""
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

        first = store.add(str(self.a))
        workspace = store.add(str(self.b), "项目二")
        self.assertEqual(workspace.name, "项目二")
        self.assertEqual(store.active_id, workspace.id)

        self.assertTrue(store.rename(workspace.id, "  改名了  "))
        self.assertEqual(store.get(workspace.id).name, "改名了")

        self.assertTrue(store.set_active(first.id))
        self.assertEqual(store.active().id, first.id)

        self.assertTrue(store.remove(workspace.id))
        self.assertIsNone(store.get(workspace.id))
        self.assertFalse(store.remove(workspace.id))

    def test_add_rejects_missing_and_duplicate(self):
        store = WorkspaceStore(self.path, default=self.a)
        store.add(str(self.a))
        with self.assertRaises(ValueError):
            store.add(str(self.a))
        with self.assertRaises(ValueError):
            store.add("Z:/nope")

    def test_rename_rejects_empty(self):
        store = WorkspaceStore(self.path, default=self.a)
        workspace = store.add(str(self.a))
        self.assertFalse(store.rename(workspace.id, "   "))


if __name__ == "__main__":
    unittest.main()
