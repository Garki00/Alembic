"""Individual tool registration and lossless group migration."""
import json
import tempfile
import unittest
from pathlib import Path

from config.store import ToolStore
from tools.context import ToolContext
from tools.registry import LEGACY_TOOL_GROUPS, TOOL_SPECS, build_tools, expand_enabled_names


class SplitToolsTest(unittest.TestCase):
    def test_each_entry_builds_only_its_original_tool(self):
        for group, names in LEGACY_TOOL_GROUPS.items():
            self.assertNotIn(group, TOOL_SPECS)
            self.assertEqual(expand_enabled_names({group}), set(names))
            for name in names:
                tools = build_tools({name}, {}, ToolContext())
                self.assertEqual([tool.name for tool in tools], [name])
            tools = build_tools({group}, {}, ToolContext())
            self.assertEqual({tool.name for tool in tools}, set(names))

    def test_disabled_groups_and_config_survive_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tools.json'
            data = {'version': 3, 'tools': {
                group: {'enabled': False, 'config': {'TOKEN': 'secret', 'x': 'old'}}
                for group in LEGACY_TOOL_GROUPS
            }}
            data['tools']['read_file'] = {'enabled': True, 'config': {'x': 'new'}}
            path.write_text(json.dumps(data), encoding='utf-8')
            store = ToolStore(path)
            state = store.state()
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['version'], 4)
            for group, names in LEGACY_TOOL_GROUPS.items():
                self.assertNotIn(group, saved['tools'])
                for name in names:
                    self.assertEqual(state[name]['enabled'], name == 'read_file')
                    self.assertEqual(saved['tools'][name]['config']['TOKEN'], 'secret')
            self.assertEqual(saved['tools']['read_file']['config']['x'], 'new')
            before = path.read_bytes()
            store.ensure_saved()
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(store.update('file_write', enabled=True))
            self.assertTrue(all(store.state()[name]['enabled'] for name in LEGACY_TOOL_GROUPS['file_write']))
            store.update('delete_file', enabled=False)
            self.assertFalse(store.state()['delete_file']['enabled'])
            self.assertTrue(store.state()['write_file']['enabled'])

    def test_sources_and_original_order_are_public(self):
        with tempfile.TemporaryDirectory() as directory:
            items = ToolStore(Path(directory) / 'tools.json').public()
            self.assertTrue(all(item['source'] == 'builtin' and item['source_id'] == '' for item in items))
            for item in items:
                if item['name'] == 'praise_subagent':
                    # A settings-only entry: no model tool, only the post-run review.
                    self.assertEqual(item['tool_names'], [])
                else:
                    self.assertGreaterEqual(len(item['tool_names']), 1)
            names = [item['name'] for item in items]
            self.assertGreater(names.index('get_weather'), names.index('git_commit'))


if __name__ == '__main__':
    unittest.main()
