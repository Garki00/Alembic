// desktop/main.js
// Electron 外壳：拉起 Python sidecar（python -m web），等它就绪后用内置浏览器打开。
//
// 关键点：
// - 后端用 ALEMBIC_PORT=0 由系统分配端口，避免和别的服务撞车；
// - 每次启动生成随机 token，窗口用 /?token=... 首开，服务端换成 HttpOnly Cookie；
// - 窗口/应用退出时连同子进程树一起结束，不留孤儿进程。

// 最早的落盘标记：不依赖 electron，用来判断「进程到底有没有跑到 JS」。
// 如果启动后连 boot.log 都没有，说明崩在 Chromium 初始化阶段。
try {
  const bootDir = require("node:path").join(
    process.env.APPDATA || require("node:os").homedir(),
    "Alembic",
    "runtime",
  );
  require("node:fs").mkdirSync(bootDir, { recursive: true });
  require("node:fs").appendFileSync(
    require("node:path").join(bootDir, "boot.log"),
    `[${new Date().toISOString()}] boot argv=${process.argv.slice(1).join(" ")}\n`,
  );
} catch (_) { /* 记不下来也不能影响启动 */ }

const { app, BrowserWindow, Menu, shell } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

// Chromium 需要在 userData 目录里建单实例锁、缓存等文件。目录不可写时
// requestSingleInstanceLock() 会返回 false（表现为「双击后什么都没有」），
// 所以启动前先探测可写性，不行就换一个目录。
function usableUserData(dir) {
  if (!dir) return false;
  try {
    fs.mkdirSync(dir, { recursive: true });
    const probe = path.join(dir, `.write-probe-${process.pid}`);
    fs.writeFileSync(probe, "ok");
    fs.unlinkSync(probe);
    return true;
  } catch (_) {
    return false;
  }
}

function pickUserData() {
  // 默认放在 %LOCALAPPDATA%\Alembic：目录由我们自己创建，避免复用旧版本
  // 留下的 %APPDATA%\Alembic（里面的锁文件/日志可能因为历史原因打不开）。
  const preferred = process.env.ALEMBIC_USER_DATA
    || path.join(process.env.LOCALAPPDATA || app.getPath("appData"), "Alembic");
  if (usableUserData(preferred)) return preferred;

  const fallbacks = [
    path.join(app.getPath("appData"), "Alembic"),
    path.join(app.getPath("temp"), "Alembic"),
  ].filter(Boolean);
  for (const dir of fallbacks) {
    if (usableUserData(dir)) return dir;
  }
  return preferred;
}

const USER_DATA = pickUserData();
app.setPath("userData", USER_DATA);

// 最早的落盘标记：不依赖 app ready，用来判断「进程到底有没有跑到 JS」。
try {
  const bootDir = path.join(USER_DATA, "runtime");
  fs.mkdirSync(bootDir, { recursive: true });
  fs.appendFileSync(
    path.join(bootDir, "boot.log"),
    `[${new Date().toISOString()}] boot argv=${process.argv.slice(1).join(" ")} userData=${USER_DATA}\n`,
  );
} catch (_) { /* 记不下来也不能影响启动 */ }

const PROJECT_ROOT = path.resolve(__dirname, "..");
const IS_SMOKE = process.env.ALEMBIC_SMOKE === "1" || process.argv.includes("--smoke");
const START_TIMEOUT_MS = 90000;

let backend = null;
let win = null;
let quitting = false;
let backendLog = [];

// 对话界面不需要 GPU 加速；关掉能避开不少显卡驱动导致的启动崩溃
app.disableHardwareAcceleration();

// 启动日志：GUI 进程的 stdout 在 Windows 上经常接不到控制台，落文件更可靠。
// 打包后默认写 userData/runtime/desktop.log，方便用户反馈问题时直接给文件。
const LOG_FILE =
  process.env.ALEMBIC_LOG || path.join(USER_DATA, "runtime", "desktop.log");
let logDirReady = false;

