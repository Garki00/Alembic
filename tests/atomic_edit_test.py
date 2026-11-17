# tests/atomic_edit_test.py
"""Atomic batch editing tool: success, rollback on failure, unique matching, and read-before-write."""

import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tools.atomic_edit import build_atomic_edit_tools
from tools.context import ToolContext
from tools.files import build_file_read_tools


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"atomic_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class AtomicEditTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ctx = ToolContext(workspace=self.root, max_output_chars=5000)
        self.tools = {
            tool.name: tool
            for tool in [*build_file_read_tools(self.ctx), *build_atomic_edit_tools(self.ctx)]
        }

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def read(self, name: str) -> str:
        return self.tools["read_file"].invoke({"path": name, "limit": 2000})

    def batch(self, edits: list[dict]) -> str:
        return self.tools["batch_edit"].invoke({"edits": edits})

    def test_multiple_files_are_changed_in_one_call(self):
        self.write("a.html", "<div>one</div>\n<p>two</p>\n")
        self.write("b.txt", "red\nblue\n")
        self.read("a.html")
        self.read("b.txt")

        result = self.batch([
            {"path": "a.html", "old_string": "<div>one</div>", "new_string": "<div>ONE</div>"},
            {"path": "a.html", "old_string": "<p>two</p>", "new_string": "<p>TWO</p>"},
            {"path": "b.txt", "old_string": "red", "new_string": "RED"},
            {"path": "b.txt", "old_string": "blue", "new_string": "BLUE"},
        ])

        self.assertIn("Atomically edited 2 file(s) with 4 replacement(s)", result)
        self.assertEqual(
            (self.root / "a.html").read_text(encoding="utf-8"),
            "<div>ONE</div>\n<p>TWO</p>\n",
        )
        self.assertEqual((self.root / "b.txt").read_text(encoding="utf-8"), "RED\nBLUE\n")

    def test_any_missing_replacement_writes_nothing(self):
        self.write("a.html", "ok\n")
        self.read("a.html")

        result = self.batch([
            {"path": "a.html", "old_string": "ok", "new_string": "changed"},
            {"path": "a.html", "old_string": "missing", "new_string": "x"},
        ])

        self.assertTrue(result.startswith("Error: [not_found]"), result)
        self.assertEqual((self.root / "a.html").read_text(encoding="utf-8"), "ok\n")

    def test_ambiguous_replacement_is_rejected(self):
        self.write("a.html", "<p>x</p>\n<p>x</p>\n")
        self.read("a.html")

        result = self.batch([
            {"path": "a.html", "old_string": "<p>x</p>", "new_string": "<p>y</p>"},
        ])

        self.assertTrue(result.startswith("Error: [ambiguous_match]"), result)
        self.assertEqual(
            (self.root / "a.html").read_text(encoding="utf-8"),
            "<p>x</p>\n<p>x</p>\n",
        )

    def test_replace_all(self):
        self.write("a.html", "x\nx\n")
        self.read("a.html")

        result = self.batch([
            {"path": "a.html", "old_string": "x", "new_string": "y", "replace_all": True},
        ])

        self.assertIn("2 replacement(s)", result)
        self.assertEqual((self.root / "a.html").read_text(encoding="utf-8"), "y\ny\n")

    def test_read_is_required(self):
        self.write("a.html", "x\n")

        result = self.batch([
            {"path": "a.html", "old_string": "x", "new_string": "y"},
        ])

        self.assertTrue(result.startswith("Error: [read_required]"), result)
        self.assertEqual((self.root / "a.html").read_text(encoding="utf-8"), "x\n")

    def test_protected_git_path_is_rejected(self):
        self.write(".git/config", "x\n")
        self.read(".git/config")

        result = self.batch([
            {"path": ".git/config", "old_string": "x", "new_string": "y"},
        ])

        self.assertTrue(result.startswith("Error: [protected_path]"), result)
        self.assertEqual((self.root / ".git" / "config").read_text(encoding="utf-8"), "x\n")

    def test_commit_failure_rolls_back_previous_file(self):
        first = self.write("a.html", "A\n")
        second = self.write("b.html", "B\n")
        self.read("a.html")
        self.read("b.html")

        real_replace = os.replace
        calls = {"count": 0}

        def flaky_replace(src, dst):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated commit failure")
            return real_replace(src, dst)

        with patch("tools.atomic_edit.os.replace", side_effect=flaky_replace):
            result = self.batch([
                {"path": "a.html", "old_string": "A", "new_string": "A2"},
                {"path": "b.html", "old_string": "B", "new_string": "B2"},
            ])

        self.assertTrue(result.startswith("Error: [commit_failed]"), result)
        self.assertEqual(first.read_text(encoding="utf-8"), "A\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "B\n")

    def test_empty_edits_is_rejected(self):
        result = self.batch([])
        self.assertTrue(result.startswith("Error: [empty_edits]"), result)


if __name__ == "__main__":
    unittest.main()
