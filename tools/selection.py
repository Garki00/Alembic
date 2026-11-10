"""Select a small, task-relevant set of model-facing tool entries."""

from __future__ import annotations

import os
import re

CORE_ENTRIES = frozenset({
    "file_read",
    "run_command",
    "todo_write",
    "ask_user_question",
})

WRITE_ENTRIES = frozenset({"file_write", "apply_patch"})

TOOL_PACKS: dict[str, frozenset[str]] = {
    "documents": frozenset({"read_document"}),
    "images": frozenset({"read_image"}),
    "web": frozenset({"web_search", "fetch_url", "download_file"}),
    "git": frozenset({"git_read", "git_commit"}),
    "process": frozenset({"background_process", "process_inspect"}),
    "time": frozenset({"get_current_time", "list_timezones"}),
    "weather": frozenset({"get_weather"}),
    "mock": frozenset({"mock_data"}),
}

_WRITE_RE = re.compile(
    r"(?i)\b(?:add|adopt|apply|build|change|continue|copy|create|delete|edit|"
    r"fix|implement|improve|mkdir|modify|move|optimi[sz]e|patch|refactor|"
    r"remove|rename|test|update|write)\b|"
    r"新增|修复|实现|应用|采用|改进|优化|修改|调整|重构|创建|写入|删除|移除|"
    r"移动|改名|复制|补丁|构建|测试|继续|做到"
)
_DOCUMENT_RE = re.compile(
    r"(?i)(?:\.(?:pdf|docx?|xlsx?|pptx?|csv|tsv)\b|"
    r"\b(?:pdf|word|excel|powerpoint|spreadsheet|document)\b|"
    r"文档|表格|幻灯片|演示文稿)"
)
_IMAGE_RE = re.compile(
    r"(?i)(?:\.(?:png|jpe?g|gif|webp|bmp|tiff?)\b|"
    r"\b(?:image|photo|picture|screenshot|ocr)\b|图片|照片|截图|识图)"
)
_WEB_RE = re.compile(
    r"(?i)(?:https?://|\b(?:browse|download|fetch|internet|online|url|"
    r"web|website)\b|网页|联网|下载|链接|网址|在线)"
)
_GIT_RE = re.compile(
    r"(?i)(?:\bgit\b|\bcommit\b|\bgit (?:branch|diff|status)\b|"
    r"提交|暂存|分支|版本库)"
)
_PROCESS_RE = re.compile(
    r"(?i)(?:\b(?:daemon|dev server|logs?|process|server|watcher)\b|"
    r"后台进程|启动服务|开发服务器|日志|监听)"
)
_TIME_RE = re.compile(
    r"(?i)(?:\b(?:date|time|timezone|time zone|today)\b|"
    r"几点|时间|时区|日期|今天)"
)
_WEATHER_RE = re.compile(
    r"(?i)(?:\b(?:forecast|temperature|weather)\b|天气|气温|预报|温度)"
)
_MOCK_RE = re.compile(r"(?i)(?:\bmock\b|模拟数据)")

_PACK_PATTERNS = {
    "documents": _DOCUMENT_RE,
    "images": _IMAGE_RE,
    "web": _WEB_RE,
    "git": _GIT_RE,
    "process": _PROCESS_RE,
    "time": _TIME_RE,
    "weather": _WEATHER_RE,
    "mock": _MOCK_RE,
}


def _dynamic_enabled() -> bool:
    return os.getenv("DYNAMIC_TOOL_PACKS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def select_tool_entries(
    enabled: set[str],
    request_text: str | None = None,
) -> set[str]:
    """Return enabled entries relevant to the request.

    A missing request or ``DYNAMIC_TOOL_PACKS=0`` returns every enabled entry.
    """
    available = set(enabled)
    text = (request_text or "").strip()
    if not text or not _dynamic_enabled():
        return available

    selected = set(CORE_ENTRIES)
    if _WRITE_RE.search(text):
        selected.update(WRITE_ENTRIES)
    for pack, pattern in _PACK_PATTERNS.items():
        if pattern.search(text):
            selected.update(TOOL_PACKS[pack])
    return selected & available