function log(message) {
  const line = `[${new Date().toISOString()}] ${message}`;
  // 打包后没有控制台，stdout 可能是断开的管道：console.log 会抛 EPIPE，
  // 绝不能让它把未捕获异常处理器自己打崩。
  try {
    console.log(line);
  } catch (_) { /* 没有控制台就算了 */ }
  if (!LOG_FILE) return;
  try {
    if (!logDirReady) {
      fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
      logDirReady = true;
    }
    fs.appendFileSync(LOG_FILE, line + "\n");
  } catch (_) { /* 日志写不出去不影响运行 */ }
}

process.on("uncaughtException", (error) => {
  log(`未捕获异常：${error.stack || error.message}`);
});
process.on("unhandledRejection", (reason) => {
  log(`未处理的 Promise：${reason && reason.stack ? reason.stack : reason}`);
});

log("main.js 已加载");

// ---------------------------------------------------------------------------
// Python 解释器
// ---------------------------------------------------------------------------
// 后端进程：打包版优先用自带 exe，开发版用仓库里的 .venv
// ---------------------------------------------------------------------------
const BACKEND_EXE = process.platform === "win32" ? "alembic-backend.exe" : "alembic-backend";

function resolvePython() {
  if (process.env.ALEMBIC_PYTHON) return process.env.ALEMBIC_PYTHON;
  const candidates =
    process.platform === "win32"
      ? [path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")]
      : [path.join(PROJECT_ROOT, ".venv", "bin", "python")];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return process.platform === "win32" ? "python" : "python3";
}

/**
 * 返回 { command, args, cwd, label }。
 *
 * 优先级：
 * 1. ALEMBIC_BACKEND 显式指定的可执行文件（开发时手动指向 dist/ 里的产物）；
 * 2. 打包后的应用：resources/backend/<exe>（electron-builder 的 extraResources）；
 * 3. 开发：仓库 .venv 里的 python -m web。
 */
function resolveBackend() {
  const override = process.env.ALEMBIC_BACKEND;
  if (override && fs.existsSync(override)) {
    return { command: override, args: [], cwd: path.dirname(override), label: "指定后端" };
  }

  // 开发时想验证「打包后的后端」，用 `npm run start:frozen`（或 --frozen）
  const localBuild = path.join(PROJECT_ROOT, "dist", "alembic-backend", BACKEND_EXE);
  if (process.argv.includes("--frozen")) {
    if (!fs.existsSync(localBuild)) {
      throw new Error(`还没有构建后端：${localBuild}\n先运行 packaging/build.ps1`);
    }
    return { command: localBuild, args: [], cwd: path.dirname(localBuild), label: "本地打包后端" };
  }

  if (app.isPackaged) {
    const packaged = path.join(process.resourcesPath, "backend", BACKEND_EXE);
    if (fs.existsSync(packaged)) {
      return { command: packaged, args: [], cwd: path.dirname(packaged), label: "打包后端" };
    }
    throw new Error(`安装包缺少后端程序：${packaged}`);
  }

  const python = resolvePython();
  return {
    command: python,
    args: ["-m", "web"],
    cwd: PROJECT_ROOT,
    label: "源码后端",
  };
}

// ---------------------------------------------------------------------------
// 启动后端：端口写文件、日志写文件（不依赖管道 stdio）
// ---------------------------------------------------------------------------
function runtimeDir() {
  const dir = path.join(app.getPath("userData"), "runtime");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function tail(file, limit = 40) {
  try {
    return fs.readFileSync(file, "utf8").split("\n").slice(-limit).join("\n").trim();
  } catch (_) {
    return "";
  }
}

function waitForPortFile(file, timeoutMs = START_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      try {
        const data = JSON.parse(fs.readFileSync(file, "utf8"));
        if (data && Number(data.port)) {
          resolve(Number(data.port));
          return;
        }
      } catch (_) { /* 后端还没写出来 */ }
      if (Date.now() > deadline) reject(new Error("等待后端端口超时"));
      else setTimeout(tick, 150);
    };
    tick();
  });
}

