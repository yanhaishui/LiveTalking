const { app, BrowserWindow, ipcMain, shell, Tray, Menu, nativeImage, dialog } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const { URL } = require("node:url");
const { spawn, spawnSync } = require("node:child_process");

let autoUpdater = null;
try {
  // 可选依赖: 未配置发布源时仅提供“检查更新”框架能力。
  ({ autoUpdater } = require("electron-updater"));
} catch (_) {
  autoUpdater = null;
}

const APP_TITLE = "码布斯 MEH 数字人系统";
const API_LOG_LIMIT = 4000;
const MAX_API_RESTART = 3;
const LOCAL_API_BASE = "http://127.0.0.1:9001";

let mainWindow = null;
let tray = null;
let apiProcess = null;
let webAdminServer = null;
let quitting = false;
let expectedApiStop = false;
let apiRestartTimer = null;

const defaultSettings = {
  runtimeMode: "local", // local | cloud
  repoRoot: "",
  remoteApiBase: "http://127.0.0.1:9001",
  autoStartApi: true,
  autoRestartApi: true,
  minimizeToTray: true,
  firstRun: true,
  livePort: 8010,
  ttsServer: "http://127.0.0.1:9000",
  updateChannel: "stable", // stable | beta
  updatesEnabled: true,
};

const state = {
  repoRoot: "",
  settingsPath: "",
  settings: { ...defaultSettings },
  webAdmin: {
    dir: "",
    url: "",
    running: false,
    source: "",
  },
  api: {
    running: false,
    pid: 0,
    localUrl: LOCAL_API_BASE,
    effectiveUrl: LOCAL_API_BASE,
    pythonPath: "",
    lastStartAt: "",
    lastStopAt: "",
    lastError: "",
    restartCount: 0,
  },
  updater: {
    supported: Boolean(autoUpdater),
    status: "idle",
    message: autoUpdater ? "未检查更新" : "未安装 electron-updater",
    lastCheckAt: "",
    configured: false,
    source: "",
  },
  checks: {
    time: "",
    summary: { ok: 0, warn: 0, error: 0 },
    items: [],
  },
  logs: [],
};

function nowIso() {
  return new Date().toISOString();
}

function pushLog(level, message) {
  const line = `[${nowIso()}] [${level}] ${message}`;
  state.logs.push(line);
  if (state.logs.length > API_LOG_LIMIT) {
    state.logs.splice(0, state.logs.length - API_LOG_LIMIT);
  }
}

function computeEffectiveApiBase() {
  if (state.settings.runtimeMode === "cloud") {
    const remote = String(state.settings.remoteApiBase || "").trim();
    return remote || LOCAL_API_BASE;
  }
  return LOCAL_API_BASE;
}

function applyEffectiveApiBase() {
  state.api.effectiveUrl = computeEffectiveApiBase();
}

function getStatus() {
  applyEffectiveApiBase();
  return {
    appTitle: APP_TITLE,
    appVersion: app.getVersion(),
    platform: process.platform,
    repoRoot: state.repoRoot,
    webAdmin: { ...state.webAdmin },
    api: { ...state.api },
    settings: { ...state.settings },
    checks: state.checks,
    updater: { ...state.updater },
  };
}

function broadcastStatus() {
  const payload = getStatus();
  BrowserWindow.getAllWindows().forEach((win) => {
    if (win && !win.isDestroyed()) {
      win.webContents.send("desktop:status", payload);
    }
  });
}

function normalizeRepoRootInput(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  return path.resolve(text);
}

function existsDir(dirPath) {
  if (!dirPath) return false;
  try {
    return fs.existsSync(dirPath) && fs.statSync(dirPath).isDirectory();
  } catch (_) {
    return false;
  }
}

function isValidRepoRoot(repoRoot) {
  if (!existsDir(repoRoot)) return false;
  const appEntry = path.join(repoRoot, "app.py");
  const controlApiEntry = path.join(repoRoot, "apps", "control_api", "__main__.py");
  return fs.existsSync(appEntry) && fs.existsSync(controlApiEntry);
}

function resolveBundledWebAdminDir() {
  const candidates = [
    path.join(process.resourcesPath || "", "web_admin"),
    path.join(__dirname, "..", "web_admin"),
  ];
  for (const dir of candidates) {
    if (existsDir(dir) && fs.existsSync(path.join(dir, "index.html"))) {
      return dir;
    }
  }
  return "";
}

function resolveRepoRoot(preferred = "") {
  const fromEnv = normalizeRepoRootInput(process.env.LIVETALKING_REPO_ROOT || "");
  const fromPreferred = normalizeRepoRootInput(preferred);
  const fromSetting = normalizeRepoRootInput(state.settings.repoRoot);

  const homeDir = app.getPath("home");
  const docDir = app.getPath("documents");
  const candidates = [
    fromEnv,
    fromPreferred,
    fromSetting,
    path.resolve(__dirname, "..", ".."),
    path.resolve(process.cwd()),
    path.join(homeDir, "IdeaProjects", "jhipster", "LiveTalking"),
    path.join(homeDir, "LiveTalking"),
    path.join(docDir, "LiveTalking"),
  ].filter(Boolean);

  for (const dir of candidates) {
    if (isValidRepoRoot(dir)) return dir;
  }
  for (const dir of candidates) {
    if (existsDir(dir)) return dir;
  }
  return path.resolve(process.cwd());
}

