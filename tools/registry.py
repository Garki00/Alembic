# tools/registry.py
"""Tool registry: the single source of truth for tools.

Register a tool once in :data:`TOOL_SPECS`; consumers derive from it:

- Web settings panel ← :data:`TOOL_SPECS` (config fields and risk level);
- Web chat ← :func:`build_tools` (assembled from enabled state and current configuration);
- CLI ← :func:`build_tools` + :func:`default_enabled_names`.

A single entry (ToolSpec) may consist of several tools (for example the file
read/write groups); enable/disable and configuration apply at entry granularity.
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
from tools.time import get_current_time, list_timezones
from tools.todo import build_todo_tools
from tools.weather import build_weather_tools

logger = logging.getLogger(__name__)

# Risk levels: read = read-only; write = modifies files in the workspace; exec = runs arbitrary commands.
RISK_READ = "read"
RISK_WRITE = "write"
RISK_EXEC = "exec"

# Field kinds: determine the settings-panel control and how values are validated on save.
FIELD_TEXT = "text"
FIELD_NUMBER = "number"
FIELD_SELECT = "select"
FIELD_SWITCH = "switch"
FIELD_KINDS = (FIELD_TEXT, FIELD_NUMBER, FIELD_SELECT, FIELD_SWITCH)

Builder = Callable[[Mapping[str, str], ToolContext], Sequence]


@dataclass(frozen=True)
class ToolField:
    """One editable tool parameter in the settings panel.

    Every field except ``key`` / ``label`` has a default, so new tools can use only
    what they need:

    - ``secret``: secret value, never echoed back; empty means keep the current value;
    - ``multiline``: multi-line text box (effective when ``kind="text"``);
    - ``kind``: control type, see ``FIELD_KINDS``;
    - ``options``: candidates for ``kind="select"``, as ``((value, display name), ...)``;
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

    def __post_init__(self) -> None:
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"未知的字段类型 {self.kind!r}（{self.key}）")
        if self.kind == FIELD_SELECT and not self.options:
            raise ValueError(f"字段 {self.key} 是 select，但没有给出 options")

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
        if self.kind == FIELD_SELECT:
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
        tool_names: Names of the tool functions actually exposed to the model (one entry may contain several).
        builder: Assembly function of type ``(config, ctx) -> Sequence[BaseTool]``.
    """

    name: str
    label: str
    description: str
    fields: tuple[ToolField, ...] = ()
    risk: str = RISK_READ
    default_enabled: bool = True
    tool_names: tuple[str, ...] = ()
    builder: Builder | None = field(default=None, repr=False)


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


def _build_mock(config: Mapping[str, str], _ctx: ToolContext) -> Sequence:
    return build_mock_tools(dict(config))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
TOOL_SPECS: dict[str, ToolSpec] = {
    "get_current_time": ToolSpec(
        name="get_current_time",
        label="时间查询",
        description="获取本地或指定时区的时间。",
        tool_names=("get_current_time",),
        builder=_build_time,
    ),
    "list_timezones": ToolSpec(
        name="list_timezones",
        label="时区检索",
        description="查询支持的地区与 IANA 时区。",
        tool_names=("list_timezones",),
        builder=_build_timezone_list,
    ),
    "get_weather": ToolSpec(
        name="get_weather",
        label="实时天气",
        description="按城市查询实时天气，需要和风天气凭据。",
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
    "file_read": ToolSpec(
        name="file_read",
        label="文件读取",
        description="浏览工作区目录、按行读取文本、按正则搜索内容、按文件名查找、查看文件元信息。",
        risk=RISK_READ,
        tool_names=("list_dir", "read_file", "search_files", "find_files", "file_stat"),
        builder=_build_file_read,
    ),
    "read_document": ToolSpec(
        name="read_document",
        label="文档读取",
        description="抽取 PDF / Word / Excel / PowerPoint / 文本的内容，支持分段读取。",
        risk=RISK_READ,
        tool_names=("read_document",),
        builder=_build_documents,
    ),
    "read_image": ToolSpec(
        name="read_image",
        label="图片读取",
        description="用视觉模型描述工作区图片并转写文字，需要支持图片输入的模型。",
        risk=RISK_READ,
        tool_names=("read_image",),
        builder=_build_images,
    ),
    "file_write": ToolSpec(
        name="file_write",
        label="文件写入",
        description="在工作区内写入、替换、移动、复制、删除文件与创建目录。",
        risk=RISK_WRITE,
        tool_names=(
            "write_file",
            "edit_file",
            "make_dir",
            "delete_file",
            "move_file",
            "copy_file",
        ),
        builder=_build_file_write,
    ),
    "apply_patch": ToolSpec(
        name="apply_patch",
        label="补丁编辑",
        description="应用标准 unified diff，一次修改多个文件或多个 hunk。",
        risk=RISK_WRITE,
        tool_names=("apply_patch",),
        builder=_build_patch,
    ),
    "batch_edit": ToolSpec(
        name="batch_edit",
        label="原子批量编辑",
        description="一次提交多处精确替换；全部成功才落盘，失败不改任何文件。",
        risk=RISK_WRITE,
        tool_names=("batch_edit",),
        builder=_build_atomic_edit,
    ),
    "fetch_url": ToolSpec(
        name="fetch_url",
        label="网页抓取",
        description="抓取网页或 HTTP 接口正文，带 SSRF 防护与大小限制。",
        fields=(
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
            ),
        ),
        risk=RISK_READ,
        tool_names=("fetch_url",),
        builder=_build_fetch,
    ),
    "web_search": ToolSpec(
        name="web_search",
        label="网页搜索",
        description="按关键词搜索网页，返回标题、链接与摘要；默认 Bing，可切换其他服务。",
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
        description="把远程 PDF / 图片 / 压缩包等下载到工作区，带 SSRF 与大小限制。",
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
        description="维护本会话的多步任务清单，让用户看到进度。",
        risk=RISK_READ,
        tool_names=("todo_write",),
        builder=_build_todo,
    ),
    "ask_user_question": ToolSpec(
        name="ask_user_question",
        label="向用户提问",
        description="需要用户补充信息或做选择时，暂停并等待回答。",
        risk=RISK_READ,
        tool_names=("ask_user_question",),
        builder=_build_questions,
    ),
    "tools_search": ToolSpec(
        name="tools_search",
        label="扩展工具搜索",
        description="按能力搜索并加载扩展工具（文档 / 图片 / 下载 / 后台进程 / Git / 时区 / 天气）。",
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
        description="在工作区内执行 shell 命令（PowerShell / CMD / Git Bash / WSL），"
                    "用于测试、构建、脚本和 Git；读写文件优先用文件工具，命令行只作兜底。"
                    "执行前需要确认。",
        risk=RISK_EXEC,
        default_enabled=True,
        tool_names=("run_command",),
        builder=_build_shell,
    ),
    "background_process": ToolSpec(
        name="background_process",
        label="后台进程",
        description="启动/停止长任务（dev server、watch），启动与停止前需要确认。",
        risk=RISK_EXEC,
        tool_names=("start_process", "stop_process"),
        builder=_build_process_exec,
    ),
    "process_inspect": ToolSpec(
        name="process_inspect",
        label="进程查看",
        description="列出后台进程、读取进程日志、等待进程退出或日志匹配（只读）。",
        risk=RISK_READ,
        tool_names=("list_processes", "read_process_logs", "wait_process"),
        builder=_build_process_inspect,
    ),
    "git_read": ToolSpec(
        name="git_read",
        label="Git 查看",
        description="查看分支与工作区改动、读取 unified diff（只读）。",
        risk=RISK_READ,
        tool_names=("git_status", "git_diff"),
        builder=_build_git_read,
    ),
    "git_commit": ToolSpec(
        name="git_commit",
        label="Git 提交",
        description="暂存指定路径并提交；不会自动 git add -A。执行前需要确认。",
        risk=RISK_EXEC,
        tool_names=("git_commit",),
        builder=_build_git_commit,
    ),
    # Mock tool: exercises the chain "registry declares field → settings panel →
    # tool reads it", and doubles as a template for new Python-only tools.
    # Disabled by default so it does not interfere with daily use.
    "mock_data": ToolSpec(
        name="mock_data",
        label="模拟数据",
        description="读取「模拟数据」里配置的文本，用于测试工具参数。",
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
    return {name for name, spec in TOOL_SPECS.items() if spec.default_enabled}


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
    names = set(TOOL_SPECS) if enabled is None else set(enabled)
    built: list = []
    for name, spec in TOOL_SPECS.items():
        if name not in names or spec.builder is None:
            continue
        config = dict((configs or {}).get(name) or {})
        try:
            built.extend(spec.builder(config, ctx))
        except Exception:  # a single entry failing to assemble must not break the whole turn
            logger.exception("装配工具 %s 失败，已跳过", name)
    return built


def spec_of(name: str) -> ToolSpec | None:
    return TOOL_SPECS.get(name)
