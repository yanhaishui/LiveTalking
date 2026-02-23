const state = {
  status: null,
  settings: null,
  logs: [],
  logFollow: true,
  logTimer: null,
  statusTimer: null,
  lastFrameUrl: "",
};

function byId(id) {
  return document.getElementById(id);
}

function safeText(value) {
  return String(value ?? "").replace(/[<>&]/g, (m) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[m]));
}

function normalizeStatusClass(status) {
  const text = String(status || "").toLowerCase();
  if (text === "ok") return "ok";
  if (text === "error") return "error";
  return "warn";
}

function setChip(id, text, type) {
  const el = byId(id);
  if (!el) return;
  el.textContent = text;
  el.className = `chip ${type}`;
}

function notify(message, type = "info") {
  const bar = byId("noticeBar");
  if (!bar) return;
  bar.textContent = message;
  bar.className = `notice ${type}`;
}

function formatDateTime(raw) {
  if (!raw) return "-";
  const date = new Date(String(raw));
  if (Number.isNaN(date.getTime())) return String(raw);
  return date.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

function appendLocalLog(line) {
  state.logs.push(`[local] ${line}`);
  if (state.logs.length > 1600) {
    state.logs.splice(0, state.logs.length - 1600);
  }
  renderLogs();
}

function renderLogs() {
  const box = byId("logBox");
  if (!box) return;
  box.textContent = state.logs.length ? state.logs.join("\n") : "暂无日志";
  box.scrollTop = box.scrollHeight;
}

function postConfigToFrame() {
  const frame = byId("webAdminFrame");
  if (!frame || !frame.contentWindow || !state.status) return;

  try {
    frame.contentWindow.postMessage(
      {
        type: "MEH_DESKTOP_CONFIG",
        apiBase: state.status.api?.effectiveUrl || "http://127.0.0.1:9001",
      },
      "*",
    );
  } catch (err) {
    appendLocalLog(`推送桌面配置到 web_admin 失败: ${err.message}`);
  }
}

function renderSettings() {
  const settings = state.settings || state.status?.settings;
  if (!settings) return;

  byId("settingRuntimeMode").value = settings.runtimeMode || "local";
  byId("settingRepoRoot").value = state.status?.repoRoot || settings.repoRoot || "";
  byId("settingRemoteApiBase").value = settings.remoteApiBase || "";
  byId("settingTtsServer").value = settings.ttsServer || "http://127.0.0.1:9000";
  byId("settingLivePort").value = String(settings.livePort || 8010);
  byId("settingUpdateChannel").value = settings.updateChannel || "stable";
  byId("settingAutoStartApi").checked = settings.autoStartApi !== false;
  byId("settingAutoRestartApi").checked = settings.autoRestartApi !== false;
  byId("settingMinimizeToTray").checked = settings.minimizeToTray !== false;
  byId("settingUpdatesEnabled").checked = settings.updatesEnabled !== false;
}

function renderChecks(checks) {
  const data = checks || state.status?.checks || { summary: { ok: 0, warn: 0, error: 0 }, items: [] };
  byId("checkOkChip").textContent = `OK ${data.summary?.ok || 0}`;
  byId("checkWarnChip").textContent = `WARN ${data.summary?.warn || 0}`;
  byId("checkErrorChip").textContent = `ERROR ${data.summary?.error || 0}`;
  byId("checkTime").textContent = data.time ? `最近检测: ${formatDateTime(data.time)}` : "未检测";

  const body = byId("checkTableBody");
  const rows = Array.isArray(data.items) ? data.items : [];
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="3">暂无体检结果</td></tr>';
    return;
  }

  body.innerHTML = rows
    .map((item) => `
      <tr>
        <td>${safeText(item.label || item.key || "-")}</td>
        <td><span class="status-pill ${normalizeStatusClass(item.status)}">${safeText(item.status || "warn")}</span></td>
        <td title="${safeText(item.suggestion || "")}">${safeText(item.detail || "-")}</td>
      </tr>
    `)
    .join("");
}

function renderOnboarding() {
  const panel = byId("onboardingPanel");
  if (!panel || !state.status?.settings) return;

  const firstRun = state.status.settings.firstRun !== false;
  panel.classList.toggle("hidden", !firstRun);
}