function normalizeSettings(raw) {
  const input = raw && typeof raw === "object" ? raw : {};
  return {
    runtimeMode: input.runtimeMode === "cloud" ? "cloud" : "local",
    repoRoot: normalizeRepoRootInput(input.repoRoot || defaultSettings.repoRoot),
    remoteApiBase: String(input.remoteApiBase || defaultSettings.remoteApiBase).trim() || defaultSettings.remoteApiBase,
    autoStartApi: input.autoStartApi !== false,
    autoRestartApi: input.autoRestartApi !== false,
    minimizeToTray: input.minimizeToTray !== false,
    firstRun: input.firstRun !== false,
    livePort: Number.isInteger(Number(input.livePort)) ? Number(input.livePort) : defaultSettings.livePort,
    ttsServer: String(input.ttsServer || defaultSettings.ttsServer).trim() || defaultSettings.ttsServer,
    updateChannel: input.updateChannel === "beta" ? "beta" : "stable",
    updatesEnabled: input.updatesEnabled !== false,
  };
}

function ensureParentDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function loadSettings() {
  state.settingsPath = path.join(app.getPath("userData"), "desktop-settings.json");
  try {
    if (fs.existsSync(state.settingsPath)) {
      const parsed = JSON.parse(fs.readFileSync(state.settingsPath, "utf8"));
      state.settings = normalizeSettings(parsed);
    } else {
      state.settings = normalizeSettings(defaultSettings);
      saveSettings();
    }
  } catch (err) {
    pushLog("WARN", `读取设置失败，已回退默认: ${err.message}`);
    state.settings = normalizeSettings(defaultSettings);
    saveSettings();
  }
  applyEffectiveApiBase();
}

function saveSettings() {
  ensureParentDir(state.settingsPath);
  fs.writeFileSync(state.settingsPath, JSON.stringify(state.settings, null, 2), "utf8");
}

