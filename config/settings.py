# config/settings.py
"""运行时配置：全部来自环境变量（可通过项目根目录的 .env 注入）。

优化版本 - 参考 Claude Code 设计理念，支持复杂任务的处理能力
"""

import os
import sys
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_app_home() -> Path:
    """用户级数据目录"""
    env = (os.getenv("ALEMBIC_HOME", "").strip() or os.getenv("SMART_AGENT_HOME", "").strip())
    if env:
        return Path(env).expanduser().resolve()

    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "Alembic"


def resolve_default_workspace() -> Path:
    """首次运行时默认添加的工作区"""
    env = os.getenv("WORKSPACE_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path.home()
    return _PROJECT_ROOT


def _env_int(name: str, default: int) -> int:
    """读取正整数环境变量"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float, *, upper: float) -> float:
    """读取 (0, upper] 范围内的浮点环境变量"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if 0 < value <= upper else default


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


class Settings:
    # 模型配置
    model_name = os.getenv("MODEL_NAME", "")
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")

    # Agent 引擎
    agent_engine = os.getenv("AGENT_ENGINE", "tool_calling").strip().lower()
    if agent_engine not in {"react", "tool_calling"}:
        agent_engine = "tool_calling"

    # 数据目录
    app_home = str(resolve_app_home())
    legacy_root = str(_PROJECT_ROOT)

    # 会话与上下文
    memory_dir = os.getenv("MEMORY_DIR", os.path.join(app_home, "memory"))
    session_id = os.getenv("SESSION_ID", "default")

    # 网页工作区
    data_dir = os.getenv("DATA_DIR", os.path.join(app_home, "data"))
    models_file = os.getenv("MODELS_FILE", os.path.join(data_dir, "models.json"))
    tools_file = os.getenv("TOOLS_FILE", os.path.join(data_dir, "tools.json"))
    workspace_file = os.getenv("WORKSPACE_FILE", os.path.join(data_dir, "workspace.json"))
    preferences_file = os.getenv("PREFERENCES_FILE", os.path.join(data_dir, "preferences.json"))
    default_workspace = str(resolve_default_workspace())
    web_host = os.getenv("WEB_HOST", "127.0.0.1")
    web_port = _env_int("WEB_PORT", 8000)

    # 前后端分离
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

    # ============================================================================
    # 上下文管理优化
    # ============================================================================

    model_context_window = _env_int("MODEL_CONTEXT_WINDOW", 200000)
    compact_trigger_ratio = _env_float("COMPACT_TRIGGER_RATIO", 0.70, upper=1.0)
    compact_reserve_tokens = _env_int("COMPACT_RESERVE_TOKENS", 12000)
    max_context_tokens = _env_int("MAX_CONTEXT_TOKENS", 0)
    max_context_messages = _env_int("MAX_CONTEXT_MESSAGES", 60)
    keep_recent_messages = _env_int("KEEP_RECENT_MESSAGES", 12)
    max_tool_output_tokens = _env_int("MAX_TOOL_OUTPUT_TOKENS", 12000)

    # ============================================================================
    # 任务执行能力
    # ============================================================================

    # 执行模式：adaptive（自适应）、conservative（保守）、aggressive（激进）
    execution_mode: Literal["adaptive", "conservative", "aggressive"] = (
        os.getenv("EXECUTION_MODE", "adaptive").strip().lower()  # type: ignore
    )
    if execution_mode not in {"adaptive", "conservative", "aggressive"}:
        execution_mode = "adaptive"

    # 根据执行模式动态调整限制
    _base_model_calls = {"conservative": 8, "adaptive": 20, "aggressive": 40}
    max_model_calls = _env_int("MAX_MODEL_CALLS", _base_model_calls.get(execution_mode, 20))

    _base_tool_calls = {"conservative": 15, "adaptive": 50, "aggressive": 100}
    max_tool_calls = _env_int("MAX_TOOL_CALLS", _base_tool_calls.get(execution_mode, 50))

    model_max_retries = _env_int("MODEL_MAX_RETRIES", 3)

    # 并行工具调用
    enable_parallel_tool_calls = _env_bool("ENABLE_PARALLEL_TOOL_CALLS", True)
    max_parallel_tool_calls = _env_int("MAX_PARALLEL_TOOL_CALLS", 10)
    enable_tool_call_batching = _env_bool("ENABLE_TOOL_CALL_BATCHING", True)

    # ============================================================================
    # 工具运行时优化
    # ============================================================================

    max_tool_output_chars = _env_int("MAX_TOOL_OUTPUT_CHARS", 48000)
    command_timeout = _env_int("COMMAND_TIMEOUT", 300)
    long_running_timeout = _env_int("LONG_RUNNING_TIMEOUT", 1800)
    workspace_dir = os.getenv("WORKSPACE_DIR", "")

    # ============================================================================
    # 智能缓存与优化
    # ============================================================================

    enable_tool_output_cache = _env_bool("ENABLE_TOOL_OUTPUT_CACHE", True)
    tool_cache_ttl = _env_int("TOOL_CACHE_TTL", 300)
    enable_smart_compression = _env_bool("ENABLE_SMART_COMPRESSION", True)
    priority_tools = os.getenv("PRIORITY_TOOLS", "read_file,search_files,run_command,list_directory").split(",")
    enable_incremental_compression = _env_bool("ENABLE_INCREMENTAL_COMPRESSION", True)

    # ============================================================================
    # 任务分解与规划
    # ============================================================================

    enable_task_decomposition = _env_bool("ENABLE_TASK_DECOMPOSITION", True)
    task_complexity_threshold = _env_int("TASK_COMPLEXITY_THRESHOLD", 5)
    max_subtasks = _env_int("MAX_SUBTASKS", 20)
    enable_task_dag = _env_bool("ENABLE_TASK_DAG", True)

    # ============================================================================
    # 多Agent协作
    # ============================================================================

    enable_multi_agent = _env_bool("ENABLE_MULTI_AGENT", True)
    max_concurrent_agents = _env_int("MAX_CONCURRENT_AGENTS", 5)
    agent_roles = os.getenv("AGENT_ROLES", "planner,coder,reviewer,tester,documenter").split(",")
    enable_agent_communication = _env_bool("ENABLE_AGENT_COMMUNICATION", True)

    # ============================================================================
    # 错误恢复与重试
    # ============================================================================

    enable_auto_recovery = _env_bool("ENABLE_AUTO_RECOVERY", True)
    max_recovery_attempts = _env_int("MAX_RECOVERY_ATTEMPTS", 3)
    enable_exponential_backoff = _env_bool("ENABLE_EXPONENTIAL_BACKOFF", True)
    base_retry_delay = _env_int("BASE_RETRY_DELAY", 1)
    max_retry_delay = _env_int("MAX_RETRY_DELAY", 30)

    # ============================================================================
    # 审批与交互
    # ============================================================================

    approval_enabled = _env_bool("APPROVAL_ENABLED", True)
    approval_timeout = _env_int("APPROVAL_TIMEOUT", 600)
    question_timeout = _env_int("QUESTION_TIMEOUT", 600)
    enable_batch_approval = _env_bool("ENABLE_BATCH_APPROVAL", True)
    trusted_operations = os.getenv("TRUSTED_OPERATIONS", "read_file,list_directory,search_files").split(",")

    # ============================================================================
    # 性能监控
    # ============================================================================

    enable_performance_monitoring = _env_bool("ENABLE_PERFORMANCE_MONITORING", True)
    enable_adaptive_limits = _env_bool("ENABLE_ADAPTIVE_LIMITS", True)
    performance_sample_interval = _env_int("PERFORMANCE_SAMPLE_INTERVAL", 10)
    cpu_threshold = _env_int("CPU_THRESHOLD", 80)
    memory_threshold = _env_int("MEMORY_THRESHOLD", 85)

    # ============================================================================
    # 日志与调试
    # ============================================================================

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    enable_verbose_logging = _env_bool("ENABLE_VERBOSE_LOGGING", False)
    enable_profiling = _env_bool("ENABLE_PROFILING", False)
    log_rotation_size = _env_int("LOG_ROTATION_SIZE", 100)
    log_backup_count = _env_int("LOG_BACKUP_COUNT", 5)


settings = Settings()
