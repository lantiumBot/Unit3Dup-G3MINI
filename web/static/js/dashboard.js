/* ── Jobs dashboard — main entry point ──────────────────────────────────────
 *
 * This file owns the shared mutable state, the Socket.IO connection and the
 * DOMContentLoaded bootstrap.  Feature modules are split into:
 *
 *   dashboard-notifications.js  — browser Notification API
 *   dashboard-console.js        — xterm panels, dock, TMDB prompt, stdin
 *   dashboard-scan.js           — scan, duplicates, preview table
 *   dashboard-jobs.js           — job rendering, lifecycle, cancel/retry/clear
 *
 * Load order in index.html:
 *   1. dashboard.js              (this file — defines globals first)
 *   2. dashboard-notifications.js
 *   3. dashboard-console.js
 *   4. dashboard-scan.js
 *   5. dashboard-jobs.js
 * ────────────────────────────────────────────────────────────────────────── */
"use strict";

// ── Shared mutable state ──────────────────────────────────────────────────
let scanItems         = [];
let hideScanHistory   = true;
let hideDuplicateZero = false;
let hideSkipped       = false;

/** Live job map: id → job object (merged from API + socket events). */
let activeJobs = {};

/** Output received before the card exists in the DOM. */
const _pendingOutput = {};

/** Job whose console panel is currently active in the dock. */
let _activeConsoleJobId = null;

/** Map jobId → { xterm, fitAddon, resizeObserver } */
const _dockXterms = new Map();

// ── Shared utility ────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ── Socket.IO ─────────────────────────────────────────────────────────────
const socket = io({
  // Start with polling (always works), then upgrade to WebSocket if the server
  // supports it.  Putting "websocket" first causes an immediate 400 on servers
  // running in threading mode (no WebSocket support), which can break the
  // fallback in some browsers.
  transports: ["polling", "websocket"],
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 1000,
});

function ensureSocketConnected(ms = 8000) {
  return new Promise(resolve => {
    if (socket.connected) return resolve(true);
    const timer = setTimeout(() => resolve(false), ms);
    const done  = () => { clearTimeout(timer); resolve(true); };
    socket.once("connect", done);
    if (!socket.connected) socket.connect();
  });
}

socket.on("connect", () => {
  const dot = document.getElementById("conn-dot");
  if (dot) { dot.className = "badge bg-success ms-2"; dot.textContent = t("nav.connected"); }
  // syncJobsFromApi will (re)start the polling timer at the "connected" interval
  stopJobsLiveSync();
  syncJobsFromApi();
});

socket.on("disconnect", () => {
  const dot = document.getElementById("conn-dot");
  if (dot) { dot.className = "badge bg-danger ms-2"; dot.textContent = t("nav.disconnected"); }
  // Start fallback polling at 3 s so the UI keeps updating while reconnecting
  if (_hasActiveJobs()) startJobsLiveSync();
});

socket.on("connect_error", () => {
  // Fired when on_connect() returns False (stale _SERVER_EPOCH after restart).
  // Trigger an immediate auth-status ping so the user is redirected to /login
  // within seconds instead of waiting up to _PING_INTERVAL.
  if (typeof window._u3d_ping_auth === "function") window._u3d_ping_auth();
});

socket.on("scan_progress", (ev) => {
  const { phase, page, fetched } = ev;
  let detail = "";
  if (phase === "inventory" && (page > 0 || fetched > 0)) {
    detail = t("scan.progress.inventory_page", page, fetched);
  }
  _setScanProgress(phase, detail, ev);
});

socket.on("job_list", jobs => {
  jobs.forEach(j => {
    activeJobs[j.id] = { ...activeJobs[j.id], ...j, tmdbId: j.tmdb_id || activeJobs[j.id]?.tmdbId || 0 };
    renderJob(j, { skipReorder: true });
  });
  reorderJobCards();
  refreshEmptyState();
  updateBadge();
  const rid = _runningJobId();
  if (rid) showActiveConsoleDock(rid);
});

socket.on("job_output", ({ id, text }) => {
  if (!document.querySelector(`.job-card[data-job-id="${id}"]`)) {
    (_pendingOutput[id] ||= []).push({ text });
    return;
  }
  if (id !== _activeConsoleJobId && activeJobs[id]?.status === "running") {
    showActiveConsoleDock(id);
  }
  writeTerminal(id, text);
});

