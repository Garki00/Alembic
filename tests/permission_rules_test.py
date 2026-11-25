# tests/permission_rules_test.py
"""Permission rules: validation, matching, persistence, and the HTTP surface."""

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from agents.approval import ApprovalPolicy
from agents.permission_service import evaluate
from config.permission_rules import (
    PermissionRule,
    PermissionRuleset,
    PermissionRulesStore,
    load_project_rules,
    matches_content,
    normalize_ruleset,
    rule_subject,
)
from web.server import create_app


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"permission_rules_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class RuleParsingTest(unittest.TestCase):
    def test_normalize_accepts_all_three_behaviors(self):
        rules = normalize_ruleset(
            {
                "rules": {
                    "allow": [{"tool": "run_command", "content": "pytest:*"}],
                    "ask": [{"tool": "fetch_url", "content": "domain:example.com"}],
                    "deny": [{"tool": "delete_file", "content": "src/*"}],
                }
            }
        )
        self.assertEqual(len(rules.allow), 1)
        self.assertEqual(len(rules.ask), 1)
        self.assertEqual(len(rules.deny), 1)
        self.assertEqual(rules.count(), 3)

    def test_normalize_rejects_bad_shapes(self):
        with self.assertRaises(ValueError):
            normalize_ruleset({"rules": {"allow": ["run_command"]}})
        with self.assertRaises(ValueError):
            normalize_ruleset({"rules": {"allow": [{"content": "x"}]}})
        with self.assertRaises(ValueError):
            normalize_ruleset({"rules": {"allow": [{"tool": "rm -rf"}]}})
        with self.assertRaises(ValueError):
            normalize_ruleset({"rules": {"allow": [{"tool": "run_command", "content": "x" * 600}]}})

    def test_project_rules_drop_allow_entries(self):
        rules = normalize_ruleset(
            {
                "rules": {
                    "allow": [{"tool": "run_command"}],
                    "ask": [{"tool": "web_search"}],
                    "deny": [{"tool": "delete_file"}],
                }
            },
            allow_allow=False,
        )
        self.assertEqual(rules.allow, [])
        self.assertEqual(len(rules.ask), 1)
        self.assertEqual(len(rules.deny), 1)

    def test_duplicate_rules_are_collapsed(self):
        rules = normalize_ruleset(
            {"rules": {"allow": [{"tool": "run_command"}, {"tool": "run_command"}]}}
        )
        self.assertEqual(len(rules.allow), 1)


class RuleMatchingTest(unittest.TestCase):
    def test_content_matching_modes(self):
        self.assertTrue(matches_content("pytest -q", "pytest -q"))
        self.assertFalse(matches_content("pytest -q", "pytest -q tests"))
        self.assertTrue(matches_content("pytest:*", "pytest -q"))
        self.assertTrue(matches_content("pytest:*", "pytest"))
        self.assertFalse(matches_content("pytest:*", "pytest2 -q"))
        self.assertTrue(matches_content("src/*.py", "src/app.py"))
        self.assertFalse(matches_content("src/*.py", "test/app.py"))

    def test_url_subject_is_the_domain(self):
        self.assertEqual(
            rule_subject("fetch_url", {"url": "https://Example.com/a?b=1"}),
            "domain:example.com",
        )
        self.assertEqual(rule_subject("run_command", {"command": "ls -la"}), "ls -la")
        self.assertEqual(rule_subject("write_file", {"path": "a/b.py"}), "a/b.py")

    def test_write_and_edit_share_a_rule_identity(self):
        rule = PermissionRule("write_file", "notes.md")
        policy = ApprovalPolicy(
            mode="build",
            rules=PermissionRuleset(allow=[rule]),
        )
        self.assertEqual(
            policy.evaluate("edit_file", {"path": "notes.md"}).behavior,
            "allow",
        )


class RulesStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = PermissionRulesStore(self.tmp / "permissions.json")

    def test_roundtrip(self):
        self.store.update({"allow": [{"tool": "run_command", "content": "pytest:*"}]})
        reopened = PermissionRulesStore(self.tmp / "permissions.json")
        rules = reopened.get()
        self.assertEqual(rules.allow[0].tool, "run_command")
        self.assertEqual(rules.allow[0].content, "pytest:*")
        saved = json.loads((self.tmp / "permissions.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["rules"]["allow"][0]["tool"], "run_command")

    def test_invalid_file_falls_back_to_empty(self):
        (self.tmp / "permissions.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.get().count(), 0)

    def test_project_rules_are_read_from_the_workspace_only(self):
        workspace = self.tmp / "repo"
        (workspace / ".alembic").mkdir(parents=True)
        (workspace / ".alembic" / "permissions.json").write_text(
            json.dumps(
                {
                    "rules": {
                        "allow": [{"tool": "run_command"}],
                        "deny": [{"tool": "delete_file"}],
                    }
                }
            ),
            encoding="utf-8",
        )
        rules = load_project_rules(workspace)
        self.assertEqual(rules.allow, [])
        self.assertEqual(len(rules.deny), 1)


class PromptChannelTest(unittest.TestCase):
    def test_ask_rule_fails_closed_when_prompts_are_disabled(self):
        policy = ApprovalPolicy(
            mode="build",
            rules=PermissionRuleset(ask=[PermissionRule("run_command")]),
            prompts_enabled=False,
        )
        decision = policy.evaluate("run_command", {"command": "echo x"})
        self.assertEqual(decision.behavior, "deny")
        self.assertEqual(decision.code, "denied_by_rule")

    def test_mode_ask_passes_when_prompts_are_disabled(self):
        policy = ApprovalPolicy(mode="build", prompts_enabled=False)
        self.assertEqual(policy.evaluate("write_file", {"path": "a.txt"}).behavior, "allow")
        self.assertEqual(policy.evaluate("read_file", {"path": "a.txt"}).behavior, "allow")

    def test_plan_still_blocks_without_prompts(self):
        policy = ApprovalPolicy(mode="plan", prompts_enabled=False)
        self.assertEqual(policy.evaluate("write_file", {"path": "a.txt"}).behavior, "deny")


class RulesApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = create_app(data_dir=self.tmp / "data", memory_dir=self.tmp / "memory")
        self.client = TestClient(self.app)

    def test_put_and_get_rules(self):
        response = self.client.put(
            "/api/permissions",
            json={
                "rules": {
                    "allow": [{"tool": "run_command", "content": "pytest:*"}],
                    "deny": [{"tool": "delete_file", "content": "src/*"}],
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["rules"]["rules"]
        self.assertEqual(data["allow"][0]["tool"], "run_command")

        fetched = self.client.get("/api/permissions").json()
        self.assertEqual(fetched["rules"]["rules"]["deny"][0]["content"], "src/*")
        self.assertEqual(fetched["project_rules"]["rules"]["allow"], [])

    def test_invalid_rules_return_400(self):
        response = self.client.put(
            "/api/permissions", json={"rules": {"allow": [{"tool": "bad tool name"}]}}
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
