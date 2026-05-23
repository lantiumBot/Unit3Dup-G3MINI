/* ── Jobs dashboard ─────────────────────────────────────────────────────── */
"use strict";

let scanItems  = [];
/** Masquer les lignes « Historique » dans le tableau de scan (défaut : oui). */
let hideScanHistory = true;
let activeJobs = {};
/** Sortie reçue avant que la carte job existe dans le DOM */
const _pendingOutput = {};
/** Job dont la console est affichée dans le dock du haut */
let _activeConsoleJobId = null;

const _JOB_SORT_ORDER = { running: 0, pending: 1, done: 2, error: 2, cancelled: 3 };
const _TERMINAL_JOB_STATUSES = new Set(["done", "error", "cancelled"]);

function _jobSortKey(status) {
  return _JOB_SORT_ORDER[status] ?? 9;
}

function reorderJobCards() {
  const list = document.getElementById("jobs-list");
  if (!list) return;
  [...list.querySelectorAll(".job-card")]
    .sort((a, b) => {
      const sa = activeJobs[a.dataset.jobId]?.status || "";
      const sb = activeJobs[b.dataset.jobId]?.status || "";
      const d  = _jobSortKey(sa) - _jobSortKey(sb);
      return d !== 0 ? d : a.dataset.jobId.localeCompare(b.dataset.jobId);
    })
    .forEach(el => list.appendChild(el));
}

function _runningJobId() {
  return Object.keys(activeJobs).find(id => activeJobs[id]?.status === "running") || null;
}

function _applyTerminalChunk(term, text, op) {
  if (!term || text == null || text === "") return;
  const atBottom = term.scrollTop + term.clientHeight >= term.scrollHeight - 10;
  if (op === "replace") {
    const raw = term.textContent;
    const endsNl = raw.endsWith("\n");
    const lines = raw.split("\n");
    if (endsNl && lines.length) lines.pop();
    if (lines.length) lines[lines.length - 1] = text.replace(/\n$/, "");
    else lines.push(text.replace(/\n$/, ""));
    term.textContent = lines.join("\n") + (text.endsWith("\n") ? "\n" : endsNl ? "\n" : "");
  } else {
    term.textContent += text;
  }
  if (atBottom) term.scrollTop = term.scrollHeight;
}

function _activeDockEl() {
  return document.getElementById("active-console-dock");
}

function _activeDockTerm() {
  return document.getElementById("active-console-terminal");
}

function setActiveDockStdin(on) {
  const inp = document.getElementById("active-console-stdin");
  const btn = document.getElementById("active-console-send");
  const row = inp?.closest(".input-row");
  if (inp) { inp.disabled = !on; if (!on) inp.value = ""; }
  if (btn) btn.disabled = !on;
  if (row) row.classList.toggle("opacity-50", !on);
}

function updateActiveDockTmdb(jobId) {
  const el = document.getElementById("active-console-tmdb");
  if (!el) return;
  const tid = _jobTmdbId(jobId);
  if (tid) {
    el.textContent = `TMDB ${tid}`;
    el.title = t("jobs.tmdb.badge_title");
    el.classList.remove("d-none");
  } else {
    el.classList.add("d-none");
  }
}

function showActiveConsoleDock(jobId) {
  const job = activeJobs[jobId];
  if (!job) return;
  _activeConsoleJobId = jobId;
  const dock = _activeDockEl();
  const dockTerm = _activeDockTerm();
  if (!dock || !dockTerm) return;

  dock.classList.remove("d-none");
  const title = document.getElementById("active-console-title");
  if (title) title.textContent = job.name;

  const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
  const cardTerm = card?.querySelector(".terminal");
  if (cardTerm?.textContent) dockTerm.textContent = cardTerm.textContent;
  else dockTerm.textContent = (job.lines || []).join("");

  if (card) collapseJobConsole(card);
  setActiveDockStdin(true);
  updateActiveDockTmdb(jobId);
  reorderJobCards();
}

function hideActiveConsoleDock(jobId) {
  const id = jobId || _activeConsoleJobId;
  if (!id) return;

  const dockTerm = _activeDockTerm();
  const card = document.querySelector(`.job-card[data-job-id="${id}"]`);
  const cardTerm = card?.querySelector(".terminal");
  if (cardTerm && dockTerm) {
    cardTerm.textContent = dockTerm.textContent;
    expandJobConsole(card);
  }

  if (_activeConsoleJobId === id) _activeConsoleJobId = null;
  _activeDockEl()?.classList.add("d-none");
  setActiveDockStdin(false);
  const tmdb = document.getElementById("active-console-tmdb");
  tmdb?.classList.add("d-none");
}