function renderStatus() {
  const status = state.status;
  if (!status) return;

  const apiRunning = Boolean(status.api?.running);
  const webRunning = Boolean(status.webAdmin?.running);
  const mode = status.settings?.runtimeMode || "local";

  setChip("modeChip", `模式: ${mode === "cloud" ? "cloud" : "local"}`, mode === "cloud" ? "warn" : "info");
  setChip("apiStatusChip", apiRunning ? "API 运行中" : "API 未启动", apiRunning ? "ok" : "warn");
  setChip("webStatusChip", webRunning ? "管理台已托管" : "管理台未托管", webRunning ? "ok" : "warn");

  const updaterStatus = status.updater?.status || "idle";
  const updaterType = updaterStatus === "error" ? "error" : updaterStatus === "available" ? "warn" : "info";
  setChip("updateChip", `更新: ${status.updater?.message || "未检查"}`, updaterType);

  byId("apiMeta").textContent = `${status.api?.effectiveUrl || "-"} | pid=${status.api?.pid || 0}`;
  const webSource = status.webAdmin?.source ? ` (${status.webAdmin.source})` : "";
  byId("webMeta").textContent = `${status.webAdmin?.url || "-"}${webSource}`;
  byId("pythonMeta").textContent = status.api?.pythonPath || "-";
  byId("repoMeta").textContent = status.repoRoot || "-";

  const frame = byId("webAdminFrame");
  const nextUrl = status.webAdmin?.url ? `${status.webAdmin.url}/` : "";
  if (frame && nextUrl && state.lastFrameUrl !== nextUrl) {
    state.lastFrameUrl = nextUrl;
    frame.src = nextUrl;
  }

  renderSettings();
  renderChecks(status.checks);
  renderOnboarding();
  postConfigToFrame();
}

async function refreshStatus() {
  if (!window.desktopBridge) {
    appendLocalLog("desktopBridge 不可用，当前页面应在 Electron 内运行");
    return;
  }

  try {
    state.status = await window.desktopBridge.getStatus();
    state.settings = state.status.settings;
    renderStatus();
  } catch (err) {
    appendLocalLog(`获取状态失败: ${err.message}`);
  }
}

async function refreshLogs() {
  if (!window.desktopBridge) return;
  try {
    const result = await window.desktopBridge.getLogs(1000);
    state.logs = Array.isArray(result?.lines) ? result.lines : [];
    renderLogs();
  } catch (err) {
    appendLocalLog(`获取日志失败: ${err.message}`);
  }
}

async function refreshChecks() {
  if (!window.desktopBridge) return;
  try {
    const checks = await window.desktopBridge.runChecks();
    renderChecks(checks);

    const errors = Number(checks?.summary?.error || 0);
    const warns = Number(checks?.summary?.warn || 0);
    if (errors > 0) {
      notify(`体检完成：${errors} 项错误，请先修复后再开播`, "error");
    } else if (warns > 0) {
      notify(`体检完成：${warns} 项告警，可继续使用但建议优化`, "warn");
    } else {
      notify("体检完成：全部通过", "ok");
    }
    await refreshStatus();
  } catch (err) {
    notify(`体检失败: ${err.message}`, "error");
  }
}

function startAutoPolling() {
  if (state.statusTimer) clearInterval(state.statusTimer);
  if (state.logTimer) clearInterval(state.logTimer);

  state.statusTimer = setInterval(() => {
    refreshStatus();
  }, 2500);

  state.logTimer = setInterval(() => {
    if (state.logFollow) {
      refreshLogs();
    }
  }, 1200);
}

function collectSettingsPatchFromForm() {
  return {
    repoRoot: byId("settingRepoRoot").value.trim(),
    runtimeMode: byId("settingRuntimeMode").value,
    remoteApiBase: byId("settingRemoteApiBase").value.trim(),
    ttsServer: byId("settingTtsServer").value.trim(),
    livePort: Number(byId("settingLivePort").value || 8010),
    updateChannel: byId("settingUpdateChannel").value,
    autoStartApi: byId("settingAutoStartApi").checked,
    autoRestartApi: byId("settingAutoRestartApi").checked,
    minimizeToTray: byId("settingMinimizeToTray").checked,
    updatesEnabled: byId("settingUpdatesEnabled").checked,
  };
}

