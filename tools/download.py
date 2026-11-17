# tools/download.py
"""File download tool: ``download_file``.

``fetch_url`` can only read text; for PDFs, images, and archives it tells the model to
download them to the workspace first. This module performs that step: it streams remote
binary data into the workspace with SSRF validation, per-hop redirect validation, a size
cap, and temporary-file protection (a failed download leaves no partial file behind).

It carries the ``write`` risk level: user confirmation is required before anything is
written to disk.
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
    """Infer a safe filename from the URL path."""
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
    """Stream the download to ``target`` and remove the temporary file on failure."""
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    current = url
    part = target.with_name(target.name + ".part")
    wrote = 0

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FetchError("write_failed", f"Could not create directory {target.parent}: {exc}.") from exc

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
                                    f"HTTP {response.status_code} did not provide a Location header.",
                                )
                            current = urljoin(current, location)
                            continue
                        if response.status_code >= 400:
                            raise FetchError(
                                "http_error",
                                f"HTTP {response.status_code}: {current}",
                                hint="Check whether the URL requires login or permission, or use a different address.",
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
                                        f"Download exceeds the {_human_size(max_bytes)} limit.",
                                        hint='Increase "Max bytes" under Settings → Tools → File Download, or download a smaller file.',
                                    )
                                handle.write(chunk)
                        os.replace(part, target)
                        return {
                            "url": str(response.url),
                            "content_type": content_type,
                            "bytes": wrote,
                        }
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    raise FetchError("download_failed", f"Download failed: {exc}.") from exc
            raise FetchError("too_many_redirects", f"Exceeded {_MAX_REDIRECTS} redirects; stopped.")
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def build_download_tools(config: Mapping[str, str], ctx: ToolContext) -> list:
    """Build ``download_file`` (configured through the settings panel)."""

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
            return tool_error("empty_url", "url must not be empty.")
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
                f"Target {relative} is a directory and cannot be saved as a file.",
                hint="Provide a concrete file name.",
            )
        if target.exists() and not overwrite:
            return tool_error(
                "destination_exists",
                f"Target already exists: {relative}.",
                hint="Use a different file name, or pass overwrite=True (confirm before overwriting).",
            )
        reason = _protected_reason(target, ctx.workspace)
        if reason:
            return tool_error("protected_path", f"Cannot write {reason}: {relative}.")

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
            f"Downloaded: {display_path(ctx.workspace, target)}\n"
            f"Size: {_human_size(result['bytes'])} ({result['bytes']} bytes)\n"
            f"Type: {result['content_type']}\n"
            f"Final URL: {result['url']}"
        )

    return [download_file]
