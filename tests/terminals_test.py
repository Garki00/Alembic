# tests/terminals_test.py
"""Terminal adapter tests: aliases, availability, argument construction, and WSL path mapping."""

import os
import unittest

from tools.terminals import (
    TERMINAL_CHOICES,
    available_terminals,
    build_invocation,
    normalize_terminal,
    windows_to_wsl_path,
)


class NormalizeTest(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize_terminal("PS"), "powershell")
        self.assertEqual(normalize_terminal("pwsh"), "powershell")
        self.assertEqual(normalize_terminal("cmd.exe"), "cmd")
        self.assertEqual(normalize_terminal("git-bash"), "gitbash")
        self.assertEqual(normalize_terminal("WSL2"), "wsl")
        self.assertEqual(normalize_terminal("fish"), "powershell")
        self.assertEqual(normalize_terminal(""), "powershell")

    def test_available_terminals_shape(self):
        items = available_terminals()
        self.assertEqual([item.value for item in items], list(TERMINAL_CHOICES))
        self.assertTrue(all(item.label for item in items))


class InvocationTest(unittest.TestCase):
    def test_powershell_wraps_exit_code(self):
        if os.name != "nt":
            self.skipTest("仅 Windows")
        invocation = build_invocation("powershell", 'echo "hi"', r"D:\work")
        argv = invocation.argv
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0].lower().rsplit("\\", 1)[-1], "powershell.exe")
        self.assertIn("-Command", argv)
        script = argv[-1]
        self.assertIn("$LASTEXITCODE", script)
        self.assertIn('& {', script)
        self.assertEqual(invocation.cwd, r"D:\work")
        self.assertFalse(invocation.shell)

    def test_powershell_quoted_executable_gets_call_operator(self):
        if os.name != "nt":
            self.skipTest("仅 Windows")
        invocation = build_invocation("powershell", '"C:\\Python\\python.exe" -V', r"D:\work")
        self.assertIn('& "C:\\Python\\python.exe"', invocation.argv[-1])

    def test_cmd_uses_raw_command_line(self):
        if os.name != "nt":
            self.skipTest("仅 Windows")
        invocation = build_invocation("cmd", 'dir "C:\\Program Files"', r"D:\work")
        self.assertEqual(invocation.argv, 'dir "C:\\Program Files"')
        self.assertTrue(invocation.shell)
        self.assertEqual(invocation.cwd, r"D:\work")

    def test_gitbash_uses_c(self):
        if os.name != "nt":
            self.skipTest("仅 Windows")
        invocation = build_invocation("gitbash", "echo hi", r"D:\work")
        self.assertIn("-c", invocation.argv)
        self.assertEqual(invocation.argv[-1], "echo hi")
        self.assertFalse(invocation.shell)

    def test_wsl_maps_cwd(self):
        if os.name != "nt":
            self.skipTest("仅 Windows")
        invocation = build_invocation("wsl", "pwd", r"D:\work")
        self.assertIsNone(invocation.cwd)
        self.assertIn("bash", invocation.argv)
        self.assertIn("/mnt/d/work", invocation.argv[-1])

    def test_windows_to_wsl_path(self):
        self.assertEqual(windows_to_wsl_path(r"D:\Garki\alembic"), "/mnt/d/Garki/alembic")
        self.assertEqual(windows_to_wsl_path("C:\\"), "/mnt/c/")


if __name__ == "__main__":
    unittest.main()
