# tools/registry.py
"""Tool registry: the single source of truth for tools.

Register a tool once in :data:`TOOL_SPECS`; consumers derive from it:

- Web settings panel ← :data:`TOOL_SPECS` (config fields and risk level);
- Web chat ← :func:`build_tools` (assembled from enabled state and current configuration);
- CLI ← :func:`build_tools` + :func:`default_enabled_names`.

Each entry exposes one tool with its own enable switch and configuration.
Legacy group names remain accepted by the assembly API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from tools.context import ToolContext
from tools.atomic_edit import build_atomic_edit_tools
from tools.catalog import build_tools_search
from tools.documents import build_document_tools
from tools.download import build_download_tools
from tools.fetch import build_fetch_tools
from tools.files import build_file_read_tools, build_file_write_tools
from tools.git import build_git_tools
from tools.images import build_image_tools
from tools.mock import build_mock_tools
from tools.patch import build_patch_tools
from tools.processes import build_process_tools
from tools.questions import build_question_tools
from tools.search import build_search_tools
from tools.shell import build_shell_tools
from tools.subagent import build_subagent_tools
from tools.time import get_current_time, list_timezones
from tools.todo import build_todo_tools
from tools.weather import build_weather_tools

logger = logging.getLogger(__name__)

# Risk levels: read = read-only; write = modifies files in the workspace; exec = runs arbitrary commands.
RISK_READ = "read"
RISK_WRITE = "write"
RISK_EXEC = "exec"

# Capability matrix values, aligned with the permission engine's vocabulary.
SIDE_EFFECT_NONE = "none"
SIDE_EFFECT_WORKSPACE = "workspace"
SIDE_EFFECT_GIT = "git"
SIDE_EFFECT_NETWORK = "network"
SIDE_EFFECT_SYSTEM = "system"
SIDE_EFFECT_SESSION = "session"
SIDE_EFFECT_USER_INTERACTION = "userInteraction"
SIDE_EFFECT_SCOPES = (
    SIDE_EFFECT_NONE,
    SIDE_EFFECT_WORKSPACE,
    SIDE_EFFECT_GIT,
    SIDE_EFFECT_NETWORK,
    SIDE_EFFECT_SYSTEM,
    SIDE_EFFECT_SESSION,
    SIDE_EFFECT_USER_INTERACTION,
)
RISK_LEVELS = ("low", "medium", "high", "critical")

# Field kinds: determine the settings-panel control and how values are validated on save.
FIELD_TEXT = "text"
FIELD_NUMBER = "number"
FIELD_SELECT = "select"
FIELD_SWITCH = "switch"
FIELD_RADIO = "radio"
FIELD_KINDS = (FIELD_TEXT, FIELD_NUMBER, FIELD_SELECT, FIELD_SWITCH, FIELD_RADIO)

Builder = Callable[[Mapping[str, str], ToolContext], Sequence]


@dataclass(frozen=True)
class ToolField:
    """One editable tool parameter in the settings panel.

    Every field except ``key`` / ``label`` has a default, so new tools can use only
    what they need:

    - ``secret``: secret value, never echoed back; empty means keep the current value;
    - ``multiline``: multi-line text box (effective when ``kind="text"``);
    - ``kind``: control type, see ``FIELD_KINDS``;
    - ``options``: candidates for ``kind="select"`` or ``kind="radio"``, as ``((value, display name), ...)``;
    - ``enabled_when``: optional ``(field_key, value)`` rule for dimming the field in the panel;
    - ``required``: UI marker only (the tool itself reports whether the value is empty);
    - ``default``: initial value when the configuration file has no such key.
    """

    key: str
    label: str
    secret: bool = False
    multiline: bool = False
    placeholder: str = ""
    kind: str = FIELD_TEXT
    options: tuple[tuple[str, str], ...] = ()
    required: bool = False
    default: str = ""
    enabled_when: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"未知的字段类型 {self.kind!r}（{self.key}）")
        if self.kind in {FIELD_SELECT, FIELD_RADIO} and not self.options:
            raise ValueError(f"字段 {self.key} 是 {self.kind}，但没有给出 options")

    def public(self) -> dict:
        """Frontend-facing structure; value-related fields are filled in by :class:`ToolStore`."""
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "multiline": self.multiline,
            "placeholder": self.placeholder,
            "kind": self.kind,
            "options": [{"value": value, "label": label} for value, label in self.options],
            "required": self.required,
            "enabled_when": (
                {"key": self.enabled_when[0], "value": self.enabled_when[1]}
                if self.enabled_when
                else None
            ),
        }

    def normalize(self, value: str) -> str:
        """Validate and normalize the value to save according to the field type.

        Raises:
            ValueError: The value does not match the field type (converted to 400 by the API).
        """
        text = str(value)
        if self.kind == FIELD_SWITCH:
            return "true" if text.strip().lower() in {"1", "true", "on", "yes"} else "false"
        if self.kind == FIELD_NUMBER and text.strip():
            try:
                float(text)
            except ValueError as exc:
                raise ValueError(f"「{self.label}」需要填写数字") from exc
        if self.kind in {FIELD_SELECT, FIELD_RADIO}:
            allowed = {item for item, _label in self.options}
            if text.strip() and text not in allowed:
                raise ValueError(f"「{self.label}」只能是：{'、'.join(sorted(allowed))}")
        return text


@dataclass(frozen=True)
class ToolSpec:
    """One tool entry that can be enabled or disabled.

    Attributes:
        name: Entry name, also the key for /api/tools/{name} and data/tools.json.
        label: Display name in the settings panel.
        description: One-line description shown to the user.
        fields: Parameters the user needs to fill in.
        risk: Risk level, see RISK_*.
        default_enabled: Whether the entry is enabled by default when data/tools.json is first generated.
        experimental: Whether the entry is marked experimental in the settings panel.
        tool_names: Names of the tool functions actually exposed to the model (one per entry).
        builder: Assembly function of type ``(config, ctx) -> Sequence[BaseTool]``.
        source: Built-in or externally installed source classification.
        source_id: Stable identifier of the supplying plugin or MCP server.
    """

    name: str
    label: str
    description: str
    fields: tuple[ToolField, ...] = ()
    risk: str = RISK_READ
    default_enabled: bool = True
    experimental: bool = False
    tool_names: tuple[str, ...] = ()
    builder: Builder | None = field(default=None, repr=False)
    source: str = "builtin"
    source_id: str = ""


# ---------------------------------------------------------------------------
# Assembly functions
# ---------------------------------------------------------------------------
def _build_time(_config: Mapping[str, str], _ctx: ToolContext) -> Sequence:
    return [get_current_time]


def _build_timezone_list(_config: Mapping[str, str], _ctx: ToolContext) -> Sequence:
    return [list_timezones]


def _build_weather(config: Mapping[str, str], _ctx: ToolContext) -> Sequence:
    return build_weather_tools(dict(config))


def _build_file_read(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_file_read_tools(ctx)


def _build_file_write(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_file_write_tools(ctx)


def _build_documents(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_document_tools(ctx)


def _build_images(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_image_tools(ctx)


def _build_fetch(config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_fetch_tools(dict(config), ctx)


def _build_search(config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_search_tools(dict(config), ctx)


def _build_download(config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_download_tools(dict(config), ctx)


def _build_patch(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_patch_tools(ctx)


def _build_atomic_edit(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_atomic_edit_tools(ctx)


def _build_todo(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_todo_tools(ctx)


def _build_questions(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_question_tools(ctx)


def _build_process_exec(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_process_tools(ctx, only={"start_process", "stop_process"})


def _build_process_inspect(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_process_tools(ctx, only={"list_processes", "read_process_logs", "wait_process"})


def _build_git_read(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_git_tools(ctx, only={"git_status", "git_diff"})


def _build_git_commit(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_git_tools(ctx, only={"git_commit"})


def _build_shell(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_shell_tools(ctx)


def _build_subagent(_config: Mapping[str, str], ctx: ToolContext) -> Sequence:
    return build_subagent_tools(ctx)


def _build_mock(config: Mapping[str, str], _ctx: ToolContext) -> Sequence:
    return build_mock_tools(dict(config))


def _single_tool(builder: Builder, name: str) -> Builder:
    """Reuse the original implementation and schema for exactly one tool."""
    def build(config, ctx):
        return [tool for tool in builder(config, ctx) if tool.name == name]
    return build


LEGACY_TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "file_read": ("list_dir", "read_file", "search_files", "find_files", "file_stat"),
    "file_write": ("write_file", "edit_file", "make_dir", "delete_file", "move_file", "copy_file"),
    "background_process": ("start_process", "stop_process"),
    "process_inspect": ("list_processes", "read_process_logs", "wait_process"),
    "git_read": ("git_status", "git_diff"),
}


def expand_enabled_names(enabled: set[str] | None) -> set[str]:
    """Expand retired entry names without changing individual tool choices."""
    if enabled is None:
        return set(TOOL_SPECS)
    return {member for name in enabled for member in LEGACY_TOOL_GROUPS.get(name, (name,))}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
TOOL_SPECS: dict[str, ToolSpec] = {
    "get_current_time": ToolSpec(
        name="get_current_time",
        label="时间查询",
        description="获取当前时间",
        tool_names=("get_current_time",),
        builder=_build_time,
    ),
    "list_timezones": ToolSpec(
        name="list_timezones",
        label="时区检索",
        description="查询支持的时区",
        tool_names=("list_timezones",),
        builder=_build_timezone_list,
    ),
    "list_dir": ToolSpec(
        name="list_dir", label="目录浏览", description="列出目录内容",
        risk=RISK_READ, tool_names=("list_dir",),
        builder=_single_tool(_build_file_read, "list_dir"),
    ),
    "read_file": ToolSpec(
        name="read_file", label="文件读取", description="读取文本文件",
        risk=RISK_READ, tool_names=("read_file",),
        builder=_single_tool(_build_file_read, "read_file"),
    ),
    "search_files": ToolSpec(
        name="search_files", label="内容搜索", description="搜索文件内容",
        risk=RISK_READ, tool_names=("search_files",),
        builder=_single_tool(_build_file_read, "search_files"),
    ),
    "find_files": ToolSpec(
        name="find_files", label="文件查找", description="按名称查找文件",
        risk=RISK_READ, tool_names=("find_files",),
        builder=_single_tool(_build_file_read, "find_files"),
    ),
    "file_stat": ToolSpec(
        name="file_stat", label="文件信息", description="查看文件元数据",
        risk=RISK_READ, tool_names=("file_stat",),
        builder=_single_tool(_build_file_read, "file_stat"),
    ),
    "read_document": ToolSpec(
        name="read_document",
        label="文档读取",
        description="读取 PDF、Office 与文本文件",
        risk=RISK_READ,
        tool_names=("read_document",),
        builder=_build_documents,
    ),
    "read_image": ToolSpec(
        name="read_image",
        label="图片读取",
        description="识别图片内容，需视觉模型",
        risk=RISK_READ,
        tool_names=("read_image",),
        builder=_build_images,
    ),
    "write_file": ToolSpec(
        name="write_file", label="文件写入", description="写入文件内容",
        risk=RISK_WRITE, tool_names=("write_file",),
        builder=_single_tool(_build_file_write, "write_file"),
    ),
    "edit_file": ToolSpec(
        name="edit_file", label="文件编辑", description="精确替换文件内容",
        risk=RISK_WRITE, tool_names=("edit_file",),
        builder=_single_tool(_build_file_write, "edit_file"),
    ),
    "make_dir": ToolSpec(
        name="make_dir", label="创建目录", description="创建工作区目录",
        risk=RISK_WRITE, tool_names=("make_dir",),
        builder=_single_tool(_build_file_write, "make_dir"),
    ),
    "delete_file": ToolSpec(
        name="delete_file", label="删除文件", description="删除文件或目录",
        risk=RISK_WRITE, tool_names=("delete_file",),
        builder=_single_tool(_build_file_write, "delete_file"),
    ),
    "move_file": ToolSpec(
        name="move_file", label="移动文件", description="移动文件或目录",
        risk=RISK_WRITE, tool_names=("move_file",),
        builder=_single_tool(_build_file_write, "move_file"),
    ),
    "copy_file": ToolSpec(
        name="copy_file", label="复制文件", description="复制文件或目录",
        risk=RISK_WRITE, tool_names=("copy_file",),
        builder=_single_tool(_build_file_write, "copy_file"),
    ),
    "apply_patch": ToolSpec(
        name="apply_patch",
        label="补丁编辑",
        description="用 unified diff 批量修改文件",
        risk=RISK_WRITE,
        tool_names=("apply_patch",),
        builder=_build_patch,
    ),
    "batch_edit": ToolSpec(
        name="batch_edit",
        label="原子批量编辑",
        description="多处精确替换，全部成功才落盘",
        risk=RISK_WRITE,
        tool_names=("batch_edit",),
        builder=_build_atomic_edit,
    ),
    "fetch_url": ToolSpec(
        name="fetch_url",
        label="网页抓取",
        description="抓取网页或接口内容",
        fields=(
            ToolField(
                "FETCH_MODE",
                "抓取方式",
                kind=FIELD_RADIO,
                options=(("local", "本地抓取"), ("api", "Fetch API")),
                default="local",
            ),
            ToolField(
                "FETCH_API_URL",
                "Fetch API 地址",
                placeholder="https://example.com/fetch?url={url_encoded}",
                enabled_when=("FETCH_MODE", "api"),
            ),
            ToolField("FETCH_TIMEOUT", "超时（秒）", kind=FIELD_NUMBER, default="20"),
            ToolField("FETCH_MAX_BYTES", "最大响应字节", kind=FIELD_NUMBER, default="2000000"),
            ToolField(
                "FETCH_ALLOW_PRIVATE",
                "允许内网地址",
                kind=FIELD_SWITCH,
                default="false",
                placeholder="仅在本机接口调试时打开",
            ),
            ToolField(
                "FETCH_USER_AGENT",
                "User-Agent",
                default="ALEMBIC/0.1 (+local)",
                enabled_when=("FETCH_MODE", "local"),
            ),
            ToolField(
                "FETCH_RENDER",
                "动态渲染",
                kind=FIELD_SWITCH,
                default="true",
                enabled_when=("FETCH_MODE", "local"),
            ),
            ToolField(
                "FETCH_RENDER_TIMEOUT",
                "渲染超时（秒）",
                kind=FIELD_NUMBER,
                default="12",
                enabled_when=("FETCH_MODE", "local"),
            ),
        ),
        risk=RISK_READ,
        tool_names=("fetch_url",),
        builder=_build_fetch,
    ),
    "web_search": ToolSpec(
        name="web_search",
        label="网页搜索",
        description="搜索网页，默认 Bing",
        fields=(
            ToolField(
                "SEARCH_PROVIDER",
                "搜索服务",
                kind=FIELD_SELECT,
                options=(
                    ("bing", "Bing（免密钥）"),
                    ("duckduckgo", "DuckDuckGo（免密钥）"),
                    ("tavily", "Tavily"),
                    ("brave", "Brave Search"),
                    ("serper", "Serper"),
                    ("searxng", "SearXNG"),
                ),
                default="bing",
            ),
            ToolField("SEARCH_API_KEY", "API Key", secret=True),
            ToolField(
                "SEARCH_ENDPOINT",
                "SearXNG 地址",
                placeholder="https://searx.example.com",
            ),
            ToolField("SEARCH_TIMEOUT", "超时（秒）", kind=FIELD_NUMBER, default="15"),
            ToolField("SEARCH_MAX_RESULTS", "默认结果数", kind=FIELD_NUMBER, default="5"),
        ),
        risk=RISK_READ,
        tool_names=("web_search",),
        builder=_build_search,
    ),
    "download_file": ToolSpec(
        name="download_file",
        label="文件下载",
        description="下载文件到工作区",
        fields=(
            ToolField("DOWNLOAD_TIMEOUT", "超时（秒）", kind=FIELD_NUMBER, default="60"),
            ToolField("DOWNLOAD_MAX_BYTES", "最大字节", kind=FIELD_NUMBER, default="20000000"),
            ToolField(
                "DOWNLOAD_ALLOW_PRIVATE",
                "允许内网地址",
                kind=FIELD_SWITCH,
                default="false",
                placeholder="仅在本机接口调试时打开",
            ),
            ToolField("DOWNLOAD_USER_AGENT", "User-Agent", default="ALEMBIC/0.1 (+local)"),
        ),
        risk=RISK_WRITE,
        tool_names=("download_file",),
        builder=_build_download,
    ),
    "todo_write": ToolSpec(
        name="todo_write",
        label="任务清单",
        description="维护会话任务清单",
        risk=RISK_READ,
        tool_names=("todo_write",),
        builder=_build_todo,
    ),
    "ask_user_question": ToolSpec(
        name="ask_user_question",
        label="向用户提问",
        description="向用户提问并等待回答",
        risk=RISK_READ,
        tool_names=("ask_user_question",),
        builder=_build_questions,
    ),
    "agent": ToolSpec(
        name="agent",
        label="子代理",
        description="把独立任务委派给子代理并汇报结果",
        risk=RISK_READ,
        tool_names=("agent", "send_message"),
        builder=_build_subagent,
    ),
    "praise_subagent": ToolSpec(
        name="praise_subagent",
        label="夸夸子代理",
        description="子代理完成后，点评其中完成得好的部分",
        risk=RISK_READ,
        default_enabled=False,
        experimental=True,
    ),
    "tools_search": ToolSpec(
        name="tools_search",
        label="扩展工具搜索",
        description="搜索并加载扩展工具",
        fields=(
            ToolField(
                "TOOLS_SEARCH_LIMIT",
                "每次最多加载",
                kind=FIELD_NUMBER,
                default="3",
                placeholder="1-10，默认 3",
            ),
        ),
        risk=RISK_READ,
        default_enabled=True,
        tool_names=("tools_search",),
        builder=build_tools_search,
    ),
    "run_command": ToolSpec(
        name="run_command",
        label="命令执行",
        description="执行 shell 命令（测试、构建、脚本、Git）",
        risk=RISK_EXEC,
        default_enabled=True,
        tool_names=("run_command",),
        builder=_build_shell,
    ),
    "start_process": ToolSpec(
        name="start_process", label="启动进程", description="启动后台任务",
        risk=RISK_EXEC, tool_names=("start_process",),
        builder=_single_tool(_build_process_exec, "start_process"),
    ),
    "stop_process": ToolSpec(
        name="stop_process", label="停止进程", description="停止后台任务",
        risk=RISK_EXEC, tool_names=("stop_process",),
        builder=_single_tool(_build_process_exec, "stop_process"),
    ),
    "list_processes": ToolSpec(
        name="list_processes", label="进程列表", description="列出后台任务",
        risk=RISK_READ, tool_names=("list_processes",),
        builder=_single_tool(_build_process_inspect, "list_processes"),
    ),
    "read_process_logs": ToolSpec(
        name="read_process_logs", label="进程日志", description="读取后台任务输出",
        risk=RISK_READ, tool_names=("read_process_logs",),
        builder=_single_tool(_build_process_inspect, "read_process_logs"),
    ),
    "wait_process": ToolSpec(
        name="wait_process", label="等待进程", description="等待后台任务或日志",
        risk=RISK_READ, tool_names=("wait_process",),
        builder=_single_tool(_build_process_inspect, "wait_process"),
    ),
    "git_status": ToolSpec(
        name="git_status", label="Git 状态", description="查看工作区状态",
        risk=RISK_READ, tool_names=("git_status",),
        builder=_single_tool(_build_git_read, "git_status"),
    ),
    "git_diff": ToolSpec(
        name="git_diff", label="Git 差异", description="查看文件改动",
        risk=RISK_READ, tool_names=("git_diff",),
        builder=_single_tool(_build_git_read, "git_diff"),
    ),
    "git_commit": ToolSpec(
        name="git_commit",
        label="Git 提交",
        description="暂存并提交指定文件",
        risk=RISK_EXEC,
        tool_names=("git_commit",),
        builder=_build_git_commit,
    ),
    "get_weather": ToolSpec(
        name="get_weather",
        label="实时天气",
        description="查询城市实时天气，需和风天气凭据",
        fields=(
            ToolField("QWEATHER_KEY_ID", "Key ID", secret=True),
            ToolField("QWEATHER_PROJECT_ID", "Project ID"),
            ToolField("QWEATHER_DEVELOPER_ID", "Developer ID"),
            ToolField("QWEATHER_PRIVATE_KEY", "Private Key（Ed25519）", secret=True, multiline=True),
            ToolField(
                "QWEATHER_API_HOST",
                "API Host",
                placeholder="填写你的和风天气 API Host（不含 https://）",
            ),
        ),
        tool_names=("get_weather",),
        default_enabled=False,
        builder=_build_weather,
    ),
    # Mock tool: exercises the chain "registry declares field → settings panel →
    # tool reads it", and doubles as a template for new Python-only tools.
    # Disabled by default so it does not interfere with daily use.
    "mock_data": ToolSpec(
        name="mock_data",
        label="模拟数据",
        description="读取自定义模拟文本",
        fields=(
            ToolField(
                "MOCK_TEXT",
                "内容",
                multiline=True,
                required=True,
                placeholder="启用后，agent 调用 mock_lookup 会原样拿到这段文字",
            ),
        ),
        default_enabled=False,
        tool_names=("mock_lookup",),
        builder=_build_mock,
    ),
}


def default_enabled_names() -> set[str]:
    """Entry names enabled by default (used by the CLI)."""
    return {name for name, spec in tuple(TOOL_SPECS.items()) if spec.default_enabled}


def build_tools(
    enabled: set[str] | None,
    configs: Mapping[str, Mapping[str, str]] | None,
    ctx: ToolContext,
) -> list:
    """Assemble actual tool instances according to enabled state.

    Args:
        enabled: Set of enabled entry names; ``None`` means all enabled.
        configs: Entry name → parameter table (such as QWeather credentials);
            when omitted, tools fall back to .env.
        ctx: Workspace and output budget.

    Returns:
        List of LangChain tool instances, ordered like :data:`TOOL_SPECS`.
    """
    names = expand_enabled_names(enabled)
    built: list = []
    for name, spec in tuple(TOOL_SPECS.items()):
        if name not in names or spec.builder is None:
            continue
        configured = configs or {}
        legacy = next((group for group, members in LEGACY_TOOL_GROUPS.items() if name in members), None)
        config = dict(configured.get(name, configured.get(legacy, {})) or {})
        try:
            built.extend(spec.builder(config, ctx))
        except Exception:  # a single entry failing to assemble must not break the whole turn
            logger.exception("Failed to assemble tool %s; skipped", name)
    return built


def spec_of(name: str) -> ToolSpec | None:
    return TOOL_SPECS.get(name)


# ---------------------------------------------------------------------------
# Capability matrix
#
# The matrix is derived from the legacy ``risk`` level plus per-entry overrides. It
# describes what a tool can touch, not whether it is enabled: the permission engine
# reads these fields to decide allow / ask / deny before the mode defaults apply.
# ---------------------------------------------------------------------------
CAPABILITY_OVERRIDES: dict[str, dict] = {
    # File edits: in-workspace mutations that ``edit`` mode treats as the accepted edit surface.
    "write_file": {"permission_name": "edit"},
    "edit_file": {"permission_name": "edit"},
    "apply_patch": {"permission_name": "edit"},
    "batch_edit": {"permission_name": "edit"},
    "make_dir": {"risk_level": "low", "permission_name": "file"},
    "move_file": {"risk_level": "medium", "permission_name": "file"},
    "copy_file": {"risk_level": "medium", "permission_name": "file"},
    "delete_file": {"risk_level": "high", "destructive": True, "permission_name": "file"},
    "download_file": {"risk_level": "medium", "side_effect_scope": "workspace", "permission_name": "file"},
    # Network reads and fetches.
    "fetch_url": {
        "read_only": True,
        "side_effect_scope": "network",
        "risk_level": "medium",
        "needs_approval": True,
        "permission_name": "web",
    },
    "web_search": {
        "read_only": True,
        "side_effect_scope": "network",
        "risk_level": "low",
        "permission_name": "web",
    },
    # Session-local state: allowed in plan mode and low risk elsewhere.
    "todo_write": {
        "read_only": True,
        "side_effect_scope": "session",
        "risk_level": "low",
        "permission_name": "session",
    },
    "ask_user_question": {
        "read_only": True,
        "side_effect_scope": "session",
        "risk_level": "low",
        "permission_name": "session",
    },
    "agent": {
        "read_only": True,
        "side_effect_scope": "none",
        "risk_level": "low",
        "permission_name": "session",
    },
    "praise_subagent": {"side_effect_scope": "session", "permission_name": "session"},
    # Git and command execution.
    "git_commit": {
        "read_only": False,
        "destructive": False,
        "side_effect_scope": "git",
        "risk_level": "medium",
        "needs_approval": True,
        "permission_name": "git",
    },
    "run_command": {"risk_level": "high", "destructive": True, "permission_name": "command"},
    "start_process": {"risk_level": "high", "permission_name": "command"},
    "stop_process": {"risk_level": "medium", "destructive": False, "permission_name": "command"},
}

_TOOL_ENTRIES: dict[str, str] = {
    tool_name: entry for entry, spec in TOOL_SPECS.items() for tool_name in spec.tool_names
}


def entry_for_tool(tool_name: str) -> str | None:
    """Map a concrete tool name back to its registry entry.

    Falls back to a live scan so tools registered after import (plugins/MCP) still
    resolve to the entry that declares them.
    """
    entry = _TOOL_ENTRIES.get(tool_name)
    if entry is not None:
        return entry
    for name, spec in TOOL_SPECS.items():
        if tool_name in spec.tool_names:
            return name
    return None


def capability_for(entry_name: str | None = None, *, tool_name: str | None = None) -> dict:
    """Resolve the capability matrix row for a registry entry or tool name.

    Unknown tools are treated conservatively: writable, system scope, high risk, and
    approval required, so a tool that skipped registration cannot slip through.
    """
    entry = entry_name or (entry_for_tool(tool_name) if tool_name else None)
    spec = TOOL_SPECS.get(entry) if entry else None
    risk = spec.risk if spec is not None else RISK_EXEC
    if risk == RISK_READ:
        base = {
            "read_only": True,
            "destructive": False,
            "side_effect_scope": SIDE_EFFECT_NONE,
            "risk_level": "low",
            "needs_approval": False,
            "permission_name": "read",
        }
    elif risk == RISK_WRITE:
        base = {
            "read_only": False,
            "destructive": False,
            "side_effect_scope": SIDE_EFFECT_WORKSPACE,
            "risk_level": "medium",
            "needs_approval": True,
            "permission_name": "file",
        }
    else:
        base = {
            "read_only": False,
            "destructive": True,
            "side_effect_scope": SIDE_EFFECT_SYSTEM,
            "risk_level": "high",
            "needs_approval": True,
            "permission_name": "command",
        }
    row = {
        **base,
        "always_ask": False,
        "allowed_in_plan_mode": False,
        "requires_user_interaction": False,
        "entry": entry or "",
        "tool_names": list(spec.tool_names) if spec is not None else ([tool_name] if tool_name else []),
    }
    if entry:
        row.update(CAPABILITY_OVERRIDES.get(entry, {}))
        if spec is not None and spec.source != "builtin":
            # Extensions (plugins / MCP) have no audited capability declaration: plan mode
            # asks for non-destructive ones instead of trusting a read risk label.
            row["permission_name"] = "mcp"
    return row