function _consoleContext(jobId) {
  const useDock = jobId && jobId === _activeConsoleJobId && _activeDockTerm();
  const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
  return {
    card,
    primary: useDock ? _activeDockTerm() : card?.querySelector(".terminal"),
    useDock,
  };
}

// TMDB ID mémorisé par terminal (un job = une console PTY, pas partagé entre jobs)
const _TMDB_PROMPT_RE = /valid\s+TMDB\s+ID\s*\(\s*0\s*=\s*skip\s*\)/i;
const _DUP_PROMPT_RE   = /Press\s*\(C\)\s*to\s*continue.*\(S\)\s*to\s*SKIP/i;
const _tmdbAutoSent   = new Set();
/** Prompt TMDB déjà traité (saisie manuelle ou auto) pour ce job */
const _tmdbPromptHandled = new Set();

function _resetTmdbPromptState(jobId) {
  _tmdbAutoSent.delete(jobId);
  _tmdbPromptHandled.delete(jobId);
}

function _jobTmdbId(jobId) {
  const id = activeJobs[jobId]?.tmdbId;
  return id > 0 && id < 9999999 ? id : null;
}

function _saveJobTmdb(jobId, id) {
  if (!activeJobs[jobId]) return;
  if (id > 0 && id < 9999999) activeJobs[jobId].tmdbId = id;
}

function _parseTmdbId(text) {
  const m = String(text).trim().match(/^(\d+)$/);
  if (!m) return null;
  const n = parseInt(m[1], 10);
  if (n === 0) return 0;
  return n > 0 && n < 9999999 ? n : null;
}

function updateTmdbBadge(card) {
  const el = card?.querySelector(".job-tmdb-badge");
  if (!el) return;
  const tid = _jobTmdbId(card?.dataset?.jobId);
  if (tid) {
    el.textContent = `TMDB ${tid}`;
    el.title = t("jobs.tmdb.badge_title");
    el.classList.remove("d-none");
  } else {
    el.classList.add("d-none");
  }
}

function _stdinForJob(jobId) {
  if (jobId === _activeConsoleJobId) {
    const dockInp = document.getElementById("active-console-stdin");
    if (dockInp && !dockInp.disabled) return dockInp;
  }
  return document.querySelector(`.job-card[data-job-id="${jobId}"] .job-stdin`);
}

function onTmdbPromptDetected(jobId) {
  if (_tmdbPromptHandled.has(jobId)) return;
  const inp = _stdinForJob(jobId);
  if (!inp || inp.disabled || activeJobs[jobId]?.status !== "running") return;

  const tid = _jobTmdbId(jobId);
  if (tid) {
    if (_tmdbAutoSent.has(jobId)) return;
    _tmdbAutoSent.add(jobId);
    setTimeout(() => {
      if (_tmdbPromptHandled.has(jobId)) return;
      if (activeJobs[jobId]?.status !== "running") return;
      autoSendTmdb(jobId, tid);
    }, 400);
  } else {
    inp.placeholder = t("jobs.tmdb.placeholder");
    inp.classList.add("border-info");
    inp.focus();
  }
  const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
  updateTmdbBadge(card);
  updateActiveDockTmdb(jobId);
}

function autoSendTmdb(jobId, id) {
  if (_tmdbPromptHandled.has(jobId)) return;
  const inp = _stdinForJob(jobId);
  if (!inp || inp.disabled) return;
  inp.value = "";
  inp.classList.remove("border-info");
  const text = String(id);
  _tmdbPromptHandled.add(jobId);
  postJobStdin(jobId, text);
  showToast(t("jobs.tmdb.reused", text), "info");
}

function checkDupPrompt(jobId, chunk) {
  if (activeJobs[jobId]?.status !== "running") return;
  if (!chunk || !_DUP_PROMPT_RE.test(chunk)) return;
  const inp = _stdinForJob(jobId);
  if (!inp || inp.disabled) return;
  inp.placeholder = t("jobs.dup_prompt.placeholder");
  inp.classList.add("border-warning");
  inp.focus();
}

