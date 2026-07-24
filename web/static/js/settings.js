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
  setChk("src-recursive_scan",      !!web.recursive_scan);
  setVal("src-duplicate_ask_pct",        web.duplicate_ask_pct ?? 0);
  setVal("src-duplicate_cache_ttl_sec",  web.duplicate_cache_ttl_sec ?? 0);
  setVal("src-max_concurrent_jobs",      web.max_concurrent_jobs ?? 1);
  setChk("src-auto_retry_on_error",      !!web.auto_retry_on_error);
  setVal("src-auto_retry_max",           web.auto_retry_max ?? 1);
  setVal("src-job_timeout_minutes",      web.job_timeout_minutes ?? 0);
  setVal("src-log_retention_days",       web.log_retention_days ?? 30);
  setVal("src-webhook_format",           web.webhook_format || "raw");
  setVal("sec-session-timeout",          web.session_timeout_minutes ?? 0);
  const ri = (web.rules || {}).integrale          || {};
  const rs = (web.rules || {}).complete_or_season || {};
  setChk("rule-integrale-enabled",   ri.enabled            !== false);
  setChk("rule-integrale-seasons",   ri.upload_seasons     !== false);
  setChk("rule-season-enabled",      rs.enabled            !== false);
  setChk("rule-season-require-tag",  rs.require_valid_tag  !== false);
  const rc = (web.rules || {}).collection || {};
  setChk("rule-collection-enabled",     rc.enabled      !== false);
  setChk("rule-collection-require-tag", !!rc.require_valid_tag);
  setVal("rule-collection-tags",        (rc.collection_tags || []).join(", "));

  // Source folder bookmarks
  _renderSettingsBookmarks(web.source_folder_bookmarks || []);

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

  setVal("src-inventory_cache_ttl_hours", web.inventory_cache_ttl_hours ?? 24);

  syncClientPanels();
  loadAutoManage(web);
  loadAutoScan(web);
  loadRssCategories(web);
  initWatcher();
  loadInventoryStatus();
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

// ── Import Unit3Dbot.json ─────────────────────────────────────────────────
function importUnit3dbot() {
  const input = document.getElementById("import-u3d-file");
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    let data;
    try {
      data = JSON.parse(await file.text());
    } catch {
      showToast(t("config.import.error_parse"), "danger");
      input.value = "";
      return;
    }
    const resp = await fetch("/api/settings/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const json = await resp.json().catch(() => ({}));
    if (resp.ok) {
      showToast(t("config.import.success"), "success");
      setTimeout(() => location.reload(), 900);
    } else {
      showToast(json.error || t("config.import.error"), "danger");
    }
    input.value = "";
  };
  input.click();
}

