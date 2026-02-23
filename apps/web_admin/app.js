/* eslint-disable no-alert */

const state = {
  apiBase: localStorage.getItem("controlApiBase") || "http://127.0.0.1:9001",
  currentSessionId: null,
  currentPage: "dashboard",
  autoRefreshEnabled: localStorage.getItem("webAdminAutoRefresh") === "1",
  autoRefreshTimer: null,
  logFollowEnabled: localStorage.getItem("webAdminLogFollow") !== "0",
  logAutoScrollEnabled: localStorage.getItem("webAdminLogAutoScroll") !== "0",
  logPollIntervalMs: Number(localStorage.getItem("webAdminLogPollIntervalMs") || "1500"),
  logPollTimer: null,
  logPollBusy: false,
  logLastError: "",
  logLines: [],
  logKeyword: "",
  logLastUpdatedAt: "",
  liveWizardStep: 1,
  selectedJobId: "",
  healthChecks: [],
  healthSummary: { ok: 0, warn: 0, error: 0, time: "" },
  avatars: [],
  voices: [],
  scripts: [],
  playlists: [],
  presets: [],
  jobs: [],
  speakerStatus: null,
  filters: {
    avatarSearch: "",
    avatarStatus: "",
    voiceSearch: "",
    voiceStatus: "",
    voiceEngine: "",
    scriptSearch: "",
    scriptEnabled: "",
    playlistSearch: "",
    playlistEnabled: "",
    presetSearch: "",
    presetTransport: "",
    jobSearch: "",
    jobStatus: "",
  },
  editing: {
    avatarId: null,
    voiceId: null,
    scriptId: null,
    playlistId: null,
    presetId: null,
  },
};

const PAGE_TITLES = {
  dashboard: "首页控制台",
  health: "系统体检",
  live: "直播控制",
  avatars: "形象管理",
  voices: "声音管理",
  scripts: "脚本管理",
  playlists: "轮播计划",
  presets: "直播预设",
  ops: "任务与消息",
};

const TTS_ENGINE_OPTIONS = [
  { value: "", label: "不使用（留空）" },
  { value: "edgetts", label: "edgetts" },
  { value: "xtts", label: "xtts" },
  { value: "gpt-sovits", label: "gpt-sovits" },
  { value: "cosyvoice", label: "cosyvoice" },
  { value: "fishtts", label: "fishtts" },
  { value: "tencent", label: "tencent" },
  { value: "doubao", label: "doubao" },
  { value: "indextts2", label: "indextts2" },
  { value: "azuretts", label: "azuretts" },
];

const STATIC_REF_FILE_OPTIONS = {
  edgetts: ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiaNeural", "zh-CN-YunjianNeural"],
  azuretts: ["zh-CN-XiaoxiaoMultilingualNeural", "zh-CN-XiaoxiaoNeural"],
  "gpt-sovits": ["speaker_default"],
  cosyvoice: ["voice_default"],
  fishtts: ["fish_default"],
  doubao: ["doubao_default"],
  tencent: ["101001"],
  indextts2: ["index_default"],
};

const STATIC_REF_TEXT_OPTIONS = {
  default: ["", "你好，欢迎来到直播间。", "这是一个数字人演示播报文本。"],
  "gpt-sovits": ["", "今天天气不错，我们来介绍产品细节。", "欢迎来到直播间，喜欢可以点关注。"],
  xtts: ["", "这是一段参考文本。", "欢迎来到直播间。"],
};

const LOG_POLL_INTERVAL_OPTIONS = [1000, 1500, 3000, 5000];
const BJT_TIMEZONE = "Asia/Shanghai";

function byId(id) {
  return document.getElementById(id);
}

function showToast(message, type = "info") {
  const stack = byId("toastStack");
  if (!stack) return;

  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  stack.appendChild(node);

  setTimeout(() => {
    node.remove();
  }, 2800);
}

function setChip(id, text, type = "info") {
  const el = byId(id);
  if (!el) return;
  el.textContent = text;
  el.className = `chip ${type}`;
}

function updateApiHealthChip(ok, text = "") {
  if (ok) {
    setChip("apiHealthChip", "API 连通", "ok");
  } else {
    setChip("apiHealthChip", text ? `API 异常: ${text}` : "API 异常", "error");
  }
}

function notify(message, type = "info", toast = true) {
  const el = byId("globalNotice");
  if (!el) return;
  el.textContent = message;
  el.dataset.type = type;
  if (toast) {
    showToast(message, type);
  }
}

async function request(path, options = {}) {
  const url = `${state.apiBase}${path}`;
  const headers = { ...(options.headers || {}) };
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (options.body && !isFormData && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  let resp;
  try {
    resp = await fetch(url, { ...options, headers });
  } catch (err) {
    updateApiHealthChip(false, "网络不可达");
    throw err;
  }
  const raw = await resp.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch (_) {
    data = { message: raw };
  }

  if (!resp.ok) {
    const err = data?.detail || data?.message || `HTTP ${resp.status}`;
    updateApiHealthChip(false, String(err));
    throw new Error(String(err));
  }
  updateApiHealthChip(true);
  return data;
}

function applyExternalApiBase(apiBase, silent = true) {
  const next = String(apiBase || "").trim();
  if (!next || next === state.apiBase) return;
  state.apiBase = next;
  localStorage.setItem("controlApiBase", state.apiBase);
  const input = byId("apiBase");
  if (input) input.value = state.apiBase;
  if (!silent) {
    notify(`已切换 API 地址: ${state.apiBase}`, "info", false);
  }
}

function setupDesktopBridge() {
  // Electron 桌面壳通过 postMessage 下发 API 地址，保证嵌入页开箱可用。
  window.addEventListener("message", (event) => {
    const payload = event && typeof event.data === "object" ? event.data : null;
    if (!payload || payload.type !== "MEH_DESKTOP_CONFIG") return;
    const apiBase = String(payload.apiBase || "").trim();
    if (!apiBase) return;
    applyExternalApiBase(apiBase, true);
  });
}

function safeText(value) {
  return String(value ?? "").replace(/[<>&]/g, (m) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[m]));
}

function toLowerText(value) {
  return String(value ?? "").toLowerCase();
}

function statusTag(status) {
  const text = String(status ?? "-");
  let cls = "info";
  if (["ok", "ready", "succeeded", "running", "enabled"].includes(text)) cls = "ok";
  if (["error", "failed", "cancelled", "disabled"].includes(text)) cls = "bad";
  if (["warn", "queued", "pending_live", "queued_to_runtime"].includes(text)) cls = "warn";
  return `<span class="tag ${cls}">${safeText(text)}</span>`;
}

function boolTag(value) {
  const enabled = Number(value) === 1 || value === true;
  return `<span class="tag ${enabled ? "ok" : "bad"}">${enabled ? "是" : "否"}</span>`;
}

function renderEmptyRow(colspan, text = "暂无数据") {
  return `<tr><td class="empty-cell" colspan="${colspan}">${safeText(text)}</td></tr>`;
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const tmp = document.createElement("textarea");
  tmp.value = text;
  tmp.style.position = "fixed";
  tmp.style.left = "-9999px";
  document.body.appendChild(tmp);
  tmp.focus();
  tmp.select();
  document.execCommand("copy");
  document.body.removeChild(tmp);
}