function checkTmdbPrompt(jobId, chunk) {
  if (activeJobs[jobId]?.status !== "running") return;
  if (_tmdbPromptHandled.has(jobId)) return;
  if (!chunk || !_TMDB_PROMPT_RE.test(chunk)) return;
  onTmdbPromptDetected(jobId);
}

async function postJobStdin(jobId, text) {
  appendTerminal(jobId, `\n> ${text}\n`);
  try {
    const r = await fetch(`/api/jobs/${jobId}/input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      const err = data.error || r.status;
      showToast(t("jobs.toast.stdin_failed", err), "warning");
      return false;
    }
    return true;
  } catch {
    showToast(t("jobs.toast.stdin_failed", "network"), "danger");
    return false;
  }
}

// ── Socket.IO ─────────────────────────────────────────────────────────────
const socket = io({
  transports: ["polling", "websocket"],
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 1000,
});

let _jobsLiveTimer = null;

function _hasActiveJobs() {
  return Object.values(activeJobs).some(j => j.status === "pending" || j.status === "running");
}

function stopJobsLiveSync() {
  if (_jobsLiveTimer) clearInterval(_jobsLiveTimer);
  _jobsLiveTimer = null;
}

/** Rafraîchit lignes + dock tant qu'il y a des jobs actifs (filet si Socket.IO en retard). */
function startJobsLiveSync() {
  stopJobsLiveSync();
  if (!_hasActiveJobs()) return;
  _jobsLiveTimer = setInterval(() => {
    if (!_hasActiveJobs()) {
      stopJobsLiveSync();
      return;
    }
    syncJobsFromApi();
  }, 800);
}

function ensureSocketConnected(ms = 8000) {
  return new Promise(resolve => {
    if (socket.connected) return resolve(true);
    const t = setTimeout(() => resolve(false), ms);
    const done = () => { clearTimeout(t); resolve(true); };
    socket.once("connect", done);
    if (!socket.connected) socket.connect();
  });
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

socket.on("connect", () => {
  const dot = document.getElementById("conn-dot");
  if (dot) { dot.className = "badge bg-success ms-2"; dot.textContent = t("nav.connected"); }
  syncJobsFromApi();
});

socket.on("disconnect", () => {
  const dot = document.getElementById("conn-dot");
  if (dot) { dot.className = "badge bg-danger ms-2"; dot.textContent = t("nav.disconnected"); }
});

socket.on("job_list", jobs => {
  jobs.forEach(j => { activeJobs[j.id] = j; renderJob(j); });
  refreshEmptyState();
  updateBadge();
  if (_hasActiveJobs()) startJobsLiveSync();
});

socket.on("job_output", ({ id, text, op }) => {
  if (id === _activeConsoleJobId && _activeDockTerm()) {
    writeTerminal(id, text, op || "append");
    return;
  }
  if (!document.querySelector(`.job-card[data-job-id="${id}"]`)) {
    (_pendingOutput[id] ||= []).push({ text, op: op || "append" });
    return;
  }
  writeTerminal(id, text, op || "append");
});

socket.on("job_status", ({ id, status, ended_at }) => {
  if (activeJobs[id]) { activeJobs[id].status = status; activeJobs[id].ended_at = ended_at; }
  if (status === "pending" || status === "running") startJobsLiveSync();
  if (["done", "error", "cancelled"].includes(status) && !_hasActiveJobs()) stopJobsLiveSync();
  if (status === "running") _resetTmdbPromptState(id);
  const card = document.querySelector(`.job-card[data-job-id="${id}"]`);
  if (card) {
    applyStatus(card, status);
    if (status === "running") {
      showActiveConsoleDock(id);
    } else if (["done", "error", "cancelled"].includes(status)) {
      hideActiveConsoleDock(id);
      setJobStdinEnabled(card, false);
    }
  } else if (status === "running") {
    showActiveConsoleDock(id);
  } else if (["done", "error", "cancelled"].includes(status)) {
    hideActiveConsoleDock(id);
  }
  reorderJobCards();
  refreshEmptyState();
  updateBadge();
});

function _removeJobCards(ids) {
  (ids || []).forEach(id => {
    if (!id) return;
    _resetTmdbPromptState(id);
    if (id === _activeConsoleJobId) hideActiveConsoleDock(id);
    document.querySelector(`.job-card[data-job-id="${id}"]`)?.remove();
    delete activeJobs[id];
    delete _pendingOutput[id];
  });
  refreshEmptyState();
  updateBadge();
  if (!_hasActiveJobs()) stopJobsLiveSync();
}

function _collectTerminalJobIds() {
  const ids = new Set();
  Object.entries(activeJobs).forEach(([id, j]) => {
    if (_TERMINAL_JOB_STATUSES.has(j?.status)) ids.add(id);
  });
  document.querySelectorAll(".job-card").forEach(card => {
    const id = card.dataset.jobId;
    const st = activeJobs[id]?.status || card.dataset.jobStatus;
    if (id && _TERMINAL_JOB_STATUSES.has(st)) ids.add(id);
  });
  return [...ids];
}

socket.on("jobs_cleared", ({ ids }) => {
  _removeJobCards(ids);
});

// ── Scan folder ───────────────────────────────────────────────────────────
async function scanFolder() {
  const settingsResp = await fetch("/api/settings");
  const { web } = await settingsResp.json();
  const src = web?.source_folder || "";
  if (!src) {
    showToast(t("jobs.toast.no_source"), "warning");
    return;
  }

  showToast(t("jobs.toast.scanning"), "info");
  const resp = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_folder: src,
      include_history: !!document.getElementById("chk-show-history")?.checked,
    }),
  });
  const data = await resp.json();
  if (data.error) { showToast(data.error, "danger"); return; }

  scanItems = data.items || [];
  hideScanHistory = true;
  window._dupSkipTh = data.duplicate_skip_threshold_pct ?? 0;
  const chkHist = document.getElementById("chk-show-history");
  if (chkHist) chkHist.checked = false;
  renderPreview(scanItems);
  updateScanSummary();

  const pending   = visibleScanItems().filter(i => i.status === "pending").length;
  const dups      = scanItems.filter(i => i.status === "duplicate").length;
  const dupAsk    = visibleScanItems().filter(i => i.status === "duplicate_ask").length;
  if (dups) {
    showToast(t("jobs.toast.scan_done_dups", scanItems.length, pending, dups), "warning");
  } else if (dupAsk) {
    showToast(t("jobs.toast.scan_done_dup_ask", scanItems.length, pending, dupAsk), "warning");
  } else {
    showToast(t("jobs.toast.scan_done", scanItems.length, pending), "success");
  }
}

// ── Preview table ─────────────────────────────────────────────────────────
function _typeLabel(type) {
  return t({ integrale: "jobs.type.integrale", season: "jobs.type.season",
             file: "jobs.type.file", unknown: "jobs.type.unknown" }[type] || "jobs.type.unknown");
}

function _fmtGb(n) {
  if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
  return `${Number(n).toFixed(2)} Go`;
}

function _dupMatchLine(label, deltaPct, geminiGb) {
  return `${escHtml(label)} <span class="text-warning">Δ ${deltaPct}%</span>`
    + ` <span class="text-muted">·</span> ${t("jobs.duplicate.gemini_size", _fmtGb(geminiGb))}`;
}

/** Lignes delta par TMDB / IGDB / torrent (scan doublons). */
function _dupDeltasHtml(duplicate) {
  if (!duplicate) return "";
  const localLine = duplicate.local_gb != null
    ? `<div class="small font-mono mb-1 text-info">${t("jobs.duplicate.local_size", _fmtGb(duplicate.local_gb))}</div>`
    : "";

  const byId = duplicate.deltas_by_id;
  if (Array.isArray(byId) && byId.length) {
    const parts = byId.map(row => _dupMatchLine(row.label, row.delta_pct, row.size_gb));
    return localLine + `<div class="small font-mono lh-sm">${parts.join("<br>")}</div>`;
  }
  const matches = duplicate.matches;
  if (Array.isArray(matches) && matches.length) {
    const parts = matches.map(m => {
      const label = m.tmdb_id ? `TMDB ${m.tmdb_id}`
        : m.igdb_id ? `IGDB ${m.igdb_id}`
        : `#${m.tracker_id ?? "?"}`;
      return _dupMatchLine(label, m.delta_pct, m.size_gb);
    });
    return localLine + `<div class="small font-mono lh-sm">${parts.join("<br>")}</div>`;
  }
  if (duplicate.delta_pct != null) {
    const id = duplicate.tmdb_id ? `TMDB ${duplicate.tmdb_id}`
      : duplicate.igdb_id ? `IGDB ${duplicate.igdb_id}` : "";
    const label = id || duplicate.name || "?";
    return localLine
      + `<div class="small font-mono">${_dupMatchLine(label, duplicate.delta_pct, duplicate.size_gb)}</div>`;
  }
  return localLine;
}

