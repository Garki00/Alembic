"""Portable runtime resolution, integrity, and missing-host execution checks."""
from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.env import build_child_env
from tools.runtimes import runtime_paths, runtime_root
from scripts.provision_runtimes import extract_archive, verify_archive, verify_install


class RuntimeTests(unittest.TestCase):
    def test_complete_bundle_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("tools.runtimes.sys.platform", "win32"), patch("tools.runtimes.platform.machine", return_value="AMD64"):
                self.assertEqual(runtime_paths(root), [])
                for name in ("installed.json", "node/node.exe", "python/python.exe", "pnpm/pnpm.exe", "bin/pnpm.cmd"):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
                self.assertEqual(runtime_paths(root)[0], root / "bin")

    @patch("tools.runtimes.runtime_paths", return_value=[])
    def test_missing_host_and_secrets(self, _paths):
        with patch("tools.runtimes.runtime_paths", return_value=[Path("bundle/bin"), Path("bundle/node")]):
            env = build_child_env({"DEEPSEEK_API_KEY": "secret", "PYTHONUTF8": "1"}, inherit={"PATH": "", "OPENAI_API_KEY": "secret", "AWS_SECRET_ACCESS_KEY": "secret"})
        self.assertEqual(env["PATH"], os.pathsep.join(["bundle\\bin", "bundle\\node"]) if os.name == "nt" else "bundle/bin:bundle/node")
        self.assertNotIn("DEEPSEEK_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertEqual(env["PYTHONUTF8"], "1")

    def test_frozen_path_is_executable_relative(self):
        with patch("tools.runtimes.sys.frozen", True, create=True), patch("tools.runtimes.sys.executable", str(Path("distribution/backend.exe").resolve())):
            self.assertEqual(runtime_root(), Path("distribution/runtimes/win32-x64").resolve())

    def test_bad_checksum_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            path.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                verify_archive(path, {"sha256": "0" * 64})

    def test_install_verification_checks_sources_not_regenerated_bytecode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").write_bytes(b"original")
            receipt = {"lock": {"version": 1}, "files": {"module.py": hashlib.sha256(b"original").hexdigest(), "__pycache__/module.pyc": "old"}}
            (root / "installed.json").write_text(json.dumps(receipt))
            verify_install(root, {"version": 1})
            (root / "module.py").write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "missing or modified"):
                verify_install(root, {"version": 1})

    def test_traversal_and_links_rejected(self):
        for name, kind in [("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "bad.tar"
                with tarfile.open(archive, "w") as stream:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    info.linkname = "outside"
                    stream.addfile(info, io.BytesIO(b""))
                with self.assertRaises(ValueError):
                    extract_archive(archive, root / "out")

    @unittest.skipUnless(os.name == "nt" and runtime_paths(), "Native Windows runtime bundle is not provisioned")
    def test_native_commands_without_host_path(self):
        env = build_child_env(inherit={"PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"), "TEMP": tempfile.gettempdir(), "TMP": tempfile.gettempdir()})
        lock = json.loads((runtime_root() / "installed.json").read_text())["lock"]
        checks = [(["node.exe", "--version"], "v" + lock["artifacts"]["node"]["version"]),
                  (["python.exe", "--version"], "Python " + lock["artifacts"]["python"]["version"]),
                  ([str(runtime_root() / "bin" / "pnpm.cmd"), "--version"], lock["artifacts"]["pnpm"]["version"])]
        for command, expected in checks:
            command[0] = shutil.which(command[0], path=env["PATH"]) or command[0]
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
        python = str(runtime_root() / "python" / "python.exe")
        result = subprocess.run([python, "-c", "import pip,venv,ssl,sqlite3,ctypes; print('stdlib and pip available')"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        scratch = Path(__file__).resolve().parent.parent / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            target = Path(directory)
            result = subprocess.run([python, "-m", "venv", str(target / "venv")], env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(target / "venv" / "Scripts" / "python.exe"), "-m", "pip", "--version"], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(target / "venv").lower(), result.stdout.lower())
            (target / "package.json").write_text(json.dumps({"private": True, "scripts": {"smoke": "node smoke.cjs"}}))
            (target / "smoke.cjs").write_text("console.log(process.execPath)")
            result = subprocess.run([str(runtime_root() / "bin" / "pnpm.cmd"), "run", "smoke"], cwd=target, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(any(Path(line.strip()).resolve() == (runtime_root() / "node" / "node.exe").resolve() for line in result.stdout.splitlines() if line.strip()), result.stdout)


if __name__ == "__main__":
    unittest.main()
