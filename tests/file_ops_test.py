# tests/file_ops_test.py
"""File lifecycle tool tests: move, copy, delete, directory creation, and metadata."""

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.files import build_file_read_tools, build_file_write_tools


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"ops_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class FileOpsTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        ctx = ToolContext(workspace=self.root, max_output_chars=4000)
        self.read = {tool.name: tool for tool in build_file_read_tools(ctx)}
        self.write = {tool.name: tool for tool in build_file_write_tools(ctx)}

    def test_make_dir_and_file_stat(self):
        text = self.write["make_dir"].invoke({"path": "a/b/c"})
        self.assertIn("Created directory:", text)
        self.assertTrue((self.root / "a" / "b" / "c").is_dir())

        (self.root / "note.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        stat = self.read["file_stat"].invoke({"path": "note.txt"})
        self.assertIn("Type: file", stat)
        self.assertIn("Lines: 3", stat)
        self.assertIn("bytes", stat)

        directory = self.read["file_stat"].invoke({"path": "a"})
        self.assertIn("Type: directory", directory)

    def test_make_dir_on_existing_file_fails(self):
        (self.root / "file.txt").write_text("x", encoding="utf-8")
        text = self.write["make_dir"].invoke({"path": "file.txt"})
        self.assertTrue(text.startswith("Error: [not_a_directory]"), text)

    def test_move_renames_file(self):
        (self.root / "old.txt").write_text("hello", encoding="utf-8")
        text = self.write["move_file"].invoke({"source": "old.txt", "destination": "new.txt"})
        self.assertIn("Moved:", text)
        self.assertFalse((self.root / "old.txt").exists())
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "hello")

    def test_move_refuses_existing_destination_without_overwrite(self):
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        (self.root / "b.txt").write_text("b", encoding="utf-8")
        text = self.write["move_file"].invoke({"source": "a.txt", "destination": "b.txt"})
        self.assertTrue(text.startswith("Error: [destination_exists]"), text)

    def test_move_overwrite_requires_read(self):
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        (self.root / "b.txt").write_text("b", encoding="utf-8")
        blocked = self.write["move_file"].invoke({
            "source": "a.txt", "destination": "b.txt", "overwrite": True,
        })
        self.assertTrue(blocked.startswith("Error: [read_required]"), blocked)

        self.read["read_file"].invoke({"path": "b.txt"})
        allowed = self.write["move_file"].invoke({
            "source": "a.txt", "destination": "b.txt", "overwrite": True,
        })
        self.assertIn("Moved:", allowed)
        self.assertEqual((self.root / "b.txt").read_text(encoding="utf-8"), "a")

    def test_move_directory_into_itself_is_rejected(self):
        (self.root / "src").mkdir()
        text = self.write["move_file"].invoke({
            "source": "src", "destination": "src/inside",
        })
        self.assertTrue(text.startswith("Error: [invalid_destination]"), text)

    def test_copy_file_and_directory(self):
        (self.root / "a.txt").write_text("hello", encoding="utf-8")
        text = self.write["copy_file"].invoke({"source": "a.txt", "destination": "b.txt"})
        self.assertIn("Copied:", text)
        self.assertEqual((self.root / "b.txt").read_text(encoding="utf-8"), "hello")

        (self.root / "src").mkdir()
        (self.root / "src" / "x.txt").write_text("x", encoding="utf-8")
        copied = self.write["copy_file"].invoke({"source": "src", "destination": "dst"})
        self.assertIn("Copied:", copied)
        self.assertEqual((self.root / "dst" / "x.txt").read_text(encoding="utf-8"), "x")

    def test_delete_file_and_recursive_directory(self):
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        text = self.write["delete_file"].invoke({"path": "a.txt"})
        self.assertIn("Deleted file:", text)
        self.assertFalse((self.root / "a.txt").exists())

        (self.root / "dir").mkdir()
        (self.root / "dir" / "x.txt").write_text("x", encoding="utf-8")
        blocked = self.write["delete_file"].invoke({"path": "dir"})
        self.assertTrue(blocked.startswith("Error: [directory_not_empty]"), blocked)

        self.write["delete_file"].invoke({"path": "dir", "recursive": True})
        self.assertFalse((self.root / "dir").exists())

    def test_delete_protects_root_and_git(self):
        root_text = self.write["delete_file"].invoke({"path": "."})
        self.assertTrue(root_text.startswith("Error: [protected_path]"), root_text)

        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("x", encoding="utf-8")
        git_text = self.write["delete_file"].invoke({"path": ".git", "recursive": True})
        self.assertTrue(git_text.startswith("Error: [protected_path]"), git_text)
        self.assertTrue((self.root / ".git").exists())

        write_text = self.write["write_file"].invoke({"path": ".git/config", "content": "y"})
        self.assertTrue(write_text.startswith("Error: [protected_path]"), write_text)
        move_text = self.write["move_file"].invoke({"source": ".git", "destination": "git-old"})
        self.assertTrue(move_text.startswith("Error: [protected_path]"), move_text)

    def test_operations_cannot_escape_workspace(self):
        outside = self.root.parent / f"outside_{uuid4().hex[:8]}"
        outside.mkdir()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "secret.txt").write_text("s", encoding="utf-8")

        for name, args in {
            "delete_file": {"path": str(outside / "secret.txt")},
            "move_file": {"source": "x.txt", "destination": str(outside / "x.txt")},
            "copy_file": {"source": str(outside / "secret.txt"), "destination": "y.txt"},
            "make_dir": {"path": str(outside / "newdir")},
        }.items():
            text = self.write[name].invoke(args)
            self.assertTrue(text.startswith("Error: [path_escape]"), f"{name}: {text}")
        self.assertTrue((outside / "secret.txt").exists())


if __name__ == "__main__":
    unittest.main()
