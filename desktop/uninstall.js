// desktop/uninstall.js
// 卸载界面控制器。只通过 preload 暴露的 window.alembicUninstall 与主进程通信。
"use strict";

const api = window.alembicUninstall;
if (!api) {
  document.body.textContent = "卸载界面未能加载 preload 接口，请从安装目录重新运行 ALEMBIC-Uninstall.exe。";
  throw new Error("alembicUninstall preload is unavailable");
}

const STEPS = [
  { id: "prepare", label: "检查安装信息" },
  { id: "stop", label: "停止正在运行的应用" },
  { id: "shortcuts", label: "删除快捷方式" },
  { id: "sandbox", label: "移除命令沙盒" },
  { id: "userdata", label: "删除用户数据" },
  { id: "registry", label: "移除卸载注册项" },
  { id: "cleanup", label: "安排删除安装目录" },
];

const el = {
  loading: document.getElementById("view-loading"),
  uninstall: document.getElementById("view-uninstall"),
  progress: document.getElementById("view-progress"),
  done: document.getElementById("view-done"),
  error: document.getElementById("view-error"),
  version: document.getElementById("info-version"),
  path: document.getElementById("info-path"),
  time: document.getElementById("info-time"),
  sandbox: document.getElementById("chk-sandbox"),
  sandboxHint: document.getElementById("sandbox-hint"),
  data: document.getElementById("chk-data"),
  confirm: document.getElementById("chk-confirm"),
  warning: document.getElementById("warning"),
  uninstallButton: document.getElementById("btn-uninstall"),
  bar: document.getElementById("progress-bar"),
  lead: document.getElementById("progress-lead"),
  stepList: document.getElementById("step-list"),
  log: document.getElementById("log"),
  logToggle: document.getElementById("btn-log"),
  doneMessage: document.getElementById("done-message"),
  errorMessage: document.getElementById("error-message"),
  errorLog: document.getElementById("error-log"),
};

let info = null;
let busy = false;
let logLines = [];
const stepNodes = new Map();

function show(view) {
  for (const key of ["loading", "uninstall", "progress", "done", "error"]) {
    el[key].hidden = key !== view;
  }
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    return date.toLocaleString("zh-CN", { hour12: false });
  } catch (_) {
    return date.toISOString();
  }
}

function appendLog(message) {
  if (!message) return;
  logLines.push(message);
  if (logLines.length > 500) logLines = logLines.slice(-500);
  el.log.textContent = logLines.join("\n");
  el.errorLog.textContent = logLines.slice(-40).join("\n");
}

function buildSteps() {
  for (const step of STEPS) {
    const li = document.createElement("li");
    li.dataset.id = step.id;
    const dot = document.createElement("span");
    dot.className = "dot";
    const label = document.createElement("span");
    label.textContent = step.label;
    const detail = document.createElement("span");
    detail.className = "step-detail";
    li.append(dot, label, detail);
    el.stepList.append(li);
    stepNodes.set(step.id, { li, detail });
  }
}

function setStep(id, status, message) {
  const node = stepNodes.get(id);
  if (!node) return;
  node.li.classList.remove("running", "done", "error", "skipped");
  if (status) node.li.classList.add(status);
  if (message) node.detail.textContent = message;
}

function setProgress(percent) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  el.bar.style.width = `${value}%`;
}

function handleProgress(payload) {
  if (!payload || typeof payload !== "object") return;
  if (payload.event === "step") {
    setStep(payload.id, payload.status, payload.status === "done" ? "" : payload.message);
    if (payload.message && payload.status === "running") appendLog(payload.message);
    if (Number.isFinite(payload.percent)) setProgress(payload.percent);
  } else if (payload.event === "log") {
    appendLog(payload.message);
  } else if (payload.event === "result") {
    if (payload.status === "error") appendLog(payload.message);
    if (Number.isFinite(payload.percent)) setProgress(payload.percent);
  }
}

