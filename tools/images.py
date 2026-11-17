# tools/images.py
"""Image reading tool: ``read_image``.

Images cannot go directly into tool results the way text can: base64 would blow up the
context, be written into the session history, and turn SSE events into multi-megabyte
strings. Instead:

1. The tool encodes the image as a data URL;
2. It calls the current session's model through ``ToolContext.vision`` for one vision
   pass (a multimodal model);
3. Only the model's textual description is passed back to the main loop.

Whether ``read_image`` is available therefore depends on the model the user selected:
a text-only DeepSeek model returns ``vision_unavailable`` / ``vision_failed`` and suggests
switching to a vision-capable provider. Without a configured vision channel (CLI, tests),
the tool likewise returns a readable error instead of raising.
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
    """Extract plain text from a model reply (works with str and content blocks)."""
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
    """Wrap a chat model as a ``(data_url, prompt) -> description`` vision channel.

    Uses the same model instance as the main conversation; providers that do not support
    image input raise on ``invoke``, which ``read_image`` converts to ``vision_failed``.
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
            raise ValueError("The model returned no image description")
        return text

    return describe


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def build_image_tools(ctx: ToolContext) -> list:
    """Build ``read_image`` (requires ``ctx.vision``)."""

    @tool
    def read_image(path: str, question: str = "") -> str:
        """Read an image from the workspace and have the vision model describe it and transcribe its text.

        Suitable for screenshots, designs, charts, and scans. Availability depends on
        whether the current model accepts image input: a text-only DeepSeek model returns
        ``vision_unavailable`` / ``vision_failed``, in which case you can switch to a
        vision-capable model such as OpenAI / Claude / Qwen, or ask the user to describe
        the image.

        Args:
            path: Image path relative to the workspace (PNG / JPEG / GIF / WebP / BMP / TIFF).
            question: What you want to learn from the image; leave empty to have the model
                fully describe the image and transcribe its text.

        Returns:
            Image metadata plus the model's textual description; returns an ``Error:``
            description when the format is unsupported, the file is too large, or the
            vision channel is unavailable.
        """
        try:
            target = resolve_in_workspace(ctx.workspace, path)
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if not target.exists():
            return tool_error("not_found", f"Image does not exist: {path}.", hint="Use list_dir or find_files to confirm the path first.")
        if target.is_dir():
            return tool_error("is_directory", f"{path} is a directory, not an image.")
        suffix = target.suffix.lower()
        mime = _IMAGE_SUFFIXES.get(suffix)
        if mime is None:
            return tool_error(
                "unsupported_image",
                f"{path} is not a supported image format.",
                hint="Supported: PNG / JPEG / GIF / WebP / BMP / TIFF.",
            )
        try:
            size = target.stat().st_size
        except OSError as exc:
            return tool_error("read_failed", f"Failed to read {path}: {exc}.")
        if size > _MAX_IMAGE_BYTES:
            return tool_error(
                "image_too_large",
                f"{path} is {_human_size(size)}, exceeding the {_human_size(_MAX_IMAGE_BYTES)} limit.",
                hint="Compress or crop the image before the agent reads it.",
            )
        if ctx.vision is None:
            return tool_error(
                "vision_unavailable",
                "The current runtime has no vision channel configured, so images cannot be analyzed.",
                hint="Use a model that accepts image input on the web (OpenAI / Claude / Qwen, etc.) and retry; alternatively, ask the user to describe the image.",
            )

        try:
            raw = target.read_bytes()
        except OSError as exc:
            return tool_error("read_failed", f"Failed to read {path}: {exc}.")
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
        except Exception as exc:  # Provider rejects images / network error / quota exhausted
            return tool_error(
                "vision_failed",
                f"Vision model call failed: {exc}",
                hint="Confirm the current model accepts image input; if it does not, switch to a vision model such as OpenAI / Claude / Qwen.",
            )

        header = (
            f"Image: {display_path(ctx.workspace, target)}"
            f" ({suffix.lstrip('.') or 'image'}, {_human_size(size)})"
        )
        body = truncate(str(answer).strip(), max(500, ctx.max_output_chars - len(header) - 20))
        return f"{header}\n\n{body}"

    return [read_image]
