# tests/files_test.py
"""文件读写工具的测试：重点覆盖工作区边界与「读后写」约束。"""

import os
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.files import build_file_read_tools, build_file_write_tools


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"files_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class FilesTestBase(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.outside = self.root.parent / f"outside_{uuid4().hex[:8]}"
        self.outside.mkdir()
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)

        ctx = ToolContext(workspace=self.root, max_output_chars=2000, command_timeout=10)
        self.read = {tool.name: tool for tool in build_file_read_tools(ctx)}
        self.write = {tool.name: tool for tool in build_file_write_tools(ctx)}


class ReadToolsTest(FilesTestBase):
    def test_list_dir_marks_directories_and_sizes(self):
        (self.root / "src").mkdir()
        (self.root / "note.txt").write_text("hello", encoding="utf-8")

        text = self.read["list_dir"].invoke({"path": "."})
        self.assertIn("src/", text)
        self.assertIn("note.txt", text)
        self.assertIn("2 项", text)

    def test_read_file_returns_numbered_lines(self):
        (self.root / "a.txt").write_text("第一行\n第二行\n第三行\n", encoding="utf-8")

        text = self.read["read_file"].invoke({"path": "a.txt"})
        self.assertIn("共 3 行", text)
        self.assertIn("1| 第一行", text)
        self.assertIn("3| 第三行", text)

    def test_read_file_respects_offset_and_limit(self):
        (self.root / "long.txt").write_text(
            "\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8"
        )

        text = self.read["read_file"].invoke({"path": "long.txt", "offset": 4, "limit": 3})
        self.assertIn("4| line4", text)
        self.assertIn("6| line6", text)
        self.assertNotIn("line7", text)
        self.assertIn("offset=7", text)

    def test_read_missing_file(self):
        text = self.read["read_file"].invoke({"path": "nope.txt"})
        self.assertTrue(text.startswith("Error: [not_found]"), text)

    def test_read_binary_file(self):
        (self.root / "bin.dat").write_bytes(b"abc\x00def")
        text = self.read["read_file"].invoke({"path": "bin.dat"})
        self.assertTrue(text.startswith("Error: [binary_file]"), text)

    def test_offset_out_of_range(self):
        (self.root / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        text = self.read["read_file"].invoke({"path": "a.txt", "offset": 99})
        self.assertTrue(text.startswith("Error: [offset_out_of_range]"), text)

    def test_search_files_finds_and_filters(self):
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "a.py").write_text("def build_agent():\n    pass\n", encoding="utf-8")
        (self.root / "pkg" / "b.md").write_text("def build_agent\n", encoding="utf-8")

        text = self.read["search_files"].invoke({"pattern": "def build_", "glob": "*.py"})
        self.assertIn("pkg/a.py:1", text)
        self.assertNotIn("b.md", text)

    def test_search_files_bad_pattern(self):
        text = self.read["search_files"].invoke({"pattern": "([utf"})
        self.assertTrue(text.startswith("Error: [bad_pattern]"), text)

    def test_find_files_by_name_and_glob(self):
        (self.root / "src" / "deep").mkdir(parents=True)
        (self.root / "src" / "main.py").write_text("x", encoding="utf-8")
        (self.root / "src" / "deep" / "agent_test.py").write_text("x", encoding="utf-8")
        (self.root / "README.md").write_text("# readme", encoding="utf-8")

        by_glob = self.read["find_files"].invoke({"pattern": "**/*.py"})
        self.assertIn("src/main.py", by_glob)
        self.assertIn("src/deep/agent_test.py", by_glob)
        self.assertNotIn("README.md", by_glob)

        by_name = self.read["find_files"].invoke({"pattern": "agent"})
        self.assertIn("agent_test.py", by_name)
        self.assertNotIn("main.py", by_name)

    def test_find_files_rejects_escape(self):
        text = self.read["find_files"].invoke({"pattern": "*.py", "path": ".."})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)

    def test_output_is_truncated(self):
        (self.root / "big.txt").write_text(
            "\n".join("x" * 200 for _ in range(200)), encoding="utf-8"
        )
        text = self.read["read_file"].invoke({"path": "big.txt", "limit": 200})
        self.assertLessEqual(len(text), 2000 + 100)
        self.assertIn("已截断", text)


