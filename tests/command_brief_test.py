# tests/command_brief_test.py
"""审批卡片里的「人话说明」：命令翻译、分步拆解与兜底。"""

import unittest

from tools.command_brief import command_steps, describe_command


class DescribeCommandTest(unittest.TestCase):
    def test_powershell_pipeline(self):
        command = (
            'Get-ChildItem *.html | Select-Object Name,Length; '
            'Write-Output "--- hrefs ---"; '
            'Select-String -Path *.html -Pattern \'href="([^"]+)"\' | '
            'ForEach-Object { $_.Matches | ForEach-Object { '
            'Test-Path (Join-Path "." $_.Groups[1].Value) } } | '
            'Format-Table -AutoSize'
        )
        text = describe_command(command)
        self.assertIn("*.html", text)
        self.assertIn("href", text)
        self.assertIn("检查路径是否存在", text)
        self.assertNotIn("Get-ChildItem", text)

    def test_steps_are_ordered(self):
        steps = command_steps("rm -rf ./dist && npm run build")
        self.assertEqual(steps, ["删除 ./dist", "运行 npm 脚本 build"])

    def test_git_and_tests(self):
        self.assertEqual(describe_command("git status --short"), "查看 Git 状态")
        self.assertEqual(describe_command("python -m unittest discover -s tests"), "运行 Python 测试")
        self.assertEqual(describe_command("npm run dev"), "运行 npm 脚本 dev")

    def test_file_operations(self):
        self.assertIn("复制", describe_command("Copy-Item a.txt b.txt"))
        self.assertIn("递归删除", describe_command("Remove-Item -Recurse -Force .tmp"))
        self.assertIn("切换到 src", describe_command("cd src"))

    def test_section_titles_become_readable_headings(self):
        command = (
            'Write-Output "--- href check ---"\n'
            'Select-String -Path *.html -Pattern \'href="([^"#]+)"\'\n'
            'Write-Output "--- tag balance ---"\n'
            "foreach ($f in Get-ChildItem *.html) { $o = ([regex]::Matches("
            "(Get-Content $f -Raw), '<p>')).Count; if ($o -eq 0) { \"$($f.Name)\" } }"
        )
        steps = command_steps(command)
        self.assertIn("检查页面链接", steps[0])
        self.assertIn("检查标签配对", steps[1])
        self.assertIn("逐个处理 *.html 文件", steps[1])
        self.assertNotIn("done", " ".join(steps))

    def test_odd_input_never_raises(self):
        for command in (
            "",
            "   ",
            "Get-ChildItem 'unclosed",
            "ForEach-Object { }",
            "&&& ; |||",
            "a | b | c",
        ):
            with self.subTest(command=command):
                self.assertIsInstance(describe_command(command), str)
                self.assertIsInstance(command_steps(command), list)

    def test_unknown_command_still_gets_a_phrase(self):
        self.assertEqual(describe_command("some-unknown-tool --flag"), "运行 some-unknown-tool 命令")


if __name__ == "__main__":
    unittest.main()