function readUpdaterConfigFromFile(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return null;
  try {
    const text = fs.readFileSync(filePath, "utf8");
    const result = {};
    text.split(/\r?\n/).forEach((line) => {
      const m = line.match(/^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.+)\s*$/);
      if (!m) return;
      if (line.startsWith("  -")) return;
      result[m[1]] = m[2].replace(/^['"]|['"]$/g, "");
    });
    return result;
  } catch (_) {
    return null;
  }
}

function resolveUpdaterSource() {
  const configPath = path.join(process.resourcesPath || "", "app-update.yml");
  const cfg = readUpdaterConfigFromFile(configPath);
  if (!cfg || !cfg.provider) {
    return {
      configured: false,
      source: "",
      summary: "未配置更新源",
      path: configPath,
    };
  }

  if (cfg.provider === "github") {
    const owner = String(cfg.owner || "").trim();
    const repo = String(cfg.repo || "").trim();
    return {
      configured: Boolean(owner && repo),
      source: `github:${owner}/${repo}`,
      summary: owner && repo ? `${owner}/${repo}` : "GitHub（配置不完整）",
      path: configPath,
    };
  }

  if (cfg.provider === "generic") {
    const url = String(cfg.url || "").trim();
    return {
      configured: Boolean(url),
      source: `generic:${url}`,
      summary: url ? url : "Generic（配置不完整）",
      path: configPath,
    };
  }

  return {
    configured: true,
    source: String(cfg.provider),
    summary: String(cfg.provider),
    path: configPath,
  };
}

function refreshUpdaterConfigState() {
  const info = resolveUpdaterSource();
  state.updater.configured = info.configured;
  state.updater.source = info.source;
  if (!info.configured) {
    state.updater.status = "idle";
    state.updater.message = "未配置更新源";
  } else if (!state.updater.lastCheckAt) {
    state.updater.message = `更新源: ${info.summary}`;
  }
}

function simplifyUpdateErrorMessage(err) {
  const raw = String(err?.message || err || "未知错误");
  if (/Cannot find latest.*yml/i.test(raw) || /404/.test(raw)) {
    return "更新源可访问，但未发布 latest 更新文件";
  }
  if (/401|403|authentication|auth/i.test(raw)) {
    return "更新源鉴权失败，请检查仓库权限";
  }
  if (/ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ETIMEDOUT|network|timeout|socket/i.test(raw)) {
    return "更新服务器不可达，请检查网络后重试";
  }
  return raw.split("\n")[0].slice(0, 180);
}

async function restartWebAdminServer() {
  await stopWebAdminServer();
  await startWebAdminServer();
}

async function updateSettings(patch) {
  const prev = { ...state.settings };
  const prevRepoRoot = state.repoRoot;
  state.settings = normalizeSettings({ ...state.settings, ...(patch || {}) });
  saveSettings();
  state.repoRoot = resolveRepoRoot(state.settings.repoRoot);
  applyEffectiveApiBase();
  if (autoUpdater) {
    autoUpdater.allowPrerelease = state.settings.updateChannel === "beta";
    autoUpdater.channel = state.settings.updateChannel === "beta" ? "beta" : "latest";
  }

  if (prevRepoRoot !== state.repoRoot) {
    pushLog("INFO", `项目目录已切换: ${state.repoRoot}`);
    await restartWebAdminServer();
    if (state.settings.runtimeMode === "local" && state.api.running) {
      await restartControlApi();
    }
  }

  if (prev.runtimeMode !== state.settings.runtimeMode) {
    if (state.settings.runtimeMode === "cloud") {
      await stopControlApi();
      pushLog("INFO", "已切换到云端模式（停止本地 control_api）");
    } else {
      pushLog("INFO", "已切换到本机模式");
      if (state.settings.autoStartApi) {
        await startControlApi();
      }
    }
  } else if (state.settings.runtimeMode === "local" && prev.autoStartApi !== state.settings.autoStartApi) {
    if (state.settings.autoStartApi && !state.api.running) {
      await startControlApi();
    }
  }

  broadcastStatus();
  return { ...state.settings };
}

function resolvePythonPath(repoRoot) {
  const fromEnv = String(process.env.LIVETALKING_PYTHON || "").trim();
  const candidates = [];

  if (fromEnv) candidates.push(fromEnv);

  if (process.platform === "win32") {
    candidates.push(path.join(repoRoot, ".venv", "Scripts", "python.exe"));
    candidates.push("python");
  } else {
    candidates.push(path.join(repoRoot, ".venv", "bin", "python3"));
    candidates.push(path.join(repoRoot, ".venv", "bin", "python"));
    candidates.push("python3");
    candidates.push("python");
  }

  for (const item of candidates) {
    if (!item) continue;
    if (path.isAbsolute(item)) {
      if (fs.existsSync(item)) return item;
      continue;
    }
    return item;
  }

  return process.platform === "win32" ? "python" : "python3";
}

async function isPortInUse(port, host = "127.0.0.1") {
  return await new Promise((resolve) => {
    const server = net.createServer();

    server.once("error", (err) => {
      if (err && err.code === "EADDRINUSE") {
        resolve({ inUse: true, error: "" });
      } else {
        resolve({ inUse: false, error: err ? err.message : "未知错误" });
      }
    });

    server.once("listening", () => {
      server.close(() => resolve({ inUse: false, error: "" }));
    });

    try {
      server.listen({ host, port });
    } catch (err) {
      resolve({ inUse: false, error: err ? err.message : "监听失败" });
    }
  });
}

async function checkTcpReachable(host, port, timeoutMs = 1200) {
  return await new Promise((resolve) => {
    const socket = new net.Socket();
    let done = false;

    const finalize = (ok) => {
      if (done) return;
      done = true;
      try {
        socket.destroy();
      } catch (_) {
        // ignore
      }
      resolve(ok);
    };

    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finalize(true));
    socket.once("timeout", () => finalize(false));
    socket.once("error", () => finalize(false));

    socket.connect(Number(port), host);
  });
}

async function httpGetText(urlText, timeoutMs = 1500) {
  return await new Promise((resolve, reject) => {
    let done = false;
    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      reject(new Error("请求超时"));
    }, timeoutMs);

    const req = http.get(urlText, (resp) => {
      let body = "";
      resp.setEncoding("utf8");
      resp.on("data", (chunk) => {
        body += chunk;
      });
      resp.on("end", () => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve({ statusCode: resp.statusCode || 0, body });
      });
    });

    req.on("error", (err) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      reject(err);
    });
  });
}

function makeCheck(key, label, status, detail, suggestion = "") {
  return { key, label, status, detail, suggestion };
}

function summarizeChecks(items) {
  const summary = { ok: 0, warn: 0, error: 0 };
  items.forEach((item) => {
    if (item.status === "ok") summary.ok += 1;
    else if (item.status === "warn") summary.warn += 1;
    else summary.error += 1;
  });
  return summary;
}