socket.on("job_status", ({ id, status, ended_at }) => {
  // K: record duration when a job finishes successfully (for ETA estimation)
  if (status === "done" && activeJobs[id]?.started_at && ended_at) {
    const dur = (new Date(ended_at) - new Date(activeJobs[id].started_at)) / 1000;
    if (typeof _recordJobDuration === "function") _recordJobDuration(dur);
  }
  if (activeJobs[id]) { activeJobs[id].status = status; activeJobs[id].ended_at = ended_at; }
  if (status === "pending" || status === "running") startJobsLiveSync();
  if (["done", "error", "cancelled"].includes(status) && !_hasActiveJobs()) stopJobsLiveSync();
  if (status === "running") _resetTmdbPromptState(id);
  if (status === "done")  _notify(t("notif.done.title"),  activeJobs[id]?.name ?? "");
  if (status === "error") _notify(t("notif.error.title"), activeJobs[id]?.name ?? "");
  const card = document.querySelector(`.job-card[data-job-id="${id}"]`);
  if (card) {
    applyStatus(card, status);
    if (status === "running") {
      _attachConsoleBody(card, id);
      showActiveConsoleDock(id);
      flushPendingOutput(id);
    } else if (["done", "error", "cancelled"].includes(status)) {
      hideActiveConsoleDock(id);
      setJobStdinEnabled(card, false);
    }
  } else if (status === "running") {
    showActiveConsoleDock(id);
    flushPendingOutput(id);
  } else if (["done", "error", "cancelled"].includes(status)) {
    hideActiveConsoleDock(id);
  }
  reorderJobCards();
  refreshEmptyState();
  updateBadge();
});

socket.on("jobs_cleared", ({ ids }) => {
  _removeJobCards(ids);
});

socket.on("scan_done", (ev) => {
  // Handled by _waitForScanResult in dashboard-scan.js — no global action needed
});

socket.on("inventory_started", (data) => {
  const msg = data?.force
    ? t("config.inventory.notif_force_started")
    : t("config.inventory.notif_started");
  addNotifToCenter(msg, "info");
});

socket.on("inventory_done", (data) => {
  if (data?.error) {
    addNotifToCenter(t("config.inventory.notif_error") + " — " + (data.error || ""), "danger");
    return;
  }
  if (data?.cached) {
    addNotifToCenter(t("config.inventory.notif_cached", data.total ?? 0), "secondary");
  } else {
    addNotifToCenter(t("config.inventory.notif_done", data.added ?? 0, data.total ?? 0), "success");
  }
  // Refresh inventory status if the settings page is open
  if (typeof loadInventoryStatus === "function") loadInventoryStatus();
});

socket.on("auto_scan_done", data => {
  if (!data || !Array.isArray(data.items)) return;
  scanItems = data.items;
  renderPreview(scanItems);
  updateScanSummary();
  _showScanActions(scanItems.length > 0);
  // Persist into the per-folder localStorage cache so getScanCache() can find it.
  // _currentScanFolder is set when a manual scan starts; fall back to a best-effort
  // fetch of the current source_folder from settings if it's not set yet.
  if (_currentScanFolder) {
    setScanCache(_currentScanFolder, scanItems);
  } else {
    fetch("/api/settings").then(r => r.json()).then(cfg => {
      const src = cfg?.web?.source_folder || "";
      if (src) setScanCache(src, scanItems);
    }).catch(() => {});
  }
  showToast(t("jobs.toast.autoscan_done", data.to_upload ?? 0), "info");
});

// ── Per-folder scan cache (localStorage, 24 h) ───────────────────────────
const _SCAN_CACHE_TTL = 86_400_000;  // 24 h

function _folderCacheKey(folder) {
  try { return "u3d_scan_folder_" + btoa(encodeURIComponent(folder)); }
  catch { return "u3d_scan_folder_default"; }
}

