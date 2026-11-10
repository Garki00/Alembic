# tools/images.py
"""图片读取工具：``read_image``。

图片不能像文本一样直接塞进工具结果，否则 base64 会撑爆上下文、写进会话历史，
还会让 SSE 事件变成几 MB 的字符串。这里的做法是：

1. 工具把图片编码成 data URL；
2. 通过 ``ToolContext.vision`` 调用「当前会话的模型」做一次视觉理解（多模态模型）；
3. 工具只把模型返回的文字描述交给主循环。

因此 ``read_image`` 是否可用取决于用户选择的模型：DeepSeek 纯文本模型会明确返回
``vision_unavailable`` / ``vision_failed``，提示切换到支持视觉的供应商。
没有配置视觉通道时（命令行、测试）同样返回可读的错误，而不是抛异常。
"""

from __future__ import annotations

import base64
from typing import Callable

from langchain.tools import tool

from tools.context import ToolContext
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace

_IMAGE_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

_MAX_IMAGE_BYTES = 8_000_000


def _content_text(content) -> str:
    """从模型回复里取纯文本（兼容 str 与 content blocks）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content or "")


def vision_from_model(model) -> Callable[[str, str], str]:
    """把聊天模型包装成 ``(data_url, prompt) -> 文字描述`` 的视觉通道。

    使用与主对话相同的模型实例；不支持图片输入的供应商会在 ``invoke`` 时抛错，
    由 ``read_image`` 统一转换成 ``vision_failed``。
    """

    def describe(data_url: str, prompt: str) -> str:
        from langchain_core.messages import HumanMessage

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        )
        response = model.invoke([message])
        text = _content_text(getattr(response, "content", "")).strip()
        if not text:
            raise ValueError("模型没有返回图片描述")
        return text

    return describe


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def build_image_tools(ctx: ToolContext) -> list:
    """构造 ``read_image``（需要 ``ctx.vision``）。"""

    @tool
    def read_image(path: str, question: str = "") -> str:
        """读取工作区里的图片，让视觉模型描述内容并转写其中的文字。

        适合截图、设计稿、图表、扫描件。是否可用取决于当前模型是否支持图片输入：
        DeepSeek 纯文本模型会返回 ``vision_unavailable`` / ``vision_failed``，
        此时可以切换到 OpenAI / Claude / 千问等支持视觉的模型，或让用户描述图片。

        Args:
            path: 相对工作区的图片路径（PNG / JPEG / GIF / WebP / BMP / TIFF）。
            question: 想从图片里了解什么；留空表示让模型完整描述并转写文字。

        Returns:
            图片元信息与模型返回的文字描述；格式不支持、过大或视觉通道不可用时
            返回 ``Error:`` 说明。
        """
        try:
            target = resolve_in_workspace(ctx.workspace, path)
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if not target.exists():
            return tool_error("not_found", f"图片不存在：{path}。", hint="先用 list_dir 或 find_files 确认路径。")
        if target.is_dir():
            return tool_error("is_directory", f"{path} 是目录，不是图片。")
        suffix = target.suffix.lower()
        mime = _IMAGE_SUFFIXES.get(suffix)
        if mime is None:
            return tool_error(
                "unsupported_image",
                f"{path} 不是支持的图片格式。",
                hint="支持 PNG / JPEG / GIF / WebP / BMP / TIFF。",
            )
        try:
            size = target.stat().st_size
        except OSError as exc:
            return tool_error("read_failed", f"读取 {path} 失败：{exc}。")
        if size > _MAX_IMAGE_BYTES:
            return tool_error(
                "image_too_large",
                f"{path} 有 {_human_size(size)}，超过 {_human_size(_MAX_IMAGE_BYTES)} 上限。",
                hint="请先压缩或裁剪图片，再让 agent 读取。",
            )
        if ctx.vision is None:
            return tool_error(
                "vision_unavailable",
                "当前运行环境没有配置视觉通道，无法识别图片。",
                hint="请在网页端使用支持图片输入的模型（OpenAI / Claude / 千问等）后重试；也可以让用户直接描述图片内容。",
            )

        try:
            raw = target.read_bytes()
        except OSError as exc:
            return tool_error("read_failed", f"读取 {path} 失败：{exc}。")
        data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

        ask = (question or "").strip()
        prompt = (
            "You are looking at an image provided by the user. Describe it accurately and "
            "transcribe all visible text (OCR). If the user asked a specific question, answer "
            "it directly using evidence from the image. Do not invent details that are not "
            "visible. Answer in the same language as the user's question."
        )
        if ask:
            prompt += f"\n\nUser question: {ask}"
        else:
            prompt += "\n\nDescribe the image, including text, layout, diagrams and key details."

        try:
            answer = ctx.vision(data_url, prompt)
        except Exception as exc:  # 供应商不支持图片 / 网络错误 / 额度不足
            return tool_error(
                "vision_failed",
                f"视觉模型调用失败：{exc}",
                hint="确认当前模型支持图片输入；如果不支持，请切换到 OpenAI / Claude / 千问等视觉模型。",
            )

        header = (
            f"图片：{display_path(ctx.workspace, target)}"
            f"（{suffix.lstrip('.') or '图片'}，{_human_size(size)}）"
        )
        body = truncate(str(answer).strip(), max(500, ctx.max_output_chars - len(header) - 20))
        return f"{header}\n\n{body}"

    return [read_image]