async function runEnvironmentChecks() {
  const checks = [];
  const repoRoot = state.repoRoot;
  const webAdminDir = path.join(repoRoot, "apps", "web_admin");
  const bundledWebAdminDir = resolveBundledWebAdminDir();
  const controlApiEntry = path.join(repoRoot, "apps", "control_api", "__main__.py");
  const modelDir = path.join(repoRoot, "models");
  const avatarsDir = path.join(repoRoot, "data", "avatars");
  const pythonPath = resolvePythonPath(repoRoot);
  const repoOk = isValidRepoRoot(repoRoot);

  checks.push(
    makeCheck(
      "repo.root",
      "仓库目录",
      repoOk ? "ok" : "error",
      repoOk ? repoRoot : `目录无效或不完整: ${repoRoot}`,
      repoOk ? "" : "请在运行设置中点击“选择项目目录”，定位到 LiveTalking 根目录",
    ),
  );

  checks.push(
    makeCheck(
      "web_admin.dir",
      "web_admin 目录",
      fs.existsSync(webAdminDir) || Boolean(bundledWebAdminDir) ? "ok" : "error",
      fs.existsSync(webAdminDir)
        ? webAdminDir
        : bundledWebAdminDir
          ? `使用桌面内置资源: ${bundledWebAdminDir}`
          : "缺少 apps/web_admin 且无内置资源",
      fs.existsSync(webAdminDir) || Boolean(bundledWebAdminDir) ? "" : "请确认代码仓库完整，或重新打包桌面端",
    ),
  );

  checks.push(
    makeCheck(
      "control_api.entry",
      "control_api 启动文件",
      fs.existsSync(controlApiEntry) ? "ok" : "error",
      fs.existsSync(controlApiEntry) ? controlApiEntry : "缺少 apps/control_api/__main__.py",
      fs.existsSync(controlApiEntry) ? "" : "请检查 apps/control_api 目录是否完整",
    ),
  );

  const pyCheck = spawnSync(pythonPath, ["--version"], {
    cwd: existsDir(repoRoot) ? repoRoot : app.getPath("home"),
    timeout: 3000,
    encoding: "utf8",
    windowsHide: true,
  });
  const pyOk = pyCheck.status === 0;
  const pyDetail = pyOk
    ? String((pyCheck.stdout || pyCheck.stderr || "").trim() || pythonPath)
    : String((pyCheck.stderr || pyCheck.stdout || "python 执行失败").trim());

  checks.push(
    makeCheck(
      "python.exec",
      "Python 解释器",
      pyOk ? "ok" : "error",
      `${pythonPath} | ${pyDetail}`,
      pyOk ? "" : "请先创建 .venv 并安装依赖（pip install -r requirements.txt）",
    ),
  );

  let modelStatus = "warn";
  let modelDetail = "未检测到 wav2lip 权重";
  if (fs.existsSync(modelDir) && fs.statSync(modelDir).isDirectory()) {
    const files = fs.readdirSync(modelDir);
    const wav2lip = files.find((f) => /wav2lip/i.test(f) && /\.pth$/i.test(f));
    if (wav2lip) {
      modelStatus = "ok";
      modelDetail = `已检测到: ${path.join(modelDir, wav2lip)}`;
    } else {
      const anyPth = files.find((f) => /\.pth$/i.test(f));
      if (anyPth) {
        modelStatus = "warn";
        modelDetail = `检测到权重但未明确 wav2lip: ${path.join(modelDir, anyPth)}`;
      }
    }
  }
  checks.push(
    makeCheck(
      "model.wav2lip",
      "wav2lip 模型",
      modelStatus,
      modelDetail,
      modelStatus === "ok" ? "" : "请确认 models 目录已放置 wav2lip 对应 .pth 文件",
    ),
  );

  let avatarCount = 0;
  if (fs.existsSync(avatarsDir) && fs.statSync(avatarsDir).isDirectory()) {
    avatarCount = fs
      .readdirSync(avatarsDir)
      .filter((name) => fs.existsSync(path.join(avatarsDir, name)) && fs.statSync(path.join(avatarsDir, name)).isDirectory())
      .length;
  }
  checks.push(
    makeCheck(
      "avatar.assets",
      "数字人素材目录",
      avatarCount > 0 ? "ok" : "warn",
      `已检测到 ${avatarCount} 个 Avatar 目录 (${avatarsDir})`,
      avatarCount > 0 ? "" : "请先完成一次 Avatar 克隆任务",
    ),
  );

  const apiPortCheck = await isPortInUse(9001);
  let apiPortStatus = "ok";
  let apiPortDetail = "9001 端口可用";
  if (state.api.running) {
    apiPortStatus = "ok";
    apiPortDetail = `9001 端口由当前应用占用 (pid=${state.api.pid || 0})`;
  } else if (apiPortCheck.inUse) {
    apiPortStatus = "error";
    apiPortDetail = "9001 端口被其他进程占用";
  } else if (apiPortCheck.error) {
    apiPortStatus = "warn";
    apiPortDetail = `9001 端口检测异常: ${apiPortCheck.error}`;
  }
  checks.push(
    makeCheck(
      "port.api",
      "control_api 端口",
      apiPortStatus,
      apiPortDetail,
      apiPortStatus === "error" ? "请释放 9001 端口或关闭冲突服务" : "",
    ),
  );

  const livePort = Number(state.settings.livePort || 8010);
  const livePortCheck = await isPortInUse(livePort);
  const liveStatus = livePortCheck.inUse ? "warn" : "ok";
  checks.push(
    makeCheck(
      "port.live",
      "直播 listen 端口",
      liveStatus,
      livePortCheck.inUse ? `${livePort} 端口已占用` : `${livePort} 端口可用`,
      livePortCheck.inUse ? "建议切换到未占用端口，避免 app.py 启动失败" : "",
    ),
  );

  let ttsStatus = "warn";
  let ttsDetail = "未配置 TTS 地址";
  const ttsServer = String(state.settings.ttsServer || "").trim();
  if (ttsServer) {
    try {
      const parsed = new URL(ttsServer);
      const host = parsed.hostname;
      const port = Number(parsed.port || (parsed.protocol === "https:" ? 443 : 80));
      const reachable = await checkTcpReachable(host, port, 1200);
      if (!reachable) {
        ttsStatus = "warn";
        ttsDetail = `${ttsServer} 端口不可达`;
      } else {
        try {
          const resp = await httpGetText(`${ttsServer.replace(/\/$/, "")}/languages`, 1600);
          if (resp.statusCode >= 200 && resp.statusCode < 500) {
            ttsStatus = "ok";
            ttsDetail = `${ttsServer} 可访问 (/languages ${resp.statusCode})`;
          } else {
            ttsStatus = "warn";
            ttsDetail = `${ttsServer} 响应异常: HTTP ${resp.statusCode}`;
          }
        } catch (err) {
          ttsStatus = "warn";
          ttsDetail = `${ttsServer} 连通但 /languages 请求失败: ${err.message}`;
        }
      }
    } catch (err) {
      ttsStatus = "warn";
      ttsDetail = `TTS 地址格式错误: ${err.message}`;
    }
  }

  checks.push(
    makeCheck(
      "tts.server",
      "XTTS 服务",
      ttsStatus,
      ttsDetail,
      ttsStatus === "ok" ? "" : "如需声音克隆，请先启动 XTTS 服务（默认 9000）",
    ),
  );

  state.checks = {
    time: nowIso(),
    summary: summarizeChecks(checks),
    items: checks,
  };
  broadcastStatus();
  return state.checks;
}

