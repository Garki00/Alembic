# tests/images_test.py
"""read_image 的测试：视觉通道注入、格式校验与错误契约。"""

import base64
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.images import build_image_tools

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"images_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class ReadImageTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "shot.png").write_bytes(_PNG)

    def tool(self, vision=None):
        return build_image_tools(ToolContext(workspace=self.root, vision=vision))[0]

    def test_returns_vision_description(self):
        captured = {}

        def vision(data_url: str, prompt: str) -> str:
            captured["url"] = data_url
            captured["prompt"] = prompt
            return "图中有一个白色方块。"

        text = self.tool(vision).invoke({"path": "shot.png", "question": "图里有什么？"})
        self.assertIn("图中有一个白色方块。", text)
        self.assertTrue(captured["url"].startswith("data:image/png;base64,"))
        self.assertIn("图里有什么", captured["prompt"])

    def test_requires_vision_channel(self):
        text = self.tool(None).invoke({"path": "shot.png"})
        self.assertTrue(text.startswith("Error: [vision_unavailable]"), text)

    def test_vision_failure_is_tool_error(self):
        def vision(data_url: str, prompt: str) -> str:
            raise RuntimeError("model does not support images")

        text = self.tool(vision).invoke({"path": "shot.png"})
        self.assertTrue(text.startswith("Error: [vision_failed]"), text)
        self.assertIn("model does not support images", text)

    def test_rejects_non_image(self):
        (self.root / "note.txt").write_text("hello", encoding="utf-8")
        text = self.tool(lambda url, prompt: "x").invoke({"path": "note.txt"})
        self.assertTrue(text.startswith("Error: [unsupported_image]"), text)

    def test_missing_file(self):
        text = self.tool(lambda url, prompt: "x").invoke({"path": "nope.png"})
        self.assertTrue(text.startswith("Error: [not_found]"), text)


if __name__ == "__main__":
    unittest.main()
