"""Packaging script encoding checks for Windows PowerShell 5.1."""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
BOM = b"\xef\xbb\xbf"

# 随安装包分发、由安装器或卸载界面调用 powershell.exe 5.1 执行的脚本。
SHIPPED = (
    "scripts/setup-sandbox.ps1",
    "packaging/install/uninstall-worker.ps1",
    "packaging/install/nsis-postinstall.ps1",
)


def repo_scripts():
    for base in ("scripts", "packaging"):
        yield from sorted((ROOT / base).rglob("*.ps1"))


class PowerShellEncodingTests(unittest.TestCase):
    def test_shipped_scripts_have_utf8_bom(self):
        # 5.1 对没有 BOM 的 UTF-8 按当前 ANSI 代码页解析；中文会让字符串提前
        # 截断并产生语法错误，卸载 worker 会直接退出，界面只显示「卸载未完成」。
        for name in SHIPPED:
            data = (ROOT / name).read_bytes()
            self.assertTrue(data.startswith(BOM), f"{name} must be saved as UTF-8 with BOM")

    def test_non_ascii_scripts_have_bom(self):
        # 纯 ASCII 脚本不受影响；含中文的脚本必须带 BOM，否则 5.1 解析结果
        # 取决于系统代码页。
        for path in repo_scripts():
            data = path.read_bytes()
            if data.startswith(BOM) or all(byte < 0x80 for byte in data):
                continue
            self.fail(f"{path.relative_to(ROOT)} contains non-ASCII text but no UTF-8 BOM")


if __name__ == "__main__":
    unittest.main()
