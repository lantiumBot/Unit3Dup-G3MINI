/* ── Settings page ──────────────────────────────────────────────────────── */
"use strict";

let validTags = [];
let teamTags  = [];

// ── Load ─────────────────────────────────────────────────────────────────
async function loadSettings() {
  let data;
  try {
    const r = await fetch("/api/settings"); data = await r.json();
  } catch { showToast(t("settings.toast.save_error"), "danger"); return; }

  const web = data.web       || {};
  const u3d = data.unit3dbot || {};

  // Scan rules
  setVal("src-source_folder",       web.source_folder || "");
  setChk("src-confirm_mode",        !!web.confirm_mode);
  setChk("src-dry_run",             !!web.dry_run);
  setVal("src-duplicate_ask_pct",        web.duplicate_ask_pct ?? 0);
  setVal("src-duplicate_cache_ttl_sec",  web.duplicate_cache_ttl_sec ?? 0);
  const ri = (web.rules || {}).integrale          || {};
  const rs = (web.rules || {}).complete_or_season || {};
  setChk("rule-integrale-enabled",   ri.enabled            !== false);
  setChk("rule-integrale-seasons",   ri.upload_seasons     !== false);
  setChk("rule-season-enabled",      rs.enabled            !== false);
  setChk("rule-season-require-tag",  rs.require_valid_tag  !== false);

  // Filter tags
  validTags = data.valid_tags || [];
  renderTags();

  // Team tags from UPLOADER_TAG
  teamTags = u3d?.UPLOADER_TAG?.TAGS_TEAM || [];
  renderTeamTags();

  // All [data-path] fields
  document.querySelectorAll("[data-path]").forEach(el => {
    const parts   = el.dataset.path.split(".");
    const section = parts[0];
    const field   = parts.slice(1).join(".");

    if (field === "MULTI_TRACKER_STR") {
      const arr = u3d?.TRACKER_CONFIG?.MULTI_TRACKER;
      el.value = Array.isArray(arr) ? arr.join(", ") : "";
      return;
    }

    let val = u3d?.[section];
    for (const k of parts.slice(1)) val = val?.[k];
    if (val === undefined || val === null) return;
    if (el.type === "checkbox") {
      const s = String(val).toLowerCase();
      el.checked = val === true || s === "true" || s === "1" || s === "yes";
    }
    else                        el.value   = String(val);
  });

  syncClientPanels();
  loadAutoManage(web);
  initWatcher();
}

// ── Required field validation ─────────────────────────────────────────────
function validateRequiredFields() {
  const always = [
    "TRACKER_CONFIG.Gemini_URL",
    "TRACKER_CONFIG.Gemini_APIKEY",
    "TRACKER_CONFIG.Gemini_PID",
    "TRACKER_CONFIG.TMDB_APIKEY",
  ];
  const byClient = {
    qbittorrent:  ["TORRENT_CLIENT_CONFIG.QBIT_HOST", "TORRENT_CLIENT_CONFIG.QBIT_PORT",
                   "TORRENT_CLIENT_CONFIG.QBIT_USER", "TORRENT_CLIENT_CONFIG.QBIT_PASS"],
    transmission: ["TORRENT_CLIENT_CONFIG.TRASM_HOST", "TORRENT_CLIENT_CONFIG.TRASM_PORT",
                   "TORRENT_CLIENT_CONFIG.TRASM_USER", "TORRENT_CLIENT_CONFIG.TRASM_PASS"],
    rtorrent:     ["TORRENT_CLIENT_CONFIG.RTORR_HOST", "TORRENT_CLIENT_CONFIG.RTORR_PORT",
                   "TORRENT_CLIENT_CONFIG.RTORR_USER", "TORRENT_CLIENT_CONFIG.RTORR_PASS"],
  };
  const client = document.getElementById("client-selector")?.value || "qbittorrent";
  const toCheck = [...always, ...(byClient[client] || [])];

  // Clear previous invalid state
  document.querySelectorAll("[data-path].is-invalid").forEach(el => el.classList.remove("is-invalid"));

  const missing = [];
  for (const path of toCheck) {
    const el = document.querySelector(`[data-path="${path}"]`);
    if (!el) continue;
    if (!(el.value || "").trim()) {
      el.classList.add("is-invalid");
      const label = el.closest("[class*='col-']")?.querySelector("label")?.textContent?.trim() || path;
      missing.push(label);
    }
  }
  if (missing.length) {
    showToast(`${t("settings.validation.missing")} ${missing.join(", ")}`, "danger");
    // Open accordion sections containing invalid fields
    document.querySelectorAll(".is-invalid").forEach(el => {
      const collapse = el.closest(".accordion-collapse");
      if (collapse && !collapse.classList.contains("show")) {
        new bootstrap.Collapse(collapse, { toggle: true });
      }
    });
    return false;
  }
  return true;
}