function _duplicateTitle(duplicate) {
  if (!duplicate) return "";
  const local = duplicate.local_gb != null ? t("jobs.duplicate.local_size", _fmtGb(duplicate.local_gb)) : "";
  const byId = duplicate.deltas_by_id;
  if (Array.isArray(byId) && byId.length) {
    const ids = byId.map(r =>
      `${r.label} Δ${r.delta_pct}% Gemini ${_fmtGb(r.size_gb)}`
    ).join(" · ");
    return local ? `${local} — ${ids}` : ids;
  }
  if (duplicate.delta_pct != null) {
    const id = duplicate.tmdb_id ? `TMDB ${duplicate.tmdb_id}` : duplicate.igdb_id ? `IGDB ${duplicate.igdb_id}` : "";
    const bit = `${id ? id + " " : ""}Δ${duplicate.delta_pct}% Gemini ${_fmtGb(duplicate.size_gb)}`;
    return local ? `${local} — ${bit}` : bit;
  }
  return local || duplicate.name || "";
}

function visibleScanItems() {
  if (!hideScanHistory) return scanItems;
  return scanItems.filter(i => i.status !== "history");
}

function updateScanSummary() {
  const el = document.getElementById("scan-summary");
  const btnStart = document.getElementById("btn-start");
  if (!el) return;
  if (!scanItems.length) {
    el.textContent = "";
    if (btnStart) btnStart.disabled = true;
    return;
  }
  const visible = visibleScanItems();
  const pending = visible.filter(i => i.status === "pending").length;
  const dupAsk  = visible.filter(i => i.status === "duplicate_ask").length;
  const histN   = scanItems.filter(i => i.status === "history").length;
  let text = t("jobs.summary", pending, visible.length - pending, visible.length);
  if (hideScanHistory && histN > 0) {
    text += ` · ${t("jobs.summary_history_hidden", histN)}`;
  }
  el.textContent = text;
  if (btnStart) btnStart.disabled = pending + dupAsk === 0;
}

