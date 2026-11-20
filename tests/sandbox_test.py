# tests/sandbox_test.py
"""Offline tests for sandbox policy, domain matching, and config storage."""

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from tools.sandbox.base import SandboxPolicy
from tools.sandbox.config import SandboxConfigStore
from tools.sandbox.proxy import domain_allowed, is_private_host


def make_tempdir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"sandbox_{uuid4().hex[:12]}"
    path.mkdir()
    return path


class SandboxPolicyTest(unittest.TestCase):
    def test_ctypes_handle_conversion(self):
        import ctypes
        from tools.sandbox.windows import _handle_value

        self.assertEqual(_handle_value(ctypes.c_void_p(3068)), 3068)
        self.assertEqual(_handle_value(3068), 3068)

    def test_network_normalization(self):
        workspace = Path.cwd()
        self.assertEqual(SandboxPolicy(workspace, network="allowlist").normalized_network(), "allowlist")
        self.assertEqual(SandboxPolicy(workspace, network="bogus").normalized_network(), "offline")

    def test_domain_allowlist(self):
        allowed = ["example.com", "*.example.org", "registry.npmjs.org:443"]
        self.assertTrue(domain_allowed("example.com", allowed))
        self.assertTrue(domain_allowed("a.example.org", allowed))
        self.assertFalse(domain_allowed("example.org", allowed))
        self.assertTrue(domain_allowed("registry.npmjs.org:443", allowed))
        self.assertFalse(domain_allowed("evil.test", allowed))

    def test_private_hosts(self):
        self.assertTrue(is_private_host("127.0.0.1"))
        self.assertTrue(is_private_host("10.0.0.8"))
        self.assertFalse(is_private_host("pypi.org"))


class SandboxConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tempdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = SandboxConfigStore(self.tmp / "sandbox.json")

    def test_defaults_and_update(self):
        config = self.store.get()
        self.assertEqual(config["network"], "offline")
        self.assertIn("pypi.org", config["allowed_domains"])
        self.assertIn("gitee.com", config["allowed_domains"])

        updated = self.store.update({
            "network": "allowlist",
            "allowed_domains": ["example.com"],
            "allowed_ports": [443],
        })
        self.assertEqual(updated["network"], "allowlist")
        self.assertEqual(updated["allowed_domains"], ["example.com"])
        self.assertEqual(self.store.get()["allowed_ports"], [443])

    def test_legacy_default_domains_gain_gitee(self):
        self.store.path.write_text(
            '{"version":1,"network":"allowlist","allowed_domains":['
            '"registry.npmjs.org","registry.npmmirror.com","pypi.org",'
            '"files.pythonhosted.org","github.com","codeload.github.com",'
            '"raw.githubusercontent.com"],"allowed_ports":[443,80],"proxy_port":47831}',
            encoding="utf-8",
        )
        config = self.store.get()
        self.assertIn("gitee.com", config["allowed_domains"])
        self.assertEqual(config["version"], 2)

    def test_utf8_bom_from_powershell_is_accepted(self):
        self.store.path.write_text(
            '\ufeff{"network":"allowlist","allowed_domains":["example.com"],'
            '"allowed_ports":[443],"proxy_port":47831}',
            encoding="utf-8",
        )
        config = self.store.get()
        self.assertEqual(config["network"], "allowlist")
        self.assertEqual(config["allowed_domains"], ["example.com"])

    def test_invalid_network_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({"network": "online"})
