"""网页工作区后端的接口测试（使用 FastAPI TestClient，不调用真实模型）。"""

import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from web.server import create_app


def make_tempdir(prefix: str = "web_") -> Path:
    """项目内临时目录（沙箱下系统临时目录不可写）。"""
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}{uuid4().hex[:12]}"
    path.mkdir()
    return path


class StubAgent:
    def invoke(self, payload):
        messages = list(payload["messages"])
        return {"messages": [*messages, AIMessage(content="这是回答")]}


class StubStreamAgent:
    def stream(self, payload, stream_mode=None):
        messages = list(payload["messages"])
        yield ("messages", (AIMessageChunk(content="你"), None))
        yield ("messages", (AIMessageChunk(content="好"), None))
        yield ("values", {"messages": [*messages, AIMessage(content="你好")]})


class StubThoughtStreamAgent:
    emits_thought_events = True

    def stream(self, payload, stream_mode=None):
        messages = list(payload["messages"])
        yield (
            "thought",
            {"id": "reasoning-1", "text": "先检查", "append": True},
        )
        yield (
            "thought",
            {"id": "reasoning-1", "text": "项目结构。", "append": True},
        )
        yield ("messages", (AIMessageChunk(content="完成"), None))
        yield ("values", {"messages": [*messages, AIMessage(content="完成")]})


def stub_factory(model_cfg, enabled_tools, workspace, **kwargs):
    return StubStreamAgent(), None


class WebWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = patch.dict("os.environ", {"DEEPSEEK_API_KEY": "", "MODEL_NAME": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = create_app(
            data_dir=self.tmp / "data",
            memory_dir=self.tmp / "memory",
            runtime_factory=stub_factory,
        )
        self.client = TestClient(self.app)

    # ---- 基础 ----
    def test_root_returns_api_info(self):
        """前后端分离后，/ 只说明 API 位置，不再返回 HTML 页面。"""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "ALEMBIC API")
        self.assertEqual(payload["api"], "/api")
        self.assertIn("frontend", payload)
        self.assertEqual(self.client.get("/static/app.js").status_code, 404)

    def test_cors_preflight_allows_frontend_origin(self):
        response = self.client.options(
            "/api/chat",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"], "http://127.0.0.1:5173"
        )

    def test_bootstrap_contains_defaults(self):
        from tools.registry import TOOL_SPECS

        data = self.client.get("/api/bootstrap").json()
        # 新设备不再自动生成默认供应商/工作区：等初始化向导与用户选择
        self.assertEqual(data["providers"], [])
        self.assertEqual(data["active_provider_id"], "")
        self.assertEqual(data["active_model_id"], "")
        self.assertIsNone(data["active_model"])
        self.assertTrue(data["needs_setup"])
        # 期望值从注册表派生：新增工具不需要改这条断言
        self.assertEqual({tool["name"] for tool in data["tools"]}, set(TOOL_SPECS))
        self.assertEqual(data["workspaces"], [])
        self.assertEqual(data["active_workspace_id"], "")

    def test_tools_enabled_by_default(self):
        data = self.client.get("/api/bootstrap").json()
        states = {tool["name"]: tool["enabled"] for tool in data["tools"]}
        # 审批机制上线后，命令执行默认开启（执行前仍需确认）
        self.assertTrue(states["run_command"])
        self.assertTrue(states["file_read"])
        self.assertTrue(states["file_write"])
        # 实时天气需要用户自己的凭据与 API Host，不再默认开启
        self.assertFalse(states["get_weather"])
        for name in ("read_document", "fetch_url", "todo_write", "ask_user_question",
                     "background_process", "process_inspect"):
            self.assertTrue(states[name], name)

    def create_provider(self) -> dict:
        """给需要模型的对话测试准备一个可用供应商。"""
        return self.client.post("/api/providers", json={
            "provider": "openai",
            "name": "测试供应商",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test-1234",
            "models": [
                {"model": "gpt-test", "name": "GPT Test", "thinking": "high"},
            ],
        }).json()["provider"]

    def create_workspace(self) -> dict:
        """给需要工作区的对话测试准备一个目录。"""
        return self.client.post("/api/workspaces", json={"path": str(self.tmp)}).json()["workspace"]

    def test_needs_setup_turns_false_after_provider_is_saved(self):
        self.assertTrue(self.client.get("/api/bootstrap").json()["needs_setup"])
        self.create_provider()
        data = self.client.get("/api/bootstrap").json()
        self.assertFalse(data["needs_setup"])
        self.assertTrue(data["active_provider_id"])
        self.assertTrue(data["active_model_id"])

    def test_bootstrap_persists_new_tool_entries(self):
        """升级到新版本后，data/tools.json 里应补齐新增条目且不丢旧配置。"""
        self.client.put("/api/tools/get_weather", json={"config": {"QWEATHER_PROJECT_ID": "keep-me"}})
        self.client.get("/api/bootstrap")

        saved = json.loads((self.tmp / "data" / "tools.json").read_text(encoding="utf-8"))
        self.assertIn("file_read", saved["tools"])
        self.assertTrue(saved["tools"]["run_command"]["enabled"])
        self.assertEqual(saved.get("version"), 3)
        self.assertEqual(saved["tools"]["get_weather"]["config"]["QWEATHER_PROJECT_ID"], "keep-me")

    # ---- 供应商与模型 ----
    def test_provider_crud_models_and_key_masking(self):
        created = self.client.post("/api/providers", json={
            "provider": "openai",
            "name": "OpenAI 兼容",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test-1234",
            "models": [
                {
                    "model": "gpt-test",
                    "name": "GPT Test",
                    "thinking": "high",
                    "thinking_levels": ["none", "high"],
                    "temperature": 0.3,
                },
            ],
        }).json()["provider"]
        self.assertTrue(created["api_key_set"])
        self.assertEqual(created["api_key_hint"], "••••1234")
        self.assertNotIn("api_key", created)
        self.assertEqual(len(created["models"]), 1)
        self.assertEqual(created["models"][0]["thinking"], "high")
        self.assertEqual(created["models"][0]["thinking_levels"], ["none", "high"])

        providers = self.client.get("/api/providers").json()["providers"]
        self.assertIn("gpt-test", [item["model"] for item in providers[0]["models"]])

        # 更新时不传 key，旧 key 应保留；模型列表整体更新
        updated = self.client.post("/api/providers", json={
            "id": created["id"],
            "provider": "openai",
            "name": "改名",
            "base_url": "https://example.com/v1",
            "models": [
                {"id": created["models"][0]["id"], "model": "gpt-test", "name": "GPT Test"},
                {"model": "gpt-2", "name": "GPT 2", "thinking": "max"},
            ],
        }).json()["provider"]
        self.assertEqual(updated["name"], "改名")
        self.assertTrue(updated["api_key_set"])
        self.assertEqual([item["model"] for item in updated["models"]], ["gpt-test", "gpt-2"])

        second = next(item for item in updated["models"] if item["model"] == "gpt-2")
        activated = self.client.post(
            f"/api/providers/{created['id']}/activate",
            json={"model_id": second["id"]},
        ).json()
        self.assertEqual(activated["active_model_id"], second["id"])
        self.assertEqual(activated["active_model"]["thinking"], "max")

        changed = self.client.patch(
            f"/api/providers/{created['id']}/models/{second['id']}/thinking",
            json={"thinking": "low"},
        ).json()
        self.assertEqual(changed["active_model"]["thinking"], "low")
        self.assertEqual(changed["active_model"]["thinking_levels"], ["low", "high", "max"])

        self.client.delete(f"/api/providers/{created['id']}")
        self.assertEqual(self.client.get("/api/providers").json()["providers"], [])

    def test_provider_requires_base_url_key_and_models(self):
        self.assertEqual(self.client.post("/api/providers", json={
            "provider": "custom", "base_url": "", "api_key": "sk-test",
            "models": [{"model": "m"}],
        }).status_code, 400)
        self.assertEqual(self.client.post("/api/providers", json={
            "provider": "custom", "base_url": "https://example.test/v1", "models": [{"model": "m"}],
        }).status_code, 400)
        self.assertEqual(self.client.post("/api/providers", json={
            "provider": "custom", "base_url": "https://example.test/v1",
            "api_key": "sk-test", "models": [{"model": ""}],
        }).status_code, 400)

    # ---- 工具 ----
    def test_tool_toggle_and_config(self):
        data = self.client.put("/api/tools/get_weather", json={
            "enabled": False,
            "config": {"QWEATHER_PROJECT_ID": "proj-123"},
        }).json()
        weather = next(tool for tool in data["tools"] if tool["name"] == "get_weather")
        self.assertFalse(weather["enabled"])
        project = next(field for field in weather["fields"] if field["key"] == "QWEATHER_PROJECT_ID")
        self.assertEqual(project["value"], "proj-123")

    def test_clear_secret_requires_explicit_request(self):
        """留空表示“保持不变”，所以清空密钥只能用 clear 显式请求。"""
        self.client.put("/api/tools/get_weather", json={"config": {"QWEATHER_KEY_ID": "test-key-id"}})
        self.client.put("/api/tools/get_weather", json={"clear": ["QWEATHER_KEY_ID"]})

        weather = next(
            tool for tool in self.client.get("/api/tools").json()["tools"]
            if tool["name"] == "get_weather"
        )
        fields = {field["key"]: field for field in weather["fields"]}
        self.assertFalse(fields["QWEATHER_KEY_ID"]["value_set"])
        self.assertEqual(self.app.state.tools.state()["get_weather"]["config"]["QWEATHER_KEY_ID"], "")

    def test_clear_ignores_unknown_keys(self):
        self.client.put("/api/tools/get_weather", json={"config": {"QWEATHER_KEY_ID": "keep"}})
        self.client.put("/api/tools/get_weather", json={"clear": ["NOT_A_FIELD"]})
        self.assertEqual(
            self.app.state.tools.state()["get_weather"]["config"]["QWEATHER_KEY_ID"], "keep"
        )

    def test_unknown_tool_returns_404(self):
        self.assertEqual(self.client.put("/api/tools/nope", json={"enabled": True}).status_code, 404)

    def test_secret_fields_are_masked_and_empty_save_keeps_value(self):
        pem = "-----BEGIN PRIVATE KEY-----\nABC\n-----END PRIVATE KEY-----"
        self.client.put("/api/tools/get_weather", json={
            "config": {"QWEATHER_KEY_ID": "test-key-id", "QWEATHER_PRIVATE_KEY": pem},
        })

        weather = next(
            tool for tool in self.client.get("/api/tools").json()["tools"]
            if tool["name"] == "get_weather"
        )
        fields = {field["key"]: field for field in weather["fields"]}

        # 密钥不回显，只给掩码提示
        self.assertEqual(fields["QWEATHER_KEY_ID"]["value"], "")
        self.assertTrue(fields["QWEATHER_KEY_ID"]["value_set"])
        self.assertEqual(fields["QWEATHER_KEY_ID"]["hint"], "••••y-id")
        self.assertNotIn("PRIVATE KEY", json.dumps(weather, ensure_ascii=False))

        # 前端留空再保存（或回传掩码）不能把真实密钥清空
        self.client.put("/api/tools/get_weather", json={
            "config": {"QWEATHER_KEY_ID": "", "QWEATHER_PRIVATE_KEY": "••••"},
        })
        stored = self.app.state.tools.state()["get_weather"]["config"]
        self.assertEqual(stored["QWEATHER_KEY_ID"], "test-key-id")
        self.assertEqual(stored["QWEATHER_PRIVATE_KEY"], pem)

    # ---- 工作区 ----
    def test_workspace_add_rename_delete_and_browse(self):
        target = self.tmp / "project"
        (target / "sub" / "deep").mkdir(parents=True)

        created = self.client.post("/api/workspaces", json={"path": str(target)})
        self.assertEqual(created.status_code, 200)
        workspace = created.json()["workspace"]
        self.assertEqual(Path(workspace["path"]), target.resolve())
        self.assertEqual(workspace["name"], "project")
        self.assertEqual(created.json()["active_id"], workspace["id"])

        # 重复添加 / 不存在的目录
        self.assertEqual(self.client.post("/api/workspaces", json={"path": str(target)}).status_code, 400)
        self.assertEqual(self.client.post("/api/workspaces", json={"path": "Z:/nope"}).status_code, 400)

        listing = self.client.get("/api/workspaces/browse", params={"path": str(target)}).json()
        self.assertEqual([item["name"] for item in listing["dirs"]], ["sub"])

        renamed = self.client.patch(f"/api/workspaces/{workspace['id']}", json={"name": "我的项目"})
        self.assertEqual(renamed.status_code, 200)
        names = {item["id"]: item["name"] for item in renamed.json()["workspaces"]}
        self.assertEqual(names[workspace["id"]], "我的项目")

        deleted = self.client.delete(f"/api/workspaces/{workspace['id']}").json()
        self.assertNotIn(workspace["id"], [item["id"] for item in deleted["workspaces"]])

    def test_workspace_file_browser_api(self):
        self.create_workspace()
        (self.tmp / "README.md").write_text("# 标题\n\n正文", encoding="utf-8")
        (self.tmp / "binary.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")

        listing = self.client.get("/api/workspace/files")
        self.assertEqual(listing.status_code, 200)
        paths = {item["path"] for item in listing.json()["entries"]}
        self.assertIn("README.md", paths)

        preview = self.client.get("/api/workspace/file", params={"path": "README.md"})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["kind"], "markdown")
        self.assertIn("# 标题", preview.json()["content"])

        unsupported = self.client.get("/api/workspace/file", params={"path": "binary.png"})
        self.assertEqual(unsupported.status_code, 200)
        self.assertFalse(unsupported.json()["supported"])
        self.assertEqual(unsupported.json()["kind"], "unsupported")

    def test_delete_workspace_removes_its_sessions(self):
        from agents.context import SessionMeta

        workspace = self.client.post("/api/workspaces", json={"path": str(self.tmp)}).json()["workspace"]
        store = self.app.state.sessions
        store.save(
            "on-workspace",
            [HumanMessage(content="你好")],
            SessionMeta("on-workspace", workspace_id=workspace["id"]),
        )
        self.assertTrue(store.path("on-workspace").exists())

        result = self.client.delete(f"/api/workspaces/{workspace['id']}").json()
        self.assertEqual(result["removed_sessions"], 1)
        self.assertFalse(store.path("on-workspace").exists())

    # ---- 会话与对话 ----
    def test_chat_stream_events_and_history(self):
        self.create_provider()
        self.create_workspace()
        session = self.client.post("/api/sessions", json={}).json()["session"]
        payload = {"session_id": session["id"], "message": "你好"}

        events = []
        with self.client.stream("POST", "/api/chat", json=payload) as response:
            self.assertEqual(response.status_code, 200)
            buffer = ""
            for chunk in response.iter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    raw, buffer = buffer.split("\n\n", 1)
                    event, data = "message", ""
                    for line in raw.splitlines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data += line[5:].strip()
                    if data:
                        events.append((event, json.loads(data)))

        kinds = [kind for kind, _ in events]
        self.assertIn("meta", kinds)
        self.assertIn("delta", kinds)
        self.assertIn("done", kinds)
        done = dict(events)["done"]
        self.assertEqual(done["text"], "你好")

        sessions = self.client.get("/api/sessions").json()["sessions"]
        self.assertEqual(sessions[0]["id"], session["id"])
        self.assertEqual(sessions[0]["turns"], 1)
        self.assertEqual(sessions[0]["title"], "你好")

        history = self.client.get(f"/api/sessions/{session['id']}/messages").json()
        roles = [item["role"] for item in history["messages"]]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(history["messages"][1]["content"], "你好")

    def test_chat_stream_exposes_thought_as_sse_event(self):
        """工作步骤所需的思考内容必须作为独立 SSE 事件送达前端。"""
        app = create_app(
            data_dir=self.tmp / "thought-data",
            memory_dir=self.tmp / "thought-memory",
            runtime_factory=lambda *args, **kwargs: (StubThoughtStreamAgent(), None),
        )
        client = TestClient(app)
        client.post("/api/providers", json={
            "provider": "openai",
            "name": "测试供应商",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test-1234",
            "models": [{"model": "gpt-test", "thinking": "high"}],
        })
        client.post("/api/workspaces", json={"path": str(self.tmp)})

        with client.stream("POST", "/api/chat", json={"message": "检查项目"}) as response:
            body = "".join(response.iter_text())

        self.assertIn("event: thought", body)
        self.assertIn(
            json.dumps(
                {"id": "reasoning-1", "text": "先检查", "append": True},
                ensure_ascii=False,
            ),
            body,
        )
        self.assertIn(
            json.dumps(
                {"id": "reasoning-1", "text": "项目结构。", "append": True},
                ensure_ascii=False,
            ),
            body,
        )
        self.assertIn("event: done", body)

    def test_chat_accepts_null_session_id(self):
        """工作区里直接发送消息应自动创建会话，而不是要求先点新建。"""
        self.create_provider()
        workspace = self.create_workspace()
        events: list[tuple[str, dict]] = []
        with self.client.stream("POST", "/api/chat", json={
            "session_id": None,
            "message": "你好",
        }) as response:
            self.assertEqual(response.status_code, 200)
            buffer = ""
            for chunk in response.iter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    raw, buffer = buffer.split("\n\n", 1)
                    event, data = "message", ""
                    for line in raw.splitlines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data += line[5:].strip()
                    if data:
                        events.append((event, json.loads(data)))
        kinds = [kind for kind, _ in events]
        self.assertIn("meta", kinds)
        self.assertIn("done", kinds)
        session_id = dict(events)["meta"]["session_id"]
        self.assertTrue(session_id)
        self.assertNotEqual(session_id, "default")
        listed = self.client.get("/api/sessions").json()["sessions"]
        created = next(item for item in listed if item["id"] == session_id)
        self.assertEqual(created["workspace_id"], workspace["id"])

    def test_chat_rejects_empty_message(self):
        session = self.client.post("/api/sessions", json={}).json()["session"]
        response = self.client.post("/api/chat", json={"session_id": session["id"], "message": " "})
        self.assertEqual(response.status_code, 400)

    def test_delete_session(self):
        session = self.client.post("/api/sessions", json={}).json()["session"]
        self.client.delete(f"/api/sessions/{session['id']}")
        self.assertEqual(self.client.get("/api/sessions").json()["sessions"], [])

    def test_chat_binds_session_to_workspace(self):
        self.create_provider()
        workspace = self.create_workspace()
        workspace_id = workspace["id"]
        session = self.client.post("/api/sessions", json={"workspace_id": workspace_id}).json()["session"]
        self.assertEqual(session["workspace_id"], workspace_id)

        with self.client.stream("POST", "/api/chat", json={
            "session_id": session["id"],
            "message": "你好",
            "workspace_id": workspace_id,
        }) as response:
            for _ in response.iter_text():
                pass

        listed = self.client.get("/api/sessions").json()["sessions"]
        self.assertEqual(listed[0]["workspace_id"], workspace_id)

    # ---- 从上游获取模型 ID ----
    def test_fetch_returns_upstream_models_without_writing(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            @staticmethod
            def json():
                return {"data": [{"id": "m-a"}, {"id": "m-b"}]}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def get(url, headers=None):
                return FakeResponse()

        provider = self.create_provider()
        body = {
            "provider_id": provider["id"],
            "base_url": "https://example.test/v1",
            "api_key": "",
            "provider": "openai",
        }
        with patch("web.server.httpx.Client", FakeClient):
            data = self.client.post("/api/providers/fetch", json=body).json()
        self.assertEqual(data["models"], ["m-a", "m-b"])
        # 获取只是返回候选列表，不写库
        providers = self.client.get("/api/providers").json()["providers"]
        self.assertEqual(len(providers), 1)
        self.assertEqual([item["model"] for item in providers[0]["models"]], ["gpt-test"])


class UpstreamMetadataTest(unittest.TestCase):
    def test_openrouter_style_metadata_is_extracted(self):
        from web.server import _upstream_model_metadata

        meta = _upstream_model_metadata({
            "id": "vendor/model",
            "context_length": 200000,
            "architecture": {"input_modalities": ["text", "image"]},
            "top_provider": {"max_completion_tokens": 64000},
            "supported_parameters": ["reasoning", "tools"],
        })
        self.assertEqual(meta["context_length"], 200000)
        self.assertEqual(meta["max_output_tokens"], 64000)
        self.assertTrue(meta["supports_images"])
        self.assertTrue(meta["supports_reasoning"])

    def test_plain_models_response_has_no_metadata(self):
        from web.server import _upstream_model_metadata

        self.assertEqual(_upstream_model_metadata({"id": "plain-model"}), {})


class CreateChatModelTest(unittest.TestCase):
    def test_thinking_level_maps_to_reasoning_effort(self):
        from config.store import ModelConfig
        from web.runtime import create_chat_model

        cfg = ModelConfig(
            id="m",
            name="m",
            provider="openai",
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="sk-test",
            thinking="high",
        )
        model = create_chat_model(cfg)
        self.assertEqual(model.reasoning_effort, "high")

        cfg.thinking = "none"
        self.assertIsNone(create_chat_model(cfg).reasoning_effort)

        cfg.thinking = "high"
        cfg.supports_reasoning = False
        self.assertIsNone(create_chat_model(cfg).reasoning_effort)

        cfg.supports_reasoning = True
        cfg.max_output_tokens = 4096
        self.assertEqual(create_chat_model(cfg).max_tokens, 4096)


if __name__ == "__main__":
    unittest.main()
