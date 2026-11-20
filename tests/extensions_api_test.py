"""HTTP contracts for installed extensions, using real stores and discovery."""
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from tools.context import ToolContext
from tools.registry import build_tools
from web.server import create_app


class ExtensionsApiTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '.tmp' / 'tests' / ('extensions_api_' + uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.app = create_app(data_dir=self.root / 'data', memory_dir=self.root / 'memory')
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.extensions.close)

    def snapshot(self):
        response = self.client.get('/api/extensions')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_defaults_and_individual_builtin_tools(self):
        snapshot = self.snapshot()
        for kind in ('tools', 'mcp', 'skills'):
            self.assertEqual(snapshot['directories'][kind], [str(self.root / 'extensions' / kind)])
            self.assertTrue((self.root / 'extensions' / kind).is_dir())
        tools = self.client.get('/api/tools').json()['tools']
        names = {tool['name'] for tool in tools}
        self.assertTrue({'read_file', 'list_dir', 'write_file', 'git_status'} <= names)
        self.assertTrue({'file_read', 'file_write', 'git_read'}.isdisjoint(names))
        self.assertTrue(all(tool['source'] == 'builtin' for tool in tools))
        self.assertTrue(any(skill['source'] == 'builtin' for skill in snapshot['skills']))

    def test_drop_plugin_refresh_enable_and_invoke(self):
        plugin = self.root / 'extensions' / 'tools' / 'sample'
        plugin.mkdir()
        (plugin / 'run.py').write_text('def echo(arguments, ctx, config):\n    return "echo:" + arguments["text"]\n', encoding='utf-8')
        (plugin / 'plugin.json').write_text(json.dumps({
            'version': 1, 'id': 'sample', 'name': 'Sample', 'tools': [{
                'name': 'echo', 'description': 'Echo supplied text.', 'entrypoint': 'run.py:echo',
                'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']},
            }],
        }), encoding='utf-8')
        response = self.client.post('/api/extensions/refresh')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['errors'], [])
        tools = self.client.get('/api/tools').json()['tools']
        item = next(tool for tool in tools if tool['name'] == 'plugin_sample_echo')
        self.assertEqual(item['source'], 'plugin')
        self.assertFalse(item['enabled'])
        self.assertEqual({tool['source'] for tool in tools}, {'builtin', 'plugin'})
        response = self.client.put('/api/tools/plugin_sample_echo', json={'enabled': True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(next(tool for tool in response.json()['tools'] if tool['name'] == item['name'])['enabled'])
        built = build_tools({item['name']}, {}, ToolContext(workspace=self.root))
        self.assertEqual(built[0].invoke({'text': '中文'}), 'echo:中文')

    def test_disabled_mcp_create_toggle_and_delete(self):
        upstream = MagicMock()
        upstream.discover.return_value = {'tools': [], 'resources': [], 'resourceTemplates': [], 'prompts': [], 'capabilities': {}}
        with patch('extensions.mcp.MCPClient', return_value=upstream) as factory:
            response = self.client.put('/api/extensions/mcp/local', json={
                'name': 'Local', 'transport': 'stdio', 'command': 'python', 'enabled': False,
                'env': {'SECRET_TEST': 'private-value'},
            })
            self.assertEqual(response.status_code, 200)
            factory.assert_not_called()
            self.assertNotIn('private-value', response.text)
            response = self.client.put('/api/extensions/mcp/local', json={'enabled': True})
            self.assertEqual(response.status_code, 200)
            upstream.discover.assert_called_once()
            self.assertEqual(upstream.discover.call_args.args[0]['env']['SECRET_TEST'], 'private-value')
            server = response.json()['mcp'][0]
            self.assertTrue(server['enabled'])
            self.assertEqual(server['status'], 'connected')
            self.assertNotIn('env', server)
            self.assertEqual(self.client.delete('/api/extensions/mcp/local').status_code, 200)
            self.assertEqual(self.snapshot()['mcp'], [])
            self.assertEqual(self.client.delete('/api/extensions/mcp/missing').status_code, 404)

    def test_skill_disable_persists_across_app_instances(self):
        skill = self.snapshot()['skills'][0]
        response = self.client.patch('/api/extensions/skills/' + skill['id'], json={'enabled': False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(next(item for item in response.json()['skills'] if item['id'] == skill['id'])['enabled'])
        app = create_app(data_dir=self.root / 'data', memory_dir=self.root / 'memory')
        self.addCleanup(app.state.extensions.close)
        with TestClient(app) as client:
            saved = client.get('/api/extensions').json()['skills']
            self.assertFalse(next(item for item in saved if item['id'] == skill['id'])['enabled'])

    def test_pick_directory_returns_selection(self):
        with patch('web.server._pick_directory', return_value={'path': str(self.root), 'cancelled': False}) as picker:
            response = self.client.post('/api/extensions/pick-directory')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['path'], str(self.root))
        picker.assert_called_once_with('', '选择插件目录')

    def test_invalid_payloads_return_client_errors(self):
        for payload in ({'command': 'python', 'enabled': 'false'}, {'command': 'python', 'args': 'bad'}, {'transport': 'unknown'}):
            self.assertEqual(self.client.put('/api/extensions/mcp/bad', json=payload).status_code, 400)
        self.assertEqual(self.client.put('/api/extensions/directories', json={'tools': 'bad'}).status_code, 400)
        self.assertEqual(self.client.patch('/api/extensions/skills/missing', json={'enabled': 'yes'}).status_code, 400)
        self.assertEqual(self.client.patch('/api/extensions/skills/missing', json={'enabled': False}).status_code, 404)
        self.assertEqual(self.snapshot()['mcp'], [])


if __name__ == '__main__':
    unittest.main()
