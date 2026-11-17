# tests/patch_test.py
"""apply_patch tests: modify, create, delete, multiple files, and read-before-write constraints."""

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.files import build_file_read_tools
from tools.patch import build_patch_tools


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"patch_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class ApplyPatchTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ctx = ToolContext(workspace=self.root, max_output_chars=5000)
        self.read = {tool.name: tool for tool in build_file_read_tools(self.ctx)}
        self.patch = build_patch_tools(self.ctx)[0]

    def read_file(self, path: str) -> str:
        return self.read["read_file"].invoke({"path": path})

    def test_replaces_unique_hunk(self):
        target = self.root / "app.py"
        target.write_text("def main():\n    print('old')\n\nprint(main)\n", encoding="utf-8")
        self.read_file("app.py")
        text = self.patch.invoke({"patch": """--- a/app.py
+++ b/app.py
@@ -1,4 +1,4 @@
 def main():
-    print('old')
+    print('new')
 
 print(main)
"""})
        self.assertIn("+1 -1", text)
        self.assertIn("print('new')", target.read_text(encoding="utf-8"))

    def test_creates_new_file(self):
        text = self.patch.invoke({"patch": """--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
"""})
        self.assertIn("+2 -0", text)
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "hello\nworld\n")

    def test_deletes_file(self):
        target = self.root / "old.txt"
        target.write_text("bye\n", encoding="utf-8")
        self.read_file("old.txt")
        text = self.patch.invoke({"patch": """--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
"""})
        self.assertIn("deleted", text)
        self.assertFalse(target.exists())

    def test_requires_read_before_edit(self):
        (self.root / "a.txt").write_text("x\n", encoding="utf-8")
        text = self.patch.invoke({"patch": """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-x
+y
"""})
        self.assertTrue(text.startswith("Error: [read_required]"), text)

    def test_context_mismatch(self):
        (self.root / "a.txt").write_text("actual\n", encoding="utf-8")
        self.read_file("a.txt")
        text = self.patch.invoke({"patch": """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-expected
+y
"""})
        self.assertTrue(text.startswith("Error: [context_mismatch]"), text)

    def test_multiple_files(self):
        (self.root / "a.txt").write_text("a\n", encoding="utf-8")
        (self.root / "b.txt").write_text("b\n", encoding="utf-8")
        self.read_file("a.txt")
        self.read_file("b.txt")
        text = self.patch.invoke({"patch": """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-a
+A
--- a/b.txt
+++ b/b.txt
@@ -1 +1 @@
-b
+B
"""})
        self.assertIn("2 file(s)", text)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "A\n")
        self.assertEqual((self.root / "b.txt").read_text(encoding="utf-8"), "B\n")

    def test_rejects_path_escape(self):
        text = self.patch.invoke({"patch": """--- a/../outside.txt
+++ b/../outside.txt
@@ -1 +1 @@
-a
+b
"""})
        self.assertTrue(text.startswith("Error: [path_escape]"), text)

    def test_empty_patch(self):
        text = self.patch.invoke({"patch": "  "})
        self.assertTrue(text.startswith("Error: [empty_patch]"), text)


if __name__ == "__main__":
    unittest.main()
