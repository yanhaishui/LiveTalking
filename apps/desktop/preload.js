const { contextBridge, ipcRenderer } = require("electron");

function onChannel(channel, handler) {
  if (typeof handler !== "function") {
    return () => {};
  }
  const wrapped = (_event, payload) => handler(payload);
  ipcRenderer.on(channel, wrapped);
  return () => {
    ipcRenderer.removeListener(channel, wrapped);
  };
}

contextBridge.exposeInMainWorld("desktopBridge", {
  getStatus: () => ipcRenderer.invoke("desktop:get-status"),
  getSettings: () => ipcRenderer.invoke("desktop:get-settings"),
  updateSettings: (patch) => ipcRenderer.invoke("desktop:update-settings", patch),
  completeOnboarding: () => ipcRenderer.invoke("desktop:complete-onboarding"),

  startApi: () => ipcRenderer.invoke("desktop:start-api"),
  stopApi: () => ipcRenderer.invoke("desktop:stop-api"),
  restartApi: () => ipcRenderer.invoke("desktop:restart-api"),
  runChecks: () => ipcRenderer.invoke("desktop:run-checks"),

  getLogs: (tail = 800) => ipcRenderer.invoke("desktop:get-logs", { tail }),
  clearLogs: () => ipcRenderer.invoke("desktop:clear-logs"),

  exportDiagnostics: () => ipcRenderer.invoke("desktop:export-diagnostics"),
  exportSettings: () => ipcRenderer.invoke("desktop:export-settings"),
  importSettings: () => ipcRenderer.invoke("desktop:import-settings"),

  openWebAdminInBrowser: () => ipcRenderer.invoke("desktop:open-web-admin"),
  checkUpdates: () => ipcRenderer.invoke("desktop:check-updates"),

  onStatus: (handler) => onChannel("desktop:status", handler),
});
