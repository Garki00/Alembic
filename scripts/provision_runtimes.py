"""Provision checksum-locked native runtimes; never called by agent command tools.

Run with a build Python: python scripts/provision_runtimes.py. Downloads are
cached in .tmp/runtime-downloads; --offline forbids network access. All archives
are verified before extraction. Existing installations are verified, not replaced.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parent.parent
LOCK = PROJECT / "packaging" / "runtimes.lock.json"


def verify_archive(path: Path, spec: dict) -> None:
    """Reject corrupt or substituted downloads before extracting any bytes."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != spec["sha256"]:
        raise ValueError(f"Checksum mismatch: {path.name}")
    if "integrity" in spec:
        expected = "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode("ascii")
        if expected != spec["integrity"]:
            raise ValueError(f"Registry integrity mismatch: {path.name}")


def extract_archive(archive: Path, destination: Path) -> None:
    """Extract only regular files/directories, rejecting traversal and links."""
    def target(name: str) -> Path:
        name = name.replace("\\", "/")
        part = PurePosixPath(name)
        if part.is_absolute() or ".." in part.parts or ":" in name:
            raise ValueError(f"Unsafe archive path: {name}")
        return destination.joinpath(*part.parts)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as stream:
            for member in stream.infolist():
                out = target(member.filename)
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Archive links are not allowed")
                if member.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                else:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with stream.open(member) as src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as stream:
            for member in stream:
                out = target(member.name)
                if member.isdir():
                    out.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with stream.extractfile(member) as src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                else:
                    raise ValueError("Archive links and special files are not allowed")


def verify_install(root: Path, lock: dict) -> None:
    """Check the receipt and immutable installed files before accepting a bundle."""
    receipt = json.loads((root / "installed.json").read_text(encoding="utf-8"))
    if receipt["lock"] != lock:
        raise ValueError("Installed runtimes do not match the runtime lock")
    for name, expected in receipt["files"].items():
        # Python regenerates bytecode on normal imports after relocation.
        # Source files and all executables remain checksum-verified.
        if "__pycache__" in PurePosixPath(name).parts or name.endswith((".pyc", ".pyo")):
            continue
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Installed runtime missing or modified: {name}")


def provision(*, offline: bool = False, verify_only: bool = False) -> Path:
    """Create a complete bundle atomically or verify the existing installation."""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    root = PROJECT / "runtimes" / lock["platform"]
    if root.exists():
        verify_install(root, lock)
        return root
    if verify_only:
        raise ValueError(f"Runtimes are not provisioned: {root}")
    cache = PROJECT / ".tmp" / "runtime-downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archives = {}
    for name, spec in lock["artifacts"].items():
        archive = cache / spec["filename"]
        if not archive.exists():
            if offline:
                raise ValueError(f"Offline archive missing: {archive}")
            partial = archive.with_suffix(archive.suffix + ".part")
            print(f"Downloading {name} {spec['version']}", flush=True)
            if not spec["url"].startswith("https://"):
                raise ValueError("Runtime downloads require HTTPS")
            with urllib.request.urlopen(spec["url"], timeout=120) as src:
                if not src.geturl().startswith("https://"):
                    raise ValueError("Runtime download redirected outside HTTPS")
                with partial.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            verify_archive(partial, spec)
            partial.replace(archive)
        verify_archive(archive, spec)
        archives[name] = archive
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".provision-", dir=root.parent) as temporary:
        staging = Path(temporary)
        bundle = staging / "bundle"
        bundle.mkdir()
        for name, archive in archives.items():
            unpacked = staging / name
            extract_archive(archive, unpacked)
            source = unpacked / lock["artifacts"][name]["directory"]
            shutil.move(str(source), str(bundle / name))
        (bundle / "bin").mkdir()
        (bundle / "bin" / "pnpm.cmd").write_text(
            '@echo off\nsetlocal\nset "PATH=%~dp0..\\node;%PATH%"\n"%~dp0..\\pnpm\\pnpm.exe" %*\nexit /b %errorlevel%\n', encoding="utf-8")
        files = {str(path.relative_to(bundle)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(bundle.rglob("*")) if path.is_file()}
        (bundle / "installed.json").write_text(json.dumps({"lock": lock, "files": files}, indent=2) + "\n", encoding="utf-8")
        bundle.rename(root)
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--verify", action="store_true")
    options = parser.parse_args()
    print(provision(offline=options.offline, verify_only=options.verify))
