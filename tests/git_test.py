# tests/git_test.py
"""Git tool tests: status, diff, and commit (using a temporary repo, never the project repo)."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.git import build_git_tools


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"git_{uuid4().hex[:12]}"
    path.mkdir()
    return path


@unittest.skipUnless(shutil.which("git"), "git 未安装")
class GitToolsTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "init")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.ctx = ToolContext(workspace=self.root, max_output_chars=5000)
        self.tools = {tool.name: tool for tool in build_git_tools(self.ctx)}

    def git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def test_status_clean_then_modified(self):
        text = self.tools["git_status"].invoke({})
        self.assertIn("Working tree clean", text)
        (self.root / "a.txt").write_text("two\n", encoding="utf-8")
        text = self.tools["git_status"].invoke({})
        self.assertIn("a.txt", text)
        self.assertIn("Modified", text)

    def test_diff_and_staged(self):
        (self.root / "a.txt").write_text("two\n", encoding="utf-8")
        text = self.tools["git_diff"].invoke({})
        self.assertIn("-one", text)
        self.assertIn("+two", text)
        text = self.tools["git_diff"].invoke({"path": "a.txt"})
        self.assertIn("a.txt", text)

    def test_commit_with_files(self):
        (self.root / "a.txt").write_text("two\n", encoding="utf-8")
        text = self.tools["git_commit"].invoke({"message": "update a", "files": ["a.txt"]})
        self.assertIn("Committed:", text)
        self.assertIn("update a", self.git("log", "-1", "--pretty=%s"))

    def test_commit_without_staged_changes(self):
        text = self.tools["git_commit"].invoke({"message": "nothing"})
        self.assertTrue(text.startswith("Error: [nothing_staged]"), text)

    def test_not_a_repo(self):
        plain = Path(tempfile.mkdtemp(prefix="alembic-norepo-"))
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        tools = {tool.name: tool for tool in build_git_tools(ToolContext(workspace=plain))}
        text = tools["git_status"].invoke({})
        self.assertTrue(text.startswith("Error: [not_a_git_repo]"), text)


if __name__ == "__main__":
    unittest.main()
