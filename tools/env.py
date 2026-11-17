# tools/env.py
"""Child process environment variables: whitelist passthrough, never inherit keys.

The environment is filtered before a child process starts: only system variables
the toolchain needs (``PATH``, ``SystemRoot``, ``APPDATA``, ``TEMP``, etc.) and
a few explicitly allowed prefixes are passed through. Secret-name detection is a
second safeguard, so names such as ``*_API_KEY``, ``*_TOKEN``, and ``*_SECRET``
are stripped even if added to the whitelist.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

#: System variables required for cross-platform toolchain execution (case-insensitive).
ALLOWED_ENV_KEYS: frozenset[str] = frozenset({
    # Process and system
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "OS", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
    # User and temporary directories
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "ALLUSERSPROFILE", "PUBLIC", "TEMP", "TMP", "TMPDIR",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
    # Terminal and PowerShell
    "PSMODULEPATH", "POWERSHELL_DISTRIBUTION_CHANNEL", "TERM", "COLORTERM",
    "LANG", "LC_ALL", "LC_CTYPE", "SHELL", "USER", "USERNAME", "LOGNAME",
    "SESSIONNAME", "WT_SESSION",
    # WSL
    "WSL_DISTRO_NAME", "WSL_INTEROP", "WSLENV",
    # Git Bash / MSYS
    "MSYSTEM", "MINGW_PREFIX", "MINGW_CHOST", "MSYS", "CHERE_INVOKING", "EXEPATH",
    # A few harmless Git / Python toolchain variables
    "GIT_EXEC_PATH", "GIT_INSTALL_ROOT", "PYTHONIOENCODING", "PYTHONUTF8",
    "PYTHONUNBUFFERED", "NODE_OPTIONS",
})

#: Names matching these patterns are always stripped (second safeguard).
_SECRET_NAME = re.compile(
    r"(?i)(?:^|_)(?:API_?KEY|ACCESS_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|"
    r"CREDENTIALS?|PRIVATE_?KEY|AUTH|COOKIE)(?:_|$)"
)

#: Variables under these vendor / cloud prefixes are all treated as secrets.
_SECRET_PREFIXES = (
    "AWS_", "AZURE_", "GOOGLE_", "GCP_", "OPENAI_", "ANTHROPIC_", "DEEPSEEK_",
    "DASHSCOPE_", "QWEN_", "SILICONFLOW_", "MOONSHOT_", "MISTRAL_", "GROQ_",
    "COHERE_", "HF_", "HUGGINGFACE_", "GITHUB_", "GITLAB_", "NPM_TOKEN",
    "NPM_AUTH", "PYPI_TOKEN", "DOCKER_AUTH", "DOCKER_CONFIG", "KUBE",
    "VAULT_", "BITWARDEN_", "ONEPASSWORD_",
)


def is_secret_name(name: str) -> bool:
    """Return whether a variable name belongs to a secret class (name only, value not read)."""
    upper = str(name or "").upper()
    if not upper:
        return True
    if upper.startswith(_SECRET_PREFIXES):
        return True
    return bool(_SECRET_NAME.search(upper))


def is_allowed_name(name: str) -> bool:
    """Return whether a whitelisted variable may be passed to child processes."""
    upper = str(name or "").upper()
    return upper in ALLOWED_ENV_KEYS and not is_secret_name(upper)


def build_child_env(
    overrides: Mapping[str, str] | None = None,
    *,
    inherit: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a clean child process environment.

    Args:
        overrides: Variables to force-set on top of this process (for example
            UTF-8 switches).
        inherit: Source environment to read; defaults to ``os.environ`` and can
            be injected by tests.

    Returns:
        A new dictionary containing only whitelisted variables. When ``PATH`` is
        missing, a safe default is added so child processes can still find commands.
    """
    source = os.environ if inherit is None else inherit
    env = {
        str(key).upper(): str(value)
        for key, value in source.items()
        if is_allowed_name(key) and value is not None
    }
    if "PATH" not in env:
        env["PATH"] = os.defpath
    for key, value in (overrides or {}).items():
        env[str(key)] = str(value)
    return env