// ── Save ─────────────────────────────────────────────────────────────────
async function saveSettings() {
  // Validate but do NOT block: web/scan settings must always be saveable even
  // if tracker credentials are incomplete. validateRequiredFields highlights
  // the missing fields in red and shows a warning toast, but we continue.
  validateRequiredFields();
  const web = {
    source_folder: getVal("src-source_folder"),
    confirm_mode:  getChk("src-confirm_mode"),
    dry_run:       getChk("src-dry_run"),
    recursive_scan: getChk("src-recursive_scan"),
    duplicate_ask_pct:        parseFloat(getVal("src-duplicate_ask_pct") || "0") || 0,
    duplicate_cache_ttl_sec:  Math.max(0, parseInt(getVal("src-duplicate_cache_ttl_sec") || "0", 10) || 0),
    max_concurrent_jobs:      Math.max(1, parseInt(getVal("src-max_concurrent_jobs") || "1", 10) || 1),
    auto_retry_on_error:      getChk("src-auto_retry_on_error"),
    auto_retry_max:           Math.max(1, parseInt(getVal("src-auto_retry_max") || "1", 10) || 1),
    job_timeout_minutes:      Math.max(0, parseInt(getVal("src-job_timeout_minutes") || "0", 10) || 0),
    log_retention_days:          Math.max(0, parseInt(getVal("src-log_retention_days") || "30", 10) || 0),
    inventory_cache_ttl_hours:   Math.max(0, parseInt(getVal("src-inventory_cache_ttl_hours") || "24", 10) || 0),
    webhook_format:              getVal("src-webhook_format") || "raw",
    session_timeout_minutes:  Math.max(0, parseInt(getVal("sec-session-timeout")    || "0", 10) || 0),
    rules: {
      integrale:          { enabled: getChk("rule-integrale-enabled"), upload_seasons: getChk("rule-integrale-seasons") },
      complete_or_season: { enabled: getChk("rule-season-enabled"), require_valid_tag: getChk("rule-season-require-tag") },
      collection: {
        enabled:          getChk("rule-collection-enabled"),
        require_valid_tag: getChk("rule-collection-require-tag"),
        collection_tags:  getVal("rule-collection-tags").split(",").map(s => s.trim()).filter(Boolean),
      },
    },
    auto_manage: getAutoManage(),
    auto_scan: {
      enabled:          getChk("as-enabled"),
      interval_minutes: Math.max(5, parseInt(getVal("as-interval") || "60", 10) || 60),
    },
    rss_categories: _getRssCategoriesConfig(),
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

  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ web, unit3dbot: u3d, valid_tags: validTags }),
    });
    showToast(resp.ok ? t("settings.toast.saved") : t("settings.toast.save_error"),
              resp.ok ? "success" : "danger");
  } catch {
    showToast(t("settings.toast.save_error"), "danger");
  }
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
  // Categories section only visible for qBittorrent
  const catItem = document.getElementById("acc-rss-categories-item");
  if (catItem) catItem.classList.toggle("d-none", v !== "qbittorrent");
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

// ── Auto-scan ─────────────────────────────────────────────────────────────
function loadAutoScan(web) {
  const as = web?.auto_scan || {};
  setChk("as-enabled",  !!as.enabled);
  setVal("as-interval", as.interval_minutes ?? 60);
  updateAutoScanStatus();
}

