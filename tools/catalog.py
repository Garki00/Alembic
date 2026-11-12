# tools/catalog.py
"""Tool catalog and on-demand activation.

The agent sees a fixed set of basic tools plus a hidden library of extended
tools. Extended tools are not sent to the model until ``tools_search``
activates them for the current session. This module owns the catalog split,
the activation state, and the small search used by ``tools_search``.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

#: Always available (subject to the user's per-entry enable switches).
BASIC_TOOLS: tuple[str, ...] = (
    # 时间与环境
    "get_current_time",
    # 工作区探索
    "list_dir",
    "find_files",
    "file_stat",
    "search_files",
    # 读取
    "read_file",
    # 写入与编辑
    "write_file",
    "edit_file",
    "apply_patch",
    "make_dir",
    "move_file",
    "copy_file",
    "delete_file",
    # 执行（shell 兜底）
    "run_command",
    # 联网
    "fetch_url",
    "web_search",
    # 规划与交互
    "todo_write",
    "ask_user_question",
)

#: Hidden until activated through ``tools_search``.
EXTENDED_TOOLS: tuple[str, ...] = (
    # 重型文档与多模态解析
    "read_document",
    "read_image",
    # 网络下载
    "download_file",
    # 后台进程管理
    "start_process",
    "stop_process",
    "list_processes",
    "read_process_logs",
    "wait_process",
    # Git 专用工作流
    "git_status",
    "git_diff",
    "git_commit",
    # 低频 / 示例
    "list_timezones",
    "get_weather",
    "mock_lookup",
)

#: Meta tools that are always visible and never part of BASIC/EXTENDED.
META_TOOLS: tuple[str, ...] = ("tools_search",)

#: Search keywords per extended tool, including Chinese terms.
TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "read_document": (
        "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "word", "excel",
        "document", "office", "文档", "表格", "幻灯片", "解析", "读取文档",
    ),
    "read_image": (
        "image", "photo", "picture", "screenshot", "ocr", "vision",
        "图片", "照片", "截图", "识图", "视觉",
    ),
    "download_file": (
        "download", "fetch file", "save url", "http file",
        "下载", "保存文件", "远程文件",
    ),
    "start_process": (
        "start server", "dev server", "background", "daemon", "watch",
        "启动服务", "后台", "守护进程", "开发服务器",
    ),
    "stop_process": ("stop process", "kill process", "停止进程", "结束进程"),
    "list_processes": ("list process", "processes", "进程列表", "后台进程"),
    "read_process_logs": ("process log", "logs", "日志", "进程输出"),
    "wait_process": ("wait process", "wait for", "等待进程", "等待日志"),
    "git_status": ("git status", "branch", "working tree", "分支", "工作区状态"),
    "git_diff": ("git diff", "diff", "changes", "差异", "改动"),
    "git_commit": ("git commit", "commit", "提交", "暂存"),
    "list_timezones": ("timezone", "time zone", "iana", "时区", "时区列表"),
    "get_weather": ("weather", "forecast", "temperature", "天气", "气温", "预报"),
    "mock_lookup": ("mock", "test data", "模拟数据", "测试数据"),
}


class ToolActivation:
    """Thread-safe set of extended tools activated in one session."""

    def __init__(self, active: set[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set(active or ())

    def activate(self, names: set[str]) -> tuple[set[str], set[str]]:
        """Add names; return ``(newly_activated, already_active)``."""
        with self._lock:
            incoming = set(names)
            already = incoming & self._active
            new = incoming - self._active
            self._active |= new
            return new, already

    def snapshot(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._active)

    def clear(self) -> None:
        with self._lock:
            self._active.clear()


@dataclass(frozen=True)
class ToolMatch:
    name: str
    label: str
    description: str
    score: int


def _registry():
    # Imported lazily so registry -> catalog -> registry is not a load cycle.
    from tools.registry import TOOL_SPECS

    return TOOL_SPECS


def entry_names_for_tools(names: set[str] | frozenset[str]) -> set[str]:
    """Map concrete tool names back to registry entry names."""
    wanted = set(names)
    return {
        entry
        for entry, spec in _registry().items()
        if wanted & set(spec.tool_names)
    }


def enabled_tool_names(enabled_entries: set[str] | None) -> frozenset[str]:
    """Concrete tool names available from the user's enabled entries."""
    entries = _registry()
    names = set(entries) if enabled_entries is None else set(enabled_entries)
    result: set[str] = set()
    for entry, spec in entries.items():
        if entry in names:
            result.update(spec.tool_names)
    return frozenset(result)