function getScanCache(folder) {
  try {
    const raw = localStorage.getItem(_folderCacheKey(folder));
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (!entry?.items || !entry?.ts) return null;
    if ((Date.now() - entry.ts) > _SCAN_CACHE_TTL) {
      localStorage.removeItem(_folderCacheKey(folder));
      return null;
    }
    return entry.items;
  } catch { return null; }
}

function setScanCache(folder, items) {
  if (!folder || !items) return;
  try {
    localStorage.setItem(_folderCacheKey(folder), JSON.stringify({ items, ts: Date.now() }));
  } catch { /* storage quota — ignore */ }
}

function clearScanCache(folder) {
  if (!folder) return;
  try { localStorage.removeItem(_folderCacheKey(folder)); } catch {}
}

// ── Server-side scan cache fallback (Feature 8) ──────────────────────────────
async function getScanCacheWithFallback(folder) {
  // Try localStorage first (fast, local)
  const local = getScanCache(folder);
  if (local) return local;
  // Fallback: server cache (survives browser cache clear)
  try {
    const r = await fetch(`/api/scan/cache?folder=${encodeURIComponent(folder)}`);
    const d = await r.json();
    if (d.items && Array.isArray(d.items) && d.items.length) {
      // Populate localStorage too
      setScanCache(folder, d.items);
      return d.items;
    }
  } catch {}
  return null;
}

async function setScanCacheWithServer(folder, items) {
  setScanCache(folder, items);  // keep localStorage
  try {
    await fetch("/api/scan/cache", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, items }),
    });
  } catch {}
}

// ── DOMContentLoaded ──────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  refreshEmptyState();
  if (!socket.connected) socket.connect();
  syncJobsFromApi();

  // Restore last scan from localStorage per-folder cache (24 h TTL)
  (async function _restoreScanCache() {
    try {
      const r   = await fetch("/api/settings");
      const cfg = await r.json();
      const src = cfg?.web?.source_folder || "";
      if (!src) return;

      // Load bookmarks into UI
      _renderBookmarks(cfg?.web?.source_folder_bookmarks || [], src);

      const items = getScanCache(src);
      if (!items) return;
      scanItems = items;
      _restoreFilterPrefs();
      renderPreview(scanItems);
      updateScanSummary();
      _showScanActions(true);
      _updateIgnoreDupsBtn();
    } catch {}
  })();
  // Restore filter checkbox preferences from localStorage
  _restoreFilterPrefs();

  // Show notification button if permission not yet granted
  const notifBtn = document.getElementById("btn-notif");
  if (notifBtn && "Notification" in window && Notification.permission !== "granted") {
    notifBtn.style.display = "";
  }
  initSortable();
});