async function updateAutoScanStatus() {
  try {
    const r = await fetch("/api/autoscan/status");
    if (!r.ok) return;
    const d = await r.json();
    const lr = document.getElementById("as-last-run");
    const lc = document.getElementById("as-last-count");
    if (lr) lr.textContent = d.last_run
      ? new Date(d.last_run).toLocaleString(typeof i18n !== "undefined" ? i18n.dateLocale?.() : undefined)
      : "—";
    if (lc) {
      if (d.last_count > 0) {
        lc.textContent = `${d.last_count} ${t("jobs.badge.to_upload")}`;
        lc.classList.remove("d-none");
      } else {
        lc.classList.add("d-none");
      }
    }
  } catch {}
}

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
  const nm = am.night_mode || {};
  setChk("am-night-enabled", !!nm.enabled);
  setVal("am-night-start",   nm.start_hour ?? 0);
  setVal("am-night-end",     nm.end_hour   ?? 7);
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
    night_mode: {
      enabled:    getChk("am-night-enabled"),
      start_hour: num("am-night-start") || 0,
      end_hour:   num("am-night-end")   || 7,
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

// ── Inventaire Gemini ─────────────────────────────────────────────────────────

function _fmtRelative(isoStr) {
  if (!isoStr) return null;
  try {
    const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
    if (diff < 60)   return `< 1 min`;
    if (diff < 3600) return `${Math.floor(diff / 60)} min`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
    return `${Math.floor(diff / 86400)}j`;
  } catch { return null; }
}

async function loadInventoryStatus() {
  try {
    const r = await fetch("/api/inventory/status");
    if (!r.ok) return;
    const d = await r.json();
    _renderInventoryStatus(d);
  } catch {}
}

function _renderInventoryStatus(d) {
  const badge = document.getElementById("inventory-running-badge");
  const text  = document.getElementById("inventory-status-text");

  if (badge) badge.classList.toggle("d-none", !d.running);

  if (!text) return;
  if (d.running) {
    text.textContent = t("config.inventory.status_running");
    return;
  }
  if (!d.last_run_at) {
    text.textContent = t("config.inventory.status_never");
    return;
  }
  const age   = _fmtRelative(d.last_checked_at || d.last_run_at);
  const total = d.total_fetched || 0;
  const added = d.added || 0;
  text.innerHTML =
    `<i class="bi bi-check-circle text-success me-1"></i>` +
    `${t("config.inventory.status_last")} ${age} — ` +
    `<strong>${total}</strong> ${t("config.inventory.status_total")}` +
    (added > 0 ? `, <strong>+${added}</strong> ${t("config.inventory.status_added")}` : "");
}

async function syncInventory(force = false) {
  const badge = document.getElementById("inventory-running-badge");
  if (badge) badge.classList.remove("d-none");

  try {
    const r = await fetch("/api/inventory/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }),
    });
    const d = await r.json();
    if (!r.ok) {
      showToast(d.error || t("config.inventory.toast_error"), "danger");
      return;
    }
    if (d.already_running) {
      showToast(t("config.inventory.toast_already_running"), "warning");
      return;
    }
    showToast(t("config.inventory.toast_started"), "info");
    // Poll until done
    const _poll = setInterval(async () => {
      try {
        const rs = await fetch("/api/inventory/status");
        const ds = await rs.json();
        _renderInventoryStatus(ds);
        if (!ds.running) {
          clearInterval(_poll);
          if (ds.error) {
            showToast(`${t("config.inventory.toast_error")} : ${ds.error}`, "danger");
          } else {
            showToast(t("config.inventory.toast_done"), "success");
          }
        }
      } catch { clearInterval(_poll); }
    }, 2000);
  } catch {
    showToast(t("config.inventory.toast_error"), "danger");
    if (badge) badge.classList.add("d-none");
  }
}

// ── Security / Auth ───────────────────────────────────────────────────────────
(async function _initSecuritySection() {
  try {
    const r = await fetch("/api/auth/status");
    if (!r.ok) return;
    const d = await r.json();
    const statusEl  = document.getElementById("security-status");
    const disableBtn = document.getElementById("btn-disable-auth");
    const currentWrap = document.getElementById("sec-current-wrap");
    if (d.auth_enabled) {
      if (statusEl) { statusEl.className = "alert alert-success py-2 mb-3 small"; statusEl.textContent = t("config.security.status_enabled"); }
      if (disableBtn) disableBtn.style.display = "";
      if (currentWrap) currentWrap.classList.remove("d-none");
    } else {
      if (statusEl) { statusEl.className = "alert alert-secondary py-2 mb-3 small"; statusEl.textContent = t("config.security.status_disabled"); }
    }
  } catch(_) {}
})();

async function savePassword() {
  const newPwd  = document.getElementById("sec-new-pwd")?.value || "";
  const confirm = document.getElementById("sec-confirm-pwd")?.value || "";
  const current = document.getElementById("sec-current-pwd")?.value || "";
  if (!newPwd) { showToast(t("config.security.error_empty"), "warning"); return; }
  if (newPwd !== confirm) { showToast(t("config.security.error_mismatch"), "warning"); return; }
  if (newPwd.length < 6)  { showToast(t("config.security.error_short"), "warning"); return; }
  const resp = await fetch("/api/auth/password", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ new_password: newPwd, current_password: current }),
  });
  const d = await resp.json().catch(() => ({}));
  if (resp.ok) {
    showToast(t("config.security.saved"), "success");
    document.getElementById("sec-new-pwd").value = "";
    document.getElementById("sec-confirm-pwd").value = "";
    document.getElementById("sec-current-pwd").value = "";
    setTimeout(() => location.reload(), 800);
  } else {
    const msg = d.error === "wrong_current" ? t("config.security.error_wrong_current")
              : d.error === "too_short"      ? t("config.security.error_short")
              : t("config.security.error_save");
    showToast(msg, "danger");
  }
}

