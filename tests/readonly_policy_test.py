# tests/readonly_policy_test.py
"""Read-only command classification and its effect on approval decisions."""

import unittest

from agents.approval import ApprovalPolicy
from agents.permission_service import evaluate
from config.permission_rules import PermissionRule, PermissionRuleset
from tools.bash_analysis import analyze
from tools.readonly_policy import is_read_only_command


class AnalysisTest(unittest.TestCase):
    def test_unsafe_syntax_is_rejected(self):
        for command in (
            "echo $(cat secret)",
            "echo `date`",
            "ls &",
            "(ls)",
            "if true; then ls; fi",
        ):
            self.assertFalse(analyze(command).permission_safe, command)

    def test_sequences_are_analyzed_per_invocation(self):
        for command in ("ls && echo ok", "ls; pwd", "cat a && cat b", "ls\nrm -rf build"):
            self.assertTrue(analyze(command).permission_safe, command)
        self.assertFalse(is_read_only_command("ls\nrm -rf build", shell="gitbash"))

    def test_simple_pipelines_are_permission_safe(self):
        analysis = analyze("cat a.txt | grep needle | wc -l")
        self.assertTrue(analysis.permission_safe)
        self.assertEqual(len(analysis.commands), 3)


class ReadOnlyCommandTest(unittest.TestCase):
    def assert_read_only(self, command: str, shell: str = "gitbash") -> None:
        self.assertTrue(is_read_only_command(command, shell=shell), command)

    def assert_needs_approval(self, command: str, shell: str = "gitbash") -> None:
        self.assertFalse(is_read_only_command(command, shell=shell), command)

    def test_everyday_read_commands(self):
        for command in (
            "wc -l notes.txt",
            "wc -l",
            "ls -la",
            "cat package.json",
            "head -n 20 app.log",
            "tail -f app.log",
            "grep -rn needle src",
            "rg --files",
            "cat a.txt | grep needle | wc -l",
            "find . -name '*.py' -type f",
            "sort -u names.txt",
            "git status",
            "git status --short",
            "git log --oneline -5",
            "git diff --stat",
            "git show HEAD",
            "git rev-parse --show-toplevel",
            "sed -n '1,10p' notes.txt",
            "sed 's/a/b/'",
            "jq '.name' package.json",
            "tree -L 2 src",
            "cd src && ls",
            "history",
            "echo hello",
            "git remote -v",
            "git config --get user.name",
            "docker ps -a",
            "gh pr list --state open",
            "hostname",
            "ps aux",
            "date +%s",
            "test -f notes.txt",
            "[ -f notes.txt ]",
        ):
            self.assert_read_only(command)

    def test_write_or_unknown_commands_need_approval(self):
        for command in (
            "rm -rf build",
            "git commit -m 'x'",
            "git push origin main",
            "git branch new-feature",
            "sed -i 's/a/b/' file.txt",
            "sed 's/a/b/' file.txt",
            "find . -delete",
            "find . -exec rm {} ;",
            "sort -o out.txt names.txt",
            "echo hi > out.txt",
            "cat a.txt > b.txt",
            "touch new.txt",
            "curl https://example.com",
            "npm install",
            "python script.py",
            "unknown-command --flag",
            "git --git-dir=/tmp/x status",
            "wc -l file.txt | tee copy.txt",
            "git config user.name someone",
            "git branch -D main",
            "git tag v1.0",
            "git remote add origin https://example.com/repo.git",
            "git ls-remote https://example.com/repo.git",
            "hostname new-name",
            "ps auxe",
            "jq -f script.jq package.json",
            "xargs rm",
            "docker run alpine",
        ):
            self.assert_needs_approval(command)

    def test_non_posix_shell_never_qualifies(self):
        for shell in ("powershell", "cmd", ""):
            self.assert_needs_approval("wc -l notes.txt", shell=shell)

    def test_read_only_git_plus_directory_change_is_rejected(self):
        self.assert_needs_approval("cd sub && git status")
        self.assert_needs_approval("git status && cd sub")


class ApprovalIntegrationTest(unittest.TestCase):
    def test_read_only_command_skips_prompt_in_build(self):
        decision = evaluate(
            "run_command", {"command": "wc -l notes.txt"}, mode="build", shell="gitbash"
        )
        self.assertEqual(decision.behavior, "allow")

    def test_non_posix_shell_keeps_approval(self):
        decision = evaluate(
            "run_command", {"command": "wc -l notes.txt"}, mode="build", shell="powershell"
        )
        self.assertEqual(decision.behavior, "ask")

    def test_plan_allows_read_only_and_blocks_writes(self):
        self.assertEqual(
            evaluate("run_command", {"command": "wc -l notes.txt"}, mode="plan", shell="gitbash").behavior,
            "allow",
        )
        self.assertEqual(
            evaluate("run_command", {"command": "rm -rf build"}, mode="plan", shell="gitbash").behavior,
            "deny",
        )

    def test_rules_still_win(self):
        deny = ApprovalPolicy(
            mode="build",
            shell="gitbash",
            rules=PermissionRuleset(deny=[PermissionRule("run_command", "wc:*")]),
        )
        self.assertEqual(
            deny.evaluate("run_command", {"command": "wc -l a.txt"}).behavior,
            "deny",
        )
        ask = ApprovalPolicy(
            mode="build",
            shell="gitbash",
            rules=PermissionRuleset(ask=[PermissionRule("run_command")]),
        )
        self.assertEqual(
            ask.evaluate("run_command", {"command": "wc -l a.txt"}).behavior,
            "ask",
        )

    def test_background_processes_are_not_affected(self):
        self.assertEqual(
            evaluate("start_process", {"command": "wc -l notes.txt"}, mode="build", shell="gitbash").behavior,
            "ask",
        )


if __name__ == "__main__":
    unittest.main()