function getMimeType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".html") return "text/html; charset=utf-8";
  if (ext === ".js") return "application/javascript; charset=utf-8";
  if (ext === ".css") return "text/css; charset=utf-8";
  if (ext === ".json") return "application/json; charset=utf-8";
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".ico") return "image/x-icon";
  return "application/octet-stream";
}

function createWebAdminRequestHandler(baseDir) {
  return (req, res) => {
    const rawUrl = String(req.url || "/");
    const pathOnly = rawUrl.split("?")[0] || "/";
    let decoded = "/";

    try {
      decoded = decodeURIComponent(pathOnly);
    } catch (_) {
      res.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Bad Request");
      return;
    }

    const relative = decoded === "/" ? "index.html" : decoded.replace(/^\/+/, "");
    const resolved = path.resolve(baseDir, relative);
    if (!resolved.startsWith(baseDir)) {
      res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Forbidden");
      return;
    }

    let finalPath = resolved;
    if (fs.existsSync(finalPath) && fs.statSync(finalPath).isDirectory()) {
      finalPath = path.join(finalPath, "index.html");
    }

    if (!fs.existsSync(finalPath) || !fs.statSync(finalPath).isFile()) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Not Found");
      return;
    }

    res.writeHead(200, { "Content-Type": getMimeType(finalPath) });
    fs.createReadStream(finalPath).pipe(res);
  };
}

async function startWebAdminServer() {
  if (webAdminServer && state.webAdmin.running) return;

  const repoWebAdminDir = path.join(state.repoRoot, "apps", "web_admin");
  const bundledWebAdminDir = resolveBundledWebAdminDir();

  let webAdminDir = "";
  let source = "";
  if (existsDir(repoWebAdminDir) && fs.existsSync(path.join(repoWebAdminDir, "index.html"))) {
    webAdminDir = repoWebAdminDir;
    source = "repo";
  } else if (bundledWebAdminDir) {
    webAdminDir = bundledWebAdminDir;
    source = "bundle";
  }

  if (!webAdminDir) {
    throw new Error(`web_admin 目录不存在: ${repoWebAdminDir}`);
  }

  const server = http.createServer(createWebAdminRequestHandler(webAdminDir));
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });

  const addr = server.address();
  const port = typeof addr === "object" && addr ? addr.port : 0;

  webAdminServer = server;
  state.webAdmin = {
    dir: webAdminDir,
    url: `http://127.0.0.1:${port}`,
    running: true,
    source,
  };

  pushLog("INFO", `web_admin 已托管: ${state.webAdmin.url} (source=${source || "unknown"})`);
  broadcastStatus();
}

async function stopWebAdminServer() {
  if (!webAdminServer) return;
  const server = webAdminServer;
  webAdminServer = null;
  await new Promise((resolve) => server.close(() => resolve()));
  state.webAdmin.running = false;
  state.webAdmin.url = "";
  pushLog("INFO", "web_admin 托管已停止");
}

function bindApiProcessLogs(child) {
  if (child.stdout) {
    child.stdout.on("data", (chunk) => {
      const text = String(chunk || "").trimEnd();
      if (text) pushLog("API", text);
    });
  }
  if (child.stderr) {
    child.stderr.on("data", (chunk) => {
      const text = String(chunk || "").trimEnd();
      if (text) pushLog("API-ERR", text);
    });
  }
}