function toggleScanHistory(chk) {
  hideScanHistory = !chk.checked;
  renderPreview(scanItems);
  updateScanSummary();
}

function renderPreview(items) {
  const body = document.getElementById("preview-body");
  const preview = document.getElementById("scan-preview");
  if (!body || !preview) return;

  if (!items.length) {
    body.innerHTML = "";
    preview.classList.add("d-none");
    return;
  }

  const rows = hideScanHistory ? items.filter(i => i.status !== "history") : items;
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="text-center text-muted py-3">${
      escHtml(t("jobs.preview_empty_visible"))
    }</td></tr>`;
    if (hideScanHistory) {
      preview.classList.add("d-none");
    } else {
      preview.classList.remove("d-none");
    }
    return;
  }
  body.innerHTML = rows.map(item => {
    const typeLabel = _typeLabel(item.type);
    const typeCls   = { integrale: "bg-info text-dark", season: "bg-success",
                        file: "bg-warning text-dark", unknown: "bg-secondary" }[item.type] || "bg-secondary";
    const tagBadge  = item.tag
      ? `<span class="badge font-mono ${item.tag_valid ? "bg-success" : "bg-warning text-dark"}">${escHtml(item.tag)}</span>`
      : `<span class="text-muted">—</span>`;
    const dupTitle = _duplicateTitle(item.duplicate);
    const statusBadge = item.status === "pending"
      ? `<span class="badge bg-primary">${t("jobs.badge.to_upload")}</span>`
      : item.status === "duplicate"
      ? `<span class="badge bg-danger" title="${escHtml(dupTitle || item.duplicate?.name || "")}">${t("jobs.badge.duplicate")}</span>`
      : item.status === "duplicate_ask"
      ? `<span class="badge bg-warning text-dark" title="${escHtml(dupTitle || item.duplicate?.name || "")}">${t("jobs.badge.duplicate_ask")}</span>`
      : item.status === "history"
      ? `<span class="badge bg-secondary">${t("jobs.badge.history")}</span>`
      : `<span class="badge bg-secondary">${t("jobs.badge.ignored")}</span>`;
    const dupDeltas = (item.status === "duplicate" || item.status === "duplicate_ask")
      ? _dupDeltasHtml(item.duplicate) : "";
    const seasons = item.seasons?.length
      ? `<span class="badge bg-secondary">${item.seasons.length}</span>` : "—";
    const selectable = item.status === "pending" || item.status === "duplicate_ask";
    const disabled = selectable ? "" : "disabled";
    const checked  = item.status === "pending" ? "checked" : "";
    const rowCls   = item.status === "duplicate_ask" ? "" : (selectable ? "" : "opacity-50");
    const dup = item.duplicate;
    const dupHint  = (item.status === "duplicate" || item.status === "duplicate_ask") && dup
      ? ` title="${escHtml(t("jobs.duplicate.hint",
          dup.name || "",
          dup.delta_pct ?? 0,
          dup.size_th ?? 10,
          _fmtGb(dup.local_gb),
          _fmtGb(dup.size_gb)
        ) + (dupTitle ? " — " + dupTitle : ""))}"` : "";
    return `<tr class="${rowCls}"${dupHint}>
      <td><input type="checkbox" class="item-chk" data-id="${escHtml(item.id)}"
                 ${checked} ${disabled}></td>
      <td class="font-mono small" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          title="${escHtml(item.name)}">${escHtml(item.name)}</td>
      <td><span class="badge ${typeCls}">${typeLabel}</span></td>
      <td>${tagBadge}</td>
      <td>${seasons}</td>
      <td>${statusBadge}${dupDeltas}</td>
    </tr>`;
  }).join("");
  preview.classList.remove("d-none");
}

