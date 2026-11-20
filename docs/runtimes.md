# Portable command runtimes

ALEMBIC bundles native Windows x64 Node.js, pnpm and a full CPython distribution. Commands receive these directories first on their sanitized PATH. No runtime is downloaded when the agent executes a command. Existing command approval and the Windows sandbox remain mandatory; missing sandbox support still fails closed.

## Build and provision

From the repository root, using the build Python:

```powershell
.\.venv\Scripts\python.exe scripts/provision_runtimes.py
.\.venv\Scripts\python.exe scripts/provision_runtimes.py --offline --verify
```

The first command downloads only the artifacts pinned in `packaging/runtimes.lock.json`, checks SHA-256 (and npm SHA-512 integrity for pnpm), rejects archive traversal and links, and atomically installs `runtimes/win32-x64`. Downloads are cached under `.tmp/runtime-downloads`. `--offline` forbids downloads; `--verify` requires an existing complete installation and checks its receipt and immutable file hashes (regenerable Python bytecode caches are excluded). A failed or altered existing installation is reported instead of silently overwritten. A release update requires deliberately updating the lock and provisioning a fresh bundle.

The locked versions were checked against official metadata on 2026-11-20:

| Component | Version | Source |
| --- | --- | --- |
| Node.js | 26.13.1, Lithium LTS | https://nodejs.org/dist/v26.13.1/SHASUMS256.txt |
| pnpm native executable | 12.21.0 | https://registry.npmjs.org/@pnpm/exe.win32-x64/12.21.0 |
| CPython | 3.13.18, standalone build 20261110 | https://github.com/astral-sh/python-build-standalone/releases/tag/20261110 |

CPython includes pip, venv, SSL and the normal standard library. pnpm's launcher explicitly prepends the bundled Node directory so package scripts use the same Node version. Package installation still depends on the session's network policy and writable workspace; bundling a package manager does not grant network access.

`packaging/build.ps1` provisions before PyInstaller runs. `packaging/backend.spec` refuses a missing or mismatched bundle and copies the complete directory, including upstream licenses, alongside `alembic-backend.exe`. The binaries are reproducible build artifacts and are excluded from Git. The build machine still needs the project's build environment; the resulting runtime bundle does not depend on host Node, pnpm or Python installations.

## Execution and verification

`tools/env.py` filters inherited and override environment variables before adding bundled paths. Model API credentials are not inherited. The Windows sandbox grants read/execute access to the fixed application bundle. No model-provided runtime directory is accepted.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p runtimes_test.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p env_test.py
```

The native smoke test empties the host PATH, runs all three executables, creates a virtual environment with pip, imports standard-library extension modules, and runs a pnpm package script that verifies its Node executable location. On other platforms or without a provisioned bundle, the native execution test is skipped explicitly. Native Windows executables are not Linux interpreters inside WSL; this bundle targets the Windows sandbox's native terminals.

Validation on 2026-11-20 also exercised the real Windows local-user sandbox through `tools.shell` with an empty host PATH and offline network policy: Node and Python versions, creation of a workspace venv and its pip, and pnpm all completed with exit code 0. The copied PyInstaller distribution bundle passed the same empty-PATH executable/venv/pnpm script smoke test.
