# web/auth.py
"""可选的本地访问令牌。

桌面外壳（Electron）在启动后端时生成随机 token 并通过 ``ALEMBIC_TOKEN``
注入（旧的 ``SMART_AGENT_TOKEN`` 仍兼容）。窗口首次用 ``/?token=<token>`` 打开，服务端校验后换成 HttpOnly Cookie 并
重定向回 ``/``，地址栏里不会留下 token。

这样即使本机别的进程扫到了端口，也调不动 ``/api/chat``（那个接口能让 agent 改文件、
执行命令，审批也能被它自己批准）。

``python -m web`` 手动启动时通常不设这个变量，保持原来「打开就能用」的方式。
"""

from __future__ import annotations

import hmac

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

COOKIE_NAME = "sa_token"
HEADER_PREFIX = "bearer "


def install_auth(app: FastAPI, token: str) -> None:
    """给应用装上令牌校验中间件。"""

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        path = request.url.path
        # CORS 预检请求不带 Authorization，放行给 CORSMiddleware 处理
        if request.method == "OPTIONS":
            return await call_next(request)
        # 外壳探活用，不泄露任何配置
        if path == "/api/health":
            return await call_next(request)

        provided = request.cookies.get(COOKIE_NAME, "")
        header = request.headers.get("authorization", "")
        if not provided and header.lower().startswith(HEADER_PREFIX):
            provided = header[len(HEADER_PREFIX):].strip()
        if provided and hmac.compare_digest(provided, token):
            return await call_next(request)

        # 首次进入：用查询参数换 cookie，然后把 token 从地址栏抹掉
        query = request.query_params.get("token", "")
        if path == "/" and query and hmac.compare_digest(query, token):
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
            return response

        if path.startswith("/api/"):
            return JSONResponse({"detail": "未授权"}, status_code=401)
        return PlainTextResponse("未授权：请从桌面应用打开。", status_code=401)
