// desktop/frontend-server.js
// 桌面外壳内的静态服务器：提供 frontend/dist 的页面，并把 /api 原样转发给
// Python sidecar。这样前端仍然用同源相对路径请求 /api，不需要改前端代码，
// 也不需要让后端在安装目录里额外提供静态资源。
//
// 说明：
// - 只监听 127.0.0.1 的随机端口，不对外暴露；
// - 静态文件严格限制在 root 目录内，拒绝路径穿越；
// - /api 转发支持 SSE（流式 chunk 直接 pipe，不缓冲）；
// - token 由前端从 /?token=... 读取后放进 Authorization 头，代理原样透传。

"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".txt": "text/plain; charset=utf-8",
  ".wasm": "application/wasm",
};

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-connection",
  "transfer-encoding",
  "upgrade",
  "te",
  "trailer",
]);

function safeFile(root, pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch (_) {
    return null;
  }
  if (!decoded || decoded.includes("\0")) return null;

  // 统一分隔符后再 resolve；resolve 之后必须仍在 root 内。
  const relative = decoded.replace(/^[/\\]+/, "").replace(/\\/g, "/");
  const target = path.resolve(root, relative);
  const normalizedRoot = path.resolve(root);
  if (target !== normalizedRoot && !target.startsWith(normalizedRoot + path.sep)) {
    return null;
  }
  // 不提供隐藏文件/目录（dist 里本来也没有）。
  const parts = path.relative(normalizedRoot, target).split(path.sep);
  if (parts.some((part) => part.startsWith("."))) return null;
  return target;
}

function sendText(res, status, text) {
  if (res.headersSent) {
    res.destroy();
    return;
  }
  const body = Buffer.from(text, "utf8");
  res.writeHead(status, {
    "Content-Type": "text/plain; charset=utf-8",
    "Content-Length": body.length,
    "Cache-Control": "no-store",
  });
  if (res.req && res.req.method === "HEAD") res.end();
  else res.end(body);
}

function serveStatic(req, res, url, root) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    sendText(res, 405, "Method Not Allowed");
    return;
  }

  let pathname = url.pathname || "/";
  if (pathname === "/") pathname = "/index.html";

  let file = safeFile(root, pathname);
  if (!file) {
    sendText(res, 404, "Not Found");
    return;
  }

  let stat = null;
  try {
    stat = fs.statSync(file);
    if (stat.isDirectory()) {
      file = path.join(file, "index.html");
      stat = fs.statSync(file);
    }
  } catch (_) {
    stat = null;
  }

  // SPA 回退：没有扩展名的路径交给 index.html（当前前端用 hash 路由，
  // 这里主要是为了兼容后续 history 路由和直接访问 /settings 这类地址）。
  if (!stat) {
    if (path.extname(pathname)) {
      sendText(res, 404, "Not Found");
      return;
    }
    file = path.join(root, "index.html");
    try {
      stat = fs.statSync(file);
    } catch (_) {
      sendText(res, 404, "前端资源不完整：缺少 index.html");
      return;
    }
  }

  const ext = path.extname(file).toLowerCase();
  const headers = {
    "Content-Type": MIME_TYPES[ext] || "application/octet-stream",
    "Content-Length": stat.size,
    // Vite 产物带内容哈希，可以长缓存；index.html 必须每次回源。
    "Cache-Control": ext === ".html" ? "no-cache" : "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
  };

  res.writeHead(200, headers);
  if (req.method === "HEAD") {
    res.end();
    return;
  }
  const stream = fs.createReadStream(file);
  stream.on("error", () => res.destroy());
  stream.pipe(res);
}

function tokensEqual(left, right) {
  const a = Buffer.from(String(left || ""), "utf8");
  const b = Buffer.from(String(right || ""), "utf8");
  return a.length === b.length && a.length > 0 && crypto.timingSafeEqual(a, b);
}