def visible_tool_names(
    enabled_names: set[str] | frozenset[str],
    activation: ToolActivation | None,
) -> frozenset[str]:
    """Basic + meta + activated extended tools, limited to enabled tools."""
    enabled = set(enabled_names)
    active = set(activation.snapshot()) if activation is not None else set()
    visible = enabled & set(BASIC_TOOLS)
    visible |= enabled & set(META_TOOLS)
    visible |= enabled & set(EXTENDED_TOOLS) & active
    return frozenset(visible)


def is_visible(
    name: str,
    enabled_names: set[str] | frozenset[str],
    activation: ToolActivation | None,
) -> bool:
    return name in visible_tool_names(enabled_names, activation)


def _tokens(text: str) -> list[str]:
    """Split a model query into latin words and CJK character runs."""
    raw = (text or "").lower()
    return re.findall(r"[a-z0-9_+#.-]+|[\u4e00-\u9fff]{1,6}", raw)


def _score_match(
    name: str,
    label: str,
    description: str,
    query: str,
    tokens: list[str],
) -> int:
    haystack = f"{name} {label} {description} {' '.join(TOOL_KEYWORDS.get(name, ()))}".lower()
    score = 0
    if query and query == name.lower():
        score += 20
    for token in tokens:
        if not token:
            continue
        if token in name.lower():
            score += 6
        elif token in label.lower() or token in description.lower():
            score += 3
        elif token in haystack:
            score += 2
    # Whole-query substring catches multi-word phrases such as "git status".
    if query and query in haystack:
        score += 4
    return score


def search_extended(
    query: str,
    enabled_names: set[str] | frozenset[str],
    active_names: set[str] | frozenset[str],
    limit: int,
) -> list[ToolMatch]:
    """Search enabled, not-yet-active extended tools. No request regex."""
    text = (query or "").strip().lower()
    tokens = _tokens(text)
    if not tokens:
        return []
    enabled = set(enabled_names)
    active = set(active_names)
    entries = _registry()
    matches: list[ToolMatch] = []
    for name in EXTENDED_TOOLS:
        if name not in enabled or name in active:
            continue
        spec = next((item for item in entries.values() if name in item.tool_names), None)
        label = spec.label if spec else name
        description = spec.description if spec else ""
        score = _score_match(name, label, description, text, tokens)
        if score > 0:
            matches.append(ToolMatch(name, label, description, score))
    matches.sort(key=lambda item: (-item.score, item.name))
    return matches[: max(1, min(int(limit), 10))]


def clamp_limit(value: int | str | None, default: int = 3) -> int:
    try:
        number = int(float(value)) if value not in (None, "") else default
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, 10))


def build_tools_search(config, ctx):
    """Build the ``tools_search`` meta tool for one agent turn."""
    from langchain.tools import tool

    from tools.errors import tool_error

    default_limit = clamp_limit((config or {}).get("TOOLS_SEARCH_LIMIT"), 3)

    @tool
    def tools_search(query: str, limit: int = 0) -> str:
        """Search the live tool registry for capabilities missing from the
        current toolset, and load matched tools into this turn.

        Trigger this whenever the task needs an action you cannot perform
        with the tools already loaded — before assuming the capability 
        exists. The registry is the source of truth; this description 
        is not a catalog and does not enumerate what is available.

        Args:
            query: Capability to look for, e.g. "PDF document", "图片 OCR",
                "dev server logs", "git commit", "weather".
            limit: Maximum tools to activate this call (1-10); 0 uses the
                configured default.

        Returns:
            The activated tool names and descriptions; no match returns hints.
        """
        text = (query or "").strip()
        if not text:
            return tool_error("empty_query", "query 不能为空。", hint="描述你需要的能力，例如 PDF、图片、后台进程、Git。")
        if ctx.activation is None:
            return tool_error("activation_unavailable", "当前会话没有扩展工具激活状态。")
        enabled = set(ctx.enabled_tool_names)
        active = ctx.activation.snapshot()
        try:
            requested = int(limit or 0)
        except (TypeError, ValueError):
            requested = 0
        wanted = clamp_limit(requested, default_limit) if requested > 0 else default_limit
        matches = search_extended(text, enabled, active, wanted)
        if not matches:
            return (
                f"没有找到可激活的扩展工具：{text}。\n"
                "可以换更具体的能力词（文档 / 图片 / 下载 / 后台进程 / Git / 时区 / 天气），"
                "或检查「设置 → 工具」里对应工具是否已启用。"
            )
        new, already = ctx.activation.activate({item.name for item in matches})
        lines = [f"已加载 {len(new)} 个扩展工具，本轮可直接调用："]
        for item in matches:
            state = "已加载" if item.name in already else "新加载"
            lines.append(f"- {item.name} ({item.label}，{state})：{item.description}")
        if already:
            lines.append("其余匹配项此前已加载，无需重复激活。")
        return "\n".join(lines)

    return [tools_search]
