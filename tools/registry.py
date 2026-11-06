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
from tools.files import build_file_read_tools, build_file_write_tools
from tools.mock import build_mock_tools
from tools.shell import build_shell_tools
from tools.time import get_current_time, list_timezones
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
                placeholder="na44ucu9jw.re.qweatherapi.com",
            ),
        ),
        tool_names=("get_weather",),
        builder=_build_weather,
    ),
    "file_read": ToolSpec(
        name="file_read",
        label="文件读取",
        description="浏览工作区目录、按行读取文本文件、按正则搜索内容。",
        risk=RISK_READ,
        tool_names=("list_dir", "read_file", "search_files"),
        builder=_build_file_read,
    ),
    "file_write": ToolSpec(
        name="file_write",
        label="文件写入",
        description="在工作区内写入文件、按精确匹配替换内容。",
        risk=RISK_WRITE,
        tool_names=("write_file", "edit_file"),
        builder=_build_file_write,
    ),
    "run_command": ToolSpec(
        name="run_command",
        label="命令执行",
        description="在工作区内执行 shell 命令，返回退出码与输出。执行前需要确认。",
        risk=RISK_EXEC,
        default_enabled=True,
        tool_names=("run_command",),
        builder=_build_shell,
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