async function disableAuth() {
  if (!confirm(t("config.security.confirm_disable"))) return;
  const resp = await fetch("/api/auth/disable", { method: "POST" });
  if (resp.ok) { showToast(t("config.security.disabled"), "success"); setTimeout(() => location.reload(), 800); }
}

// ── Source folder bookmarks (settings page) ───────────────────────────────
let _bookmarksSortable = null;

function _renderSettingsBookmarks(bookmarks) {
  const container = document.getElementById("settings-bookmarks-list");
  if (!container) return;

  // Destroy existing Sortable instance before replacing innerHTML
  if (_bookmarksSortable) { _bookmarksSortable.destroy(); _bookmarksSortable = null; }

  if (!bookmarks.length) {
    container.innerHTML = `<span class="text-muted small fst-italic" data-i18n="config.scan.bookmarks_empty">${t("config.scan.bookmarks_empty")}</span>`;
    return;
  }
  container.innerHTML = bookmarks.map(b => `
    <div class="badge bg-body-secondary border text-body d-flex align-items-center gap-1 py-1 px-2 font-mono"
         style="max-width:300px;overflow:hidden;cursor:grab" title="${escHtml(b)}"
         data-path="${escHtml(b)}">
      <i class="bi bi-grip-vertical text-muted flex-shrink-0" style="cursor:grab" title="Réordonner"></i>
      <i class="bi bi-folder2-open text-info flex-shrink-0"></i>
      <span class="text-truncate" style="max-width:220px;cursor:pointer"
            onclick="document.getElementById('src-source_folder').value=this.closest('[data-path]').dataset.path">${escHtml(b)}</span>
      <button class="btn btn-link btn-sm p-0 text-danger ms-1 flex-shrink-0"
              onclick="_settingsRemoveBookmark(this.closest('[data-path]').dataset.path)" title="Supprimer">
        <i class="bi bi-x"></i>
      </button>
    </div>`).join("");

  _initBookmarksSortable(container);
}

function _initBookmarksSortable(container) {
  if (typeof Sortable === "undefined") return;
  _bookmarksSortable = new Sortable(container, {
    animation: 150,
    ghostClass: "opacity-50",
    onEnd() {
      const newOrder = [...container.querySelectorAll("[data-path]")]
        .map(el => el.dataset.path);
      fetch("/api/settings", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ web: { source_folder_bookmarks: newOrder } }),
      }).catch(() => {});
    },
  });
}

async function _settingsAddBookmark() {
  const path = (document.getElementById("src-source_folder")?.value || "").trim();
  if (!path) { showToast(t("jobs.toast.no_source"), "warning"); return; }
  const res  = await fetch("/api/settings/bookmarks", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ action: "add", path }),
  });
  const data = await res.json();
  _renderSettingsBookmarks(data.bookmarks || []);
  showToast(t("jobs.bookmark.added", path), "success");
}

async function _settingsRemoveBookmark(path) {
  const res  = await fetch("/api/settings/bookmarks", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ action: "remove", path }),
  });
  const data = await res.json();
  _renderSettingsBookmarks(data.bookmarks || []);
}