// ── Save ─────────────────────────────────────────────────────────────────
async function saveSettings() {
  if (!validateRequiredFields()) return;
  const web = {
    source_folder: getVal("src-source_folder"),
    confirm_mode:  getChk("src-confirm_mode"),
    dry_run:       getChk("src-dry_run"),
    duplicate_ask_pct:        parseFloat(getVal("src-duplicate_ask_pct") || "0") || 0,
    duplicate_cache_ttl_sec:  Math.max(0, parseInt(getVal("src-duplicate_cache_ttl_sec") || "0", 10) || 0),
    rules: {
      integrale:          { enabled: getChk("rule-integrale-enabled"), upload_seasons: getChk("rule-integrale-seasons") },
      complete_or_season: { enabled: getChk("rule-season-enabled"), require_valid_tag: getChk("rule-season-require-tag") },
    },
    auto_manage: getAutoManage(),
  };

  // Rebuild u3d from [data-path]
  const u3d = {};
  document.querySelectorAll("[data-path]").forEach(el => {
    const parts   = el.dataset.path.split(".");
    const section = parts[0];
    const keys    = parts.slice(1);
    if (!u3d[section]) u3d[section] = {};

    if (keys.join(".") === "MULTI_TRACKER_STR") {
      u3d[section]["MULTI_TRACKER"] = el.value.split(",").map(s => s.trim()).filter(Boolean);
      return;
    }

    const raw = el.type === "checkbox" ? (el.checked ? "true" : "false")
              : el.type === "number"   ? (el.value === "" ? null : Number(el.value))
              : (el.value || null);

    let obj = u3d[section];
    keys.slice(0, -1).forEach(k => { obj[k] = obj[k] || {}; obj = obj[k]; });
    obj[keys[keys.length - 1]] = raw;
  });

  // UPLOADER_TAG team tags
  if (!u3d.UPLOADER_TAG) u3d.UPLOADER_TAG = {};
  u3d.UPLOADER_TAG.TAGS_TEAM = teamTags;

  const resp = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ web, unit3dbot: u3d, valid_tags: validTags }),
  });
  showToast(resp.ok ? t("settings.toast.saved") : t("settings.toast.save_error"),
            resp.ok ? "success" : "danger");
}

// ── Filter tags ───────────────────────────────────────────────────────────
function renderTags() {
  document.getElementById("tags-pills").innerHTML = validTags.map((tag, i) =>
    `<span class="badge bg-primary d-flex align-items-center gap-1 fs-6 px-2 py-1">
       ${esc(tag)}
       <button type="button" class="btn-close btn-close-white ms-1" style="font-size:.5rem"
               onclick="removeTag(${i})"></button>
     </span>`
  ).join("");
}
function addTag() {
  const inp = document.getElementById("new-tag-input");
  const tag = inp.value.trim().toUpperCase();
  if (!tag) return;
  if (validTags.includes(tag)) { showToast(t("settings.toast.tag_exists"), "warning"); return; }
  validTags.push(tag); inp.value = ""; renderTags();
}
function removeTag(i) { validTags.splice(i, 1); renderTags(); }
document.getElementById("new-tag-input")?.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); addTag(); }
});

