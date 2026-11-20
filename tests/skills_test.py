"""Focused Agent Skills metadata, progressive disclosure, and confinement tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from extensions.skills import (
    SkillError, discover_skills, load_skill, read_skill_resource, render_skill_catalog,
)


class SkillsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_skill(self, name="example", extra="", body="Secret body marker", directory=None):
        directory = directory or self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Useful skill description\n{extra}---\n{body}\n",
            encoding="utf-8",
        )
        return directory

    def test_metadata_contract_and_progressive_disclosure(self):
        directory = self.make_skill(extra=(
            'license: MIT\ncompatibility: Python 3\nmetadata:\n  version: "1.0"\n'
            'allowed-tools: Read Bash(git:*)\n'
        ))
        (directory / "scripts").mkdir()
        (directory / "scripts" / "never.py").write_text("raise Exception('never execute')")
        skills, errors = discover_skills([self.root], source="builtin")
        self.assertEqual(errors, [])
        skill = skills[0]
        self.assertEqual(skill.metadata, {"version": "1.0"})
        self.assertEqual(skill.allowed_tools, "Read Bash(git:*)")
        self.assertEqual(set(skill.to_dict()), {
            "id", "name", "description", "source", "enabled", "path", "license", "compatibility", "error",
        })
        self.assertEqual(skill.public_dict(), skill.to_dict())
        catalog = render_skill_catalog(skills)
        self.assertNotIn("Secret body marker", catalog)
        self.assertIn("load_skill", catalog)
        self.assertIn("read_skill_resource", catalog)
        self.assertIn("Secret body marker", load_skill(skill))
        self.assertIn("never execute", read_skill_resource(skill, "scripts/never.py"))
        self.assertEqual(read_skill_resource(skill, "scripts"), "never.py")

    def test_stable_path_identity_and_duplicate_roots(self):
        a = self.make_skill(directory=self.root / "one" / "example")
        b = self.make_skill(directory=self.root / "two" / "example")
        first, errors = discover_skills([self.root, a])
        second, _ = discover_skills([b, a], source="other")
        self.assertFalse(errors)
        self.assertEqual(len(first), 2)
        self.assertEqual([s.id for s in first], [s.id for s in second])
        self.assertNotEqual(first[0].id, first[1].id)

    def test_validation_boundaries(self):
        for name in ["a", "a" * 64, "my-skill-2", "数据分析"]:
            with self.subTest(valid=name):
                directory = self.make_skill(name)
                self.assertEqual(len(discover_skills([directory])[0]), 1)
        for name in ["Upper", "-bad", "bad-", "bad--name", "bad_name", "a" * 65]:
            with self.subTest(invalid=name):
                directory = self.make_skill(name)
                skills, errors = discover_skills([directory])
                self.assertEqual(skills, [])
                self.assertEqual(len(errors), 1)

    def test_invalid_frontmatter_isolated_from_valid_skills(self):
        self.make_skill()
        cases = {
            "mismatch": "name: other\ndescription: Fine",
            "missing": "name: missing",
            "empty": 'name: empty\ndescription: " "',
            "long": 'name: long\ndescription: ' + 'x' * 1025,
            "compat": 'name: compat\ndescription: Fine\ncompatibility: ' + 'x' * 501,
            "meta": 'name: meta\ndescription: Fine\nmetadata: {version: 1}',
            "tools": 'name: tools\ndescription: Fine\nallowed-tools: [Read]',
            "duplicate": 'name: duplicate\nname: duplicate\ndescription: Fine',
            "unsafe": 'name: unsafe\ndescription: !!python/object/apply:os.system [echo bad]',
            "alias": 'name: alias\ndescription: &a Fine\nlicense: *a',
        }
        for name, header in cases.items():
            directory = self.root / name
            directory.mkdir()
            (directory / "SKILL.md").write_text(f"---\n{header}\n---\n", encoding="utf-8")
        skills, errors = discover_skills([self.root])
        self.assertEqual([s.name for s in skills], ["example"])
        self.assertEqual(len(errors), len(cases))

    def test_disabled_and_changed_skill_fail_closed(self):
        directory = self.make_skill()
        skill = discover_skills([directory])[0][0]
        skill.enabled = False
        self.assertEqual(render_skill_catalog([skill]), "")
        with self.assertRaises(SkillError):
            load_skill(skill)
        with self.assertRaises(SkillError):
            read_skill_resource(skill, "SKILL.md")
        skill.enabled = True
        (directory / "SKILL.md").write_text("no frontmatter")
        with self.assertRaises(SkillError):
            load_skill(skill)

    def test_resource_paths_and_encoding(self):
        directory = self.make_skill()
        skill = discover_skills([directory])[0][0]
        for path in ["../outside", "sub/../../outside", "..\\outside", "/etc/passwd", "C:\\private", "C:private", "file:stream"]:
            with self.subTest(path=path), self.assertRaises(SkillError):
                read_skill_resource(skill, path)
        (directory / "binary").write_bytes(b"\x00binary")
        (directory / "bad-utf8").write_bytes(b"\xff")
        for path in ["binary", "bad-utf8", "missing"]:
            with self.subTest(path=path), self.assertRaises(SkillError):
                read_skill_resource(skill, path)
        with patch("extensions.skills.MAX_RESOURCE_BYTES", 4), self.assertRaises(SkillError):
            read_skill_resource(skill, "SKILL.md")
        with patch("extensions.skills.MAX_SKILL_BYTES", 4):
            self.assertEqual(len(discover_skills([directory])[1]), 1)

    def test_symlinks_cannot_escape(self):
        directory = self.make_skill()
        outside = self.root / "outside.txt"
        outside.write_text("private")
        try:
            (directory / "escape").symlink_to(outside)
            (directory / "inside").symlink_to(directory / "SKILL.md")
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable on this host")
        skill = discover_skills([directory])[0][0]
        with self.assertRaises(SkillError):
            read_skill_resource(skill, "escape")
        self.assertNotIn("escape", read_skill_resource(skill, "."))
        self.assertIn("Secret body marker", read_skill_resource(skill, "inside"))
        (directory / "SKILL.md").unlink()
        (directory / "SKILL.md").symlink_to(outside)
        self.assertEqual(len(discover_skills([directory])[1]), 1)

    def test_missing_root_and_listing_limit(self):
        self.assertEqual(len(discover_skills([self.root / "missing"])[1]), 1)
        directory = self.make_skill()
        skill = discover_skills([directory])[0][0]
        with patch("extensions.skills.MAX_DIRECTORY_ENTRIES", 0), self.assertRaises(SkillError):
            read_skill_resource(skill, ".")

    def test_builtin_is_valid_and_reference_loads(self):
        root = Path(__file__).resolve().parents[1] / "extensions/builtin/skills"
        skills, errors = discover_skills([root], source="builtin")
        self.assertEqual(errors, [])
        review = next(s for s in skills if s.name == "code-review")
        self.assertIn("Review code changes", load_skill(review))
        self.assertIn("Review checklist", read_skill_resource(review, "references/review-checklist.md"))


if __name__ == "__main__":
    unittest.main()
