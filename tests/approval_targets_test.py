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
        for name in ("delete_file", "move_file", "copy_file", "make_dir", "start_process", "stop_process"):
            self.assertIn(name, gated, name)
        for name in ("file_stat", "read_document", "fetch_url", "todo_write", "ask_user_question"):
            self.assertNotIn(name, gated, name)


if __name__ == "__main__":
    unittest.main()
