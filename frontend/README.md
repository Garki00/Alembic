# Smart Agent 前端

Vue 3 + Vite + TypeScript 实现的对话工作区。后端是仓库根目录的 FastAPI 应用，
两者通过 `/api` 通信，开发时由 Vite 代理请求。

## 开发

```powershell
npm install
npm run dev       # http://127.0.0.1:5173
```

如果 npm 的 registry 被配置成了二进制镜像，安装会 404，可显式指定：

```powershell
npm install --ignore-scripts --registry=https://registry.npmmirror.com
```

`--ignore-scripts` 不是必须的，但在受限终端里能避开 esbuild 等包的 postinstall
子进程被拒（Vue / Vite 本身不依赖这些 lifecycle 脚本）。

先在仓库根目录启动后端：

```powershell
.\.venv\Scripts\python.exe -m web   # http://127.0.0.1:8000
```

或者在仓库根目录一键启动两者：`.\scripts\dev.ps1`。

## 构建

```powershell
npm run build     # 先 vue-tsc 类型检查，再输出 dist/
npm run preview   # 用 4173 端口预览构建产物（/api 仍代理到 8000）
```

## 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VITE_BACKEND_URL` | `http://127.0.0.1:8000` | Vite 开发/预览服务器的 `/api` 代理目标 |
| `VITE_API_BASE_URL` | 空 | 直连后端时使用（如 `http://127.0.0.1:8000`）；留空表示走同源 `/api` |

在 `frontend/.env.local` 里设置即可（已在 `.gitignore` 中忽略）。

如果后端设置了 `SMART_AGENT_TOKEN`，前端首次访问可带 `?token=...`，
读取后会存入 sessionStorage 并改用 `Authorization: Bearer` 请求头。

## 目录

```
src/
  api/          fetch 封装、SSE 解析、后端数据结构类型
  components/   侧边栏、对话流、输入区、设置、审批卡片、工作区浏览
  lib/markdown.ts  轻量 Markdown 渲染（无第三方依赖）
  stores/       Pinia：workspace（模型/工具/工作区/会话）、chat、ui
  assets/main.css  设计系统（纸白 + 薄荷绿）
  App.vue       应用外壳与初始化
```
