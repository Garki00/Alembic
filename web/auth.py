# web/auth.py
"""Optional local access-token protection.

The desktop shell (Electron) generates a random token at backend startup and
injects it through ``ALEMBIC_TOKEN`` (the legacy ``SMART_AGENT_TOKEN`` is still
supported). On first launch the window opens ``/?token=<token>``; the server
validates it, sets an HttpOnly cookie, and redirects to ``/`` so the token does
not remain in the address bar.

The token prevents other local processes from calling ``/api/chat`` even if they
discover the port. That endpoint can modify files and run commands, and an
attacker able to call it could also approve its own approval requests.

``python -m web`` usually starts without this variable set, keeping the
"open and use" behavior.
"""

from __future__ import annotations

import hmac

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

COOKIE_NAME = "sa_token"
HEADER_PREFIX = "bearer "


def install_auth(app: FastAPI, token: str) -> None:
    """Install token-validation middleware on the application."""

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