async function uploadLocalFile(endpoint, file, fileTypeLabel = "文件", options = {}) {
  if (!file) {
    throw new Error(`请选择要上传的${fileTypeLabel}`);
  }
  const encodedName = encodeURIComponent(file.name || `upload_${Date.now()}`);
  const onProgress = typeof options.onProgress === "function" ? options.onProgress : null;

  notify(`正在上传${fileTypeLabel}: ${file.name} ...`, "info", false);

  return await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${state.apiBase}${endpoint}`, true);
    xhr.setRequestHeader("X-Filename", encodedName);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");

    xhr.upload.onprogress = (evt) => {
      if (!evt.lengthComputable || !onProgress) return;
      const percent = Math.round((evt.loaded / evt.total) * 100);
      onProgress(percent, `${percent}%`);
    };

    xhr.onerror = () => {
      updateApiHealthChip(false, "网络不可达");
      reject(new Error("网络请求失败"));
    };

    xhr.onload = () => {
      let data = {};
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : {};
      } catch (_) {
        data = { message: xhr.responseText };
      }

      if (xhr.status < 200 || xhr.status >= 300) {
        const msg = String(data?.detail || data?.message || `HTTP ${xhr.status}`);
        updateApiHealthChip(false, msg);
        if (msg.toLowerCase().includes("not found")) {
          reject(new Error("上传接口不存在，请重启 control_api 到最新版本后重试"));
          return;
        }
        reject(new Error(msg));
        return;
      }

      updateApiHealthChip(true);
      if (!data?.path) {
        reject(new Error("上传成功但未返回可用路径"));
        return;
      }
      if (onProgress) onProgress(100, "100%");
      resolve(data);
    };

    xhr.send(file);
  });
}

function parseOptionalNumber(raw, asInt = false) {
  const text = String(raw ?? "").trim();
  if (!text) return null;
  const num = Number(text);
  if (!Number.isFinite(num)) return null;
  return asInt ? Math.floor(num) : num;
}

function extractPushUrl(extraArgs) {
  if (!Array.isArray(extraArgs)) return "";
  for (let i = 0; i < extraArgs.length; i += 1) {
    if (String(extraArgs[i]) === "--push_url" && i + 1 < extraArgs.length) {
      return String(extraArgs[i + 1] || "");
    }
  }
  return "";
}

function mergeExtraArgsPushUrl(extraArgs, pushUrl) {
  const src = Array.isArray(extraArgs) ? extraArgs : [];
  const out = [];
  for (let i = 0; i < src.length; i += 1) {
    const token = String(src[i]);
    if (token === "--push_url") {
      i += 1;
      continue;
    }
    out.push(token);
  }
  if (pushUrl) out.push("--push_url", pushUrl);
  return out;
}

function setSelectOptions(selectEl, rows, labelFn, valueFn, emptyLabel = null) {
  if (!selectEl) return;
  const current = selectEl.value;
  selectEl.innerHTML = "";

  if (emptyLabel !== null) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = emptyLabel;
    selectEl.appendChild(opt);
  }

  rows.forEach((row) => {
    const opt = document.createElement("option");
    opt.value = String(valueFn(row));
    opt.textContent = labelFn(row);
    selectEl.appendChild(opt);
  });

  if (current && Array.from(selectEl.options).some((o) => o.value === current)) {
    selectEl.value = current;
  }
}

function optionsFromValues(values, includeEmpty = true) {
  const rows = [];
  if (includeEmpty) {
    rows.push({ value: "", label: "留空" });
  }
  values.forEach((v) => rows.push({ value: String(v), label: String(v) }));
  return rows;
}

function uniqueNonEmpty(values) {
  return Array.from(new Set(values.map((v) => String(v ?? "").trim()).filter(Boolean)));
}

function getRefFileOptionsByTts(ttsEngine) {
  const engine = String(ttsEngine || "").toLowerCase().trim();
  if (!engine) return optionsFromValues([], true);
  if (engine === "xtts") {
    const voiceIds = uniqueNonEmpty(state.voices.map((v) => v.id));
    return optionsFromValues(voiceIds, true);
  }
  return optionsFromValues(STATIC_REF_FILE_OPTIONS[engine] || [], true);
}

function getRefTextOptionsByTts(ttsEngine) {
  const engine = String(ttsEngine || "").toLowerCase().trim();
  return optionsFromValues(STATIC_REF_TEXT_OPTIONS[engine] || STATIC_REF_TEXT_OPTIONS.default, false);
}

function refreshTtsOptions() {
  const rows = TTS_ENGINE_OPTIONS.map((x) => ({ ...x }));
  ["liveTts", "presetTts", "presetEditTts"].forEach((id) => {
    setSelectOptions(byId(id), rows, (r) => r.label, (r) => r.value, null);
  });
}

function refreshRefOptions(scope) {
  const map = {
    live: { tts: "liveTts", refFile: "liveRefFile", refText: "liveRefText" },
    preset: { tts: "presetTts", refFile: "presetRefFile", refText: "presetRefText" },
    presetEdit: { tts: "presetEditTts", refFile: "presetEditRefFile", refText: "presetEditRefText" },
  };
  const cfg = map[scope];
  if (!cfg) return;

  const ttsSel = byId(cfg.tts);
  const refFileSel = byId(cfg.refFile);
  const refTextSel = byId(cfg.refText);
  if (!ttsSel || !refFileSel || !refTextSel) return;

  const fileRows = getRefFileOptionsByTts(ttsSel.value);
  const textRows = getRefTextOptionsByTts(ttsSel.value);
  setSelectOptions(refFileSel, fileRows, (r) => r.label, (r) => r.value, null);
  setSelectOptions(refTextSel, textRows, (r) => r.label, (r) => r.value, null);
}

function refreshAvatarSelects() {
  const avatarRows = state.avatars;
  ["liveAvatarId", "presetAvatarId", "presetEditAvatarId"].forEach((id) => {
    const emptyLabel = id === "liveAvatarId" ? "请选择 Avatar" : "请选择 Avatar";
    setSelectOptions(byId(id), avatarRows, (r) => `${r.name} (${r.id})`, (r) => r.id, emptyLabel);
  });
}

function refreshVoiceSelects() {
  const voiceRows = state.voices;
  ["presetVoiceId", "presetEditVoiceId", "voicePreviewVoiceId"].forEach((id) => {
    const emptyLabel = id === "voicePreviewVoiceId" ? "请选择 Voice" : "不使用 Voice";
    setSelectOptions(byId(id), voiceRows, (r) => `${r.name} (${r.id})`, (r) => r.id, emptyLabel);
  });
}

function refreshPlaylistFormOptions() {
  setSelectOptions(byId("playlistItemPlaylistId"), state.playlists, (r) => `${r.name} (${r.id})`, (r) => r.id, "请选择计划");
  setSelectOptions(byId("playlistItemScriptId"), state.scripts, (r) => `${r.title} (${r.id})`, (r) => r.id, "请选择脚本");
}

function updateDashboard() {
  byId("dashAvatarCount").textContent = String(state.avatars.length);
  byId("dashVoiceCount").textContent = String(state.voices.length);
  byId("dashScriptCount").textContent = String(state.scripts.length);
  byId("dashPlaylistCount").textContent = String(state.playlists.length);

  const runningJobs = state.jobs.filter((j) => j.status === "queued" || j.status === "running").length;
  byId("dashRunningJobs").textContent = String(runningJobs);

  if (state.currentSessionId) {
    byId("dashLiveText").textContent = `运行中 (${state.currentSessionId})`;
  } else {
    byId("dashLiveText").textContent = "未运行";
  }

  const dashBody = byId("dashJobTableBody");
  const dashRows = state.jobs.slice(0, 8);
  dashBody.innerHTML = dashRows.length ? dashRows.map((row) => `
    <tr>
      <td>${safeText(row.id)}</td>
      <td>${safeText(row.job_type)}</td>
      <td>${statusTag(row.status)}</td>
      <td>${safeText((row.retry_count || 0) + "/" + (row.max_retries || 0))}</td>
      <td>${safeText(formatDateTimeBjt(row.updated_at))}</td>
    </tr>
  `).join("") : renderEmptyRow(5, "暂无任务记录");
}

function renderAvatarTable() {
  const keyword = toLowerText(state.filters.avatarSearch);
  const status = state.filters.avatarStatus;
  const rows = state.avatars.filter((row) => {
    const matchedKeyword = !keyword
      || toLowerText(row.name).includes(keyword)
      || toLowerText(row.id).includes(keyword)
      || toLowerText(row.avatar_path).includes(keyword);
    const matchedStatus = !status || String(row.status || "") === status;
    return matchedKeyword && matchedStatus;
  });

  byId("avatarCountLabel").textContent = `${rows.length} / ${state.avatars.length} 条`;
  const body = byId("avatarTableBody");
  body.innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td>${safeText(row.name)}</td>
      <td>${safeText(row.id)}</td>
      <td>${safeText(row.avatar_path)}</td>
      <td>${statusTag(row.status || "-")}</td>
      <td>
        <div class="actions">
          <button class="btn ghost sm" data-action="copy-id" data-type="avatar" data-id="${safeText(row.id)}">复制ID</button>
          <button class="btn ghost sm" data-action="edit" data-type="avatar" data-id="${safeText(row.id)}">编辑</button>
          <button class="btn danger sm" data-action="delete" data-type="avatar" data-id="${safeText(row.id)}">删除</button>
        </div>
      </td>
    </tr>
  `).join("") : renderEmptyRow(5, "暂无匹配的形象记录");
}