function proxyApi(req, res, backendPort, log) {
  const headers = { ...req.headers };
  for (const name of HOP_BY_HOP) delete headers[name];
  // http.request 需要目标 host；保留原始 path/query。
  headers.host = `127.0.0.1:${backendPort}`;

  const upstream = http.request(
    {
      host: "127.0.0.1",
      port: backendPort,
      method: req.method,
      path: req.url,
      headers,
    },
    (upstreamRes) => {
      const outHeaders = { ...upstreamRes.headers };
      for (const name of HOP_BY_HOP) delete outHeaders[name];
      res.writeHead(upstreamRes.statusCode || 502, outHeaders);
      // 直接 pipe：SSE 的每个 chunk 会立即写出，不做整包缓冲。
      upstreamRes.pipe(res);
      upstreamRes.on("error", () => res.destroy());
    },
  );

  upstream.on("error", (error) => {
    log(`API 代理失败：${error.message}`);
    if (!res.headersSent) {
      sendText(res, 502, `ALEMBIC 后端不可用：${error.message}`);
    } else {
      res.destroy();
    }
  });

  // 浏览器取消请求时（例如停止生成）一并断开上游。
  res.on("close", () => upstream.destroy());
  req.on("error", () => upstream.destroy());
  req.pipe(upstream);
}

/**
 * 启动静态服务器。
 *
 * 首次访问 `/?token=<token>` 时同时做两件事：设置 HttpOnly Cookie（刷新、
 * sessionStorage 被清空后仍能访问 /api），并正常返回 index.html，让前端
 * initAuthToken 从 URL 读取 token 存进 sessionStorage。第一次请求不依赖 cookie
 * 是否被浏览器接受。
 *
 * @param {{ root: string, backendPort: number, token?: string, log?: (message: string) => void }} options
 * @returns {Promise<{ port: number, close: () => Promise<void> }>}
 */
function startFrontendServer({ root, backendPort, token = "", log = () => {} }) {
  const staticRoot = path.resolve(root);
  if (!fs.existsSync(path.join(staticRoot, "index.html"))) {
    return Promise.reject(new Error(`前端目录缺少 index.html：${staticRoot}`));
  }

  const sockets = new Set();
  const server = http.createServer((req, res) => {
    let url;
    try {
      url = new URL(req.url || "/", "http://127.0.0.1");
    } catch (_) {
      sendText(res, 400, "Bad Request");
      return;
    }
    if (url.pathname === "/api" || url.pathname.startsWith("/api/")) {
      proxyApi(req, res, backendPort, log);
      return;
    }

    if (url.pathname === "/" && token) {
      const provided = url.searchParams.get("token") || "";
      if (provided) {
        if (!tokensEqual(provided, token)) {
          sendText(res, 401, "未授权：token 无效");
          return;
        }
        // 前端也会读 URL 里的 token；cookie 是刷新时的兜底。
        res.setHeader("Set-Cookie", `sa_token=${token}; Path=/; HttpOnly; SameSite=Lax`);
        res.setHeader("Cache-Control", "no-store");
      }
    }

    serveStatic(req, res, url, staticRoot);
  });

  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  server.on("clientError", (_error, socket) => {
    try {
      socket.end("HTTP/1.1 400 Bad Request\r\n\r\n");
    } catch (_) {
      /* 已经断开 */
    }
  });

  return new Promise((resolve, reject) => {
    const onError = (error) => reject(error);
    server.once("error", onError);
    server.listen(0, "127.0.0.1", () => {
      server.removeListener("error", onError);
      const address = server.address();
      const port = address && typeof address === "object" ? address.port : 0;
      log(`前端静态服务已启动：http://127.0.0.1:${port}（root=${staticRoot}）`);
      resolve({
        port,
        close: () =>
          new Promise((done) => {
            for (const socket of sockets) socket.destroy();
            server.close(() => done());
          }),
      });
    });
  });
}

module.exports = { startFrontendServer };
