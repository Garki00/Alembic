# tools/registry.py
"""工具注册表：工具的唯一事实来源。

此前同一份工具清单散在三处（``config/store.py`` 的 TOOL_SPECS、
``agents/agent.py`` 的 TOOLS、``web/runtime.py`` 的 TOOL_REGISTRY），
新增一个工具要改三遍，漏一处就会出现「设置面板能开关、命令行却没有」。

现在改为：**在这里登记一次**，三处消费者各自派生：

- 网页设置面板 ← :data:`TOOL_SPECS`（含配置字段、风险等级）；
- 网页对话 ← :func:`build_tools`（按启用状态 + 当前配置装配）；
- 命令行 ← :func:`build_tools` + :func:`default_enabled_names`。

一个条目（ToolSpec）可以由多个工具组成（如文件读写组），
启用/禁用与配置都以「条目」为粒度。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from tools.context import ToolContext
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

# 风险等级：read 只读；write 会改工作区内的文件；exec 会执行任意命令。
RISK_READ = "read"
RISK_WRITE = "write"
RISK_EXEC = "exec"

# 字段形态：决定设置面板用什么控件，以及保存时怎么校验。
FIELD_TEXT = "text"
FIELD_NUMBER = "number"
FIELD_SELECT = "select"
FIELD_SWITCH = "switch"
FIELD_KINDS = (FIELD_TEXT, FIELD_NUMBER, FIELD_SELECT, FIELD_SWITCH)

Builder = Callable[[Mapping[str, str], ToolContext], Sequence]


@dataclass(frozen=True)
class ToolField:
    """设置面板里可编辑的一项工具参数。

    除 ``key`` / ``label`` 外的字段都有默认值，新增工具按需使用即可：

    - ``secret``：密钥，不回显，留空表示保持原值；
    - ``multiline``：多行文本框（``kind="text"`` 时生效）；
    - ``kind``：控件形态，见 ``FIELD_KINDS``；
    - ``options``：``kind="select"`` 时的候选，``((值, 显示名), ...)``；
    - ``required``：仅用于界面标记（是否为空由工具自己报错）；
    - ``default``：配置文件里没有这个键时的初始值。
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
        """给前端的结构；值相关的字段由 :class:`ToolStore` 补齐。"""
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
        """按字段类型校验并规范化待保存的值。

        Raises:
            ValueError: 值不符合该字段的类型（由接口转成 400）。
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
    """一个可启停的工具条目。

    Attributes:
        name: 条目名，也是 /api/tools/{name} 与 data/tools.json 的键。
        label: 设置面板显示名。
        description: 给用户看的一句话说明。
        fields: 需要用户填写的参数。
        risk: 风险等级，见 RISK_*。
        default_enabled: 首次生成 data/tools.json 时是否默认启用。
        tool_names: 实际暴露给模型的工具函数名（一个条目可含多个）。
        builder: ``(config, ctx) -> Sequence[BaseTool]`` 的装配函数。
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
# 装配函数
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
# 注册表
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
    # 模拟工具：验证「注册表声明字段 → 设置面板 → 工具读到」这条链路，
    # 同时也是新增工具只写 Python 的样板。默认关闭，避免干扰日常使用。
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
    """默认启用的条目名（供命令行使用）。"""
    return {name for name, spec in TOOL_SPECS.items() if spec.default_enabled}


def build_tools(
    enabled: set[str] | None,
    configs: Mapping[str, Mapping[str, str]] | None,
    ctx: ToolContext,
) -> list:
    """按启用状态装配实际工具实例。

    Args:
        enabled: 启用的条目名集合；``None`` 表示全部启用。
        configs: 条目名 → 参数表（如 QWeather 凭据）；缺省时工具回落到 .env。
        ctx: 工作区与输出预算。

    Returns:
        LangChain 工具实例列表，顺序与 :data:`TOOL_SPECS` 一致。
    """
    names = set(TOOL_SPECS) if enabled is None else set(enabled)
    built: list = []
    for name, spec in TOOL_SPECS.items():
        if name not in names or spec.builder is None:
            continue
        config = dict((configs or {}).get(name) or {})
        try:
            built.extend(spec.builder(config, ctx))
        except Exception:  # 单个条目装配失败不应拖垮整轮对话
            logger.exception("装配工具 %s 失败，已跳过", name)
    return built


def spec_of(name: str) -> ToolSpec | None:
    return TOOL_SPECS.get(name)
