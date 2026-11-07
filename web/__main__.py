"""便捷启动：python -m web

两种用法：

- 手动：``python -m web``（默认 127.0.0.1:8000，浏览器直接打开）
- 桌面外壳：设置 ``ALEMBIC_PORT=0`` 由系统分配端口，启动后向 stdout 打印
  ``ALEMBIC_PORT=<port>`` 供 Electron 逐行解析；再配合 ``ALEMBIC_TOKEN``
  启用访问令牌。旧的 ``SMART_AGENT_*`` 变量仍作为兼容别名。
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
    """先自己 bind，再交给 uvicorn：这样才能拿到系统分配的临时端口。"""
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
