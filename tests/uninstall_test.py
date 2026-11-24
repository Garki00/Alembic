"""Static invariants for the Windows uninstaller flow."""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
MAIN = (ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
WORKER = (ROOT / "packaging" / "install" / "uninstall-worker.ps1").read_text(encoding="utf-8-sig")


class UninstallFlowTests(unittest.TestCase):
    def test_elevated_wait_does_not_wait_for_descendants(self):
        # PowerShell 5.1 的 Start-Process -Wait 会等待整棵进程树。worker 启动的
        # 清理进程会等主窗口退出，主窗口又在等这里，曾导致卸载界面永久卡住。
        self.assertIn("$p.WaitForExit()", MAIN)
        self.assertNotIn("-Verb RunAs -Wait", MAIN)

    def test_uninstaller_runs_from_temp_copy(self):
        # 安装目录里的卸载器无法删除正在运行的自己，必须先复制到 %TEMP% 再运行。
        self.assertIn("UNINSTALL_COPY_MARKER", MAIN)
        self.assertIn("copyUninstallerRuntime", MAIN)
        self.assertIn("ALEMBIC_UNINSTALL_DIR", MAIN)

    def test_install_dir_is_deleted_during_uninstall(self):
        # 方案 A：目录在卸载过程中删除，不能再依赖“窗口关闭后”的延迟清理。
        self.assertIn("function Remove-InstallDir", WORKER)
        self.assertNotIn("function Schedule-Cleanup", WORKER)
        self.assertIn("Remove-InstallDir $script:FullInstallDir", WORKER)


if __name__ == "__main__":
    unittest.main()
