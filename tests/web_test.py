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


def stub_factory(model_cfg, enabled_tools, workspace, **kwargs):
    return StubStreamAgent(), None


class WebWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""})
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
        self.assertEqual(len(data["models"]), 1)
        self.assertTrue(data["active_id"])
        # 期望值从注册表派生：新增工具不需要改这条断言
        self.assertEqual({tool["name"] for tool in data["tools"]}, set(TOOL_SPECS))
        self.assertEqual(len(data["workspaces"]), 1)
        self.assertTrue(data["active_workspace_id"])
        self.assertEqual(Path(data["workspaces"][0]["path"]), Path.cwd().resolve())

    def test_tools_enabled_by_default(self):
        data = self.client.get("/api/bootstrap").json()
        states = {tool["name"]: tool["enabled"] for tool in data["tools"]}
        # 审批机制上线后，命令执行默认开启（执行前仍需确认）
        self.assertTrue(states["run_command"])
        self.assertTrue(states["file_read"])
        self.assertTrue(states["file_write"])
        for name in ("read_document", "fetch_url", "todo_write", "ask_user_question",
                     "background_process", "process_inspect"):
            self.assertTrue(states[name], name)

    def test_bootstrap_persists_new_tool_entries(self):
        """升级到新版本后，data/tools.json 里应补齐新增条目且不丢旧配置。"""
        self.client.put("/api/tools/get_weather", json={"config": {"QWEATHER_PROJECT_ID": "keep-me"}})
        self.client.get("/api/bootstrap")

        saved = json.loads((self.tmp / "data" / "tools.json").read_text(encoding="utf-8"))
        self.assertIn("file_read", saved["tools"])
        self.assertTrue(saved["tools"]["run_command"]["enabled"])
        self.assertEqual(saved.get("version"), 2)
        self.assertEqual(saved["tools"]["get_weather"]["config"]["QWEATHER_PROJECT_ID"], "keep-me")

    # ---- 模型 ----
    def test_model_crud_and_key_masking(self):
        created = self.client.post("/api/models", json={
            "name": "OpenAI 兼容",
            "provider": "openai",
            "model": "gpt-test",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test-1234",
            "temperature": 0.3,
        }).json()["model"]
        self.assertTrue(created["api_key_set"])
        self.assertEqual(created["api_key_hint"], "••••1234")
        self.assertNotIn("api_key", created)

        models = self.client.get("/api/models").json()["models"]
        self.assertIn("gpt-test", [item["model"] for item in models])

        # 更新时不传 key，旧 key 应保留
        updated = self.client.post("/api/models", json={
            "id": created["id"], "name": "改名", "provider": "openai",
            "model": "gpt-test", "base_url": "https://example.com/v1",
        }).json()["model"]
        self.assertEqual(updated["name"], "改名")
        self.assertTrue(updated["api_key_set"])

        self.client.post(f"/api/models/{created['id']}/activate")
        self.assertEqual(self.client.get("/api/models").json()["active_id"], created["id"])

        self.client.delete(f"/api/models/{created['id']}")
        self.assertNotIn(created["id"],
                         [item["id"] for item in self.client.get("/api/models").json()["models"]])

    def test_model_requires_model_or_name(self):
        response = self.client.post("/api/models", json={"name": "", "model": ""})
        self.assertEqual(response.status_code, 400)

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
        self.client.put("/api/tools/get_weather", json={"config": {"QWEATHER_KEY_ID": "T95D4DCEH6"}})
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
            "config": {"QWEATHER_KEY_ID": "T95D4DCEH6", "QWEATHER_PRIVATE_KEY": pem},
        })

        weather = next(
            tool for tool in self.client.get("/api/tools").json()["tools"]
            if tool["name"] == "get_weather"
        )
        fields = {field["key"]: field for field in weather["fields"]}

        # 密钥不回显，只给掩码提示
        self.assertEqual(fields["QWEATHER_KEY_ID"]["value"], "")
        self.assertTrue(fields["QWEATHER_KEY_ID"]["value_set"])
        self.assertEqual(fields["QWEATHER_KEY_ID"]["hint"], "••••CEH6")
        self.assertNotIn("PRIVATE KEY", json.dumps(weather, ensure_ascii=False))

        # 前端留空再保存（或回传掩码）不能把真实密钥清空
        self.client.put("/api/tools/get_weather", json={
            "config": {"QWEATHER_KEY_ID": "", "QWEATHER_PRIVATE_KEY": "••••"},
        })
        stored = self.app.state.tools.state()["get_weather"]["config"]
        self.assertEqual(stored["QWEATHER_KEY_ID"], "T95D4DCEH6")
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

    def test_chat_rejects_empty_message(self):
        session = self.client.post("/api/sessions", json={}).json()["session"]
        response = self.client.post("/api/chat", json={"session_id": session["id"], "message": " "})
        self.assertEqual(response.status_code, 400)

    def test_delete_session(self):
        session = self.client.post("/api/sessions", json={}).json()["session"]
        self.client.delete(f"/api/sessions/{session['id']}")
        self.assertEqual(self.client.get("/api/sessions").json()["sessions"], [])

    def test_chat_binds_session_to_workspace(self):
        workspace_id = self.client.get("/api/bootstrap").json()["active_workspace_id"]
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

    # ---- 从上游导入模型 ----
    def test_fetch_imports_all_upstream_models(self):
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

        body = {"base_url": "https://example.test/v1", "api_key": "sk-upstream", "provider": "openai"}
        with patch("web.server.httpx.Client", FakeClient):
            first = self.client.post("/api/models/fetch", json=body).json()
        self.assertEqual(first["added"], ["m-a", "m-b"])
        self.assertEqual(first["skipped"], [])

        imported = {item["model"] for item in self.client.get("/api/models").json()["models"]}
        self.assertIn("m-a", imported)
        self.assertIn("m-b", imported)

        # 重复导入应全部跳过，不产生重复条目
        with patch("web.server.httpx.Client", FakeClient):
            second = self.client.post("/api/models/fetch", json=body).json()
        self.assertEqual(second["added"], [])
        self.assertEqual(second["skipped"], ["m-a", "m-b"])


if __name__ == "__main__":
    unittest.main()
