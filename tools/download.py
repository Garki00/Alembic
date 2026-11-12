# tools/download.py
"""文件下载工具：``download_file``。

``fetch_url`` 只能读文本，遇到 PDF / 图片 / 压缩包会提示「先下载到工作区」。
本模块补上这一步：把远程二进制流式写入工作区，带 SSRF 校验、重定向逐跳校验、
大小上限和临时文件保护（失败不会留下半个文件）。

它属于 ``write`` 风险：真正落盘前需要用户确认。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urljoin, urlparse

import httpx
from langchain.tools import tool

from tools.context import ToolContext
from tools.errors import tool_error
from tools.fetch import (
    _MAX_REDIRECTS,
    _REDIRECT_STATUS,
    FetchError,
    _number,
    _setting,
    _validate_target,
)
from tools.files import _protected_reason
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace


def _filename_from_url(url: str) -> str:
    """从 URL 路径推断一个安全的文件名。"""
    try:
        name = unquote(Path(urlparse(url).path).name)
    except ValueError:
        name = ""
    name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name).strip("._")
    return name[:120] or "download.bin"


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _stream_to_file(
    url: str,
    target: Path,
    *,
    timeout: int,
    max_bytes: int,
    allow_private: bool,
    user_agent: str,
) -> dict:
    """流式下载到 ``target``，失败时清理临时文件。"""
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    current = url
    part = target.with_name(target.name + ".part")
    wrote = 0

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FetchError("write_failed", f"无法创建目录 {target.parent}：{exc}。") from exc

    try:
        with httpx.Client(timeout=float(timeout), follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                _validate_target(current, allow_private)
                try:
                    with client.stream("GET", current, headers=headers) as response:
                        if response.status_code in _REDIRECT_STATUS:
                            location = response.headers.get("location", "")
                            if not location:
                                raise FetchError(
                                    "bad_redirect",
                                    f"HTTP {response.status_code} 没有给出 Location。",
                                )
                            current = urljoin(current, location)
                            continue
                        if response.status_code >= 400:
                            raise FetchError(
                                "http_error",
                                f"HTTP {response.status_code}：{current}",
                                hint="确认 URL 是否需要登录、是否有访问权限，或换一个地址。",
                            )
                        content_type = (
                            response.headers.get("content-type") or "application/octet-stream"
                        ).split(";", 1)[0].strip().lower()
                        with part.open("wb") as handle:
                            for chunk in response.iter_bytes():
                                if not chunk:
                                    continue
                                wrote += len(chunk)
                                if wrote > max_bytes:
                                    raise FetchError(
                                        "file_too_large",
                                        f"下载内容超过 {_human_size(max_bytes)} 上限。",
                                        hint="可以在设置 → 工具 → 文件下载里调大「最大字节」，或直接下载更小的文件。",
                                    )
                                handle.write(chunk)
                        os.replace(part, target)
                        return {
                            "url": str(response.url),
                            "content_type": content_type,
                            "bytes": wrote,
                        }
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    raise FetchError("download_failed", f"下载失败：{exc}。") from exc
            raise FetchError("too_many_redirects", f"重定向超过 {_MAX_REDIRECTS} 次，已停止。")
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def build_download_tools(config: Mapping[str, str], ctx: ToolContext) -> list:
    """构造 ``download_file``（配置来自设置面板）。"""

    @tool
    def download_file(url: str, path: str = "", overwrite: bool = False) -> str:
        """Download a remote file to the workspace; suitable for binary content such as PDFs, images, and archives.

        Args:
            url: A complete http/https address.
            path: The relative path in the workspace to save to; if left empty, the filename is inferred from the URL.
            overwrite: Whether to overwrite when the target already exists (requires confirmation in approval).

        Returns:
            The save path, size, type, and final URL; returns ``Error:`` on failure.
        """
        text = (url or "").strip()
        if not text:
            return tool_error("empty_url", "url 不能为空。")
        if "://" not in text:
            text = "https://" + text

        relative = (path or "").strip() or _filename_from_url(text)
        try:
            target = resolve_in_workspace(ctx.workspace, relative, access="write")
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if target.exists() and target.is_dir():
            return tool_error(
                "destination_is_directory",
                f"目标 {relative} 是目录，不能作为文件保存。",
                hint="请给出具体的文件名。",
            )
        if target.exists() and not overwrite:
            return tool_error(
                "destination_exists",
                f"目标已存在：{relative}。",
                hint="换一个文件名，或传 overwrite=True（覆盖前请确认）。",
            )
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"不能写入{reason}：{relative}。")

        allow_private = _setting(config, "DOWNLOAD_ALLOW_PRIVATE", "false").lower() in {
            "1", "true", "on", "yes",
        }
        timeout = _number(config, "DOWNLOAD_TIMEOUT", 60, low=5, high=300)
        max_bytes = _number(
            config, "DOWNLOAD_MAX_BYTES", 20_000_000, low=10_000, high=200_000_000
        )
        user_agent = _setting(config, "DOWNLOAD_USER_AGENT", "ALEMBIC/0.1 (+local)")

        try:
            result = _stream_to_file(
                text,
                target,
                timeout=timeout,
                max_bytes=max_bytes,
                allow_private=allow_private,
                user_agent=user_agent,
            )
        except FetchError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)

        return (
            f"已下载：{display_path(ctx.workspace, target)}\n"
            f"大小：{_human_size(result['bytes'])}（{result['bytes']} 字节）\n"
            f"类型：{result['content_type']}\n"
            f"最终 URL：{result['url']}"
        )

    return [download_file]