function startBackend() {
  const token = crypto.randomBytes(24).toString("hex");
  const dir = runtimeDir();
  const portFile = path.join(dir, "sidecar.json");
  const logPath = path.join(dir, "backend.log");

  let target;
  try {
    target = resolveBackend();
  } catch (error) {
    return Promise.reject(error);
  }

  try {
    fs.rmSync(portFile, { force: true });
  } catch (_) { /* 没旧文件也无所谓 */ }

  log(`启动后端：${target.label} ${target.command} ${target.args.join(" ")}（cwd=${target.cwd}）`);

  // 用文件描述符而不是管道：受限环境里管道 stdio 会被拒（spawn EPERM），
  // 顺便把后端日志完整留档。日志文件打不开也不能挡住启动。
  let stdioOut = "ignore";
  try {
    stdioOut = fs.openSync(logPath, "a");
  } catch (error) {
    log(`后端日志文件打不开（${error.code || error.message}），改为丢弃输出`);
  }
  backend = spawn(target.command, target.args, {
    cwd: target.cwd,
    env: {
      ...process.env,
      ALEMBIC_TOKEN: token,
      ALEMBIC_PORT: "0",
      ALEMBIC_PORT_FILE: portFile,
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
    },
    stdio: ["ignore", stdioOut, stdioOut],
    windowsHide: true,
  });
  if (typeof stdioOut === "number") fs.closeSync(stdioOut); // 子进程已经继承句柄
  log(`后端进程已启动 pid=${backend.pid}`);

  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      if (error) reject(error);
      else resolve(value);
    };

    backend.on("error", (error) => {
      log(`后端启动失败：${error.message}`);
      finish(new Error(`无法启动后端：${error.message}\n\n程序：${target.command}`));
    });

    backend.on("exit", (code, signal) => {
      log(`后端退出 code=${code} signal=${signal}`);
      if (quitting) return;
      if (!settled) {
        finish(new Error(`后端退出（code=${code}, signal=${signal}）。\n\n${tail(logPath)}`));
        return;
      }
      log(`后端意外退出（code=${code}），显示错误页`);
      showError(new Error(`智能体服务意外退出（code=${code}）。\n\n${tail(logPath)}`));
    });

    waitForPortFile(portFile)
      .then((port) => {
        log(`后端已就绪，端口 ${port}`);
        finish(null, { port, token });
      })
      .catch((error) => {
        finish(new Error(`${error.message}。\n\n${tail(logPath)}`));
      });
  });
}

function waitForHealth(port, timeoutMs = START_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(
        { host: "127.0.0.1", port, path: "/api/health", timeout: 2000 },
        (response) => {
          response.resume();
          if (response.statusCode === 200) resolve();
          else retry();
        },
      );
      request.on("error", retry);
      request.on("timeout", () => {
        request.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() > deadline) reject(new Error("后端健康检查超时"));
      else setTimeout(attempt, 200);
    };
    attempt();
  });
}

// ---------------------------------------------------------------------------
// 窗口
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// 窗口：先显示「正在启动」，后端就绪后切到应用；失败则在窗口里说明原因
// ---------------------------------------------------------------------------
function pageUrl(html) {
  return "data:text/html;charset=utf-8," + encodeURIComponent(html);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
}

const LOADING_HTML = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
 html,body{height:100%;margin:0}
 body{display:flex;align-items:center;justify-content:center;background:#faf8f1;color:#4c5549;
      font-family:"Segoe UI","Microsoft YaHei",sans-serif}
 .box{text-align:center}
 .dot{width:10px;height:10px;margin:0 auto 18px;border-radius:50%;background:#397157;
      animation:pulse 1.2s ease-in-out infinite}
 p{margin:0;font-size:13px}
 .hint{margin-top:8px;color:#8b9787;font-size:11px}
 @keyframes pulse{0%,100%{opacity:.25;transform:scale(.85)}50%{opacity:1;transform:scale(1)}}
</style></head><body><div class="box"><div class="dot"></div>
<p>正在启动智能体后端…</p><p class="hint">首次启动可能需要十几秒</p></div></body></html>`;

function errorHtml(title, detail, logFile, backendLog) {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
 html,body{height:100%;margin:0}
 body{background:#faf8f1;color:#4c5549;font-family:"Segoe UI","Microsoft YaHei",sans-serif;
      padding:40px;box-sizing:border-box}
 h1{font-size:18px;margin:0 0 12px;color:#8a4436}
 pre{white-space:pre-wrap;word-break:break-all;background:#fffdf8;border:1px solid #e6c8bd;
     border-radius:6px;padding:14px 16px;font-size:12px;line-height:1.6;font-family:Consolas,monospace}
 .hint{color:#65735f;font-size:12px;margin-top:14px;line-height:1.8}
 code{font-family:Consolas,monospace;word-break:break-all}
</style></head><body><h1>${escapeHtml(title)}</h1><pre>${escapeHtml(detail)}</pre>
<p class="hint">外壳日志：<code>${escapeHtml(logFile || "（未启用文件日志）")}</code><br>
后端日志：<code>${escapeHtml(backendLog || "")}</code></p></body></html>`;
}