class WorkspaceBoundaryTest(FilesTestBase):
    def test_relative_escape_is_rejected(self):
        text = self.read["read_file"].invoke({"path": "../outside.txt"})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)

    def test_absolute_outside_is_rejected(self):
        target = self.outside / "secret.txt"
        target.write_text("secret", encoding="utf-8")

        text = self.read["read_file"].invoke({"path": str(target)})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)

    def test_write_outside_is_rejected(self):
        target = self.outside / "new.txt"
        text = self.write["write_file"].invoke({"path": str(target), "content": "x"})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)
        self.assertFalse(target.exists())

    def test_similar_prefix_directory_is_not_inside(self):
        """D:\\work 不应把 D:\\workspace-evil 当成自己的子目录。"""
        sibling = self.root.parent / (self.root.name + "-evil")
        sibling.mkdir()
        self.addCleanup(shutil.rmtree, sibling, ignore_errors=True)
        target = sibling / "x.txt"
        target.write_text("x", encoding="utf-8")

        text = self.read["read_file"].invoke({"path": str(target)})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)

    def test_no_workspace(self):
        tools = {t.name: t for t in build_file_read_tools(ToolContext(workspace=None))}
        text = tools["read_file"].invoke({"path": "a.txt"})
        self.assertTrue(text.startswith("Error: [no_workspace]"), text)


class WriteToolsTest(FilesTestBase):
    def test_write_creates_new_file_and_parents(self):
        text = self.write["write_file"].invoke({"path": "pkg/sub/new.txt", "content": "hi"})
        self.assertIn("已创建", text)
        self.assertIn("变更：+1 -0", text)
        self.assertIn("校验：文件已存在", text)
        self.assertEqual((self.root / "pkg" / "sub" / "new.txt").read_text(encoding="utf-8"), "hi")

    def test_overwrite_requires_read_first(self):
        target = self.root / "exist.txt"
        target.write_text("old", encoding="utf-8")

        blocked = self.write["write_file"].invoke({"path": "exist.txt", "content": "new"})
        self.assertTrue(blocked.startswith("Error: [read_required]"), blocked)
        self.assertEqual(target.read_text(encoding="utf-8"), "old")

        self.read["read_file"].invoke({"path": "exist.txt"})
        allowed = self.write["write_file"].invoke({"path": "exist.txt", "content": "new"})
        self.assertIn("已覆盖", allowed)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_write_after_external_change_is_stale(self):
        target = self.root / "race.txt"
        target.write_text("v1", encoding="utf-8")
        self.read["read_file"].invoke({"path": "race.txt"})

        target.write_text("v2", encoding="utf-8")
        os.utime(target, None)  # 确保 mtime 变化

        text = self.write["write_file"].invoke({"path": "race.txt", "content": "v3"})
        self.assertTrue(text.startswith("Error: [stale_read]"), text)
        self.assertEqual(target.read_text(encoding="utf-8"), "v2")

    def test_edit_file_replaces_unique_match(self):
        target = self.root / "a.py"
        target.write_text("def f():\n    return 1\n", encoding="utf-8")
        self.read["read_file"].invoke({"path": "a.py"})

        text = self.write["edit_file"].invoke({
            "path": "a.py", "old_string": "return 1", "new_string": "return 2",
        })
        self.assertIn("替换 1 处", text)
        self.assertIn("变更：+1 -1", text)
        self.assertEqual(target.read_text(encoding="utf-8"), "def f():\n    return 2\n")

    def test_edit_file_requires_read(self):
        target = self.root / "b.py"
        target.write_text("x = 1\n", encoding="utf-8")
        text = self.write["edit_file"].invoke({
            "path": "b.py", "old_string": "x = 1", "new_string": "x = 2",
        })
        self.assertTrue(text.startswith("Error: [read_required]"), text)

    def test_edit_file_ambiguous_match(self):
        target = self.root / "c.py"
        target.write_text("x = 1\nx = 1\n", encoding="utf-8")
        self.read["read_file"].invoke({"path": "c.py"})

        text = self.write["edit_file"].invoke({
            "path": "c.py", "old_string": "x = 1", "new_string": "x = 2",
        })
        self.assertTrue(text.startswith("Error: [ambiguous_match]"), text)
        self.assertIn("2 次", text)

        replaced = self.write["edit_file"].invoke({
            "path": "c.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True,
        })
        self.assertIn("替换 2 处", replaced)

    def test_edit_file_missing_old_string(self):
        target = self.root / "d.py"
        target.write_text("hello\n", encoding="utf-8")
        self.read["read_file"].invoke({"path": "d.py"})

        text = self.write["edit_file"].invoke({
            "path": "d.py", "old_string": "not-there", "new_string": "x",
        })
        self.assertTrue(text.startswith("Error: [not_found]"), text)


if __name__ == "__main__":
    unittest.main()