function toggleAll(master) {
  document.querySelectorAll(".item-chk:not([disabled])").forEach(c => c.checked = master.checked);
}

// ── Start jobs ────────────────────────────────────────────────────────────
async function startJobs() {
  const checked = new Set(
    [...document.querySelectorAll(".item-chk:checked")].map(c => c.dataset.id)
  );
  const toSend = scanItems.filter(i =>
    (i.status === "pending" || i.status === "duplicate_ask") && checked.has(i.id)
  );
  if (!toSend.length) { showToast(t("jobs.toast.no_selected"), "warning"); return; }

  const forcedDups = toSend.filter(i => i.status === "duplicate_ask");
  if (forcedDups.length && !confirm(t("jobs.confirm.upload_duplicates", forcedDups.length))) {
    return;
  }

  document.getElementById("scan-preview").classList.add("d-none");
  document.getElementById("btn-start").disabled = true;
  scanItems = [];

  const resp = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: toSend }),
  });
  await ensureSocketConnected();
  const data = await resp.json();
  (data.created || []).forEach(j => { activeJobs[j.id] = j; renderJob(j); });
  await syncJobsFromApi();
  refreshEmptyState();
  updateBadge();
  startJobsLiveSync();
  showToast(t("jobs.toast.jobs_created", data.created?.length || 0), "success");
}

// ── Job rendering ─────────────────────────────────────────────────────────
function _syncJobLinesToDom(job) {
  if (!job?.lines?.length) return;
  const text = job.lines.join("");
  const card = document.querySelector(`.job-card[data-job-id="${job.id}"]`);
  const cardTerm = card?.querySelector(".terminal");
  if (cardTerm) {
    cardTerm.textContent = text;
    cardTerm.scrollTop = cardTerm.scrollHeight;
  }
  if (job.id === _activeConsoleJobId) {
    const dockTerm = _activeDockTerm();
    if (dockTerm) {
      dockTerm.textContent = text;
      dockTerm.scrollTop = dockTerm.scrollHeight;
    }
  }
}

