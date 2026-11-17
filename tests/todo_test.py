# tests/todo_test.py
"""Todo list tool tests: validation, persistence, session isolation, and event push."""

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import SessionRef, ToolContext
from tools.todo import TodoStore, build_todo_tools, normalize_todos, render_todos


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"todo_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class TodoTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = TodoStore(self.root / "todos")
        self.events: list[tuple[str, dict]] = []

    def tool(self, session_id: str = "s1"):
        ctx = ToolContext(
            workspace=self.root,
            session=SessionRef(session_id),
            todo_store=self.store,
            emit=lambda kind, data: self.events.append((kind, data)),
        )
        return next(t for t in build_todo_tools(ctx) if t.name == "todo_write")

    def test_write_renders_and_persists(self):
        text = self.tool().invoke({
            "todos": [
                {"content": "读取配置", "status": "completed"},
                {"content": "修改代码", "status": "in_progress"},
                {"content": "跑测试", "status": "pending"},
            ]
        })
        self.assertIn("Todo list: 1/3 completed", text)
        self.assertIn("[x] 1. 读取配置", text)
        self.assertIn("[~] 2. 修改代码", text)
        self.assertIn("[ ] 3. 跑测试", text)
        self.assertEqual(self.events[0][0], "todo")

        path = self.root / "todos" / "s1.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["session_id"], "s1")
        self.assertEqual(len(saved["items"]), 3)
        self.assertEqual(self.store.load("s1")[1]["status"], "in_progress")

    def test_empty_list_clears(self):
        self.tool().invoke({"todos": [{"content": "a", "status": "pending"}]})
        text = self.tool().invoke({"todos": []})
        self.assertIn("Todo list: 0/0 completed", text)
        self.assertEqual(self.store.load("s1"), [])

    def test_sessions_are_isolated(self):
        self.tool("a").invoke({"todos": [{"content": "A", "status": "pending"}]})
        self.tool("b").invoke({"todos": [{"content": "B", "status": "pending"}]})
        self.assertEqual(self.store.load("a")[0]["content"], "A")
        self.assertEqual(self.store.load("b")[0]["content"], "B")

    def test_clear_removes_file(self):
        self.tool().invoke({"todos": [{"content": "a", "status": "pending"}]})
        self.store.clear("s1")
        self.assertEqual(self.store.load("s1"), [])
        self.assertFalse((self.root / "todos" / "s1.json").exists())

    def test_validation_errors(self):
        tool = self.tool()
        no_content = tool.invoke({"todos": [{"status": "pending"}]})
        self.assertTrue(no_content.startswith("Error: [bad_todos]"), no_content)

        bad_status = tool.invoke({"todos": [{"content": "a", "status": "unknown"}]})
        self.assertTrue(bad_status.startswith("Error: [bad_todos]"), bad_status)

        two_active = tool.invoke({
            "todos": [
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ]
        })
        self.assertTrue(two_active.startswith("Error: [bad_todos]"), two_active)

    def test_status_aliases_and_render(self):
        items = normalize_todos([
            {"content": "a", "status": "done"},
            {"content": "b", "status": "doing"},
            {"content": "c", "status": "待办"},
        ])
        self.assertEqual([item["status"] for item in items], ["completed", "in_progress", "pending"])
        self.assertIn("1/3", render_todos(items))

    def test_load_ignores_corrupt_file(self):
        path = self.root / "todos" / "bad.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.load("bad"), [])


if __name__ == "__main__":
    unittest.main()
