# tools/env.py
"""子进程环境变量：白名单透传，密钥一律不继承。

Agent 启动的 shell / 后台进程与后端本身共享同一个 OS 用户，但不应共享
后端的环境变量。最典型的问题是 ``.env`` 里的模型密钥被 ``load_dotenv()``
写进 ``os.environ`` 后，任意一条命令都能用 ``env`` / ``Get-ChildItem Env:``
把密钥打印出来。

因此这里只放行工具链真正需要的系统变量（``PATH``、``SystemRoot``、
``APPDATA``、``TEMP`` 等），再叠加极少数显式允许的前缀；其余一律丢弃。
白名单是主规则，密钥模式识别是第二道保险：即使以后有人往白名单里加变量，
``*_API_KEY`` / ``*_TOKEN`` / ``*_SECRET`` 之类仍会被剥离。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

#: 工具链跨平台运行所必需的系统变量（大小写不敏感）。
ALLOWED_ENV_KEYS: frozenset[str] = frozenset({
    # 进程与系统
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "OS", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
    # 用户目录与临时目录
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "ALLUSERSPROFILE", "PUBLIC", "TEMP", "TMP", "TMPDIR",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
    # 终端与 PowerShell
    "PSMODULEPATH", "POWERSHELL_DISTRIBUTION_CHANNEL", "TERM", "COLORTERM",
    "LANG", "LC_ALL", "LC_CTYPE", "SHELL", "USER", "USERNAME", "LOGNAME",
    "SESSIONNAME", "WT_SESSION",
    # WSL
    "WSL_DISTRO_NAME", "WSL_INTEROP", "WSLENV",
    # Git Bash / MSYS
    "MSYSTEM", "MINGW_PREFIX", "MINGW_CHOST", "MSYS", "CHERE_INVOKING", "EXEPATH",
    # Git / Python 工具链的少量无害变量
    "GIT_EXEC_PATH", "GIT_INSTALL_ROOT", "PYTHONIOENCODING", "PYTHONUTF8",
    "PYTHONUNBUFFERED", "NODE_OPTIONS",
})

#: 变量名命中这些模式时一律剥离（第二道保险）。
_SECRET_NAME = re.compile(
    r"(?i)(?:^|_)(?:API_?KEY|ACCESS_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|"
    r"CREDENTIALS?|PRIVATE_?KEY|AUTH|COOKIE)(?:_|$)"
)

#: 这些供应商 / 云平台前缀下的变量全部视为密钥。
_SECRET_PREFIXES = (
    "AWS_", "AZURE_", "GOOGLE_", "GCP_", "OPENAI_", "ANTHROPIC_", "DEEPSEEK_",
    "DASHSCOPE_", "QWEN_", "SILICONFLOW_", "MOONSHOT_", "MISTRAL_", "GROQ_",
    "COHERE_", "HF_", "HUGGINGFACE_", "GITHUB_", "GITLAB_", "NPM_TOKEN",
    "NPM_AUTH", "PYPI_TOKEN", "DOCKER_AUTH", "DOCKER_CONFIG", "KUBE",
    "VAULT_", "BITWARDEN_", "ONEPASSWORD_",
)


def is_secret_name(name: str) -> bool:
    """判断变量名是否属于密钥类（只按名字，不读取值）。"""
    upper = str(name or "").upper()
    if not upper:
        return True
    if upper.startswith(_SECRET_PREFIXES):
        return True
    return bool(_SECRET_NAME.search(upper))


def is_allowed_name(name: str) -> bool:
    """白名单内的变量才允许传给子进程。"""
    upper = str(name or "").upper()
    return upper in ALLOWED_ENV_KEYS and not is_secret_name(upper)


def build_child_env(
    overrides: Mapping[str, str] | None = None,
    *,
    inherit: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """构造干净的子进程环境。

    Args:
        overrides: 在本进程之上强制设置的变量（例如 UTF-8 相关开关）。
        inherit: 要读取的源环境，默认 ``os.environ``；测试可注入。

    Returns:
        只包含白名单变量的新字典。``PATH`` 缺失时会补一个安全默认值，
        避免子进程完全找不到命令。
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
