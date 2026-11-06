# tests/tool_fields_test.py
"""工具参数的通用契约：只要在注册表里声明 fields，接口与设置面板就自动适配。

这些用例遍历 TOOL_SPECS，而不是针对某个具体工具，因此**新增工具会被自动覆盖**，
不需要再补测试。
"""

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from tools.context import ToolContext
from tools.registry import (
    FIELD_NUMBER,
    FIELD_SELECT,
    FIELD_SWITCH,
    TOOL_SPECS,
    ToolField,
    ToolSpec,
    build_tools,
)
from web.server import create_app


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"fields_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class ToolFieldContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = create_app(data_dir=self.tmp / "data", memory_dir=self.tmp / "memory")
        self.client = TestClient(self.app)

    def entry(self, tool_name: str) -> dict:
        tools = self.client.get("/api/tools").json()["tools"]
        return next(item for item in tools if item["name"] == tool_name)

    def test_every_declared_field_is_exposed_and_round_trips(self):
        """注册表里每个字段：该露的露、该藏的藏、存进去能读回来。"""
        declared = 0
        for name, spec in TOOL_SPECS.items():
            if not spec.fields:
                continue

            payload = {field.key: f"值-{index}" for index, field in enumerate(spec.fields)}
            response = self.client.put(f"/api/tools/{name}", json={"config": payload})
            self.assertEqual(response.status_code, 200)

            exposed = {field["key"]: field for field in self.entry(name)["fields"]}
            self.assertEqual(
                list(exposed),
                [field.key for field in spec.fields],
                f"{name} 的字段顺序应与注册表一致",
            )

            for field in spec.fields:
                item = exposed[field.key]
                if field.secret:
                    self.assertEqual(item["value"], "", f"{name}.{field.key} 不应回显")
                    self.assertTrue(item["value_set"], f"{name}.{field.key} 应标记已配置")
                    self.assertTrue(item["hint"], f"{name}.{field.key} 应给掩码提示")
                else:
                    self.assertEqual(item["value"], payload[field.key])
                declared += 1

        self.assertGreater(declared, 0, "注册表里没有任何带参数的工具")

    def test_non_secret_field_is_cleared_by_empty_value(self):
        for name, spec in TOOL_SPECS.items():
            field = next((item for item in spec.fields if not item.secret), None)
            if field is None:
                continue
            self.client.put(f"/api/tools/{name}", json={"config": {field.key: "x"}})
            self.client.put(f"/api/tools/{name}", json={"config": {field.key: ""}})
            exposed = {item["key"]: item for item in self.entry(name)["fields"]}
            self.assertEqual(exposed[field.key]["value"], "")

    def test_secret_field_survives_empty_value_and_can_be_cleared_explicitly(self):
        for name, spec in TOOL_SPECS.items():
            field = next((item for item in spec.fields if item.secret), None)
            if field is None:
                continue
            self.client.put(f"/api/tools/{name}", json={"config": {field.key: "secret-value"}})
            # 留空 = 保持原值
            self.client.put(f"/api/tools/{name}", json={"config": {field.key: ""}})
            exposed = {item["key"]: item for item in self.entry(name)["fields"]}
            self.assertTrue(exposed[field.key]["value_set"])
            # 显式清除
            self.client.put(f"/api/tools/{name}", json={"clear": [field.key]})
            exposed = {item["key"]: item for item in self.entry(name)["fields"]}
            self.assertFalse(exposed[field.key]["value_set"])

    def test_unknown_field_key_is_ignored(self):
        """前端不会传别的键，但接口不该因此把无关内容写进配置。"""
        for name, spec in TOOL_SPECS.items():
            if not spec.fields:
                continue
            self.client.put(f"/api/tools/{name}", json={"config": {"NOT_A_REAL_KEY": "x"}})
            stored = self.app.state.tools.state()[name]["config"]
            self.assertNotIn("NOT_A_REAL_KEY", stored)


class NewToolAutoAdaptTest(unittest.TestCase):
    """模拟「只往注册表加一个条目」：设置面板与接口应自动出现它。"""

    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = create_app(data_dir=self.tmp / "data", memory_dir=self.tmp / "memory")
        self.client = TestClient(self.app)
        # 先把现有配置落盘，模拟「老用户已有 tools.json」
        self.client.get("/api/bootstrap")

    def test_spec_added_later_shows_up_without_touching_data(self):
        spec = ToolSpec(
            name="demo_tool",
            label="示例工具",
            description="用于验证自动适配。",
            fields=(
                ToolField("DEMO_TOKEN", "Token", secret=True),
                ToolField("DEMO_REGION", "Region", placeholder="cn-north"),
            ),
            tool_names=("demo_tool",),
        )
        with patch.dict(TOOL_SPECS, {"demo_tool": spec}, clear=False):
            bootstrap = self.client.get("/api/bootstrap").json()
            entry = next(item for item in bootstrap["tools"] if item["name"] == "demo_tool")

        self.assertTrue(entry["enabled"])  # default_enabled 生效
        self.assertEqual([field["key"] for field in entry["fields"]], ["DEMO_TOKEN", "DEMO_REGION"])
        self.assertEqual(entry["fields"][1]["placeholder"], "cn-north")
        self.assertEqual(entry["fields"][0]["hint"], "")  # 未配置时没有掩码

    def test_new_spec_is_written_to_existing_tools_json(self):
        spec = ToolSpec(
            name="demo_tool_2",
            label="示例工具 2",
            description="写盘验证。",
            fields=(ToolField("DEMO_KEY", "Key"),),
        )
        with patch.dict(TOOL_SPECS, {"demo_tool_2": spec}, clear=False):
            self.client.get("/api/bootstrap")

        import json

        saved = json.loads((self.tmp / "data" / "tools.json").read_text(encoding="utf-8"))
        self.assertIn("demo_tool_2", saved["tools"])
        self.assertTrue(saved["tools"]["demo_tool_2"]["enabled"])


