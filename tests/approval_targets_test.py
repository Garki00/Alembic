# tests/approval_targets_test.py
"""新增文件/进程工具接入审批时的目标与记忆键。"""

import unittest

from agents.approval import ApprovalPolicy, describe_call, gated_tools
from tools.registry import TOOL_SPECS


class ApprovalTargetTest(unittest.TestCase):
    def policy(self) -> ApprovalPolicy:
        return ApprovalPolicy(gated_tools(set(TOOL_SPECS)), {})

    def test_write_and_edit_share_file_key(self):
        policy = self.policy()
        write_key = policy.key("write_file", {"path": "a.py", "content": "x"})
        edit_key = policy.key("edit_file", {"path": "a.py", "old_string": "x", "new_string": "y"})
        self.assertEqual(write_key, edit_key)
        self.assertEqual(write_key, "file:a.py")

    def test_delete_has_its_own_key(self):
        policy = self.policy()
        key = policy.key("delete_file", {"path": "a.py"})
        self.assertEqual(key, "delete:a.py")
        self.assertNotEqual(key, policy.key("write_file", {"path": "a.py", "content": "x"}))

    def test_move_target_includes_both_paths(self):
        target, summary, _preview = describe_call(
            "move_file", {"source": "a.txt", "destination": "b.txt"}
        )
        self.assertEqual(target, "a.txt → b.txt")
        self.assertIn("移动", summary)
        self.assertEqual(
            self.policy().key("move_file", {"source": "a.txt", "destination": "b.txt"}),
            "move:a.txt → b.txt",
        )

    def test_delete_summary(self):
        target, summary, preview = describe_call("delete_file", {"path": "old.txt"})
        self.assertEqual(target, "old.txt")
        self.assertIn("删除", summary)
        self.assertIn("old.txt", preview)

    def test_new_write_and_process_tools_are_gated(self):
        gated = gated_tools(set(TOOL_SPECS))
        for name in (
            "delete_file",
            "move_file",
            "copy_file",
            "make_dir",
            "start_process",
            "stop_process",
            "apply_patch",
            "batch_edit",
            "download_file",
            "git_commit",
        ):
            self.assertIn(name, gated, name)
        for name in (
            "file_stat",
            "read_document",
            "read_image",
            "fetch_url",
            "web_search",
            "git_status",
            "git_diff",
            "todo_write",
            "ask_user_question",
            "wait_process",
        ):
            self.assertNotIn(name, gated, name)

    def test_patch_and_download_have_readable_summaries(self):
        target, summary, preview = describe_call(
            "apply_patch",
            {"patch": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"},
        )
        self.assertEqual(target, "a.py")
        self.assertIn("应用补丁", summary)
        self.assertIn("-x", preview)

        target, summary, preview = describe_call(
            "batch_edit",
            {"edits": [
                {"path": "a.html", "old_string": "old", "new_string": "new"},
                {"path": "b.html", "old_string": "x", "new_string": "y"},
            ]},
        )
        self.assertEqual(target, "a.html、b.html")
        self.assertIn("原子编辑", summary)
        self.assertIn("old", preview)

        target, summary, _preview = describe_call(
            "download_file", {"url": "https://example.com/a.pdf", "path": "docs/a.pdf"}
        )
        self.assertEqual(target, "docs/a.pdf")
        self.assertIn("下载文件", summary)

        _target, summary, _preview = describe_call("git_commit", {"message": "feat: x"})
        self.assertIn("提交 Git", summary)


if __name__ == "__main__":
    unittest.main()
