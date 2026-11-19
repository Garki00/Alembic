# tools/paths.py
"""Workspace path resolution: the single boundary check for file and command tools.

Every write to disk must go through :func:`resolve_in_workspace`, which performs three key steps:

1. ``resolve()`` first, then check containment. Symlinks, ``..``, and Windows
   drive-relative paths reveal their real location after resolving, so the real
   path is what gets checked;
2. ``Path.relative_to`` rather than string ``startswith``. The latter would treat
   ``D:\\work`` as containing ``D:\\workspace-evil``;
3. Inside the boundary, credential paths are excluded as well: even when the
   workspace is the user's home directory, ``.ssh``, ``.aws``, private keys, and
   ``.env`` cannot be read (``.env`` may be written, but its contents cannot be
   read back).

This third layer covers file tools only. Shell commands are governed by command
policy and approval, and must not be treated as an OS-level sandbox.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

Access = Literal["read", "write"]

#: Contents of these directories are treated as credentials; both reads and writes are rejected.
_SENSITIVE_DIRS = {".ssh", ".aws", ".kube", ".gnupg", ".docker"}

#: Read-sensitive directories: .docker's config.json may contain registry credentials.
_SENSITIVE_READ_DIRS = _SENSITIVE_DIRS

#: File names that are never readable.
_SENSITIVE_READ_NAMES = {
    ".netrc", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_dsa",
    "id_ecdsa", "id_ed25519",
}

#: File names that are never writable (.env may be written but not read).
_SENSITIVE_WRITE_NAMES = {".netrc"}

_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}

#: These .env variant names are templates and remain readable.
_ENV_TEMPLATE_SUFFIXES = {".example", ".sample", ".template", ".dist"}


class WorkspacePathError(ValueError):
    """A path is out of bounds, protected, or unresolvable; carries a machine-readable error code for ``Error:`` output."""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def normalize_workspace(raw: str | Path | None) -> Path | None:
    """Normalize an externally supplied workspace string into an absolute path; return None for empty input."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve()
    except OSError:
        return None


@lru_cache(maxsize=1)
def _app_home() -> Path | None:
    """Application data directory (resolved once and reused by path checks)."""
    try:
        from config.settings import settings

        return Path(settings.app_home).expanduser().resolve()
    except (ImportError, OSError):
        return None


def protected_reason(target: Path, root: Path | None, access: Access = "read") -> str:
    """Return why the path is a protected credential path; an empty string means it is allowed."""
    if root is None:
        return ""
    try:
        relative = target.relative_to(root)
    except ValueError:
        return ""
    parts = [part.lower() for part in relative.parts]
    dirs = _SENSITIVE_READ_DIRS if access == "read" else _SENSITIVE_DIRS
    for part in parts[:-1]:
        if part in dirs:
            return f"protected credential directory {part}"
    name = target.name.lower()
    if name in dirs:
        return f"protected credential directory {name}"

    # The application's own data directory (models.json / tools.json / sessions) is
    # outside workspace semantics and stays off-limits even if selected as a workspace.
    app_home = _app_home()
    if app_home is not None and (target == app_home or app_home in target.parents):
        return "application data directory"

    if access == "read":
        if name == ".env" or (
            name.startswith(".env.") and not any(name.endswith(s) for s in _ENV_TEMPLATE_SUFFIXES)
        ):
            return "environment variable file (may contain secrets)"
        if name in _SENSITIVE_READ_NAMES or name.startswith(("id_rsa.", "id_ed25519.")):
            return f"credential file {target.name}"
        if target.suffix.lower() in _SENSITIVE_SUFFIXES:
            return f"credential file {target.name}"
    else:
        if name in _SENSITIVE_WRITE_NAMES:
            return f"credential file {target.name}"

    return ""


def is_protected_read(target: Path, root: Path | None) -> bool:
    """Quick check used by recursive searches to skip protected read targets."""
    return bool(protected_reason(target, root, "read"))


