"""Convenience launcher for ``python -m web``.

Two usage modes:

- Manual: ``python -m web`` (defaults to 127.0.0.1:8000; open it directly in a browser).
- Desktop shell: set ``ALEMBIC_PORT=0`` to let the OS assign a port, then print
  ``ALEMBIC_PORT=<port>`` to stdout for Electron to parse line by line. Set
  ``ALEMBIC_TOKEN`` to enable access-token authentication. The legacy
  ``SMART_AGENT_*`` variables remain supported as compatibility aliases.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

from config.settings import settings
from config.storage import ensure_storage
from web.server import app


def _bind(host: str, port: int) -> socket.socket:
    """Bind the socket before handing it to uvicorn so an OS-assigned ephemeral port is available."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock


def _env(name: str) -> str:
    """Read ALEMBIC_* with a legacy SMART_AGENT_* fallback."""
    return os.getenv(f"ALEMBIC_{name}", "").strip() or os.getenv(f"SMART_AGENT_{name}", "").strip()


def main() -> None:
    host = settings.web_host
    requested = int(_env("PORT") or settings.web_port)

    # 先把用户数据目录准备好（首次运行会把仓库里的 data/ memory/ 迁过去）
    storage = ensure_storage()
    for name in storage["migrated"]:
        print(f"已迁移历史数据：{name} → {storage['home']}", flush=True)

    try:
        sock = _bind(host, requested)
    except OSError as exc:
        print(f"无法监听 {host}:{requested}（{exc}）。可换端口：$env:WEB_PORT=8001", file=sys.stderr)
        raise SystemExit(1) from exc

    port = sock.getsockname()[1]

    # 外壳读这一行；格式稳定，便于逐行解析
    print(f"ALEMBIC_PORT={port}", flush=True)
    print(f"ALEMBIC 后端已启动：http://{host}:{port}（API 前缀 /api）", flush=True)
    print(f"前端开发地址：{settings.frontend_url}（另开终端：cd frontend && npm run dev）", flush=True)
    print(f"数据目录：{storage['home']}", flush=True)

    # 桌面外壳走文件而不是管道（受限环境里管道 stdio 可能被拒）
    port_file = _env("PORT_FILE")
    if port_file:
        try:
            Path(port_file).write_text(
                json.dumps({"port": port, "url": f"http://{host}:{port}"}),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"写入端口文件失败：{exc}", file=sys.stderr, flush=True)

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