function clearApiRestartTimer() {
  if (apiRestartTimer) {
    clearTimeout(apiRestartTimer);
    apiRestartTimer = null;
  }
}

function scheduleApiRestart(reason = "") {
  if (quitting || state.settings.runtimeMode !== "local" || !state.settings.autoRestartApi) {
    return;
  }
  if (state.api.restartCount >= MAX_API_RESTART) {
    pushLog("ERROR", `control_api 自动重启已达上限(${MAX_API_RESTART})，停止重试`);
    return;
  }

  state.api.restartCount += 1;
  const delayMs = 1200 * Math.pow(2, state.api.restartCount - 1);
  pushLog("WARN", `将在 ${delayMs}ms 后尝试第 ${state.api.restartCount} 次自动重启 control_api。${reason}`);
  clearApiRestartTimer();
  apiRestartTimer = setTimeout(() => {
    startControlApi().catch((err) => {
      pushLog("ERROR", `自动重启失败: ${err.message}`);
      scheduleApiRestart("重启失败");
    });
  }, delayMs);
}

async function startControlApi() {
  if (state.settings.runtimeMode !== "local") {
    pushLog("INFO", "当前为云端模式，跳过本地 control_api 启动");
    state.api.running = false;
    state.api.pid = 0;
    broadcastStatus();
    return;
  }

  if (apiProcess && !apiProcess.killed) {
    state.api.running = true;
    broadcastStatus();
    return;
  }

  if (!isValidRepoRoot(state.repoRoot)) {
    const msg = `control_api 启动失败: 项目目录无效 (${state.repoRoot})，请在运行设置中重新选择 LiveTalking 目录`;
    state.api.lastError = msg;
    pushLog("ERROR", msg);
    broadcastStatus();
    throw new Error(msg);
  }

  const portCheck = await isPortInUse(9001);
  if (portCheck.inUse) {
    const msg = "control_api 启动失败: 9001 端口已被占用";
    state.api.lastError = msg;
    pushLog("ERROR", msg);
    broadcastStatus();
    throw new Error(msg);
  }

  const pythonPath = resolvePythonPath(state.repoRoot);
  const args = ["-m", "apps.control_api"];
  expectedApiStop = false;

  pushLog("INFO", `启动 control_api: ${pythonPath} ${args.join(" ")}`);

  const child = spawn(pythonPath, args, {
    cwd: state.repoRoot,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  apiProcess = child;
  state.api.pythonPath = pythonPath;
  state.api.pid = child.pid || 0;
  state.api.running = true;
  state.api.lastStartAt = nowIso();
  state.api.lastError = "";
  state.api.restartCount = 0;

  bindApiProcessLogs(child);
  broadcastStatus();

  child.on("exit", (code, signal) => {
    if (apiProcess !== child) return;

    apiProcess = null;
    state.api.running = false;
    state.api.pid = 0;
    state.api.lastStopAt = nowIso();

    const msg = `control_api 已退出, code=${code ?? "null"}, signal=${signal || ""}`;
    pushLog(expectedApiStop ? "INFO" : "WARN", msg);

    if (!expectedApiStop && !quitting && code && code !== 0) {
      state.api.lastError = msg;
      scheduleApiRestart("进程异常退出");
    }
    expectedApiStop = false;
    broadcastStatus();
  });

  child.on("error", (err) => {
    const msg = `control_api 启动失败: ${err.message}`;
    pushLog("ERROR", msg);
    state.api.lastError = msg;
    state.api.running = false;
    state.api.pid = 0;
    broadcastStatus();
  });
}

async function stopControlApi() {
  clearApiRestartTimer();
  if (!apiProcess) {
    state.api.running = false;
    state.api.pid = 0;
    broadcastStatus();
    return;
  }

  const child = apiProcess;
  expectedApiStop = true;
  apiProcess = null;
  state.api.running = false;
  state.api.pid = 0;
  state.api.lastStopAt = nowIso();
  broadcastStatus();

  pushLog("INFO", `停止 control_api, pid=${child.pid}`);

  await new Promise((resolve) => {
    const timeout = setTimeout(() => {
      try {
        if (process.platform === "win32") {
          spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], { windowsHide: true });
        } else {
          child.kill("SIGKILL");
        }
      } catch (_) {
        // ignore
      }
      resolve();
    }, 3200);

    child.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });

    try {
      if (process.platform === "win32") {
        spawn("taskkill", ["/pid", String(child.pid), "/t"], { windowsHide: true });
      } else {
        child.kill("SIGTERM");
      }
    } catch (_) {
      clearTimeout(timeout);
      resolve();
    }
  });
}

async function restartControlApi() {
  await stopControlApi();
  await startControlApi();
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createMainWindow();
    return;
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
}

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1600,
    height: 980,
    minWidth: 1320,
    minHeight: 780,
    title: APP_TITLE,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.loadFile(path.join(__dirname, "renderer", "index.html"));
  mainWindow = win;

  win.on("close", (event) => {
    if (!quitting && state.settings.minimizeToTray) {
      event.preventDefault();
      win.hide();
      pushLog("INFO", "窗口已最小化到托盘");
    }
  });

  win.on("closed", () => {
    if (mainWindow === win) {
      mainWindow = null;
    }
  });

  broadcastStatus();
}