def resolve_in_workspace(root: Path | None, raw: str, *, access: Access = "read") -> Path:
    """Resolve a path inside the workspace, rejecting any escape and any protected path.

    Args:
        root: The workspace root directory.
        raw: The path supplied by the user or model.
        access: ``read`` additionally rejects credential files; ``write`` rejects
            only credential paths that must never be written (``.env`` may be
            written, but cannot be read back afterwards).

    Raises:
        WorkspacePathError: No workspace, invalid path, path escape, or protected path.
    """
    if root is None:
        raise WorkspacePathError(
            "no_workspace",
            "No workspace is available, so files cannot be operated on.",
            hint="Select a workspace directory in the top bar first.",
        )

    text = (raw or "").strip()
    candidate = Path(text).expanduser() if text else Path(".")
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        target = candidate.resolve()
    except OSError as exc:
        raise WorkspacePathError(
            "bad_path",
            f"Cannot resolve path: {text or '.'} ({exc}).",
        ) from exc

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError(
            "path_escape",
            f"Path {text or '.'} is outside the current workspace; access denied.",
            hint=f"Current workspace: {root}. Use a relative path (for example src/main.py) or an absolute path inside the workspace.",
        ) from exc

    reason = protected_reason(target, root, access)
    if reason:
        raise WorkspacePathError(
            "protected_path",
            f"Cannot access {reason}: {text or '.'}.",
            hint="Manage credential files directly in the system; the Agent does not read or modify them.",
        )

    return target


def _credential_reason(target: Path) -> str:
    """Credential check that does not depend on a workspace root."""
    parts = [part.lower() for part in target.parts[:-1]]
    for part in parts:
        if part in _SENSITIVE_READ_DIRS:
            return f"protected credential directory {part}"
    name = target.name.lower()
    if name in _SENSITIVE_READ_DIRS:
        return f"protected credential directory {name}"
    if name == ".env" or (
        name.startswith(".env.") and not any(name.endswith(s) for s in _ENV_TEMPLATE_SUFFIXES)
    ):
        return "environment variable file (may contain secrets)"
    if name in _SENSITIVE_READ_NAMES or name.startswith(("id_rsa.", "id_ed25519.")):
        return f"credential file {target.name}"
    if target.suffix.lower() in _SENSITIVE_SUFFIXES:
        return f"credential file {target.name}"
    return ""


def resolve_readable_path(raw: str, *, root: Path | None = None) -> Path:
    """Resolve a path for the side-panel preview, including paths outside the workspace.

    Read-only inspection of logs and diffs often points outside the workspace, so
    containment is not required here. Credential files stay blocked, and the
    application's ``data`` directory (provider keys, sessions) stays off-limits.

    Raises:
        WorkspacePathError: Invalid path or a protected target.
    """
    text = (raw or "").strip()
    candidate = Path(text).expanduser() if text else Path(".")
    if not candidate.is_absolute() and root is not None:
        candidate = root / candidate

    try:
        target = candidate.resolve()
    except OSError as exc:
        raise WorkspacePathError(
            "bad_path",
            f"Cannot resolve path: {text or '.'} ({exc}).",
        ) from exc

    reason = _credential_reason(target)
    if reason:
        raise WorkspacePathError(
            "protected_path",
            f"Cannot access {reason}: {text or '.'}.",
            hint="Manage credential files directly in the system.",
        )

    try:
        from config.settings import settings

        data_dir = Path(settings.data_dir).expanduser().resolve()
    except (ImportError, OSError):
        data_dir = None
    if data_dir is not None and (target == data_dir or data_dir in target.parents):
        raise WorkspacePathError(
            "protected_path",
            f"Cannot access the application data directory: {text or '.'}.",
            hint="Provider keys and session files are not previewable.",
        )
    return target


def display_path(root: Path | None, target: Path) -> str:
    """Convert an absolute path into a workspace-relative display name for reuse by the model."""
    if root is None:
        return str(target)
    try:
        rel = target.relative_to(root)
    except ValueError:
        return str(target)
    return str(rel).replace("\\", "/") or "."