function updateConfirmState() {
  const ready = Boolean(info && info.ok && el.confirm.checked && !busy);
  el.uninstallButton.disabled = !ready;
  if (el.data.checked) {
    el.warning.hidden = false;
    el.warning.textContent = "勾选「删除用户数据」后，会话和配置将无法恢复。工作区目录不受影响。";
  } else {
    el.warning.hidden = true;
  }
}

async function start() {
  if (!info || !info.ok || busy) return;
  busy = true;
  show("progress");
  setProgress(0);
  for (const step of STEPS) setStep(step.id, "", "");
  el.lead.textContent = "正在准备卸载…";

  const unsubscribe = api.onProgress((payload) => {
    handleProgress(payload);
    if (payload.event === "step" && payload.message) el.lead.textContent = payload.message;
  });

  try {
    const result = await api.start({
      removeSandbox: el.sandbox.checked,
      removeUserData: el.data.checked,
    });
    if (result && result.ok) {
      setProgress(100);
      const warnings = Array.isArray(result.warnings) ? result.warnings : [];
      el.doneMessage.textContent = warnings.length
        ? `卸载完成，但有 ${warnings.length} 项需要手动处理。安装目录会在本窗口关闭后自动删除。`
        : "应用文件已移除。安装目录会在本窗口关闭后几秒内自动删除；如有残留，可手动删除安装位置。";
      show("done");
    } else {
      const message = (result && result.error) || "卸载失败，请查看日志。";
      el.errorMessage.textContent = message;
      el.errorLog.hidden = false;
      el.errorLog.textContent = logLines.slice(-40).join("\n") || "（没有日志）";
      show("error");
    }
  } catch (error) {
    el.errorMessage.textContent = String((error && error.message) || error);
    el.errorLog.hidden = false;
    el.errorLog.textContent = logLines.slice(-40).join("\n") || "（没有日志）";
    show("error");
  } finally {
    busy = false;
    unsubscribe();
  }
}

async function load() {
  buildSteps();
  api.onBusy(() => {
    el.lead.textContent = "卸载正在进行，请等待完成后再关闭窗口。";
  });

  try {
    info = await api.info();
  } catch (error) {
    info = { ok: false, error: String((error && error.message) || error) };
  }

  if (!info || !info.ok) {
    el.errorMessage.textContent = (info && info.error) || "找不到安装记录。";
    show("error");
    return;
  }

  el.version.textContent = info.version || "—";
  el.path.textContent = info.installDir || "—";
  el.time.textContent = formatDate(info.installedAt);

  const sandboxCreated = Boolean(info.sandbox && info.sandbox.created);
  el.sandbox.checked = sandboxCreated;
  el.sandbox.disabled = !sandboxCreated;
  if (!sandboxCreated) {
    el.sandboxHint.textContent = "没有检测到由本次安装创建的沙盒账户；重装或手动初始化过的账户不会被自动删除。";
  }

  el.confirm.addEventListener("change", updateConfirmState);
  el.data.addEventListener("change", updateConfirmState);
  el.uninstallButton.addEventListener("click", start);
  el.logToggle.addEventListener("click", () => {
    const visible = el.log.hidden;
    el.log.hidden = !visible;
    el.errorLog.hidden = !visible;
    el.logToggle.textContent = visible ? "隐藏详细日志" : "显示详细日志";
  });

  show("uninstall");
  updateConfirmState();
}

document.getElementById("btn-close").addEventListener("click", async () => {
  const result = await api.close();
  if (result && !result.ok) el.lead.textContent = result.error;
});
document.getElementById("btn-cancel").addEventListener("click", () => api.close());
document.getElementById("btn-done").addEventListener("click", () => api.close());
document.getElementById("btn-error-close").addEventListener("click", () => api.close());
document.getElementById("btn-open-log").addEventListener("click", () => api.openLog());

load();