function renderJob(job) {
  let card = document.querySelector(`.job-card[data-job-id="${job.id}"]`);
  if (card) {
    applyStatus(card, job.status);
    _syncJobLinesToDom(job);
    setJobStdinEnabled(card, job.status === "pending");
    updateTmdbBadge(card);
    if (job.status === "running") showActiveConsoleDock(job.id);
    flushPendingOutput(job.id);
    reorderJobCards();
    return;
  }

  const tpl   = document.getElementById("tpl-job");
  const clone = tpl.content.cloneNode(true);
  card = clone.querySelector(".job-card");
  card.dataset.jobId = job.id;

  card.querySelector(".job-type").textContent = _typeLabel(job.type);
  card.querySelector(".job-name").textContent = job.name;
  if (job.tag) card.querySelector(".job-tag").textContent = job.tag;
  else         card.querySelector(".job-tag").remove();

  if (job.seasons > 0) {
    const badge = document.createElement("span");
    badge.className = "badge bg-secondary font-mono";
    badge.textContent = t("jobs.seasons_count", job.seasons);
    card.querySelector(".card-header").insertBefore(badge, card.querySelector(".job-cancel-btn"));
  }

  // Apply i18n to cloned template elements
  card.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  card.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  card.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });

  _syncJobLinesToDom(job);

  setJobStdinEnabled(card, job.status === "pending");

  card.querySelector(".job-stdin").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); sendStdinEl(job.id, e.target); }
  });

  applyStatus(card, job.status);
  updateTmdbBadge(card);
  document.getElementById("jobs-list").appendChild(clone);
  if (job.status === "running") showActiveConsoleDock(job.id);
  flushPendingOutput(job.id);
  reorderJobCards();
}

function flushPendingOutput(jobId) {
  const pending = _pendingOutput[jobId];
  if (!pending?.length) return;
  delete _pendingOutput[jobId];
  pending.forEach(({ text, op }) => writeTerminal(jobId, text, op));
}

async function syncJobsFromApi() {
  try {
    const resp = await fetch("/api/jobs");
    const jobs = await resp.json();
    const apiIds = new Set(jobs.map(j => j.id));
    document.querySelectorAll(".job-card").forEach(card => {
      const id = card.dataset.jobId;
      if (id && !apiIds.has(id)) _removeJobCards([id]);
    });
    jobs.forEach(j => {
      activeJobs[j.id] = { ...activeJobs[j.id], ...j };
      renderJob(j);
    });
    const rid = _runningJobId();
    if (rid) showActiveConsoleDock(rid);
    reorderJobCards();
    refreshEmptyState();
    updateBadge();
    if (_hasActiveJobs()) startJobsLiveSync();
    else stopJobsLiveSync();
  } catch { /* ignore */ }
}

function applyStatus(card, status) {
  const badge  = card.querySelector(".job-badge");
  const spin   = card.querySelector(".job-spinner");
  const cancel = card.querySelector(".job-cancel-btn");

  card.dataset.jobStatus = status;
  spin.classList.toggle("d-none", status !== "running");

  const map = {
    pending:   ["bg-secondary",         "jobs.status.pending"],
    running:   ["bg-primary",           "jobs.status.running"],
    done:      ["bg-success",           "jobs.status.done"],
    error:     ["bg-danger",            "jobs.status.error"],
    cancelled: ["bg-warning text-dark", "jobs.status.cancelled"],
  };
  const [cls, key] = map[status] || ["bg-secondary", status];
  badge.className   = `job-badge badge ${cls}`;
  badge.textContent = t(key) || status;
  cancel.classList.toggle("d-none", ["done", "error", "cancelled"].includes(status));

  if (["done", "error", "cancelled"].includes(status)) {
    collapseJobConsole(card);
    setJobStdinEnabled(card, false);
  }
}

function expandJobConsole(card) {
  const body = card?.querySelector(".job-console-body");
  if (body) body.classList.remove("d-none");
}

function collapseJobConsole(card) {
  const body = card?.querySelector(".job-console-body");
  if (body) body.classList.add("d-none");
}

function setJobStdinEnabled(card, on) {
  const row = card?.querySelector(".input-row");
  const inp = card?.querySelector(".job-stdin");
  const btn = card?.querySelector(".job-send-btn");
  if (inp) { inp.disabled = !on; if (!on) inp.value = ""; }
  if (btn) btn.disabled = !on;
  if (row) row.classList.toggle("opacity-50", !on);
}