// ── Team tags ─────────────────────────────────────────────────────────────
function renderTeamTags() {
  const box = document.getElementById("team-tags-pills");
  if (!box) return;
  box.innerHTML = teamTags.map((tag, i) =>
    `<span class="badge bg-warning text-dark d-flex align-items-center gap-1 fs-6 px-2 py-1">
       ${esc(tag)}
       <button type="button" class="btn-close ms-1" style="font-size:.5rem"
               onclick="removeTeamTag(${i})"></button>
     </span>`
  ).join("");
}
function addTeamTag() {
  const inp = document.getElementById("new-team-tag-input");
  const tag = inp.value.trim().toUpperCase();
  if (!tag) return;
  if (teamTags.includes(tag)) { showToast(t("settings.toast.team_tag_exists"), "warning"); return; }
  teamTags.push(tag); inp.value = ""; renderTeamTags();
}
function removeTeamTag(i) { teamTags.splice(i, 1); renderTeamTags(); }
document.getElementById("new-team-tag-input")?.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); addTeamTag(); }
});

// ── Client panels ─────────────────────────────────────────────────────────
document.getElementById("client-selector")?.addEventListener("change", syncClientPanels);
function syncClientPanels() {
  const v = document.getElementById("client-selector")?.value || "qbittorrent";
  document.getElementById("client-qbit") ?.classList.toggle("d-none", v !== "qbittorrent");
  document.getElementById("client-trasm")?.classList.toggle("d-none", v !== "transmission");
  document.getElementById("client-rtorr")?.classList.toggle("d-none", v !== "rtorrent");
}

// ── Password reveal ───────────────────────────────────────────────────────
document.querySelectorAll(".toggle-pass").forEach(btn => {
  btn.addEventListener("click", () => {
    const inp  = btn.previousElementSibling;
    const show = inp.type === "password";
    inp.type   = show ? "text" : "password";
    btn.querySelector("i").className = show ? "bi bi-eye-slash" : "bi bi-eye";
  });
});

// ── AutoManager ───────────────────────────────────────────────────────────
function loadAutoManage(web) {
  const am = web?.auto_manage || {};
  setChk("am-enabled",         !!am.enabled);
  setVal("am-interval",        am.interval_minutes ?? 60);
  const ar = am.auto_remove   || {};
  setChk("am-remove-enabled",  !!ar.enabled);
  setVal("am-remove-days",     ar.after_days   ?? 30);
  setVal("am-remove-seeders",  ar.min_seeders  ?? 5);
  const rs = am.auto_reseed   || {};
  setChk("am-reseed-enabled",  !!rs.enabled);
  setVal("am-reseed-seeders",  rs.below_seeders ?? 2);
  setChk("am-gemini-scan",     !!rs.gemini_dead_scan);
  loadAutoManageLog();
}

function getAutoManage() {
  const num = id => Number(document.getElementById(id)?.value || 0);
  return {
    enabled:          getChk("am-enabled"),
    interval_minutes: num("am-interval") || 60,
    auto_remove: {
      enabled:     getChk("am-remove-enabled"),
      after_days:  num("am-remove-days")    || 30,
      min_seeders: num("am-remove-seeders") || 5,
    },
    auto_reseed: {
      enabled:          getChk("am-reseed-enabled"),
      below_seeders:    num("am-reseed-seeders") || 2,
      gemini_dead_scan: getChk("am-gemini-scan"),
    },
  };
}

async function runAutoManage() {
  await fetch("/api/automanage/run", { method: "POST" });
  showToast(t("settings.toast.am_started"), "info");
  setTimeout(loadAutoManageLog, 2000);
}

async function loadAutoManageLog() {
  const box = document.getElementById("am-log");
  if (!box) return;
  try {
    const r = await fetch("/api/automanage/status");
    const d = await r.json();
    const lines = (d.log || []).map(e => {
      const ts  = (e.ts || "").substring(11, 19);
      const pfx = e.level === "warning" ? "⚠ " : e.level === "error" ? "✖ " : "· ";
      return `[${ts}] ${pfx}${e.msg}`;
    });
    box.textContent = lines.length ? lines.join("\n") : t("config.am.log.no_log");
  } catch { box.textContent = t("config.am.log.error"); }
  box.scrollTop = box.scrollHeight;
}

