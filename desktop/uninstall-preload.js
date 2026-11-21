// desktop/uninstall-preload.js
// 卸载界面的最小桥接：渲染进程不能直接使用 Node，只能通过这里暴露的
// 几个 IPC 方法读取安装记录、启动卸载 worker 和订阅进度。
"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("alembicUninstall", {
  info: () => ipcRenderer.invoke("uninstall:info"),
  start: (options) => ipcRenderer.invoke("uninstall:start", options),
  close: () => ipcRenderer.invoke("uninstall:close"),
  openLog: () => ipcRenderer.invoke("uninstall:open-log"),
  onProgress: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("uninstall:progress", listener);
    return () => ipcRenderer.removeListener("uninstall:progress", listener);
  },
  onBusy: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("uninstall:busy", listener);
    return () => ipcRenderer.removeListener("uninstall:busy", listener);
  },
});
