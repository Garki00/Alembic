# tests/paths_test.py
"""Workspace path boundary and credential protection tests."""

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.files import build_file_read_tools, build_file_write_tools
from tools.paths import WorkspacePathError, resolve_in_workspace


def make_root() -> Path:
    base = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"paths_{uuid4().hex[:12]}"
    root.mkdir()
    return root


class ProtectedPathTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (self.root / ".ssh").mkdir()
        (self.root / ".ssh" / "id_rsa").write_text("PRIVATE", encoding="utf-8")
        (self.root / ".env").write_text("DEEPSEEK_API_KEY=sk-x\n", encoding="utf-8")
        (self.root / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
        (self.root / "server.pem").write_text("PEM", encoding="utf-8")

    def test_normal_path_allowed(self):
        target = resolve_in_workspace(self.root, "src/app.py")
        self.assertEqual(target, (self.root / "src" / "app.py").resolve())

    def test_escape_still_rejected(self):
        with self.assertRaises(WorkspacePathError) as caught:
            resolve_in_workspace(self.root, "../outside.txt")
        self.assertEqual(caught.exception.code, "path_escape")

    def test_read_blocks_credentials(self):
        for name in (".env", ".ssh/id_rsa", "server.pem"):
            with self.assertRaises(WorkspacePathError) as caught:
                resolve_in_workspace(self.root, name)
            self.assertEqual(caught.exception.code, "protected_path", name)

    def test_env_template_is_readable(self):
        target = resolve_in_workspace(self.root, ".env.example")
        self.assertTrue(str(target).endswith(".env.example"))

    def test_env_can_be_written_but_not_read(self):
        target = resolve_in_workspace(self.root, ".env", access="write")
        self.assertTrue(str(target).endswith(".env"))
        with self.assertRaises(WorkspacePathError):
            resolve_in_workspace(self.root, ".env", access="read")

    def test_sensitive_directories_cannot_be_written(self):
        with self.assertRaises(WorkspacePathError) as caught:
            resolve_in_workspace(self.root, ".ssh/config", access="write")
        self.assertEqual(caught.exception.code, "protected_path")

    def test_tools_enforce_the_same_boundary(self):
        ctx = ToolContext(workspace=self.root, max_output_chars=4000)
        reads = {tool.name: tool for tool in build_file_read_tools(ctx)}
        writes = {tool.name: tool for tool in build_file_write_tools(ctx)}

        text = reads["read_file"].invoke({"path": ".env"})
        self.assertTrue(text.startswith("Error: [protected_path]"), text)

        text = writes["write_file"].invoke({"path": ".ssh/config", "content": "x"})
        self.assertTrue(text.startswith("Error: [protected_path]"), text)

        text = writes["write_file"].invoke({"path": ".env.local", "content": "A=1\n"})
        self.assertFalse(text.startswith("Error:"), text)


if __name__ == "__main__":
    unittest.main()
