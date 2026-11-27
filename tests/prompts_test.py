# tests/prompts_test.py
"""Named system prompts: storage, selection, API, and prompt replacement."""

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from config.store import PromptStore
from web.server import create_app


def make_tempdir(prefix: str = "prompts_") -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}{uuid4().hex[:12]}"
    path.mkdir()
    return path


class PromptStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = make_tempdir() / "prompts.json"
        self.store = PromptStore(self.path)

    def test_create_and_select(self) -> None:
        first = self.store.create("发布工程师", "You are a release engineer.")
        second = self.store.create("评审员", "You are a reviewer.")
        self.assertEqual(self.store.state()["selected"], "")
        self.store.select(first["id"])
        self.assertEqual(self.store.state()["selected"], first["id"])
        self.assertEqual(self.store.selected_content(), "You are a release engineer.")
        self.store.select("")
        self.assertEqual(self.store.selected_content(), "")
        self.assertEqual(len(self.store.state()["prompts"]), 2)
        self.assertNotEqual(first["id"], second["id"])

    def test_update_keeps_omitted_fields(self) -> None:
        prompt = self.store.create("发布工程师", "old body")
        updated = self.store.update(prompt["id"], content="new body")
        self.assertEqual(updated["name"], "发布工程师")
        self.assertEqual(updated["content"], "new body")

    def test_delete_selected_returns_to_default(self) -> None:
        prompt = self.store.create("发布工程师", "body")
        self.store.select(prompt["id"])
        state = self.store.delete(prompt["id"])
        self.assertEqual(state["selected"], "")
        self.assertEqual(state["prompts"], [])

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create("", "body")
        with self.assertRaises(ValueError):
            self.store.create("name", "   ")
        with self.assertRaises(ValueError):
            self.store.create("x" * 81, "body")

    def test_legacy_prompt_is_imported_once(self) -> None:
        self.store.migrate_legacy("Legacy body")
        state = self.store.state()
        self.assertEqual(len(state["prompts"]), 1)
        self.assertEqual(state["selected"], state["prompts"][0]["id"])
        self.assertEqual(self.store.selected_content(), "Legacy body")
        # A second call must not duplicate once the file exists.
        self.store.migrate_legacy("Legacy body")
        self.assertEqual(len(self.store.state()["prompts"]), 1)

    def test_unknown_selection_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            self.store.select("p-missing")
        with self.assertRaises(KeyError):
            self.store.update("p-missing", name="n", content="c")
        with self.assertRaises(KeyError):
            self.store.delete("p-missing")


class PromptApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tempdir()
        app = create_app(data_dir=self.root, memory_dir=self.root / "memory")
        self.client = TestClient(app)

    def test_crud_and_selection(self) -> None:
        created = self.client.post(
            "/api/prompts", json={"name": "发布工程师", "content": "You are a release engineer."}
        )
        self.assertEqual(created.status_code, 200)
        state = created.json()
        self.assertEqual(state["selected"], "")
        prompt = state["prompts"][0]
        self.assertEqual(prompt["name"], "发布工程师")

        updated = self.client.put(
            f"/api/prompts/{prompt['id']}",
            json={"name": "发布工程师", "content": "You ship releases."},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["prompts"][0]["content"], "You ship releases.")

        selected = self.client.post("/api/prompts/select", json={"id": prompt["id"]})
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["selected"], prompt["id"])

        default = self.client.post("/api/prompts/select", json={"id": ""})
        self.assertEqual(default.json()["selected"], "")

        deleted = self.client.delete(f"/api/prompts/{prompt['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["prompts"], [])

    def test_validation_and_missing_ids(self) -> None:
        self.assertEqual(
            self.client.post("/api/prompts", json={"name": "", "content": "x"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/prompts", json={"name": "n", "content": " "}).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                "/api/prompts/p-missing", json={"name": "n", "content": "c"}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.delete("/api/prompts/p-missing").status_code, 404)
        self.assertEqual(
            self.client.post("/api/prompts/select", json={"id": "p-missing"}).status_code,
            404,
        )

    def test_selected_prompt_loads_into_prompt_state(self) -> None:
        self.client.post(
            "/api/prompts", json={"name": "发布工程师", "content": "You are a release engineer."}
        )
        prompt = self.client.get("/api/prompts").json()["prompts"][0]
        self.client.post("/api/prompts/select", json={"id": prompt["id"]})
        self.assertEqual(self.client.get("/api/prompts").json()["selected"], prompt["id"])


class PromptRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tempdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        env = patch.dict("os.environ", {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""})
        env.start()
        self.addCleanup(env.stop)
        self.received: list[str] = []

        def factory(model_cfg, enabled_tools, workspace, **kwargs):
            self.received.append(str(kwargs.get("custom_prompt") or ""))

            class Agent:
                def stream(self, payload, stream_mode=None):
                    messages = list(payload["messages"])
                    yield ("values", {"messages": [*messages, AIMessage(content="ok")]})

            return Agent(), None

        self.app = create_app(
            data_dir=self.root / "data",
            memory_dir=self.root / "memory",
            runtime_factory=factory,
        )
        self.client = TestClient(self.app)
        self.client.post(
            "/api/providers",
            json={
                "provider": "openai",
                "name": "测试供应商",
                "base_url": "https://example.test/v1",
                "api_key": "sk-test-1234",
                "models": [{"model": "gpt-test", "name": "GPT Test", "thinking": "high"}],
            },
        )
        self.client.post("/api/workspaces", json={"path": str(self.root)})

    def chat(self) -> None:
        with self.client.stream("POST", "/api/chat", json={"message": "hi"}) as response:
            self.assertEqual(response.status_code, 200)
            for _ in response.iter_text():
                pass

    def test_selected_prompt_reaches_the_runtime(self) -> None:
        self.chat()
        self.assertEqual(self.received[-1], "")

        state = self.client.post(
            "/api/prompts", json={"name": "发布工程师", "content": "You ship releases."}
        ).json()
        prompt = state["prompts"][0]
        self.client.post("/api/prompts/select", json={"id": prompt["id"]})
        self.chat()
        self.assertEqual(self.received[-1], "You ship releases.")

        self.client.post("/api/prompts/select", json={"id": ""})
        self.chat()
        self.assertEqual(self.received[-1], "")


if __name__ == "__main__":
    unittest.main()