function escHtml(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ── RSS Categories ────────────────────────────────────────────────────────────

const _RSS_CAT_DEFS = [
  { key: "movies",             icon: "bi-film",          color: "text-primary", i18n: "config.rss_categories.cat.movies" },
  { key: "series",             icon: "bi-display",       color: "text-success", i18n: "config.rss_categories.cat.series" },
  { key: "movies_animation",   icon: "bi-stars",         color: "text-warning", i18n: "config.rss_categories.cat.movies_animation" },
  { key: "series_animation",   icon: "bi-magic",         color: "text-warning", i18n: "config.rss_categories.cat.series_animation" },
  { key: "movies_documentary", icon: "bi-camera-video",  color: "text-info",    i18n: "config.rss_categories.cat.movies_documentary" },
  { key: "series_documentary", icon: "bi-camera-reels",  color: "text-info",    i18n: "config.rss_categories.cat.series_documentary" },
];

function loadRssCategories(web) {
  const cfg = web?.rss_categories || {};
  setChk("rss-cat-enabled", !!cfg.enabled);
  _renderRssCategories(cfg.categories || {});
}

function _renderRssCategories(cats) {
  const container = document.getElementById("rss-cat-rows");
  if (!container) return;
  container.innerHTML = _RSS_CAT_DEFS.map(def => {
    const c       = cats[def.key] || {};
    const enabled = !!c.enabled;
    const name    = c.qbit_name || "";
    return `
      <div class="col-md-6 col-lg-4">
        <div class="card bg-body-secondary h-100">
          <div class="card-body d-flex flex-column gap-2 py-2 px-3">
            <div class="d-flex align-items-center gap-2">
              <i class="bi ${escHtml(def.icon)} ${escHtml(def.color)}"></i>
              <span class="fw-semibold small">${t(def.i18n)}</span>
              <div class="form-check form-switch mb-0 ms-auto">
                <input class="form-check-input" type="checkbox" id="rss-cat-${escHtml(def.key)}-enabled"
                       ${enabled ? "checked" : ""}>
              </div>
            </div>
            <div class="input-group input-group-sm">
              <input type="text" class="form-control form-control-sm font-monospace"
                     id="rss-cat-${escHtml(def.key)}-name"
                     value="${escHtml(name)}"
                     placeholder="${t("config.rss_categories.qbit_name_placeholder")}">
              <span class="input-group-text p-1" id="rss-cat-${escHtml(def.key)}-status"></span>
            </div>
          </div>
        </div>
      </div>`;
  }).join("");
}

function _getRssCategoriesConfig() {
  const cats = {};
  for (const def of _RSS_CAT_DEFS) {
    const enabled  = document.getElementById(`rss-cat-${def.key}-enabled`)?.checked ?? false;
    const qbitName = (document.getElementById(`rss-cat-${def.key}-name`)?.value || "").trim();
    cats[def.key]  = { qbit_name: qbitName, enabled };
  }
  return { enabled: getChk("rss-cat-enabled"), categories: cats };
}

async function rssCatCheck() {
  const btn = document.getElementById("rss-cat-check-btn");
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${t("config.rss_categories.checking")}`; }
  try {
    const r = await fetch("/api/rss/categories/check");
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showToast(d.error || t("config.rss_categories.check_error"), "danger");
      return;
    }
    const data = await r.json();
    if (data.error) showToast(data.error, "warning");

    for (const def of _RSS_CAT_DEFS) {
      const statusEl = document.getElementById(`rss-cat-${def.key}-status`);
      if (!statusEl) continue;
      const info = data.categories?.[def.key];
      if (!info) { statusEl.innerHTML = ""; continue; }
      if (!info.qbit_name) {
        statusEl.innerHTML = "";
        continue;
      }
      if (info.exists) {
        const tip = info.save_path ? ` title="${escHtml(info.save_path)}"` : "";
        statusEl.innerHTML = `<i class="bi bi-check-circle-fill text-success"${tip}></i>`;
      } else {
        statusEl.innerHTML = `<i class="bi bi-x-circle-fill text-danger" title="${t("config.rss_categories.check_missing")}"></i>`;
      }
    }
    showToast(t("config.rss_categories.check_ok"), "success");
  } catch (e) {
    showToast(t("config.rss_categories.check_error"), "danger");
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = `<i class="bi bi-patch-check me-1"></i>${t("config.rss_categories.check_btn")}`; }
  }
}