class FieldKindTest(unittest.TestCase):
    """select / number / switch 三种字段形态的序列化与校验。"""

    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = create_app(data_dir=self.tmp / "data", memory_dir=self.tmp / "memory")
        self.client = TestClient(self.app)
        self.client.get("/api/bootstrap")
        self.spec = ToolSpec(
            name="demo_kinds",
            label="字段类型",
            description="验证 select / number / switch。",
            fields=(
                ToolField("MODE", "模式", kind=FIELD_SELECT, options=(("fast", "快速"), ("full", "完整"))),
                ToolField("LIMIT", "上限", kind=FIELD_NUMBER),
                ToolField("ENABLED", "开关", kind=FIELD_SWITCH, default="false"),
                ToolField("NOTE", "备注", required=True),
            ),
        )

    def entry(self) -> dict:
        with patch.dict(TOOL_SPECS, {"demo_kinds": self.spec}, clear=False):
            data = self.client.get("/api/tools").json()
        return next(item for item in data["tools"] if item["name"] == "demo_kinds")

    def test_public_exposes_kind_options_and_required(self):
        fields = {item["key"]: item for item in self.entry()["fields"]}
        self.assertEqual(fields["MODE"]["kind"], "select")
        self.assertEqual([item["value"] for item in fields["MODE"]["options"]], ["fast", "full"])
        self.assertEqual(fields["LIMIT"]["kind"], "number")
        self.assertEqual(fields["ENABLED"]["kind"], "switch")
        self.assertTrue(fields["NOTE"]["required"])
        self.assertFalse(fields["MODE"]["required"])
        # 未保存过时取 default
        self.assertEqual(fields["ENABLED"]["value"], "false")

    def test_valid_values_are_saved(self):
        with patch.dict(TOOL_SPECS, {"demo_kinds": self.spec}, clear=False):
            response = self.client.put("/api/tools/demo_kinds", json={
                "config": {"MODE": "fast", "LIMIT": "3.5", "ENABLED": "1", "NOTE": "hi"},
            })
            self.assertEqual(response.status_code, 200)
            stored = self.app.state.tools.state()["demo_kinds"]["config"]
        self.assertEqual(stored["MODE"], "fast")
        self.assertEqual(stored["LIMIT"], "3.5")
        self.assertEqual(stored["ENABLED"], "true")  # 规范化
        self.assertEqual(stored["NOTE"], "hi")

    def test_invalid_select_is_rejected(self):
        with patch.dict(TOOL_SPECS, {"demo_kinds": self.spec}, clear=False):
            response = self.client.put("/api/tools/demo_kinds", json={"config": {"MODE": "slow"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("模式", response.json()["detail"])

    def test_invalid_number_is_rejected(self):
        with patch.dict(TOOL_SPECS, {"demo_kinds": self.spec}, clear=False):
            response = self.client.put("/api/tools/demo_kinds", json={"config": {"LIMIT": "abc"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("数字", response.json()["detail"])


class MockToolTest(unittest.TestCase):
    """模拟工具：配置的文本能原样交给 agent。"""

    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = create_app(data_dir=self.tmp / "data", memory_dir=self.tmp / "memory")
        self.client = TestClient(self.app)
        self.client.get("/api/bootstrap")

    def build(self):
        tools = build_tools(
            {"mock_data"},
            self.app.state.tools.configs(),
            ToolContext(workspace=Path.cwd()),
        )
        return {tool.name: tool for tool in tools}["mock_lookup"]

    def test_disabled_by_default(self):
        tools = {item["name"]: item for item in self.client.get("/api/tools").json()["tools"]}
        self.assertFalse(tools["mock_data"]["enabled"])

    def test_agent_gets_configured_text(self):
        self.client.put("/api/tools/mock_data", json={
            "enabled": True,
            "config": {"MOCK_TEXT": "这是测试用的自定义内容"},
        })
        text = self.build().invoke({"query": "测试"})
        self.assertIn("这是测试用的自定义内容", text)
        self.assertIn("测试", text)

    def test_empty_text_returns_error(self):
        self.client.put("/api/tools/mock_data", json={"enabled": True, "config": {"MOCK_TEXT": ""}})
        text = self.build().invoke({})
        self.assertTrue(text.startswith("Error: [missing_config]"), text)


if __name__ == "__main__":
    unittest.main()