function createTray() {
  if (tray) return;

  const iconCandidates = [
    path.join(state.repoRoot, "assets", "main.png"),
    path.join(process.resourcesPath || "", "assets", "main.png"),
  ];
  const iconPath = iconCandidates.find((item) => fs.existsSync(item)) || "";
  const icon = iconPath ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();

  tray = new Tray(icon);
  tray.setToolTip(APP_TITLE);

  const buildMenu = () =>
    Menu.buildFromTemplate([
      { label: APP_TITLE, enabled: false },
      { type: "separator" },
      { label: "显示主界面", click: () => showMainWindow() },
      { label: "启动 Control API", click: () => startControlApi().catch((err) => pushLog("ERROR", err.message)) },
      { label: "停止 Control API", click: () => stopControlApi().catch((err) => pushLog("ERROR", err.message)) },
      { label: "重启 Control API", click: () => restartControlApi().catch((err) => pushLog("ERROR", err.message)) },
      { type: "separator" },
      {
        label: "退出",
        click: () => {
          quitting = true;
          app.quit();
        },
      },
    ]);

  tray.setContextMenu(buildMenu());
  tray.on("double-click", () => showMainWindow());
}

function setupAutoUpdater() {
  if (!autoUpdater) return;
  refreshUpdaterConfigState();

  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = state.settings.updateChannel === "beta";
  autoUpdater.channel = state.settings.updateChannel === "beta" ? "beta" : "latest";

  autoUpdater.on("checking-for-update", () => {
    state.updater.status = "checking";
    state.updater.lastCheckAt = nowIso();
    state.updater.message = "正在检查更新";
    pushLog("INFO", "正在检查桌面端更新");
    broadcastStatus();
  });

  autoUpdater.on("update-available", (info) => {
    state.updater.status = "available";
    state.updater.message = `检测到新版本: ${info?.version || "unknown"}`;
    pushLog("INFO", state.updater.message);
    broadcastStatus();
  });

  autoUpdater.on("update-not-available", () => {
    state.updater.status = "idle";
    state.updater.message = "当前已是最新版本";
    pushLog("INFO", state.updater.message);
    broadcastStatus();
  });

  autoUpdater.on("error", (err) => {
    state.updater.status = "error";
    const msg = simplifyUpdateErrorMessage(err);
    state.updater.message = `更新检查失败: ${msg}`;
    pushLog("WARN", `${state.updater.message}; raw=${String(err?.message || err || "")}`);
    broadcastStatus();
  });
}

async function checkForUpdates() {
  if (!autoUpdater) {
    return {
      ok: false,
      message: "electron-updater 未安装，无法检查更新",
    };
  }
  if (!state.settings.updatesEnabled) {
    return {
      ok: false,
      message: "已禁用自动更新检查（可在设置中重新开启）",
    };
  }
  refreshUpdaterConfigState();
  if (!state.updater.configured) {
    return {
      ok: false,
      message: "未配置更新源，请先发布桌面版本或配置 publish",
    };
  }

  try {
    await autoUpdater.checkForUpdates();
    return {
      ok: true,
      message: "已触发更新检查，请查看状态栏",
    };
  } catch (err) {
    const msg = simplifyUpdateErrorMessage(err);
    return {
      ok: false,
      message: `更新检查失败: ${msg}`,
    };
  }
}

function buildDiagnosticPayload() {
  return {
    generatedAt: nowIso(),
    appTitle: APP_TITLE,
    status: getStatus(),
    checks: state.checks,
    logs: state.logs.slice(-2000),
  };
}

async function exportDiagnostics() {
  const diagnostics = buildDiagnosticPayload();
  const stamp = nowIso().replace(/[:.]/g, "-");
  const defaultPath = path.join(app.getPath("desktop"), `meh-diagnostics-${stamp}.json`);

  const picked = await dialog.showSaveDialog({
    title: "导出诊断包",
    defaultPath,
    filters: [{ name: "JSON", extensions: ["json"] }],
  });

  if (picked.canceled || !picked.filePath) {
    return { ok: false, message: "已取消导出" };
  }

  fs.writeFileSync(picked.filePath, JSON.stringify(diagnostics, null, 2), "utf8");
  pushLog("INFO", `诊断包已导出: ${picked.filePath}`);
  return { ok: true, path: picked.filePath };
}