function writeTerminal(jobId, text, op) {
  if (text == null || text === "") return;
  const { card, primary, useDock } = _consoleContext(jobId);
  if (!primary) return;

  if (!useDock && card) {
    const st = activeJobs[jobId]?.status;
    if (!["done", "error", "cancelled"].includes(st) &&
        card.querySelector(".job-console-body")?.classList.contains("d-none")) {
      expandJobConsole(card);
    }
  }

  _applyTerminalChunk(primary, text, op);
  if (useDock && card) {
    const cardTerm = card.querySelector(".terminal");
    if (cardTerm) _applyTerminalChunk(cardTerm, text, op);
  }
  checkTmdbPrompt(jobId, text);
  checkDupPrompt(jobId, text);
}

function appendTerminal(jobId, text) {
  writeTerminal(jobId, text, "append");
}

// ── stdin ─────────────────────────────────────────────────────────────────
function sendStdin(btn) {
  const card = btn.closest(".job-card");
  if (card) sendStdinEl(card.dataset.jobId, card.querySelector(".job-stdin"));
}

function sendActiveDockStdin() {
  if (_activeConsoleJobId) sendStdinEl(_activeConsoleJobId, document.getElementById("active-console-stdin"));
}

async function sendStdinEl(jobId, inputEl) {
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  inputEl.classList.remove("border-info", "border-warning");
  const tid = _parseTmdbId(text);
  if (tid !== null && tid > 0) {
    _saveJobTmdb(jobId, tid);
    _tmdbPromptHandled.add(jobId);
    const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
    updateTmdbBadge(card);
    updateActiveDockTmdb(jobId);
    showToast(t("jobs.tmdb.remembered", tid), "success");
  }
  await postJobStdin(jobId, text);
}

// ── Cancel / clear ────────────────────────────────────────────────────────
function cancelJob(btn) {
  const jid = btn.closest(".job-card").dataset.jobId;
  fetch(`/api/jobs/${jid}`, { method: "DELETE" });
}

async function clearDoneJobs() {
  const localIds = _collectTerminalJobIds();
  const histRemoved = scanItems.filter(i => i.status === "history").length;
  scanItems = scanItems.filter(i => i.status !== "history");

  hideScanHistory = true;
  const chkHist = document.getElementById("chk-show-history");
  if (chkHist) chkHist.checked = false;
  renderPreview(scanItems);
  updateScanSummary();

  let jobsRemoved = 0;
  if (localIds.length) {
    try {
      const resp = await fetch("/api/jobs/clear", { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      const allIds = [...new Set([...localIds, ...(data.ids || [])])];
      _removeJobCards(allIds);
      jobsRemoved = allIds.length;
    } catch {
      _removeJobCards(localIds);
      jobsRemoved = localIds.length;
    }
  }

  const total = histRemoved + jobsRemoved;
  if (!total) {
    showToast(t("jobs.toast.clear_none"), "info");
    return;
  }
  if (histRemoved && jobsRemoved) {
    showToast(t("jobs.toast.clear_done_mix", histRemoved, jobsRemoved), "success");
  } else if (histRemoved) {
    showToast(t("jobs.toast.clear_history", histRemoved), "success");
  } else {
    showToast(t("jobs.toast.clear_done", jobsRemoved), "success");
  }
}

// ── UI helpers ────────────────────────────────────────────────────────────
function refreshEmptyState() {
  const hasJobs = document.querySelector(".job-card") !== null;
  document.getElementById("jobs-empty")?.classList.toggle("d-none", hasJobs);
}

function updateBadge() {
  const n  = Object.values(activeJobs).filter(j => ["pending","running"].includes(j.status)).length;
  const el = document.getElementById("badge-running");
  if (!el) return;
  el.textContent = n;
  el.classList.toggle("d-none", n === 0);
}

function escHtml(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])
  );
}

// Re-render job statuses when locale changes
document.addEventListener("locale-changed", () => {
  document.querySelectorAll(".job-card").forEach(card => {
    const jid = card.dataset.jobId;
    if (activeJobs[jid]) applyStatus(card, activeJobs[jid].status);
    updateTmdbBadge(card);
  });
  refreshEmptyState();
});

document.addEventListener("DOMContentLoaded", () => {
  refreshEmptyState();
  if (!socket.connected) socket.connect();
  syncJobsFromApi();
  document.getElementById("active-console-send")?.addEventListener("click", sendActiveDockStdin);
  document.getElementById("active-console-stdin")?.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); sendActiveDockStdin(); }
  });
});
