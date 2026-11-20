# tests/env_test.py
"""Subprocess environment allowlist: system variables pass through, secret variables are stripped."""

import unittest
from unittest.mock import patch

from tools.env import build_child_env, is_allowed_name, is_secret_name


class EnvPolicyTest(unittest.TestCase):
    @patch("tools.runtimes.runtime_paths", return_value=[])
    def test_system_variables_are_kept(self, _paths):
        env = build_child_env(
            inherit={
                "Path": r"C:\Windows\System32",
                "SystemRoot": r"C:\Windows",
                "APPDATA": r"C:\Users\me\AppData\Roaming",
                "TEMP": r"C:\Temp",
            }
        )
        self.assertEqual(env["PATH"], r"C:\Windows\System32")
        self.assertEqual(env["SYSTEMROOT"], r"C:\Windows")
        self.assertEqual(env["APPDATA"], r"C:\Users\me\AppData\Roaming")
        self.assertEqual(env["TEMP"], r"C:\Temp")

    def test_secrets_are_dropped(self):
        env = build_child_env(
            inherit={
                "PATH": "x",
                "DEEPSEEK_API_KEY": "sk-deepseek",
                "OPENAI_API_KEY": "sk-openai",
                "AWS_SECRET_ACCESS_KEY": "aws-secret",
                "GITHUB_TOKEN": "ghp_x",
                "MY_PASSWORD": "hunter2",
                "SOME_SECRET": "s",
            }
        )
        for name in (
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "MY_PASSWORD",
            "SOME_SECRET",
        ):
            self.assertNotIn(name, env)

    def test_overrides_are_applied(self):
        env = build_child_env(
            {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            inherit={"PATH": "x"},
        )
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")

    def test_missing_path_gets_default(self):
        env = build_child_env(inherit={})
        self.assertIn("PATH", env)
        self.assertTrue(env["PATH"])

    def test_name_classification(self):
        self.assertTrue(is_secret_name("OPENAI_API_KEY"))
        self.assertTrue(is_secret_name("SESSION_TOKEN"))
        self.assertFalse(is_secret_name("PATH"))
        self.assertTrue(is_allowed_name("SystemRoot"))
        self.assertFalse(is_allowed_name("DEEPSEEK_API_KEY"))


if __name__ == "__main__":
    unittest.main()
