# config/settings.py
"""运行时配置：全部来自环境变量（可通过项目根目录的 .env 注入）。"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # 把 .env 里的变量注入进程环境

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_app_home() -> Path:
    """用户级数据目录。

    优先级：``SMART_AGENT_HOME`` → 平台约定目录下的 ``SmartAgent``：

    - Windows：``%APPDATA%\\SmartAgent``
    - macOS：``~/Library/Application Support/SmartAgent``
    - Linux：``$XDG_DATA_HOME/SmartAgent``（默认 ``~/.local/share/SmartAgent``）

    安装目录可能是只读的（Program Files），用户数据不能写在代码旁边。
    """
    env = os.getenv("SMART_AGENT_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()

    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "SmartAgent"


def resolve_default_workspace() -> Path:
    """首次运行时默认添加的工作区。

    源码运行用仓库根目录；打包后代码在临时解压目录里，改成用户主目录。
    """
    env = os.getenv("WORKSPACE_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):  # PyInstaller 等打包产物
        return Path.home()
    return _PROJECT_ROOT


def _env_int(name: str, default: int) -> int:
    """读取正整数环境变量，非法值退回默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float, *, upper: float) -> float:
    """读取 (0, upper] 范围内的浮点环境变量，非法值退回默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if 0 < value <= upper else default


class Settings:
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")

    # Agent 执行范式：react（原生 ReAct 文本循环）或 tool_calling（LangChain create_agent）
    agent_engine = os.getenv("AGENT_ENGINE", "react").strip().lower()
    if agent_engine not in {"react", "tool_calling"}:
        agent_engine = "react"

    # ---- 数据目录（默认在用户级目录，见 resolve_app_home）----
    app_home = str(resolve_app_home())
    legacy_root = str(_PROJECT_ROOT)

    # ---- 会话与上下文 ----
    memory_dir = os.getenv("MEMORY_DIR", os.path.join(app_home, "memory"))
    session_id = os.getenv("SESSION_ID", "default")  # 默认会话 ID

    # ---- 网页工作区 ----
    data_dir = os.getenv("DATA_DIR", os.path.join(app_home, "data"))
    models_file = os.getenv("MODELS_FILE", os.path.join(data_dir, "models.json"))
    tools_file = os.getenv("TOOLS_FILE", os.path.join(data_dir, "tools.json"))
    workspace_file = os.getenv("WORKSPACE_FILE", os.path.join(data_dir, "workspace.json"))
    preferences_file = os.getenv("PREFERENCES_FILE", os.path.join(data_dir, "preferences.json"))
    default_workspace = str(resolve_default_workspace())
    web_host = os.getenv("WEB_HOST", "127.0.0.1")
    web_port = _env_int("WEB_PORT", 8000)

    # ---- 前后端分离 ----
    # 前端默认由 Vite 开发服务器提供；后端只提供 /api，不再渲染页面。
    frontend_url = os.getenv("FRONTEND_URL", "http://127.0.0.1:5173")
    cors_origins = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,"
            "http://127.0.0.1:4173,http://localhost:4173",
        ).split(",")
        if origin.strip()
    ]

    # 模型窗口：压缩阈值默认 = 窗口 × 触发比例 − 预留（给回复和摘要请求留余量）
    model_context_window = _env_int("MODEL_CONTEXT_WINDOW", 131072)
    compact_trigger_ratio = _env_float("COMPACT_TRIGGER_RATIO", 0.8, upper=1.0)
    compact_reserve_tokens = _env_int("COMPACT_RESERVE_TOKENS", 8000)

    # 绝对阈值覆盖（0 表示按上面的窗口比例推导）
    max_context_tokens = _env_int("MAX_CONTEXT_TOKENS", 0)
    max_context_messages = _env_int("MAX_CONTEXT_MESSAGES", 40)  # 消息条数上限
    keep_recent_messages = _env_int("KEEP_RECENT_MESSAGES", 8)  # 压缩时保留的最近消息
    max_tool_output_tokens = _env_int("MAX_TOOL_OUTPUT_TOKENS", 8000)  # 单条工具输出写入历史的预算

    # ---- 运行保护 ----
    max_model_calls = _env_int("MAX_MODEL_CALLS", 8)  # 单轮模型调用上限
    max_tool_calls = _env_int("MAX_TOOL_CALLS", 15)  # 单轮工具调用上限
    model_max_retries = _env_int("MODEL_MAX_RETRIES", 2)  # 模型调用失败重试次数

    # ---- 工具运行时 ----
    max_tool_output_chars = _env_int("MAX_TOOL_OUTPUT_CHARS", 20000)  # 单个工具输出的字符上限
    command_timeout = _env_int("COMMAND_TIMEOUT", 60)  # run_command 默认超时（秒）
    workspace_dir = os.getenv("WORKSPACE_DIR", "")  # 命令行模式的默认工作区，留空用当前目录

    # ---- 工具审批与提问 ----
    approval_enabled = os.getenv("APPROVAL_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    approval_timeout = _env_int("APPROVAL_TIMEOUT", 300)  # 等待人确认的秒数，超时按拒绝
    question_timeout = _env_int("QUESTION_TIMEOUT", 300)  # 等待用户回答的秒数，超时按未回答


settings = Settings()