function renderVoiceTable() {
  const keyword = toLowerText(state.filters.voiceSearch);
  const status = state.filters.voiceStatus;
  const engine = state.filters.voiceEngine;
  const rows = state.voices.filter((row) => {
    const matchedKeyword = !keyword
      || toLowerText(row.name).includes(keyword)
      || toLowerText(row.id).includes(keyword)
      || toLowerText(row.ref_wav_path).includes(keyword);
    const matchedStatus = !status || String(row.status || "") === status;
    const matchedEngine = !engine || String(row.engine || "") === engine;
    return matchedKeyword && matchedStatus && matchedEngine;
  });

  byId("voiceCountLabel").textContent = `${rows.length} / ${state.voices.length} 条`;
  const body = byId("voiceTableBody");
  body.innerHTML = rows.length ? rows.map((row) => {
    const preview = row.preview_wav_path || row.profile?.preview_wav_path || "-";
    return `
      <tr>
        <td>${safeText(row.name)}</td>
        <td>${safeText(row.id)}</td>
        <td>${safeText(row.engine)}</td>
        <td>${statusTag(row.status || "-")}</td>
        <td>${safeText(preview)}</td>
        <td>
          <div class="actions">
            <button class="btn ghost sm" data-action="copy-id" data-type="voice" data-id="${safeText(row.id)}">复制ID</button>
            <button class="btn ghost sm" data-action="preview" data-type="voice" data-id="${safeText(row.id)}">试听</button>
            <button class="btn ghost sm" data-action="edit" data-type="voice" data-id="${safeText(row.id)}">编辑</button>
            <button class="btn danger sm" data-action="delete" data-type="voice" data-id="${safeText(row.id)}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join("") : renderEmptyRow(6, "暂无匹配的声音记录");
}

function renderScriptTable() {
  const keyword = toLowerText(state.filters.scriptSearch);
  const enabled = state.filters.scriptEnabled;
  const rows = state.scripts.filter((row) => {
    const matchedKeyword = !keyword
      || toLowerText(row.title).includes(keyword)
      || toLowerText(row.id).includes(keyword)
      || toLowerText(row.category).includes(keyword)
      || toLowerText(row.content).includes(keyword);
    const rowEnabled = Number(row.enabled) === 1 || row.enabled === true;
    const matchedEnabled = !enabled || String(rowEnabled) === enabled;
    return matchedKeyword && matchedEnabled;
  });

  byId("scriptCountLabel").textContent = `${rows.length} / ${state.scripts.length} 条`;
  const body = byId("scriptTableBody");
  body.innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td>${safeText(row.title)}</td>
      <td>${safeText(row.id)}</td>
      <td>${safeText(row.category || "-")}</td>
      <td>${safeText(row.priority ?? 0)}</td>
      <td>${boolTag(row.enabled)}</td>
      <td>
        <div class="actions">
          <button class="btn ghost sm" data-action="copy-id" data-type="script" data-id="${safeText(row.id)}">复制ID</button>
          <button class="btn ghost sm" data-action="edit" data-type="script" data-id="${safeText(row.id)}">编辑</button>
          <button class="btn danger sm" data-action="delete" data-type="script" data-id="${safeText(row.id)}">删除</button>
        </div>
      </td>
    </tr>
  `).join("") : renderEmptyRow(6, "暂无匹配的脚本记录");
}

function renderPlaylistTable() {
  const keyword = toLowerText(state.filters.playlistSearch);
  const enabled = state.filters.playlistEnabled;
  const rows = state.playlists.filter((row) => {
    const rowEnabled = Number(row.enabled) === 1 || row.enabled === true;
    const matchedKeyword = !keyword
      || toLowerText(row.name).includes(keyword)
      || toLowerText(row.id).includes(keyword)
      || toLowerText(row.mode).includes(keyword);
    const matchedEnabled = !enabled || String(rowEnabled) === enabled;
    return matchedKeyword && matchedEnabled;
  });

  byId("playlistCountLabel").textContent = `${rows.length} / ${state.playlists.length} 条`;
  const body = byId("playlistTableBody");
  body.innerHTML = rows.length ? rows.map((row) => {
    const items = Array.isArray(row.items) ? row.items : [];
    const preview = items.slice(0, 3).map((it) => it.script_title || it.script_id).join(" / ") || "-";
    return `
      <tr>
        <td>${safeText(row.name)}</td>
        <td>${safeText(row.id)}</td>
        <td>${safeText(row.mode)}</td>
        <td>${safeText(row.interval_sec)}s</td>
        <td>${boolTag(row.enabled)}</td>
        <td>${items.length}</td>
        <td>${safeText(preview)}</td>
        <td>
          <div class="actions">
            <button class="btn ghost sm" data-action="copy-id" data-type="playlist" data-id="${safeText(row.id)}">复制ID</button>
            <button class="btn ghost sm" data-action="edit" data-type="playlist" data-id="${safeText(row.id)}">编辑</button>
            <button class="btn danger sm" data-action="delete" data-type="playlist" data-id="${safeText(row.id)}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join("") : renderEmptyRow(8, "暂无匹配的轮播计划");
}

function renderPresetTable() {
  const keyword = toLowerText(state.filters.presetSearch);
  const transport = state.filters.presetTransport;
  const rows = state.presets.filter((row) => {
    const matchedKeyword = !keyword
      || toLowerText(row.name).includes(keyword)
      || toLowerText(row.id).includes(keyword)
      || toLowerText(row.avatar_id).includes(keyword)
      || toLowerText(row.voice_id).includes(keyword);
    const matchedTransport = !transport || String(row.transport || "") === transport;
    return matchedKeyword && matchedTransport;
  });

  byId("presetCountLabel").textContent = `${rows.length} / ${state.presets.length} 条`;
  const body = byId("presetTableBody");
  body.innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td>${safeText(row.name)}</td>
      <td>${safeText(row.id)}</td>
      <td>${safeText(row.avatar_id)}</td>
      <td>${safeText(row.voice_id || "-")}</td>
      <td>${safeText(row.model)}/${safeText(row.transport)}:${safeText(row.listen_port)}</td>
      <td>${safeText(row.tts || "-")}</td>
      <td>
        <div class="actions">
          <button class="btn ghost sm" data-action="copy-id" data-type="preset" data-id="${safeText(row.id)}">复制ID</button>
          <button class="btn sm" data-action="start-preset" data-type="preset" data-id="${safeText(row.id)}">开播</button>
          <button class="btn ghost sm" data-action="edit" data-type="preset" data-id="${safeText(row.id)}">编辑</button>
          <button class="btn danger sm" data-action="delete" data-type="preset" data-id="${safeText(row.id)}">删除</button>
        </div>
      </td>
    </tr>
  `).join("") : renderEmptyRow(7, "暂无匹配的直播预设");
}

function renderJobTable() {
  const keyword = toLowerText(state.filters.jobSearch);
  const status = state.filters.jobStatus;
  const rows = state.jobs.filter((row) => {
    const matchedKeyword = !keyword || toLowerText(row.id).includes(keyword) || toLowerText(row.job_type).includes(keyword);
    const matchedStatus = !status || String(row.status || "") === status;
    return matchedKeyword && matchedStatus;
  });

  byId("jobCountLabel").textContent = `${rows.length} / ${state.jobs.length} 条`;
  const body = byId("jobTableBody");
  body.innerHTML = rows.length ? rows.map((row) => {
    const cancelable = row.status === "queued" || row.status === "running";
    const retryable = ["failed", "cancelled", "succeeded"].includes(String(row.status || ""));
    const shortError = String(row.error || "").trim();
    return `
      <tr>
        <td>${safeText(row.id)}</td>
        <td>${safeText(row.job_type)}</td>
        <td>${statusTag(row.status)}</td>
        <td>${safeText(row.progress ?? 0)}%</td>
        <td>${safeText((row.retry_count || 0) + "/" + (row.max_retries || 0))}</td>
        <td>${safeText(shortError ? shortError.slice(0, 90) : "-")}</td>
        <td>${safeText(formatDateTimeBjt(row.updated_at))}</td>
        <td>
          <div class="actions">
            <button class="btn ghost sm" data-action="job-detail" data-type="job" data-id="${safeText(row.id)}">详情</button>
            <button class="btn sm" data-action="retry-job" data-type="job" data-id="${safeText(row.id)}" ${retryable ? "" : "disabled"}>重试</button>
            <button class="btn ghost sm" data-action="copy-id" data-type="job" data-id="${safeText(row.id)}">复制ID</button>
            <button class="btn danger sm" data-action="cancel-job" data-type="job" data-id="${safeText(row.id)}" ${cancelable ? "" : "disabled"}>取消</button>
          </div>
        </td>
      </tr>
    `;
  }).join("") : renderEmptyRow(8, "暂无匹配的任务记录");
}

function renderHealthChecks() {
  const rows = Array.isArray(state.healthChecks) ? state.healthChecks : [];
  const body = byId("healthCheckTableBody");
  if (!body) return;

  byId("healthSummaryOk").textContent = `OK ${state.healthSummary.ok || 0}`;
  byId("healthSummaryWarn").textContent = `WARN ${state.healthSummary.warn || 0}`;
  byId("healthSummaryError").textContent = `ERROR ${state.healthSummary.error || 0}`;
  byId("healthSummaryTime").textContent = state.healthSummary.time
    ? `检测时间: ${formatDateTimeBjt(state.healthSummary.time)}`
    : "未检测";

  body.innerHTML = rows.length
    ? rows.map((row) => `
      <tr>
        <td>${safeText(row.label || row.key || "-")}</td>
        <td>${statusTag(row.status || "info")}</td>
        <td>${safeText(row.detail || "-")}</td>
        <td>${safeText(row.suggestion || "-")}</td>
      </tr>
    `).join("")
    : renderEmptyRow(4, "暂无体检结果");
}

function updateLiveWizardSummary() {
  const el = byId("liveWizardSummary");
  if (!el) return;
  const data = {
    avatar_id: byId("liveAvatarId")?.value || "",
    model: byId("liveModel")?.value || "",
    transport: byId("liveTransport")?.value || "",
    listen_port: byId("livePort")?.value || "",
    tts: byId("liveTts")?.value || "",
    ref_file: byId("liveRefFile")?.value || "",
    ref_text: byId("liveRefText")?.value || "",
    tts_server: byId("liveTtsServer")?.value || "",
    push_url: byId("livePushUrl")?.value || "",
  };
  el.textContent = JSON.stringify(data, null, 2);
}

function renderLiveWizard() {
  const step = Math.max(1, Math.min(3, Number(state.liveWizardStep || 1)));
  state.liveWizardStep = step;

  document.querySelectorAll("[data-step-panel]").forEach((el) => {
    el.classList.toggle("active", String(el.getAttribute("data-step-panel")) === String(step));
  });
  document.querySelectorAll("[data-step-btn]").forEach((el) => {
    el.classList.toggle("active", String(el.getAttribute("data-step-btn")) === String(step));
  });

  byId("btnLiveWizardPrev").disabled = step <= 1;
  byId("btnLiveWizardNext").disabled = step >= 3;
  byId("btnLiveStartSubmit").style.display = step === 3 ? "inline-flex" : "none";
  updateLiveWizardSummary();
}

function setUploadProgress(prefix, percent, text = "") {
  const wrap = byId(`${prefix}UploadProgressWrap`);
  const bar = byId(`${prefix}UploadProgressBar`);
  const label = byId(`${prefix}UploadProgressText`);
  if (!wrap || !bar || !label) return;

  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  wrap.classList.remove("hidden");
  bar.style.width = `${value}%`;
  label.textContent = text || `${value}%`;
}

function resetUploadProgress(prefix) {
  const wrap = byId(`${prefix}UploadProgressWrap`);
  const bar = byId(`${prefix}UploadProgressBar`);
  const label = byId(`${prefix}UploadProgressText`);
  if (!wrap || !bar || !label) return;
  wrap.classList.add("hidden");
  bar.style.width = "0%";
  label.textContent = "0%";
}

function validateFileByRules(file, rules) {
  if (!file) return { ok: true, message: "" };
  const suffix = `.${String(file.name || "").split(".").pop()?.toLowerCase() || ""}`;
  if (Array.isArray(rules.exts) && rules.exts.length && !rules.exts.includes(suffix)) {
    return { ok: false, message: `文件类型不支持，仅支持: ${rules.exts.join(", ")}` };
  }
  const maxBytes = Number(rules.maxBytes || 0);
  if (maxBytes > 0 && Number(file.size || 0) > maxBytes) {
    return { ok: false, message: `文件过大，最大 ${(maxBytes / 1024 / 1024).toFixed(0)}MB` };
  }
  return { ok: true, message: "" };
}

async function loadAvatars() {
  state.avatars = await request("/api/v1/avatars");
  renderAvatarTable();
  refreshAvatarSelects();
  updateDashboard();
}

async function loadVoices() {
  state.voices = await request("/api/v1/voices");
  renderVoiceTable();
  refreshVoiceSelects();
  refreshRefOptions("live");
  refreshRefOptions("preset");
  refreshRefOptions("presetEdit");
  updateDashboard();
}

async function loadScripts() {
  state.scripts = await request("/api/v1/scripts");
  renderScriptTable();
  refreshPlaylistFormOptions();
  updateDashboard();
}

async function loadPlaylists() {
  state.playlists = await request("/api/v1/playlists");
  renderPlaylistTable();
  refreshPlaylistFormOptions();
  updateDashboard();
}

async function loadPresets() {
  state.presets = await request("/api/v1/live/presets");
  renderPresetTable();
}

async function loadJobs() {
  state.jobs = await request("/api/v1/jobs");
  renderJobTable();
  updateDashboard();
}

async function loadSystemChecks(options = {}) {
  const ttsServer = String(options.ttsServer || byId("healthTtsServer")?.value || "http://127.0.0.1:9000").trim();
  const listenPort = Number(byId("livePort")?.value || 8010);
  const query = new URLSearchParams({
    tts_server: ttsServer || "http://127.0.0.1:9000",
    listen_port: String(listenPort),
  });
  const data = await request(`/api/v1/system/checks?${query.toString()}`);
  state.healthChecks = Array.isArray(data.checks) ? data.checks : [];
  state.healthSummary = {
    ok: Number(data.summary?.ok || 0),
    warn: Number(data.summary?.warn || 0),
    error: Number(data.summary?.error || 0),
    time: String(data.time || ""),
  };
  renderHealthChecks();
}

function closeJobDrawer() {
  byId("jobDrawer")?.classList.remove("open");
  byId("jobDrawerMask")?.classList.remove("open");
  if (byId("btnRetryJobFromDrawer")) byId("btnRetryJobFromDrawer").disabled = false;
  state.selectedJobId = "";
}

async function openJobDrawer(jobId) {
  const data = await request(`/api/v1/jobs/${jobId}`);
  state.selectedJobId = jobId;
  const retryable = ["failed", "cancelled", "succeeded"].includes(String(data.status || ""));
  byId("jobDrawerTitle").textContent = `任务详情: ${jobId}`;
  byId("jobDrawerMeta").textContent = [
    `类型: ${data.job_type || "-"}`,
    `状态: ${data.status || "-"}`,
    `进度: ${data.progress ?? 0}%`,
    `重试: ${(data.retry_count || 0)}/${(data.max_retries || 0)}`,
    `错误: ${data.error || "-"}`,
    `更新时间: ${formatDateTimeBjt(data.updated_at)}`,
  ].join(" | ");
  byId("jobDrawerPayload").textContent = JSON.stringify(data.payload || {}, null, 2);
  byId("jobDrawerResult").textContent = JSON.stringify(data.result || {}, null, 2);
  const lines = Array.isArray(data.logs)
    ? data.logs.map((item) => `[${formatDateTimeBjt(item.created_at)}] ${item.level}: ${item.message}`).join("\n")
    : "";
  byId("jobDrawerLogs").textContent = lines || "暂无日志";
  byId("btnRetryJobFromDrawer").disabled = !retryable;
  byId("jobDrawer").classList.add("open");
  byId("jobDrawerMask").classList.add("open");
}

async function retryJobById(jobId) {
  const result = await request(`/api/v1/jobs/${jobId}:retry`, { method: "POST" });
  await loadJobs();
  notify(`已创建重试任务: ${result.message}`, "ok");
  return result.message;
}

function normalizeLogPollInterval(raw) {
  const value = Number(raw);
  const picked = LOG_POLL_INTERVAL_OPTIONS.find((x) => x === value);
  return picked || 1500;
}

function formatClock(date) {
  return date.toLocaleTimeString("zh-CN", { hour12: false, timeZone: BJT_TIMEZONE });
}

function formatDateTimeBjt(raw) {
  if (!raw) return "-";
  const date = raw instanceof Date ? raw : new Date(String(raw));
  if (Number.isNaN(date.getTime())) return String(raw);
  return date.toLocaleString("zh-CN", {
    hour12: false,
    timeZone: BJT_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function renderLogPanel() {
  const logBox = byId("logBox");
  const meta = byId("logMetaText");
  if (!logBox) return;

  const keyword = state.logKeyword.trim().toLowerCase();
  const total = state.logLines.length;
  const filtered = keyword ? state.logLines.filter((line) => toLowerText(line).includes(keyword)) : state.logLines;

  let content = "";
  if (total <= 0) {
    content = state.currentSessionId ? "会话已启动，正在等待日志输出..." : "当前没有运行中的会话。";
  } else if (filtered.length <= 0) {
    content = `没有匹配关键词 "${state.logKeyword}" 的日志。`;
  } else {
    content = filtered.join("\n");
  }

  logBox.textContent = content;
  if (state.logAutoScrollEnabled) {
    logBox.scrollTop = logBox.scrollHeight;
  }

  if (meta) {
    const status = state.currentSessionId ? "运行中" : "未运行";
    const ts = state.logLastUpdatedAt || "--";
    const count = keyword ? `显示 ${filtered.length}/${total} 行` : `共 ${total} 行`;
    meta.textContent = `${status} | 最近更新 ${ts} | ${count}`;
  }
}

function clearLogPollingTimer() {
  if (state.logPollTimer) {
    clearInterval(state.logPollTimer);
    state.logPollTimer = null;
  }
}

function shouldEnableLogPolling() {
  return state.currentPage === "live" && state.logFollowEnabled;
}

async function pollLiveLogsOnce() {
  if (state.logPollBusy) return;
  state.logPollBusy = true;
  try {
    await refreshLiveStatus();
    await loadCurrentLogs({ limit: 400 });
    state.logLastError = "";
  } catch (err) {
    const msg = String(err?.message || err);
    if (state.logLastError !== msg) {
      state.logLastError = msg;
      notify(`日志自动追踪失败: ${msg}`, "warn", false);
    }
  } finally {
    state.logPollBusy = false;
  }
}

function updateLogPolling(triggerNow = true) {
  clearLogPollingTimer();
  if (!shouldEnableLogPolling()) {
    return;
  }
  state.logPollTimer = setInterval(() => {
    pollLiveLogsOnce().catch(() => {});
  }, state.logPollIntervalMs);

  if (triggerNow) {
    pollLiveLogsOnce().catch(() => {});
  }
}

async function refreshLiveStatus() {
  const current = await request("/api/v1/live/sessions/current");
  if (current.running) {
    state.currentSessionId = current.session_id;
    byId("liveStatus").textContent = `运行中 | session=${current.session_id} | pid=${current.pid}`;
    setChip("liveHealthChip", "直播运行中", "ok");
  } else {
    state.currentSessionId = null;
    byId("liveStatus").textContent = "未运行";
    setChip("liveHealthChip", "直播未运行", "warn");
  }
  updateDashboard();
}

async function loadCurrentLogs(options = {}) {
  const limit = Number(options.limit || 300);
  if (!state.currentSessionId) {
    renderLogPanel();
    return;
  }
  const data = await request(`/api/v1/live/sessions/${state.currentSessionId}/logs?limit=${limit}`);
  state.logLines = Array.isArray(data.lines) ? data.lines.map((line) => String(line ?? "")) : [];
  state.logLastUpdatedAt = formatClock(new Date());
  renderLogPanel();
}

async function loadSpeakerStatus() {
  const data = await request("/api/v1/live/speaker/status");
  state.speakerStatus = data;
  byId("speakerStatusBox").textContent = JSON.stringify(data, null, 2);
}

function findById(rows, id) {
  return rows.find((row) => String(row.id) === String(id)) || null;
}

function showPage(page, updateHash = true) {
  const targetPage = PAGE_TITLES[page] ? page : "dashboard";
  state.currentPage = targetPage;
  localStorage.setItem("webAdminCurrentPage", targetPage);
  if (updateHash) {
    window.location.hash = `#${targetPage}`;
  }

  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("active", el.getAttribute("data-view") === targetPage);
  });

  document.querySelectorAll(".nav-link").forEach((el) => {
    el.classList.toggle("active", el.getAttribute("data-page") === targetPage);
  });

  byId("currentPageTitle").textContent = PAGE_TITLES[targetPage];
  if (targetPage === "live") {
    renderLogPanel();
  }
  if (targetPage !== "ops") {
    closeJobDrawer();
  }
  updateLogPolling(true);
}

function initNavigation() {
  byId("sideNav").addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    if (!target.classList.contains("nav-link")) return;
    const page = target.getAttribute("data-page") || "dashboard";
    showPage(page);
    refreshCurrentPageData().catch((err) => notify(err.message, "error", false));
  });

  window.addEventListener("hashchange", () => {
    const hashPage = window.location.hash.replace("#", "").trim();
    if (PAGE_TITLES[hashPage]) {
      showPage(hashPage, false);
      refreshCurrentPageData().catch((err) => notify(err.message, "error", false));
    }
  });

  const hashPage = window.location.hash.replace("#", "").trim();
  const savedPage = localStorage.getItem("webAdminCurrentPage") || "dashboard";
  showPage(PAGE_TITLES[hashPage] ? hashPage : savedPage, false);
}

function openAvatarEditor(id) {
  const row = findById(state.avatars, id);
  if (!row) return;
  state.editing.avatarId = id;
  byId("avatarEditId").value = row.id;
  byId("avatarEditName").value = row.name || "";
  byId("avatarEditStatus").value = row.status || "ready";
  byId("avatarEditCover").value = row.cover_image || "";
  showPage("avatars");
}

function openVoiceEditor(id) {
  const row = findById(state.voices, id);
  if (!row) return;
  state.editing.voiceId = id;
  byId("voiceEditId").value = row.id;
  byId("voiceEditName").value = row.name || "";
  byId("voiceEditEngine").value = row.engine || "xtts";
  byId("voiceEditStatus").value = row.status || "ready";
  byId("voiceEditRef").value = row.ref_wav_path || "";
  showPage("voices");
}

function openScriptEditor(id) {
  const row = findById(state.scripts, id);
  if (!row) return;
  state.editing.scriptId = id;
  byId("scriptEditId").value = row.id;
  byId("scriptEditTitle").value = row.title || "";
  byId("scriptEditCategory").value = row.category || "";
  byId("scriptEditPriority").value = String(row.priority ?? 0);
  byId("scriptEditEnabled").value = Number(row.enabled) === 1 || row.enabled === true ? "true" : "false";
  byId("scriptEditContent").value = row.content || "";
  showPage("scripts");
}

function openPlaylistEditor(id) {
  const row = findById(state.playlists, id);
  if (!row) return;
  state.editing.playlistId = id;
  byId("playlistEditId").value = row.id;
  byId("playlistEditName").value = row.name || "";
  byId("playlistEditMode").value = row.mode || "sequential";
  byId("playlistEditInterval").value = String(row.interval_sec || 30);
  byId("playlistEditEnabled").value = Number(row.enabled) === 1 || row.enabled === true ? "true" : "false";
  showPage("playlists");
}

function openPresetEditor(id) {
  const row = findById(state.presets, id);
  if (!row) return;
  state.editing.presetId = id;

  byId("presetEditId").value = row.id;
  byId("presetEditName").value = row.name || "";
  byId("presetEditAvatarId").value = row.avatar_id || "";
  byId("presetEditVoiceId").value = row.voice_id || "";
  byId("presetEditModel").value = row.model || "wav2lip";
  byId("presetEditTransport").value = row.transport || "virtualcam";
  byId("presetEditListenPort").value = String(row.listen_port || 8010);
  byId("presetEditTts").value = row.tts || "";
  refreshRefOptions("presetEdit");
  byId("presetEditRefFile").value = row.ref_file || "";
  byId("presetEditRefText").value = row.ref_text || "";
  byId("presetEditTtsServer").value = row.tts_server || "";
  byId("presetEditPushUrl").value = extractPushUrl(row.extra_args);
  showPage("presets");
}

function triggerVoicePreview(id) {
  byId("voicePreviewVoiceId").value = id;
  showPage("voices");
}

async function startByPreset(presetId) {
  const result = await request("/api/v1/live/sessions:start", {
    method: "POST",
    body: JSON.stringify({ preset_id: presetId }),
  });
  state.currentSessionId = result.session_id;
  await refreshLiveStatus();
  await loadCurrentLogs();
  updateLogPolling(true);
  notify("已按预设启动直播", "ok");
  showPage("live");
}

async function deleteByType(type, id) {
  if (type === "avatar") {
    await request(`/api/v1/avatars/${id}`, { method: "DELETE" });
    await loadAvatars();
  } else if (type === "voice") {
    await request(`/api/v1/voices/${id}`, { method: "DELETE" });
    await loadVoices();
  } else if (type === "script") {
    await request(`/api/v1/scripts/${id}`, { method: "DELETE" });
    await Promise.all([loadScripts(), loadPlaylists()]);
  } else if (type === "playlist") {
    await request(`/api/v1/playlists/${id}`, { method: "DELETE" });
    await loadPlaylists();
  } else if (type === "preset") {
    await request(`/api/v1/live/presets/${id}`, { method: "DELETE" });
    await loadPresets();
  } else if (type === "job") {
    await request(`/api/v1/jobs/${id}:cancel`, {
      method: "POST",
      body: JSON.stringify({ reason: "user cancel" }),
    });
    await loadJobs();
  }
}

function bindFilterInput(id, key, renderFn, eventName = "input") {
  const el = byId(id);
  if (!el) return;
  el.addEventListener(eventName, () => {
    state.filters[key] = String(el.value || "").trim();
    renderFn();
  });
}

function bindFilterInputs() {
  bindFilterInput("avatarSearch", "avatarSearch", renderAvatarTable, "input");
  bindFilterInput("avatarStatusFilter", "avatarStatus", renderAvatarTable, "change");

  bindFilterInput("voiceSearch", "voiceSearch", renderVoiceTable, "input");
  bindFilterInput("voiceStatusFilter", "voiceStatus", renderVoiceTable, "change");
  bindFilterInput("voiceEngineFilter", "voiceEngine", renderVoiceTable, "change");

  bindFilterInput("scriptSearch", "scriptSearch", renderScriptTable, "input");
  bindFilterInput("scriptEnabledFilter", "scriptEnabled", renderScriptTable, "change");

  bindFilterInput("playlistSearch", "playlistSearch", renderPlaylistTable, "input");
  bindFilterInput("playlistEnabledFilter", "playlistEnabled", renderPlaylistTable, "change");

  bindFilterInput("presetSearch", "presetSearch", renderPresetTable, "input");
  bindFilterInput("presetTransportFilter", "presetTransport", renderPresetTable, "change");

  bindFilterInput("jobSearch", "jobSearch", renderJobTable, "input");
  bindFilterInput("jobStatusFilter", "jobStatus", renderJobTable, "change");
}

async function refreshCurrentPageData() {
  if (state.currentPage === "dashboard") {
    await Promise.all([refreshLiveStatus(), loadJobs()]);
    return;
  }
  if (state.currentPage === "health") {
    await loadSystemChecks();
    return;
  }
  if (state.currentPage === "live") {
    await refreshLiveStatus();
    await loadCurrentLogs();
    return;
  }
  if (state.currentPage === "avatars") {
    await Promise.all([loadAvatars(), loadJobs()]);
    return;
  }
  if (state.currentPage === "voices") {
    await Promise.all([loadVoices(), loadJobs()]);
    return;
  }
  if (state.currentPage === "scripts") {
    await Promise.all([loadScripts(), loadPlaylists()]);
    return;
  }
  if (state.currentPage === "playlists") {
    await loadPlaylists();
    return;
  }
  if (state.currentPage === "presets") {
    await Promise.all([loadPresets(), refreshLiveStatus()]);
    return;
  }
  if (state.currentPage === "ops") {
    await Promise.all([loadJobs(), loadSpeakerStatus()]);
    return;
  }
  await loadAllData();
}

function setupLogViewerControls() {
  state.logPollIntervalMs = normalizeLogPollInterval(state.logPollIntervalMs);

  const followSwitch = byId("logFollowSwitch");
  const autoScrollSwitch = byId("logAutoScrollSwitch");
  const intervalSelect = byId("logPollInterval");
  const filterInput = byId("logFilterKeyword");
  if (!followSwitch || !autoScrollSwitch || !intervalSelect || !filterInput) {
    return;
  }

  followSwitch.checked = state.logFollowEnabled;
  autoScrollSwitch.checked = state.logAutoScrollEnabled;
  intervalSelect.value = String(state.logPollIntervalMs);
  filterInput.value = state.logKeyword;

  followSwitch.addEventListener("change", () => {
    state.logFollowEnabled = followSwitch.checked;
    localStorage.setItem("webAdminLogFollow", state.logFollowEnabled ? "1" : "0");
    updateLogPolling(true);
    notify(state.logFollowEnabled ? "已开启实时日志追踪" : "已暂停实时日志追踪", "info");
  });

  autoScrollSwitch.addEventListener("change", () => {
    state.logAutoScrollEnabled = autoScrollSwitch.checked;
    localStorage.setItem("webAdminLogAutoScroll", state.logAutoScrollEnabled ? "1" : "0");
    renderLogPanel();
  });

  intervalSelect.addEventListener("change", () => {
    state.logPollIntervalMs = normalizeLogPollInterval(intervalSelect.value);
    intervalSelect.value = String(state.logPollIntervalMs);
    localStorage.setItem("webAdminLogPollIntervalMs", String(state.logPollIntervalMs));
    updateLogPolling(false);
    notify(`日志刷新间隔已更新为 ${state.logPollIntervalMs}ms`, "info", false);
  });

  filterInput.addEventListener("input", () => {
    state.logKeyword = String(filterInput.value || "").trim();
    renderLogPanel();
  });

  byId("btnClearLogsView").addEventListener("click", () => {
    state.logLines = [];
    renderLogPanel();
    notify("日志视图已清空（不影响后台日志文件）", "info", false);
  });

  byId("btnCopyLogs").addEventListener("click", async () => {
    try {
      await copyText(byId("logBox").textContent || "");
      notify("当前日志视图已复制", "ok", false);
    } catch (err) {
      notify(`复制日志失败: ${err.message}`, "error");
    }
  });

  renderLogPanel();
}

function setupLiveWizard() {
  const prevBtn = byId("btnLiveWizardPrev");
  const nextBtn = byId("btnLiveWizardNext");
  if (!prevBtn || !nextBtn) return;

  prevBtn.addEventListener("click", () => {
    state.liveWizardStep = Math.max(1, state.liveWizardStep - 1);
    renderLiveWizard();
  });

  nextBtn.addEventListener("click", () => {
    state.liveWizardStep = Math.min(3, state.liveWizardStep + 1);
    renderLiveWizard();
  });

  document.querySelectorAll("[data-step-btn]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const step = Number(btn.getAttribute("data-step-btn") || 1);
      state.liveWizardStep = Math.max(1, Math.min(3, step));
      renderLiveWizard();
    });
  });

  [
    "liveAvatarId",
    "liveModel",
    "liveTransport",
    "livePort",
    "liveTts",
    "liveRefFile",
    "liveRefText",
    "liveTtsServer",
    "livePushUrl",
  ].forEach((id) => {
    const el = byId(id);
    if (!el) return;
    const evt = el.tagName === "SELECT" ? "change" : "input";
    el.addEventListener(evt, () => updateLiveWizardSummary());
  });

  renderLiveWizard();
}