// ── Cache-age helper for bookmark dropdown (#15) ──────────────────────────
function _getCacheAge(folder) {
  try {
    const raw = localStorage.getItem(_folderCacheKey(folder));
    if (!raw) return "";
    const entry = JSON.parse(raw);
    if (!entry?.ts) return "";
    const ageMs = Date.now() - entry.ts;
    if (ageMs < 0 || ageMs > _SCAN_CACHE_TTL) return "";
    const mins  = Math.floor(ageMs / 60_000);
    if (mins < 1)   return "< 1 min";
    if (mins < 60)  return `${mins} min`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} h`;
    return `${Math.floor(hours / 24)} j`;
  } catch { return ""; }
}

// ── Source folder bookmarks ───────────────────────────────────────────────
function _renderBookmarks(bookmarks, currentFolder) {
  const menu    = document.getElementById("folder-bookmarks-menu");
  const wrap    = document.getElementById("folder-dropdown-wrap");
  const lbl     = document.getElementById("current-folder-label");
  const cntBadge = document.getElementById("folder-bookmarks-count");
  if (!menu) return;

  if (lbl) {
    lbl.textContent = currentFolder || t("jobs.no_source_folder");
    lbl.title       = currentFolder || "";
  }

  // Badge count (#16)
  if (cntBadge) {
    if (bookmarks.length) {
      cntBadge.textContent    = bookmarks.length;
      cntBadge.style.display  = "";
    } else {
      cntBadge.style.display  = "none";
    }
  }

  if (!bookmarks.length) {
    wrap?.classList.add("d-none");
    return;
  }
  wrap?.classList.remove("d-none");

  // Build HTML using data attributes instead of inline onclick to avoid
  // any HTML/JS escaping ambiguity — event listeners are attached below.
  menu.innerHTML = bookmarks.map(b => {
    const age      = _getCacheAge(b);
    const ageBadge = age
      ? `<span class="badge bg-light text-secondary ms-auto border small" style="font-size:.7em;white-space:nowrap">` +
        `<i class="bi bi-clock me-1"></i>${escHtml(age)}</span>`
      : (b === currentFolder ? '<i class="bi bi-check2 ms-auto text-success"></i>' : "");
    const checkmark = age && b === currentFolder ? '<i class="bi bi-check2 text-success ms-1"></i>' : "";
    return `
    <li>
      <button class="dropdown-item font-mono small d-flex align-items-center gap-2"
              data-switch-folder="${escHtml(b)}"
              title="${escHtml(b)}">
        <i class="bi bi-folder2-open text-info flex-shrink-0"></i>
        <span class="text-truncate" style="max-width:400px">${escHtml(b)}</span>
        ${ageBadge}${checkmark}
      </button>
    </li>`;
  }).join("") +
    `<li><hr class="dropdown-divider"></li>
     <li><button class="dropdown-item small text-muted" onclick="_addCurrentFolderBookmark()">
       <i class="bi bi-bookmark-plus me-1"></i><span data-i18n="jobs.bookmark.add">${t("jobs.bookmark.add")}</span>
     </button></li>`;

  // Attach click listeners — the path is read from the DOM attribute (no JS/HTML
  // escaping issues; dataset.switchFolder gives back the raw path string).
  menu.querySelectorAll("[data-switch-folder]").forEach(btn => {
    btn.addEventListener("click", () => _switchFolder(btn.dataset.switchFolder));
  });
}

async function _switchFolder(newFolder) {
  // Save new source_folder in settings
  await fetch("/api/settings", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ web: { source_folder: newFolder } }),
  });

  // ── FIX: update _currentScanFolder immediately so _doScan() uses the
  // correct folder.  _doScan() reads _currentScanFolder first; if it is
  // truthy it skips the /api/settings fetch entirely, so without this
  // assignment it would re-scan the *previous* folder.
  _currentScanFolder = newFolder;

  // Also update the folder label right away (no need to wait for the scan)
  const lbl = document.getElementById("current-folder-label");
  if (lbl) { lbl.textContent = newFolder; lbl.title = newFolder; }

  // Try to restore from local cache first; fallback to full scan
  const cached = getScanCache(newFolder);
  if (cached) {
    scanItems = cached;
    _restoreFilterPrefs();
    renderPreview(scanItems);
    updateScanSummary();
    _showScanActions(scanItems.length > 0);
    _updateIgnoreDupsBtn();
    // Refresh bookmark indicator (mark new current folder, update cache ages)
    const r   = await fetch("/api/settings");
    const cfg = await r.json();
    _renderBookmarks(cfg?.web?.source_folder_bookmarks || [], newFolder);
    showToast(t("jobs.bookmark.switched", newFolder), "info");
  } else {
    // No cache → trigger a full scan on the new folder
    scanFolder();
  }
}

async function _addCurrentFolderBookmark() {
  const r   = await fetch("/api/settings");
  const cfg = await r.json();
  const src = cfg?.web?.source_folder || "";
  if (!src) { showToast(t("jobs.toast.no_source"), "warning"); return; }
  const res  = await fetch("/api/settings/bookmarks", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ action: "add", path: src }),
  });
  const data = await res.json();
  _renderBookmarks(data.bookmarks || [], src);
  showToast(t("jobs.bookmark.added", src), "success");
}

async function removeBookmark(path) {
  const r    = await fetch("/api/settings");
  const cfg  = await r.json();
  const src  = cfg?.web?.source_folder || "";
  const res  = await fetch("/api/settings/bookmarks", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ action: "remove", path }),
  });
  const data = await res.json();
  _renderBookmarks(data.bookmarks || [], src);
}

window.addEventListener("pageshow", () => {
  if (!socket.connected) socket.connect();
  syncJobsFromApi();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    if (!socket.connected) socket.connect();
    if (_hasActiveJobs()) syncJobsFromApi();
  }
});