async function exportSettingsFile() {
  const defaultPath = path.join(app.getPath("desktop"), "meh-desktop-settings.json");
  const picked = await dialog.showSaveDialog({
    title: "导出桌面设置",
    defaultPath,
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  if (picked.canceled || !picked.filePath) {
    return { ok: false, message: "已取消导出" };
  }

  fs.writeFileSync(picked.filePath, JSON.stringify(state.settings, null, 2), "utf8");
  pushLog("INFO", `设置已导出: ${picked.filePath}`);
  return { ok: true, path: picked.filePath };
}

async function importSettingsFile() {
  const picked = await dialog.showOpenDialog({
    title: "导入桌面设置",
    properties: ["openFile"],
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  if (picked.canceled || !picked.filePaths?.length) {
    return { ok: false, message: "已取消导入" };
  }

  const filePath = picked.filePaths[0];
  const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
  await updateSettings(parsed);
  pushLog("INFO", `设置已导入: ${filePath}`);
  return { ok: true, path: filePath };
}

async function pickRepoRoot() {
  const picked = await dialog.showOpenDialog({
    title: "选择 LiveTalking 项目目录",
    properties: ["openDirectory"],
  });
  if (picked.canceled || !picked.filePaths?.length) {
    return { ok: false, message: "已取消选择" };
  }

  const selected = normalizeRepoRootInput(picked.filePaths[0]);
  state.settings.repoRoot = selected;
  saveSettings();
  state.repoRoot = resolveRepoRoot(selected);

  pushLog("INFO", `已选择项目目录: ${state.repoRoot}`);
  await restartWebAdminServer();
  if (state.settings.runtimeMode === "local" && state.settings.autoStartApi) {
    await startControlApi().catch((err) => {
      pushLog("WARN", `选择项目目录后自动启动 API 失败: ${err.message}`);
    });
  }
  await runEnvironmentChecks();
  broadcastStatus();

  return {
    ok: true,
    path: state.repoRoot,
    valid: isValidRepoRoot(state.repoRoot),
  };
}

function registerIpcHandlers() {
  ipcMain.handle("desktop:get-status", async () => getStatus());

  ipcMain.handle("desktop:get-settings", async () => ({ ...state.settings }));

  ipcMain.handle("desktop:update-settings", async (_event, patch) => {
    const data = await updateSettings(patch || {});
    await runEnvironmentChecks();
    return data;
  });

  ipcMain.handle("desktop:complete-onboarding", async () => {
    state.settings.firstRun = false;
    saveSettings();
    broadcastStatus();
    return { ok: true };
  });

  ipcMain.handle("desktop:start-api", async () => {
    await startControlApi();
    await runEnvironmentChecks();
    return getStatus();
  });

  ipcMain.handle("desktop:stop-api", async () => {
    await stopControlApi();
    await runEnvironmentChecks();
    return getStatus();
  });

  ipcMain.handle("desktop:restart-api", async () => {
    await restartControlApi();
    await runEnvironmentChecks();
    return getStatus();
  });

  ipcMain.handle("desktop:run-checks", async () => {
    return await runEnvironmentChecks();
  });

  ipcMain.handle("desktop:get-logs", async (_event, payload) => {
    const tail = Math.max(50, Math.min(3000, Number(payload?.tail || 800)));
    return { lines: state.logs.slice(-tail) };
  });

  ipcMain.handle("desktop:clear-logs", async () => {
    state.logs = [];
    return { ok: true };
  });

  ipcMain.handle("desktop:export-diagnostics", async () => {
    return await exportDiagnostics();
  });

  ipcMain.handle("desktop:export-settings", async () => {
    return await exportSettingsFile();
  });

  ipcMain.handle("desktop:import-settings", async () => {
    return await importSettingsFile();
  });

  ipcMain.handle("desktop:pick-repo-root", async () => {
    return await pickRepoRoot();
  });

  ipcMain.handle("desktop:open-web-admin", async () => {
    if (!state.webAdmin.url) {
      await startWebAdminServer();
    }
    if (!state.webAdmin.url) {
      throw new Error("web_admin 还未启动，请先选择正确项目目录");
    }
    await shell.openExternal(state.webAdmin.url);
    return { ok: true };
  });

  ipcMain.handle("desktop:check-updates", async () => {
    return await checkForUpdates();
  });
}

async function bootstrap() {
  loadSettings();
  state.repoRoot = resolveRepoRoot(state.settings.repoRoot);
  applyEffectiveApiBase();
  setupAutoUpdater();

  pushLog("INFO", `仓库路径: ${state.repoRoot}`);
  pushLog("INFO", `运行模式: ${state.settings.runtimeMode}`);

  await startWebAdminServer();

  if (state.settings.runtimeMode === "local" && state.settings.autoStartApi) {
    await startControlApi();
  }

  await runEnvironmentChecks();
  createMainWindow();
  createTray();

  if (app.isPackaged) {
    const result = await checkForUpdates();
    if (!result.ok) {
      pushLog("WARN", result.message);
    }
  }
}

app.whenReady().then(async () => {
  registerIpcHandlers();

  try {
    await bootstrap();
  } catch (err) {
    pushLog("ERROR", `桌面端初始化失败: ${err.message}`);
    state.api.lastError = String(err.message || err);
    createMainWindow();
    broadcastStatus();
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("before-quit", async () => {
  quitting = true;
  clearApiRestartTimer();
  await stopControlApi();
  await stopWebAdminServer();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