function writeSmokeReport(report) {
  if (!process.env.ALEMBIC_SMOKE_REPORT) return;
  try {
    fs.writeFileSync(process.env.ALEMBIC_SMOKE_REPORT, report, "utf8");
  } catch (_) { /* 写不出去也不影响退出 */ }
}

// Chromium 的 GPU / 渲染子进程在某些受限环境里会以「拒绝访问」失败
// （launch-failed, systemErrorCode=5），表现就是窗口一片空白。
// 这种情况下自动带 --no-sandbox 重启一次；已经带了这个参数就不再重启，避免循环。
let relaunched = false;

function relaunchNoSandbox() {
  if (relaunched) return;
  relaunched = true;
  quitting = true;
  // 重启前先结束当前后端，避免留下孤儿进程（用 kill 而不是 taskkill，
  // 因为紧接着就要 app.exit，等不到异步命令返回）
  try {
    if (backend && backend.exitCode === null) backend.kill();
  } catch (_) { /* 已经退出就算了 */ }
  app.relaunch({ args: process.argv.slice(1).concat(["--no-sandbox", "--disable-gpu"]) });
  app.exit(0);
}

function maybeRelaunchWithoutSandbox(details) {
  if (relaunched || process.argv.includes("--no-sandbox")) return;
  if (!details || details.reason !== "launch-failed") return;
  log("Chromium 子进程启动被拒，改用 --no-sandbox 重启");
  relaunchNoSandbox();
}

function createWindow() {
  win = new BrowserWindow({
    width: 1320,
    height: 880,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: "#faf8f1",
    title: "ALEMBIC",
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.once("ready-to-show", () => {
    log("ready-to-show");
    if (!IS_SMOKE) win.show();
  });

  win.webContents.on("render-process-gone", (_event, details) => {
    log(`渲染进程退出：${JSON.stringify(details)}`);
    maybeRelaunchWithoutSandbox(details);
  });

  win.webContents.on("did-fail-load", (_event, code, description, url) => {
    log(`页面加载失败：${code} ${description} ${url}`);
  });

  app.on("child-process-gone", (_event, details) => {
    log(`子进程退出：${JSON.stringify(details)}`);
    maybeRelaunchWithoutSandbox(details);
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });

  win.webContents.on("did-finish-load", async () => {
    const url = win.webContents.getURL();
    log(`页面加载完成：${url.startsWith("data:") ? "启动页" : url}`);
    if (!IS_SMOKE) return;
    if (!url.startsWith("http://127.0.0.1")) return; // 启动页/错误页不做应用自检

    let report = "";
    try {
      // 302 换 cookie 之后才是真正的页面，等一小段再自检
      await new Promise((resolve) => setTimeout(resolve, 900));
      // 顺手验证一次真实渲染：工作区、对话、工具卡片都应出现
      report = await win.webContents.executeJavaScript(`(() => {
        const workspaces = document.querySelectorAll(".ws-group").length;
        const sessions = document.querySelectorAll(".session-item").length;
        const openSettings = document.querySelector("#open-settings");
        if (openSettings) openSettings.click();
        const toolsTab = document.querySelector('[data-tab="tools"]');
        if (toolsTab) toolsTab.click();
        const tools = document.querySelectorAll(".tool-card").length;
        return JSON.stringify({ title: document.title, workspaces, sessions, tools });
      })()`);
      console.log(`SMOKE_OK ${report}`);
    } catch (error) {
      report = JSON.stringify({ error: error.message });
      console.log(`SMOKE_FAIL ${error.message}`);
    }
    writeSmokeReport(report);
    setTimeout(() => app.quit(), 300);
  });

  win.loadURL(pageUrl(LOADING_HTML)).catch(() => {
    /* 被错误页替换时会走到这里，属正常情况 */
  });
  win.on("closed", () => {
    win = null;
  });

  // 自检兜底：万一一直没跑到应用页，也要留下报告并退出
  if (IS_SMOKE) {
    setTimeout(() => {
      if (process.env.ALEMBIC_SMOKE_REPORT && !fs.existsSync(process.env.ALEMBIC_SMOKE_REPORT)) {
        writeSmokeReport(JSON.stringify({ error: "timeout" }));
      }
      log("自检超时，退出");
      app.quit();
    }, 45000).unref();
  }
}

function loadApp(port, token) {
  return win.loadURL(`http://127.0.0.1:${port}/?token=${token}`);
}

function showError(error) {
  const detail = String((error && error.message) || error || "未知错误");
  log(`显示错误页：${detail}`);
  if (!win) return;
  const backendLog = path.join(runtimeDir(), "backend.log");
  win
    .loadURL(pageUrl(errorHtml("ALEMBIC 启动失败", detail, LOG_FILE, backendLog)))
    .catch(() => { /* 连错误页都加载不了就只能看日志了 */ });
  if (!IS_SMOKE) win.show();
}

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------
function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  if (process.platform === "win32") {
    // Python 会再拉起子进程，直接结束整棵进程树
    spawn("taskkill", ["/pid", String(backend.pid), "/T", "/F"], { windowsHide: true });
  } else {
    backend.kill("SIGTERM");
  }
}

function buildMenu() {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      { label: "ALEMBIC", submenu: [{ role: "quit", label: "退出" }] },
      {
        label: "视图",
        submenu: [
          { role: "reload", label: "重新加载" },
          { role: "toggleDevTools", label: "开发者工具" },
          { type: "separator" },
          { role: "resetZoom", label: "实际大小" },
          { role: "zoomIn", label: "放大" },
          { role: "zoomOut", label: "缩小" },
          { role: "togglefullscreen", label: "全屏" },
        ],
      },
    ]),
  );
}