// ── Helpers ───────────────────────────────────────────────────────────────
const setVal = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
const getVal = id => document.getElementById(id)?.value || "";
const setChk = (id, v) => { const e = document.getElementById(id); if (e) e.checked = !!v; };
const getChk = id => !!document.getElementById(id)?.checked;
const esc    = s => String(s || "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));

// ── Watcher (état JSON + console live) ────────────────────────────────────
const socket = typeof io !== "undefined" ? io() : null;

const _WATCHER_PHASE_BADGE = {
  stopped:   "secondary",
  starting:  "info",
  running:   "primary",
  idle:      "success",
  processing:"warning",
  watchdog:  "info",
};

function renderWatcherState(st) {
  const pre = document.getElementById("watcher-state-json");
  if (!pre || !st) return;
  const { events, ...rest } = st;
  const view = {
    ...rest,
    events_count: (events || []).length,
    events_recent: events || [],
  };
  pre.textContent = JSON.stringify(view, null, 2);

  const badge = document.getElementById("watcher-phase-badge");
  if (badge) {
    const ph = st.phase || "stopped";
    badge.className = `badge bg-${_WATCHER_PHASE_BADGE[ph] || "secondary"} font-mono`;
    badge.textContent = st.running ? t(`config.watcher.phase.${ph}`) : t("config.watcher.phase.stopped");
  }

  const summary = document.getElementById("watcher-queue-summary");
  if (summary) {
    const n = st.queue_count ?? (st.queue || []).length;
    const wd = st.watchdog_remaining_sec;
    let extra = "";
    if (wd != null && st.phase === "watchdog") {
      extra = ` · ${t("config.watcher.watchdog", Math.ceil(wd))}`;
    }
    summary.textContent = t("config.watcher.queue_summary", n) + extra;
  }

  const startBtn = document.getElementById("watcher-btn-start");
  const stopBtn  = document.getElementById("watcher-btn-stop");
  const stdinInp = document.getElementById("watcher-stdin");
  if (startBtn) startBtn.disabled = !!st.running;
  if (stopBtn)  stopBtn.disabled  = !st.running;
  if (stdinInp) stdinInp.disabled = !st.running;
}

async function refreshWatcherState() {
  try {
    const r = await fetch("/api/watcher/status");
    renderWatcherState(await r.json());
  } catch { /* ignore */ }
}

function appendWatcherConsole(text, op) {
  const box = document.getElementById("watcher-console");
  if (!box || !text) return;
  if (op === "replace") {
    const parts = box.textContent.split("\n");
    parts.pop();
    parts.push(text.replace(/\n$/, ""));
    box.textContent = parts.join("\n");
  } else {
    box.textContent += text;
  }
  box.scrollTop = box.scrollHeight;
}

function initWatcher() {
  if (!socket) return;
  refreshWatcherState();

  socket.on("watcher_state", renderWatcherState);
  socket.on("watcher_output", data => appendWatcherConsole(data.text || "", data.op));
  socket.on("watcher_console_sync", data => {
    const box = document.getElementById("watcher-console");
    if (!box) return;
    box.textContent = (data.lines || []).join("");
    box.scrollTop = box.scrollHeight;
  });
}

async function startWatcher() {
  try {
    const r = await fetch("/api/watcher/start", { method: "POST" });
    const d = await r.json();
    if (!d.ok) {
      const key = d.error ? `config.watcher.error.${d.error}` : "config.watcher.error.generic";
      showToast(t(key), "danger");
      if (d.state) renderWatcherState(d.state);
      return;
    }
    const box = document.getElementById("watcher-console");
    if (box) box.textContent = "";
    renderWatcherState(d.state);
    showToast(t("config.watcher.toast.started"), "success");
  } catch {
    showToast(t("config.watcher.error.generic"), "danger");
  }
}

async function stopWatcher() {
  try {
    await fetch("/api/watcher/stop", { method: "POST" });
    showToast(t("config.watcher.toast.stopped"), "info");
    refreshWatcherState();
  } catch {
    showToast(t("config.watcher.error.generic"), "danger");
  }
}

function sendWatcherStdin() {
  const inp = document.getElementById("watcher-stdin");
  const text = (inp?.value || "").trim();
  if (!text || !socket) return;
  socket.emit("watcher_stdin", { text });
  inp.value = "";
}

document.getElementById("watcher-stdin")?.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); sendWatcherStdin(); }
});

document.addEventListener("DOMContentLoaded", loadSettings);