function setupAutoRefresh() {
  const switchEl = byId("autoRefreshSwitch");
  if (!switchEl) return;

  switchEl.checked = state.autoRefreshEnabled;
  const updateTimer = () => {
    if (state.autoRefreshTimer) {
      clearInterval(state.autoRefreshTimer);
      state.autoRefreshTimer = null;
    }
    if (!state.autoRefreshEnabled) {
      return;
    }
    state.autoRefreshTimer = setInterval(() => {
      refreshCurrentPageData().catch((err) => {
        notify(`自动刷新失败: ${err.message}`, "warn", false);
      });
    }, 15000);
  };

  switchEl.addEventListener("change", () => {
    state.autoRefreshEnabled = switchEl.checked;
    localStorage.setItem("webAdminAutoRefresh", state.autoRefreshEnabled ? "1" : "0");
    updateTimer();
    notify(state.autoRefreshEnabled ? "已开启自动刷新" : "已关闭自动刷新", "info");
  });

  updateTimer();
  window.addEventListener("beforeunload", () => {
    if (state.autoRefreshTimer) {
      clearInterval(state.autoRefreshTimer);
      state.autoRefreshTimer = null;
    }
    clearLogPollingTimer();
  });
}

function bindEvents() {
  byId("apiBase").value = state.apiBase;
  refreshTtsOptions();
  refreshRefOptions("live");
  refreshRefOptions("preset");
  refreshRefOptions("presetEdit");
  bindFilterInputs();
  setupLogViewerControls();
  setupLiveWizard();
  setupAutoRefresh();

  byId("avatarVideoFile").addEventListener("change", () => {
    const file = byId("avatarVideoFile").files?.[0] || null;
    resetUploadProgress("avatar");
    if (file) {
      const check = validateFileByRules(file, {
        exts: [".mp4", ".mov", ".mkv", ".avi", ".webm"],
        maxBytes: 1024 * 1024 * 1024,
      });
      if (!check.ok) {
        byId("avatarUploadHint").textContent = `文件校验失败: ${check.message}`;
        notify(`视频校验失败: ${check.message}`, "warn", false);
        return;
      }
    }
    byId("avatarUploadHint").textContent = file
      ? `已选择: ${file.name}（${Math.round(file.size / 1024)} KB），提交时自动上传。`
      : "可直接选择本机视频，系统会自动上传并回填路径。";
  });

  byId("voiceRefWavFile").addEventListener("change", () => {
    const file = byId("voiceRefWavFile").files?.[0] || null;
    resetUploadProgress("voice");
    if (file) {
      const check = validateFileByRules(file, {
        exts: [".wav"],
        maxBytes: 100 * 1024 * 1024,
      });
      if (!check.ok) {
        byId("voiceUploadHint").textContent = `文件校验失败: ${check.message}`;
        notify(`音频校验失败: ${check.message}`, "warn", false);
        return;
      }
    }
    byId("voiceUploadHint").textContent = file
      ? `已选择: ${file.name}（${Math.round(file.size / 1024)} KB），提交时自动上传。`
      : "可直接选择本机 wav 文件，系统会自动上传并回填路径。";
  });

  byId("btnSaveApi").addEventListener("click", () => {
    state.apiBase = byId("apiBase").value.trim();
    localStorage.setItem("controlApiBase", state.apiBase);
    notify("API 地址已保存", "ok");
  });

  byId("btnReloadAll").addEventListener("click", async () => {
    try {
      await loadAllData();
      notify("全局刷新完成", "ok");
    } catch (e) {
      notify(`全局刷新失败: ${e.message}`, "error");
    }
  });

  byId("btnReloadJobsDash").addEventListener("click", () => loadJobs().catch((e) => notify(e.message, "error")));
  byId("btnRunHealthChecks").addEventListener("click", () => loadSystemChecks().catch((e) => notify(`体检失败: ${e.message}`, "error")));
  byId("btnCloseJobDrawer").addEventListener("click", () => closeJobDrawer());
  byId("jobDrawerMask").addEventListener("click", () => closeJobDrawer());
  byId("btnRetryJobFromDrawer").addEventListener("click", async () => {
    if (!state.selectedJobId) {
      notify("请先选择任务", "warn");
      return;
    }
    try {
      await retryJobById(state.selectedJobId);
      closeJobDrawer();
    } catch (err) {
      notify(`任务重试失败: ${err.message}`, "error");
    }
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeJobDrawer();
  });

  byId("btnRefreshStatus").addEventListener("click", () => refreshLiveStatus().catch((e) => notify(e.message, "error")));
  byId("btnLoadLogs").addEventListener("click", async () => {
    try {
      await refreshLiveStatus();
      await loadCurrentLogs();
      notify("日志已手动刷新", "ok", false);
    } catch (err) {
      notify(`日志加载失败: ${err.message}`, "error");
    }
  });
  byId("btnRefreshSpeaker").addEventListener("click", () => loadSpeakerStatus().catch((e) => notify(e.message, "error")));

  byId("liveTts").addEventListener("change", () => refreshRefOptions("live"));
  byId("presetTts").addEventListener("change", () => refreshRefOptions("preset"));
  byId("presetEditTts").addEventListener("change", () => refreshRefOptions("presetEdit"));

  byId("startLiveForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.liveWizardStep < 3) {
      state.liveWizardStep = 3;
      renderLiveWizard();
      notify("请在第 3 步确认配置后启动直播", "info", false);
      return;
    }
    const payload = {
      avatar_id: byId("liveAvatarId").value,
      model: byId("liveModel").value,
      transport: byId("liveTransport").value,
      listen_port: Number(byId("livePort").value),
      tts: byId("liveTts").value || null,
      ref_file: byId("liveRefFile").value || null,
      ref_text: byId("liveRefText").value || null,
      tts_server: byId("liveTtsServer").value.trim() || null,
      extra_args: mergeExtraArgsPushUrl([], byId("livePushUrl").value.trim()),
    };
    try {
      const result = await request("/api/v1/live/sessions:start", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.currentSessionId = result.session_id;
      await refreshLiveStatus();
      await loadCurrentLogs();
      updateLogPolling(true);
      notify("直播已启动", "ok");
    } catch (err) {
      notify(`启动失败: ${err.message}`, "error");
    }
  });

  byId("btnStopLive").addEventListener("click", async () => {
    if (!state.currentSessionId) {
      notify("当前没有运行中的直播", "warn");
      return;
    }
    try {
      await request(`/api/v1/live/sessions/${state.currentSessionId}:stop`, {
        method: "POST",
        body: JSON.stringify({ force: false }),
      });
      state.currentSessionId = null;
      await refreshLiveStatus();
      updateLogPolling(false);
      renderLogPanel();
      notify("直播已停止", "ok");
    } catch (err) {
      notify(`停止失败: ${err.message}`, "error");
    }
  });

  byId("avatarForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const selectedVideo = byId("avatarVideoFile").files?.[0] || null;
    let resolvedVideoPath = byId("avatarVideoPath").value.trim();

    if (selectedVideo) {
      const check = validateFileByRules(selectedVideo, {
        exts: [".mp4", ".mov", ".mkv", ".avi", ".webm"],
        maxBytes: 1024 * 1024 * 1024,
      });
      if (!check.ok) {
        notify(`视频校验失败: ${check.message}`, "warn");
        return;
      }
      setUploadProgress("avatar", 0, "0%");
      try {
        const uploaded = await uploadLocalFile("/api/v1/uploads/avatar-video", selectedVideo, "视频", {
          onProgress: (percent, text) => setUploadProgress("avatar", percent, text),
        });
        resolvedVideoPath = String(uploaded.path);
        byId("avatarVideoPath").value = resolvedVideoPath;
        byId("avatarUploadHint").textContent = `上传完成: ${uploaded.saved_name} -> ${resolvedVideoPath}`;
      } catch (err) {
        notify(`视频上传失败: ${err.message}`, "error");
        resetUploadProgress("avatar");
        return;
      }
    }

    if (!resolvedVideoPath) {
      notify("请选择视频文件，或填写服务器可访问的视频路径", "warn");
      return;
    }

    const payload = {
      name: byId("avatarName").value.trim(),
      video_path: resolvedVideoPath,
      img_size: Number(byId("avatarImgSize").value || 256),
      face_det_batch_size: 16,
      pads: [0, 10, 0, 0],
      overwrite: false,
    };
    const customId = byId("avatarCustomId").value.trim();
    if (customId) payload.avatar_id = customId;

    try {
      await request("/api/v1/avatars:clone", { method: "POST", body: JSON.stringify(payload) });
      byId("avatarForm").reset();
      byId("avatarImgSize").value = "256";
      byId("avatarUploadHint").textContent = "可直接选择本机视频，系统会自动上传并回填路径。";
      resetUploadProgress("avatar");
      await loadJobs();
      notify("Avatar 克隆任务已提交", "ok");
    } catch (err) {
      notify(`Avatar 克隆任务创建失败: ${err.message}`, "error");
    }
  });

  byId("avatarEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.editing.avatarId) {
      notify("请先在列表中选择要编辑的形象", "warn");
      return;
    }
    const payload = {
      name: byId("avatarEditName").value.trim(),
      status: byId("avatarEditStatus").value.trim() || "ready",
      cover_image: byId("avatarEditCover").value.trim() || null,
    };
    try {
      await request(`/api/v1/avatars/${state.editing.avatarId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      await loadAvatars();
      notify("形象已更新", "ok");
    } catch (err) {
      notify(`形象更新失败: ${err.message}`, "error");
    }
  });

  byId("avatarEditCancel").addEventListener("click", () => {
    state.editing.avatarId = null;
    byId("avatarEditForm").reset();
  });

  byId("voiceForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const selectedWav = byId("voiceRefWavFile").files?.[0] || null;
    let resolvedRefWavPath = byId("voiceRefWav").value.trim();

    if (selectedWav) {
      const check = validateFileByRules(selectedWav, {
        exts: [".wav"],
        maxBytes: 100 * 1024 * 1024,
      });
      if (!check.ok) {
        notify(`音频校验失败: ${check.message}`, "warn");
        return;
      }
      setUploadProgress("voice", 0, "0%");
      try {
        const uploaded = await uploadLocalFile("/api/v1/uploads/voice-wav", selectedWav, "音频", {
          onProgress: (percent, text) => setUploadProgress("voice", percent, text),
        });
        resolvedRefWavPath = String(uploaded.path);
        byId("voiceRefWav").value = resolvedRefWavPath;
        byId("voiceUploadHint").textContent = `上传完成: ${uploaded.saved_name} -> ${resolvedRefWavPath}`;
      } catch (err) {
        notify(`音频上传失败: ${err.message}`, "error");
        resetUploadProgress("voice");
        return;
      }
    }

    if (!resolvedRefWavPath) {
      notify("请选择 wav 音频，或填写服务器可访问的音频路径", "warn");
      return;
    }

    const payload = {
      name: byId("voiceName").value.trim(),
      engine: "xtts",
      ref_wav_path: resolvedRefWavPath,
      tts_server: byId("voiceTtsServer").value.trim() || "http://127.0.0.1:9000",
      generate_preview: byId("voiceGeneratePreview").value === "true",
      preview_text: byId("voicePreviewText").value.trim() || "你好，欢迎来到直播间。",
      preview_language: "zh-cn",
      preview_stream_chunk_size: 20,
      temperature: parseOptionalNumber(byId("voiceTemperature").value),
      speed: parseOptionalNumber(byId("voiceSpeed").value),
      top_k: parseOptionalNumber(byId("voiceTopK").value, true),
      top_p: parseOptionalNumber(byId("voiceTopP").value),
      repetition_penalty: parseOptionalNumber(byId("voiceRepetitionPenalty").value),
    };

    const customId = byId("voiceCustomId").value.trim();
    if (customId) payload.voice_id = customId;

    try {
      await request("/api/v1/voices:clone", { method: "POST", body: JSON.stringify(payload) });
      byId("voiceForm").reset();
      byId("voiceTtsServer").value = "http://127.0.0.1:9000";
      byId("voiceGeneratePreview").value = "true";
      byId("voicePreviewText").value = "你好，欢迎来到直播间。";
      byId("voiceUploadHint").textContent = "可直接选择本机 wav 文件，系统会自动上传并回填路径。";
      resetUploadProgress("voice");
      await loadJobs();
      notify("声音克隆任务已提交", "ok");
    } catch (err) {
      notify(`Voice 克隆任务创建失败: ${err.message}`, "error");
    }
  });

  byId("voiceEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.editing.voiceId) {
      notify("请先在列表中选择要编辑的声音", "warn");
      return;
    }
    const payload = {
      name: byId("voiceEditName").value.trim(),
      engine: byId("voiceEditEngine").value.trim() || "xtts",
      status: byId("voiceEditStatus").value.trim() || "ready",
      ref_wav_path: byId("voiceEditRef").value.trim() || null,
    };
    try {
      await request(`/api/v1/voices/${state.editing.voiceId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      await loadVoices();
      notify("声音已更新", "ok");
    } catch (err) {
      notify(`声音更新失败: ${err.message}`, "error");
    }
  });

  byId("voiceEditCancel").addEventListener("click", () => {
    state.editing.voiceId = null;
    byId("voiceEditForm").reset();
  });

  byId("voicePreviewForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const voiceId = byId("voicePreviewVoiceId").value;
    if (!voiceId) {
      notify("请先选择要试听的 Voice", "warn");
      return;
    }

    const payload = {
      text: byId("voicePreviewInputText").value.trim(),
      tts_server: byId("voicePreviewServer").value.trim() || "http://127.0.0.1:9000",
      language: "zh-cn",
      stream_chunk_size: 20,
      temperature: parseOptionalNumber(byId("voicePreviewTemperature").value),
      speed: parseOptionalNumber(byId("voicePreviewSpeed").value),
      top_k: parseOptionalNumber(byId("voicePreviewTopK").value, true),
      top_p: parseOptionalNumber(byId("voicePreviewTopP").value),
      repetition_penalty: parseOptionalNumber(byId("voicePreviewPenalty").value),
    };

    if (!payload.text) {
      notify("试听文案不能为空", "warn");
      return;
    }

    try {
      const result = await request(`/api/v1/voices/${voiceId}:preview`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      byId("voicePreviewResult").textContent = JSON.stringify(result, null, 2);
      await loadVoices();
      notify("试听生成成功", "ok");
    } catch (err) {
      byId("voicePreviewResult").textContent = `试听失败: ${err.message}`;
      notify(`试听失败: ${err.message}`, "error");
    }
  });

  byId("scriptForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      title: byId("scriptTitle").value.trim(),
      category: byId("scriptCategory").value.trim() || null,
      content: byId("scriptContent").value.trim(),
      priority: 0,
      enabled: true,
    };

    try {
      await request("/api/v1/scripts", { method: "POST", body: JSON.stringify(payload) });
      byId("scriptForm").reset();
      await Promise.all([loadScripts(), loadPlaylists()]);
      notify("脚本已新增", "ok");
    } catch (err) {
      notify(`脚本创建失败: ${err.message}`, "error");
    }
  });

  byId("scriptEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.editing.scriptId) {
      notify("请先在列表中选择要编辑的脚本", "warn");
      return;
    }

    const payload = {
      title: byId("scriptEditTitle").value.trim(),
      category: byId("scriptEditCategory").value.trim() || null,
      content: byId("scriptEditContent").value.trim(),
      priority: Number(byId("scriptEditPriority").value || 0),
      enabled: byId("scriptEditEnabled").value === "true",
    };

    try {
      await request(`/api/v1/scripts/${state.editing.scriptId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      await Promise.all([loadScripts(), loadPlaylists()]);
      notify("脚本已更新", "ok");
    } catch (err) {
      notify(`脚本更新失败: ${err.message}`, "error");
    }
  });

  byId("scriptEditCancel").addEventListener("click", () => {
    state.editing.scriptId = null;
    byId("scriptEditForm").reset();
  });

  byId("playlistForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      name: byId("playlistName").value.trim(),
      mode: byId("playlistMode").value,
      interval_sec: Number(byId("playlistIntervalSec").value || 30),
      enabled: byId("playlistEnabled").value === "true",
    };

    try {
      await request("/api/v1/playlists", { method: "POST", body: JSON.stringify(payload) });
      byId("playlistForm").reset();
      byId("playlistIntervalSec").value = "30";
      byId("playlistEnabled").value = "true";
      byId("playlistMode").value = "sequential";
      await loadPlaylists();
      notify("轮播计划已新增", "ok");
    } catch (err) {
      notify(`轮播计划创建失败: ${err.message}`, "error");
    }
  });

  byId("playlistItemForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const playlistId = byId("playlistItemPlaylistId").value;
    const payload = {
      script_id: byId("playlistItemScriptId").value,
      sort_order: Number(byId("playlistItemSortOrder").value || 0),
      weight: Number(byId("playlistItemWeight").value || 1),
    };

    if (!playlistId || !payload.script_id) {
      notify("请先选择计划与脚本", "warn");
      return;
    }

    try {
      await request(`/api/v1/playlists/${playlistId}/items`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await loadPlaylists();
      notify("脚本已添加到计划", "ok");
    } catch (err) {
      notify(`添加失败: ${err.message}`, "error");
    }
  });

  byId("playlistEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.editing.playlistId) {
      notify("请先在列表中选择要编辑的轮播计划", "warn");
      return;
    }

    const payload = {
      name: byId("playlistEditName").value.trim(),
      mode: byId("playlistEditMode").value,
      interval_sec: Number(byId("playlistEditInterval").value || 30),
      enabled: byId("playlistEditEnabled").value === "true",
    };

    try {
      await request(`/api/v1/playlists/${state.editing.playlistId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      await loadPlaylists();
      notify("轮播计划已更新", "ok");
    } catch (err) {
      notify(`轮播计划更新失败: ${err.message}`, "error");
    }
  });

  byId("playlistEditCancel").addEventListener("click", () => {
    state.editing.playlistId = null;
    byId("playlistEditForm").reset();
  });

  byId("presetForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      name: byId("presetName").value.trim(),
      avatar_id: byId("presetAvatarId").value,
      voice_id: byId("presetVoiceId").value || null,
      model: byId("presetModel").value,
      transport: byId("presetTransport").value,
      listen_port: Number(byId("presetListenPort").value || 8010),
      tts: byId("presetTts").value || null,
      tts_server: byId("presetTtsServer").value.trim() || null,
      ref_file: byId("presetRefFile").value || null,
      ref_text: byId("presetRefText").value || null,
      extra_args: mergeExtraArgsPushUrl([], byId("presetPushUrl").value.trim()),
    };

    if (!payload.avatar_id) {
      notify("请先创建 Avatar 再创建预设", "warn");
      return;
    }

    try {
      await request("/api/v1/live/presets", { method: "POST", body: JSON.stringify(payload) });
      byId("presetForm").reset();
      byId("presetListenPort").value = "8010";
      byId("presetModel").value = "wav2lip";
      byId("presetTransport").value = "virtualcam";
      byId("presetTts").value = "";
      refreshRefOptions("preset");
      await loadPresets();
      notify("直播预设已新增", "ok");
    } catch (err) {
      notify(`直播预设创建失败: ${err.message}`, "error");
    }
  });

  byId("presetEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.editing.presetId) {
      notify("请先在列表中选择要编辑的预设", "warn");
      return;
    }

    const payload = {
      name: byId("presetEditName").value.trim(),
      avatar_id: byId("presetEditAvatarId").value,
      voice_id: byId("presetEditVoiceId").value || null,
      model: byId("presetEditModel").value,
      transport: byId("presetEditTransport").value,
      listen_port: Number(byId("presetEditListenPort").value || 8010),
      tts: byId("presetEditTts").value || null,
      tts_server: byId("presetEditTtsServer").value.trim() || null,
      ref_file: byId("presetEditRefFile").value || null,
      ref_text: byId("presetEditRefText").value || null,
      extra_args: mergeExtraArgsPushUrl([], byId("presetEditPushUrl").value.trim()),
    };

    try {
      await request(`/api/v1/live/presets/${state.editing.presetId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      await loadPresets();
      notify("直播预设已更新", "ok");
    } catch (err) {
      notify(`直播预设更新失败: ${err.message}`, "error");
    }
  });

  byId("presetEditCancel").addEventListener("click", () => {
    state.editing.presetId = null;
    byId("presetEditForm").reset();
  });

  byId("speakerSayForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      text: byId("speakerSayText").value.trim(),
      interrupt: byId("speakerSayInterrupt").value === "true",
      priority: Number(byId("speakerSayPriority").value || 60),
    };

    if (!payload.text) {
      notify("请输入播报文本", "warn");
      return;
    }

    try {
      await request("/api/v1/live/speaker/say", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      byId("speakerSayText").value = "";
      byId("speakerSayInterrupt").value = "false";
      byId("speakerSayPriority").value = "60";
      await loadSpeakerStatus();
      notify("播报任务已入队", "ok");
    } catch (err) {
      notify(`播报入队失败: ${err.message}`, "error");
    }
  });

  byId("platformMessageForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const sourceMsgId = byId("platformMsgId").value.trim();
    const content = byId("platformMsgContent").value.trim();

    if (!sourceMsgId || !content) {
      notify("消息 ID 和内容不能为空", "warn");
      return;
    }

    const payload = {
      messages: [
        {
          platform: byId("platformMsgPlatform").value.trim() || "douyin",
          room_id: byId("platformMsgRoomId").value.trim() || "room_demo_001",
          source_msg_id: sourceMsgId,
          user_name: byId("platformMsgUserName").value.trim() || null,
          content,
          priority: Number(byId("platformMsgPriority").value || 70),
          auto_generate_reply: byId("platformMsgAutoReply").value === "true",
          auto_speak: byId("platformMsgAutoSpeak").value === "true",
          interrupt: byId("platformMsgInterrupt").value === "true",
          strategy: "rule",
        },
      ],
    };

    try {
      const result = await request("/api/v1/platform/messages:ingest", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      byId("platformMsgResult").textContent = JSON.stringify(result, null, 2);
      byId("platformMsgId").value = "";
      byId("platformMsgContent").value = "";
      await Promise.all([loadSpeakerStatus(), loadJobs()]);
      notify("平台消息已提交", "ok");
    } catch (err) {
      byId("platformMsgResult").textContent = `提交失败: ${err.message}`;
      notify(`平台消息提交失败: ${err.message}`, "error");
    }
  });

  [
    ["btnReloadAvatars", loadAvatars],
    ["btnReloadVoices", loadVoices],
    ["btnReloadScripts", loadScripts],
    ["btnReloadPlaylists", loadPlaylists],
    ["btnReloadPresets", loadPresets],
    ["btnReloadJobs", loadJobs],
  ].forEach(([id, fn]) => {
    byId(id).addEventListener("click", () => fn().catch((e) => notify(e.message, "error")));
  });

  document.body.addEventListener("click", async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const action = target.getAttribute("data-action");
    const type = target.getAttribute("data-type");
    const id = target.getAttribute("data-id");
    if (!action || !type || !id) return;

    try {
      if (action === "edit") {
        if (type === "avatar") openAvatarEditor(id);
        else if (type === "voice") openVoiceEditor(id);
        else if (type === "script") openScriptEditor(id);
        else if (type === "playlist") openPlaylistEditor(id);
        else if (type === "preset") openPresetEditor(id);
        return;
      }

      if (action === "preview" && type === "voice") {
        triggerVoicePreview(id);
        notify(`已选择 ${id}，请在声音试听区生成`, "info");
        return;
      }

      if (action === "copy-id") {
        await copyText(id);
        notify(`已复制 ${type} ID: ${id}`, "ok");
        return;
      }

      if (action === "job-detail" && type === "job") {
        await openJobDrawer(id);
        return;
      }

      if (action === "retry-job" && type === "job") {
        await retryJobById(id);
        return;
      }

      if (action === "start-preset" && type === "preset") {
        await startByPreset(id);
        return;
      }

      if (action === "delete") {
        const ok = confirm(`确认删除 ${type}: ${id} ?`);
        if (!ok) return;
        await deleteByType(type, id);
        notify(`${type} 已删除`, "ok");
        return;
      }

      if (action === "cancel-job" && type === "job") {
        await deleteByType("job", id);
        notify(`任务 ${id} 已取消`, "ok");
      }
    } catch (err) {
      notify(`操作失败: ${err.message}`, "error");
    }
  });
}

async function loadAllData() {
  await Promise.all([
    loadSystemChecks().catch(() => {}),
    refreshLiveStatus(),
    loadSpeakerStatus(),
    loadAvatars(),
    loadVoices(),
    loadScripts(),
    loadPlaylists(),
    loadPresets(),
    loadJobs(),
  ]);
}

async function bootstrap() {
  setupDesktopBridge();
  initNavigation();
  bindEvents();
  try {
    await loadAllData();
    notify("系统数据加载完成", "ok");
  } catch (e) {
    notify(`初始化失败: ${e.message}`, "error");
  }
}

bootstrap();