// 单实例锁：拿不到时不能一律退出——目录不可写时 Chromium 建不了锁文件，
// 也会返回 false，那就会变成「双击后什么都没有」。
function runningInstancePid() {
  try {
    const text = fs.readFileSync(path.join(USER_DATA, "instance.pid"), "utf8").trim();
    const pid = Number(text);
    if (!pid) return 0;
    process.kill(pid, 0); // 不抛异常说明进程还活着
    return pid;
  } catch (_) {
    return 0;
  }
}

const gotLock = app.requestSingleInstanceLock();
const otherInstance = gotLock ? 0 : runningInstancePid();
log(`单实例锁：${gotLock}${otherInstance ? `，已有实例 pid=${otherInstance}` : ""}`);

if (!gotLock && otherInstance) {
  log("已有实例在运行，退出本次启动");
  app.quit();
} else {
  if (!gotLock) {
    log("单实例锁不可用（目录可能不可写），继续启动");
  }
  try {
    fs.writeFileSync(path.join(USER_DATA, "instance.pid"), String(process.pid));
  } catch (_) { /* 写不了就退化成允许多实例 */ }

  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    buildMenu();
    // 已知环境需要关沙箱时可直接跳过第一次尝试（也方便排查问题）
    if (process.env.ALEMBIC_FORCE_NO_SANDBOX === "1" && !process.argv.includes("--no-sandbox")) {
      log("按 ALEMBIC_FORCE_NO_SANDBOX 重启为无沙箱模式");
      relaunchNoSandbox();
      return;
    }
    // 先把窗口显示出来（启动页），避免「启动后什么都没有」
    createWindow();
    log("窗口已创建，拉起后端");
    try {
      const { port, token } = await startBackend();
      log("等待健康检查");
      await waitForHealth(port);
      log("健康检查通过，加载应用页面");
      await loadApp(port, token);
    } catch (error) {
      log(`启动失败：${error.message || error}`);
      showError(error);
      if (IS_SMOKE) {
        writeSmokeReport(JSON.stringify({ error: String(error.message || error) }));
        setTimeout(() => app.quit(), 500);
      }
    }
  });
}

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  quitting = true;
  stopBackend();
});
process.on("exit", stopBackend);

