// desktop/preload.js
// Minimal bridge for the desktop window frame and About dialog. The renderer
// has no direct access to Node or Electron.
"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("alembic", {
  getInfo: () => ipcRenderer.invoke("app:info"),
  minimize: () => ipcRenderer.invoke("window:minimize"),
  toggleMaximize: () => ipcRenderer.invoke("window:toggle-maximize"),
  close: () => ipcRenderer.invoke("window:close"),
  isMaximized: () => ipcRenderer.invoke("window:is-maximized"),
  onMaximized: (callback) => {
    const listener = (_event, value) => callback(Boolean(value));
    ipcRenderer.on("window:maximized", listener);
    return () => ipcRenderer.removeListener("window:maximized", listener);
  },
});