function bindEvents() {
  byId("btnStartApi")?.addEventListener("click", async () => {
    try {
      await window.desktopBridge.startApi();
      notify("已启动本地 control_api", "ok");
      await Promise.all([refreshStatus(), refreshLogs(), refreshChecks()]);
    } catch (err) {
      notify(`启动 API 失败: ${err.message}`, "error");
    }
  });

  byId("btnStopApi")?.addEventListener("click", async () => {
    try {
      await window.desktopBridge.stopApi();
      notify("已停止本地 control_api", "warn");
      await Promise.all([refreshStatus(), refreshLogs(), refreshChecks()]);
    } catch (err) {
      notify(`停止 API 失败: ${err.message}`, "error");
    }
  });

  byId("btnRestartApi")?.addEventListener("click", async () => {
    try {
      await window.desktopBridge.restartApi();
      notify("已重启本地 control_api", "ok");
      await Promise.all([refreshStatus(), refreshLogs(), refreshChecks()]);
    } catch (err) {
      notify(`重启 API 失败: ${err.message}`, "error");
    }
  });

  byId("btnOpenBrowser")?.addEventListener("click", async () => {
    try {
      await window.desktopBridge.openWebAdminInBrowser();
    } catch (err) {
      const msg = String(err?.message || err);
      if (msg.includes("web_admin")) {
        notify("打开管理台失败：请先点击“选择项目目录”并定位到 LiveTalking 根目录", "error");
      } else {
        notify(`打开浏览器失败: ${msg}`, "error");
      }
    }
  });

  byId("btnPickRepoRoot")?.addEventListener("click", async () => {
    try {
      const result = await window.desktopBridge.pickRepoRoot();
      if (!result?.ok) {
        notify(result?.message || "已取消选择项目目录", "info");
        return;
      }
      if (result.valid) {
        notify(`项目目录已更新: ${result.path}`, "ok");
      } else {
        notify(`目录已记录，但不完整: ${result.path}，请确认是 LiveTalking 根目录`, "warn");
      }
      await Promise.all([refreshStatus(), refreshChecks(), refreshLogs()]);
    } catch (err) {
      notify(`选择项目目录失败: ${err.message}`, "error");
    }
  });

  byId("btnRefreshFrame")?.addEventListener("click", () => {
    const frame = byId("webAdminFrame");
    if (frame?.src) {
      frame.src = frame.src;
      setTimeout(() => postConfigToFrame(), 500);
      notify("已刷新嵌入页", "info");
    }
  });

  byId("btnRunChecks")?.addEventListener("click", () => refreshChecks());
  byId("btnOnboardingRunChecks")?.addEventListener("click", () => refreshChecks());

  byId("btnCheckUpdates")?.addEventListener("click", async () => {
    try {
      const result = await window.desktopBridge.checkUpdates();
      notify(result.message || "已触发更新检查", result.ok ? "info" : "warn");
      await refreshStatus();
    } catch (err) {
      notify(`更新检查失败: ${err.message}`, "error");
    }
  });

  byId("settingsForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const patch = collectSettingsPatchFromForm();
      state.settings = await window.desktopBridge.updateSettings(patch);
      notify("设置已保存", "ok");
      await Promise.all([refreshStatus(), refreshChecks()]);
    } catch (err) {
      notify(`保存设置失败: ${err.message}`, "error");
    }
  });

  byId("btnImportSettings")?.addEventListener("click", async () => {
    try {
      const result = await window.desktopBridge.importSettings();
      if (result.ok) {
        notify(`设置已导入: ${result.path}`, "ok");
        await Promise.all([refreshStatus(), refreshChecks()]);
      } else {
        notify(result.message || "已取消导入", "info");
      }
    } catch (err) {
      notify(`导入设置失败: ${err.message}`, "error");
    }
  });

  byId("btnExportSettings")?.addEventListener("click", async () => {
    try {
      const result = await window.desktopBridge.exportSettings();
      if (result.ok) {
        notify(`设置已导出: ${result.path}`, "ok");
      } else {
        notify(result.message || "已取消导出", "info");
      }
    } catch (err) {
      notify(`导出设置失败: ${err.message}`, "error");
    }
  });

  byId("btnExportDiagnostics")?.addEventListener("click", async () => {
    try {
      const result = await window.desktopBridge.exportDiagnostics();
      if (result.ok) {
        notify(`诊断包已导出: ${result.path}`, "ok");
      } else {
        notify(result.message || "已取消导出", "info");
      }
    } catch (err) {
      notify(`导出诊断包失败: ${err.message}`, "error");
    }
  });

  byId("btnOnboardingDone")?.addEventListener("click", async () => {
    try {
      await window.desktopBridge.completeOnboarding();
      notify("首次引导已完成", "ok");
      await refreshStatus();
    } catch (err) {
      notify(`操作失败: ${err.message}`, "error");
    }
  });

  byId("btnRefreshLogs")?.addEventListener("click", () => refreshLogs());

  byId("btnCopyLogs")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(state.logs.join("\n"));
      notify("日志已复制到剪贴板", "ok");
    } catch (err) {
      notify(`复制日志失败: ${err.message}`, "error");
    }
  });

  byId("btnClearLogs")?.addEventListener("click", async () => {
    try {
      await window.desktopBridge.clearLogs();
      state.logs = [];
      renderLogs();
      notify("日志视图已清空", "info");
    } catch (err) {
      notify(`清空日志失败: ${err.message}`, "error");
    }
  });

  byId("logFollowSwitch")?.addEventListener("change", (event) => {
    state.logFollow = Boolean(event?.target?.checked);
  });

  byId("webAdminFrame")?.addEventListener("load", () => {
    postConfigToFrame();
  });

  if (window.desktopBridge) {
    window.desktopBridge.onStatus((payload) => {
      state.status = payload;
      state.settings = payload?.settings || state.settings;
      renderStatus();
    });
  }
}

async function bootstrap() {
  if (!window.desktopBridge) {
    notify("desktopBridge 不可用，请在 Electron 中打开该页面", "error");
    return;
  }

  bindEvents();
  await refreshStatus();
  await refreshLogs();
  await refreshChecks();
  startAutoPolling();
  notify("桌面端运行就绪", "ok");
}

bootstrap();
