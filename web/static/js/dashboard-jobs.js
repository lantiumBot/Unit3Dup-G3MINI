/* ── Job rendering, creation, cancel, retry, clear ──────────────────────────
 *
 * Depends on globals defined in dashboard.js:
 *   activeJobs, _pendingOutput, _activeConsoleJobId, scanItems, socket
 * Depends on: t(), showToast(), escHtml(), _notify()
 * Depends on console module: showActiveConsoleDock, hideActiveConsoleDock,
 *   _resetTmdbPromptState, writeTerminal, updateTmdbBadge, flushPendingOutput
 * ────────────────────────────────────────────────────────────────────────── */
"use strict";

const _JOB_SORT_ORDER      = { running: 0, pending: 1, done: 2, error: 2, cancelled: 3 };
const _TERMINAL_JOB_STATUSES = new Set(["done", "error", "cancelled"]);

// ── K: ETA estimation ─────────────────────────────────────────────────────
// Rolling window of the last 10 completed job durations (seconds).
const _recentDurations = [];
const _ETA_WINDOW = 10;
const _BULK_CONFIRM_THRESHOLD = 10;   // L: confirm before starting this many jobs

function _recordJobDuration(sec) {
  if (sec <= 0 || sec > 3600) return;  // ignore unrealistic values
  _recentDurations.push(sec);
  if (_recentDurations.length > _ETA_WINDOW) _recentDurations.shift();
  _updateAllEtaBadges();
  updateQueueEta();
}

function _avgDurationSec() {
  if (!_recentDurations.length) return null;
  return _recentDurations.reduce((s, x) => s + x, 0) / _recentDurations.length;
}

function _fmtEta(sec) {
  if (sec < 60) return `~${Math.round(sec)}s`;
  return `~${Math.round(sec / 60)}min`;
}

function _updateAllEtaBadges() {
  const avg = _avgDurationSec();
  document.querySelectorAll(".job-card[data-job-status='pending']").forEach(card => {
    let badge = card.querySelector(".job-eta-badge");
    if (!avg) {
      if (badge) badge.remove();
      return;
    }
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "job-eta-badge badge bg-info text-dark font-mono ms-1";
      badge.title = t("jobs.eta.badge_title") || "Durée estimée";
      const header = card.querySelector(".card-header");
      if (header) header.insertBefore(badge, card.querySelector(".job-cancel-btn"));
    }
    badge.textContent = _fmtEta(avg);
  });
  updateQueueEta();
}

function updateQueueEta() {
  const el = document.getElementById("queue-eta-label");
  if (!el) return;
  const avg     = _avgDurationSec();
  const pending = Object.values(activeJobs).filter(j => j.status === "pending").length;
  const running = Object.values(activeJobs).filter(j => j.status === "running").length;
  const total   = pending + (running > 0 ? 1 : 0);
  if (!avg || total === 0) { el.textContent = ""; return; }
  const etaSec = avg * (pending + (running > 0 ? 0.5 : 0));
  const mins   = Math.round(etaSec / 60);
  el.textContent = mins < 1 ? t("jobs.eta.queue_less1min") : t("jobs.eta.queue_minutes", mins);
}

function _jobSortKey(status) {
  return _JOB_SORT_ORDER[status] ?? 9;
}

// ── Sort / reorder ────────────────────────────────────────────────────────
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

// ── Drag-and-drop (SortableJS) ────────────────────────────────────────────
let _sortable = null;

function initSortable() {
  const list = document.getElementById("jobs-list");
  if (!list || _sortable || typeof Sortable === "undefined") return;
  _sortable = Sortable.create(list, {
    animation: 150,
    handle: ".job-drag-handle",
    filter: ".job-card:not([data-job-status='pending'])",
    preventOnFilter: false,
    onEnd: () => {
      const ids = [...document.querySelectorAll(".job-card[data-job-status='pending']")]
        .map(c => c.dataset.jobId)
        .filter(Boolean);
      if (!ids.length) return;
      fetch("/api/jobs/reorder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).catch(() => {});
    },
  });
}

// ── Socket.IO live sync polling fallback ──────────────────────────────────
let _jobsLiveTimer = null;

function _hasActiveJobs() {
  return Object.values(activeJobs).some(j => j.status === "pending" || j.status === "running");
}

function stopJobsLiveSync() {
  if (_jobsLiveTimer) clearInterval(_jobsLiveTimer);
  _jobsLiveTimer = null;
}

function startJobsLiveSync() {
  stopJobsLiveSync();
  if (!_hasActiveJobs()) return;
  // Poll even when socket is connected: events from background threads can be
  // dropped (eventlet / threading mismatch). Socket events still provide
  // real-time updates when they arrive; this is a safety-net fallback.
  const interval = socket.connected ? 5000 : 3000;
  _jobsLiveTimer = setInterval(() => {
    if (!_hasActiveJobs()) { stopJobsLiveSync(); return; }
    syncJobsFromApi();
  }, interval);
}

// ── DOM helpers ───────────────────────────────────────────────────────────
function _runningJobId() {
  return Object.keys(activeJobs).find(id => activeJobs[id]?.status === "running") || null;
}

function _syncJobLinesToDom(job) {
  if (!job?.lines?.length) return;
  const liveViaSocket = socket.connected && job.status === "running";
  const text = job.lines.join("");
  const card = document.querySelector(`.job-card[data-job-id="${job.id}"]`);
  const cardTerm = card?.querySelector(".terminal");
  if (cardTerm && !(liveViaSocket && cardTerm.textContent !== "")) {
    cardTerm.textContent = text;
    cardTerm.scrollTop = cardTerm.scrollHeight;
  }
}

function _attachConsoleBody(card, jobId) {
  if (card.querySelector(".job-console-body")) return;
  const body = document.createElement("div");
  body.className = "job-console-body d-none";
  body.innerHTML = `
    <div class="terminal"></div>
    <div class="input-row d-flex gap-2 p-2 border-top bg-body-secondary align-items-center">
      <span class="input-group-text font-mono small px-2">›</span>
      <span class="badge bg-info text-dark font-mono job-tmdb-badge d-none flex-shrink-0"
            data-i18n-title="jobs.tmdb.badge_title" title="${t("jobs.tmdb.badge_title")}"></span>
      <input type="text" class="form-control form-control-sm font-mono job-stdin"
             data-i18n-placeholder="jobs.stdin_placeholder"
             placeholder="${t("jobs.stdin_placeholder")}" autocomplete="off">
      <button class="btn btn-sm btn-primary job-send-btn" onclick="sendStdin(this)">
        <i class="bi bi-send-fill"></i>
      </button>
    </div>`;
  body.querySelector(".job-stdin").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); sendStdinEl(jobId, e.target); }
  });
  card.appendChild(body);
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

// ── Job card render ───────────────────────────────────────────────────────
function applyStatus(card, status) {
  const badge   = card.querySelector(".job-badge");
  const spin    = card.querySelector(".job-spinner");
  const cancel  = card.querySelector(".job-cancel-btn");

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

  const retryBtn   = card.querySelector(".job-retry-btn");
  const dragHandle = card.querySelector(".job-drag-handle");
  if (retryBtn)   retryBtn.classList.toggle("d-none",  !["error", "cancelled"].includes(status));
  if (dragHandle) dragHandle.classList.toggle("d-none", status !== "pending");

  if (["done", "error", "cancelled"].includes(status)) {
    collapseJobConsole(card);
    setJobStdinEnabled(card, false);
    // Close the floating dock panel if it's open for this job
    hideActiveConsoleDock(card.dataset.jobId);
  }
}

function flushPendingOutput(jobId) {
  const pending = _pendingOutput[jobId];
  if (!pending?.length) return;
  delete _pendingOutput[jobId];
  pending.forEach(({ text }) => writeTerminal(jobId, text));
}

function renderJob(job, { skipReorder = false } = {}) {
  let card = document.querySelector(`.job-card[data-job-id="${job.id}"]`);
  if (card) {
    applyStatus(card, job.status);
    if (job.status === "running") _attachConsoleBody(card, job.id);
    _syncJobLinesToDom(job);
    setJobStdinEnabled(card, job.status === "pending");
    updateTmdbBadge(card);
    if (job.status === "running") showActiveConsoleDock(job.id);
    flushPendingOutput(job.id);
    if (!skipReorder) reorderJobCards();
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
    badge.className   = "badge bg-secondary font-mono";
    badge.textContent = t("jobs.seasons_count", job.seasons);
    card.querySelector(".card-header").insertBefore(badge, card.querySelector(".job-cancel-btn"));
  }

  // Auto-retry badge (#8) — shown when job was retried at least once
  if ((job.retry_count ?? 0) > 0) {
    const retryBadge = document.createElement("span");
    retryBadge.className   = "badge bg-warning text-dark font-mono";
    retryBadge.title       = `Auto-retry ${job.retry_count}`;
    retryBadge.textContent = `↺ ${job.retry_count}`;
    card.querySelector(".card-header").insertBefore(retryBadge, card.querySelector(".job-cancel-btn"));
  }

  // K: ETA badge for pending jobs when duration history is available
  if (job.status === "pending") {
    const avg = _avgDurationSec();
    if (avg) {
      const etaBadge = document.createElement("span");
      etaBadge.className   = "job-eta-badge badge bg-info text-dark font-mono ms-1";
      etaBadge.title       = t("jobs.eta.badge_title") || "Durée estimée";
      etaBadge.textContent = _fmtEta(avg);
      card.querySelector(".card-header").insertBefore(etaBadge, card.querySelector(".job-cancel-btn"));
    }
  }

  card.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  card.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  card.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });

  const consoleBody = card.querySelector(".job-console-body");
  if (job.status === "pending" && consoleBody) {
    consoleBody.remove();
  } else if (consoleBody) {
    card.querySelector(".job-stdin")?.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); sendStdinEl(job.id, e.target); }
    });
  }

  applyStatus(card, job.status);
  updateTmdbBadge(card);
  document.getElementById("jobs-list").appendChild(clone);
  _syncJobLinesToDom(job);
  if (job.status === "running") showActiveConsoleDock(job.id);
  flushPendingOutput(job.id);
  if (!skipReorder) reorderJobCards();
}

// F: guard against concurrent calls (polling + socket reconnect can overlap)
let _syncInFlight = false;

async function syncJobsFromApi() {
  if (_syncInFlight) return;
  _syncInFlight = true;
  try {
    // Use ?fields=no_lines for polling: lines arrive via socket job_output events.
    // The initial load on connect uses full lines via the same call — the backend
    // only omits the `lines` key; _syncJobLinesToDom handles a missing key gracefully.
    const resp  = await fetch("/api/jobs?fields=no_lines");
    const jobs  = await resp.json();
    const apiIds = new Set(jobs.map(j => j.id));
    document.querySelectorAll(".job-card").forEach(card => {
      const id = card.dataset.jobId;
      if (id && !apiIds.has(id)) _removeJobCards([id]);
    });
    jobs.forEach(j => {
      const prev = activeJobs[j.id] || {};
      activeJobs[j.id] = { ...prev, ...j, tmdbId: j.tmdb_id || prev.tmdbId || 0 };
      const existingCard = document.querySelector(`.job-card[data-job-id="${j.id}"]`);
      if (socket.connected && j.status === "running" && existingCard) {
        applyStatus(existingCard, j.status);
      } else {
        renderJob(j, { skipReorder: true });
      }
    });
    const rid = _runningJobId();
    if (rid) showActiveConsoleDock(rid);

    // Close dock panels for jobs that finished while the page was closed or
    // the socket was disconnected (syncJobsFromApi is the fallback path).
    [..._dockXterms.keys()].forEach(jid => {
      const st = activeJobs[jid]?.status;
      if (st && st !== "running") hideActiveConsoleDock(jid);
    });

    reorderJobCards();
    refreshEmptyState();
    updateBadge();
    if (_hasActiveJobs()) startJobsLiveSync();
    else stopJobsLiveSync();
  } catch { /* ignore */ } finally {
    _syncInFlight = false;
  }
}

// ── Job actions ───────────────────────────────────────────────────────────
// Save duplicate_ask items (not selected) + all duplicate items to history (fire-and-forget)
function _saveIgnoredDuplicates(checkedIds) {
  const toIgnore = scanItems.filter(i =>
    i.status === "duplicate" ||
    (i.status === "duplicate_ask" && !checkedIds.has(i.id))
  );
  if (!toIgnore.length) return 0;
  fetch("/api/jobs/skip-duplicates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: toIgnore }),
  }).catch(() => {});
  return toIgnore.filter(i => i.status === "duplicate_ask").length;
}

// ── Verification modal (pre-start name check) ─────────────────────────────

/** Items selected for the current verification session. */
let _verifyItems = [];
/** Map of item id → original normalized name (to detect user edits). */
let _verifyOriginals = {};

// ── Verify modal helpers ───────────────────────────────────────────────────

function _verifyTokens(name) {
  const dashIdx = name.lastIndexOf("-");
  const body    = dashIdx > 0 ? name.slice(0, dashIdx) : name;
  const team    = dashIdx > 0 ? name.slice(dashIdx + 1) : "";
  return { tokens: body.toUpperCase().split(".").filter(Boolean), team };
}

function _verifyDiff(orig, norm) {
  const o = _verifyTokens(orig);
  const n = _verifyTokens(norm);
  const oSet = new Set(o.tokens);
  const nSet = new Set(n.tokens);
  return {
    removed: o.tokens.filter(t => !nSet.has(t)),
    added:   n.tokens.filter(t => !oSet.has(t)),
  };
}

function _verifySourceHtml(orig, norm) {
  if (orig === norm) return escHtml(orig);
  const { removed } = _verifyDiff(orig, norm);
  const removedUp = new Set(removed.map(t => t.toUpperCase()));
  const dashIdx   = orig.lastIndexOf("-");
  const body      = dashIdx > 0 ? orig.slice(0, dashIdx) : orig;
  const team      = dashIdx > 0 ? orig.slice(dashIdx) : "";
  const parts     = body.split(".");
  const rendered  = parts.map(tok => {
    if (removedUp.has(tok.toUpperCase())) {
      return `<del class="text-danger" style="opacity:.8">${escHtml(tok)}</del>`;
    }
    return escHtml(tok);
  }).join(`<span class="text-muted">.</span>`);
  return rendered + escHtml(team);
}

function _verifyDiffBadgesHtml(orig, norm) {
  if (orig === norm) return `<span class="badge bg-secondary text-white" style="font-size:.6rem">identique</span>`;
  const { removed, added } = _verifyDiff(orig, norm);
  const parts = [];
  removed.forEach(t => parts.push(`<span class="badge font-mono me-1" style="font-size:.6rem;background:#dc354580">-${escHtml(t)}</span>`));
  added.forEach(t   => parts.push(`<span class="badge font-mono me-1" style="font-size:.6rem;background:#19875480">+${escHtml(t)}</span>`));
  return parts.join("") || `<span class="badge bg-secondary" style="font-size:.6rem">modifié</span>`;
}

/**
 * Open the verification modal for the given items.
 * Fetches normalized names from /api/normalize and populates the table.
 */
async function _openVerifyModal(items) {
  _verifyItems   = items;
  _verifyOriginals = {};

  const modal     = bootstrap.Modal.getOrCreateInstance(document.getElementById("modal-verify"));
  const tbody     = document.getElementById("verify-tbody");
  const loading   = document.getElementById("verify-loading");
  const tableWrap = document.getElementById("verify-table-wrap");
  const confirmBtn = document.getElementById("btn-verify-confirm");
  const countEl   = document.getElementById("verify-count");

  // Show loading state
  tbody.innerHTML = "";
  loading.classList.remove("d-none");
  tableWrap.classList.add("d-none");
  confirmBtn.disabled = true;
  countEl.textContent = "";
  modal.show();

  // Sync any TMDB ID edits that may not have fired onchange yet (e.g. user typed
  // a new ID then immediately clicked "Lancer les jobs" without blurring first).
  // Also clear stale tmdb_origin when the ID was manually changed, so the server
  // fetches the correct English title for the new ID.
  items.forEach(it => {
    const input = document.querySelector(`.tmdb-id-input[data-item-id="${CSS.escape(it.id)}"]`);
    if (!input) return;
    const n = parseInt(input.value, 10);
    const liveId = (Number.isFinite(n) && n > 0) ? n : 0;
    if (liveId !== (it.tmdb_id || 0)) {
      _scanTmdbChanged(it.id, input.value); // commits to scanItems
      it.tmdb_id      = liveId;             // also update the local reference
      it.tmdb_origin  = "";                 // stale — let server re-fetch for new ID
    }
  });

  // Fetch normalized names
  let normalizedMap = {};
  let subItemsMap   = {};
  try {
    const resp = await fetch("/api/normalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    (data.items || []).forEach(n => {
      normalizedMap[n.id] = n.normalized || n.name;
      if (n.sub_items && n.sub_items.length) subItemsMap[n.id] = n.sub_items;
    });
  } catch (err) {
    console.warn("[verify] /api/normalize failed:", err);
    items.forEach(it => { normalizedMap[it.id] = it.name; });
  }

  // Store originals for changed-detection styling
  items.forEach(it => { _verifyOriginals[it.id] = normalizedMap[it.id] || it.name; });

  // Populate table — main rows + optional accordion sub-rows for packs
  const rowsHtml = [];
  items.forEach((item, idx) => {
    const origName     = item.name || "";
    const normName     = normalizedMap[item.id] || origName;
    const normSafe     = escHtml(normName);
    const safeId       = escHtml(item.id);
    const subs         = subItemsMap[item.id] || [];
    const hasAccordion = subs.length > 0;

    const _isSerieEp = item.type === "file" && (item.episode_num != null || /\bS\d{1,2}E\d{1,2}\b/i.test(item.name));
    const _typeLbl   = _isSerieEp ? t("jobs.type.episode") : _typeLabel(item.type);
    const _typeCls   = _isSerieEp ? "bg-success" : "bg-secondary";
    const typeBadge  = `<span class="badge ${_typeCls} font-mono ms-1" style="font-size:.6rem">${escHtml(_typeLbl)}</span>`;
    const srcHtml   = _verifySourceHtml(origName, normName);
    const diffHtml  = _verifyDiffBadgesHtml(origName, normName);

    const numCell = hasAccordion
      ? `<span class="verify-toggle-btn me-1" data-verify-toggle="${safeId}"
              onclick="_toggleVerifyAccordion('${safeId}')"
              title="${t('verify.accordion.hint') || 'Voir les uploads prévus'}"
              style="cursor:pointer;user-select:none">&#9654;</span>${idx + 1}`
      : String(idx + 1);

    rowsHtml.push(`<tr data-verify-id="${safeId}">
      <td class="text-muted small text-nowrap">${numCell}</td>
      <td class="font-mono small text-break" style="max-width:320px">
        ${srcHtml}${typeBadge}
      </td>
      <td style="min-width:260px">
        <div class="d-flex gap-1 align-items-center">
          <input type="text"
                 class="form-control form-control-sm font-mono verify-name-input flex-grow-1"
                 data-item-id="${safeId}"
                 data-original="${normSafe}"
                 value="${normSafe}"
                 oninput="_onVerifyInput(this)"
                 autocomplete="off"
                 spellcheck="false">
          <button type="button" class="btn btn-sm btn-outline-secondary py-0 px-1 flex-shrink-0"
                  title="Réinitialiser au nom normalisé"
                  onclick="_resetVerifyInput(this)" style="font-size:.75rem">&#8634;</button>
        </div>
        <div class="mt-1">${diffHtml}</div>
      </td>
    </tr>`);

    // Accordion sub-rows (hidden by default, editable)
    subs.forEach((sub, si) => {
      const isLast     = si === subs.length - 1;
      const connector  = isLast ? "└─" : "├─";
      const badgeCls   = sub.type === "season" ? "bg-info text-dark" : "bg-secondary";
      const subNormSafe   = escHtml(sub.name || "");
      const subSrcSafe    = escHtml(sub.source_name || "");
      rowsHtml.push(`<tr class="verify-sub-row d-none bg-body-secondary border-top border-bottom border-secondary-subtle" data-verify-parent="${safeId}">
        <td></td>
        <td class="small text-muted py-1 ps-1 text-nowrap">
          <span class="font-mono text-muted me-1" style="font-size:1rem;vertical-align:middle">${connector}</span><span class="badge ${badgeCls} font-mono" style="font-size:.65rem">${escHtml(sub.label)}</span>
        </td>
        <td class="py-1" style="min-width:260px">
          <div class="d-flex gap-1 align-items-center">
            <input type="text"
                   class="form-control form-control-sm font-mono verify-sub-input flex-grow-1"
                   data-source-name="${subSrcSafe}"
                   data-original="${subNormSafe}"
                   value="${subNormSafe}"
                   oninput="_onVerifyInput(this)"
                   autocomplete="off"
                   spellcheck="false">
            <button type="button" class="btn btn-sm btn-outline-secondary py-0 px-1 flex-shrink-0"
                    title="Réinitialiser"
                    onclick="_resetVerifyInput(this)" style="font-size:.75rem">&#8634;</button>
          </div>
        </td>
      </tr>`);
    });
  });
  tbody.innerHTML = rowsHtml.join("");

  // Count badge
  countEl.textContent = t("verify.count", items.length) || `${items.length} élément(s)`;

  // Show table
  loading.classList.add("d-none");
  tableWrap.classList.remove("d-none");
  confirmBtn.disabled = false;

  // Apply locale to new elements
  document.querySelectorAll("#modal-verify [data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
}

/** Reset an input (main or sub) to the auto-normalized value. */
function _resetVerifyInput(btn) {
  const input = btn.closest(".d-flex").querySelector("input[data-original]");
  if (!input) return;
  input.value = input.dataset.original || "";
  _onVerifyInput(input);
}

/** Highlight input if value differs from the auto-normalized original. */
function _onVerifyInput(input) {
  const original = input.dataset.original || "";
  const changed  = input.value.trim() !== original.trim();
  input.classList.toggle("is-valid",   changed);
  input.classList.toggle("border-warning", changed);
}

/** Toggle accordion sub-rows for an integrale / S01 pack row. */
function _toggleVerifyAccordion(id) {
  const subRows = document.querySelectorAll(`[data-verify-parent="${id}"]`);
  const btn     = document.querySelector(`[data-verify-toggle="${id}"]`);
  const isOpen  = subRows.length > 0 && !subRows[0].classList.contains("d-none");
  subRows.forEach(r => r.classList.toggle("d-none", isOpen));
  if (btn) btn.innerHTML = isOpen ? "&#9654;" : "&#9660;";
}

/**
 * Called when user clicks "Lancer les jobs" inside the verification modal.
 * Collects (possibly edited) names, stores as custom_name, then creates jobs.
 */
async function _confirmVerify() {
  const confirmBtn = document.getElementById("btn-verify-confirm");
  confirmBtn.disabled = true;

  // Collect edited names from main rows
  document.querySelectorAll(".verify-name-input").forEach(input => {
    const id   = input.dataset.itemId;
    const item = _verifyItems.find(i => i.id === id);
    if (!item) return;
    const edited = input.value.trim();
    if (edited && edited !== (input.dataset.original || "")) {
      item.custom_name = edited;
    } else {
      // Store the normalized name even when unchanged — useful for display in job transcript
      item.custom_name = edited || item.name;
    }
  });

  // Collect sub-item edited names (season packs within integrale / S01E01)
  // Stored as sub_custom_names: { source_name → custom_name } on the parent item.
  const subNamesByParent = {};
  document.querySelectorAll(".verify-sub-input").forEach(input => {
    const parentId   = input.closest("tr").dataset.verifyParent;
    const sourceName = input.dataset.sourceName;
    const edited     = input.value.trim();
    if (!parentId || !sourceName || !edited) return;
    if (!subNamesByParent[parentId]) subNamesByParent[parentId] = {};
    subNamesByParent[parentId][sourceName] = edited;
  });
  _verifyItems.forEach(item => {
    const subs = subNamesByParent[item.id];
    if (subs) item.sub_custom_names = subs;
  });

  // Hide modal
  bootstrap.Modal.getOrCreateInstance(document.getElementById("modal-verify")).hide();

  // Actually create the jobs
  await _doStartJobs(_verifyItems);
}

/**
 * Core job-creation logic (extracted from startJobs so it can be called
 * both directly and after the verification modal confirms).
 */
async function _doStartJobs(toSend) {
  document.getElementById("scan-preview").classList.add("d-none");
  document.getElementById("btn-start").disabled = true;
  scanItems = [];

  const resp = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: toSend }),
  });
  await ensureSocketConnected();
  const data    = await resp.json();
  const created = data.created || [];

  await new Promise(resolve => {
    let i = 0;
    function renderBatch() {
      const end = Math.min(i + 10, created.length);
      for (; i < end; i++) {
        const j = created[i];
        activeJobs[j.id] = { ...j, tmdbId: j.tmdb_id || 0 };
        renderJob(j, { skipReorder: true });
      }
      if (i < created.length) {
        (window.requestIdleCallback || setTimeout)(renderBatch, 0);
      } else {
        reorderJobCards();
        resolve();
      }
    }
    renderBatch();
  });
  await syncJobsFromApi();
  refreshEmptyState();
  updateBadge();
  startJobsLiveSync();
  const jobsTabEl = document.getElementById("tab-jobs-btn");
  if (jobsTabEl) bootstrap.Tab.getOrCreateInstance(jobsTabEl).show();
  showToast(t("jobs.toast.jobs_created", data.created?.length || 0), "success");
}

async function startJobs() {
  const checked = new Set(
    [...document.querySelectorAll(".item-chk:checked")].map(c => c.dataset.id)
  );
  const toSend = scanItems.filter(i =>
    (i.status === "pending" || i.status === "duplicate_ask") && checked.has(i.id)
  );

  // Save ignored duplicate_ask + all duplicate items BEFORE the early-return guard
  const ignoredAskCount = _saveIgnoredDuplicates(checked);
  if (ignoredAskCount > 0) {
    showToast(t("jobs.toast.dup_ask_ignored", ignoredAskCount), "secondary");
  }

  if (!toSend.length) {
    // Nothing to upload — if we only had duplicates to ignore, still clear the preview
    if (ignoredAskCount > 0 || scanItems.every(i => i.status === "duplicate")) {
      document.getElementById("scan-preview").classList.add("d-none");
      scanItems = [];
      updateScanSummary();
      _showScanActions(false);
    } else {
      showToast(t("jobs.toast.no_selected"), "warning");
    }
    return;
  }

  // L: bulk confirm when starting many items at once
  if (toSend.length >= _BULK_CONFIRM_THRESHOLD) {
    if (!confirm(t("jobs.confirm.bulk_start", toSend.length))) return;
  }

  const forcedDups = toSend.filter(i => i.status === "duplicate_ask");
  if (forcedDups.length && !confirm(t("jobs.confirm.upload_duplicates", forcedDups.length))) return;

  // ── Verification step: show modal before creating jobs ────────────────────
  await _openVerifyModal(toSend);
  // _confirmVerify() (modal button) will call _doStartJobs() when confirmed.
}

function cancelJob(btn) {
  const jid = btn.closest(".job-card").dataset.jobId;
  fetch(`/api/jobs/${jid}`, { method: "DELETE" });
}

async function retryJob(btn) {
  const jid  = btn.closest(".job-card").dataset.jobId;
  const r    = await fetch(`/api/jobs/${jid}/retry`, { method: "POST" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.created) {
    showToast(t("jobs.toast.retry_failed"), "danger");
    return;
  }
  const j = data.created;
  activeJobs[j.id] = { ...j, tmdbId: j.tmdb_id || 0 };
  renderJob(j);
  refreshEmptyState();
  updateBadge();
  showToast(t("jobs.toast.retry_created"), "success");
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

async function clearDoneJobs() {
  const localIds  = _collectTerminalJobIds();
  const histRemoved = scanItems.filter(i => i.status === "history").length;
  scanItems = scanItems.filter(i => i.status !== "history");

  // Persist updated scanItems so stale "history" rows don't re-appear on next
  // page load from the localStorage cache.
  if (typeof _currentScanFolder !== "undefined" && _currentScanFolder) {
    setScanCache(_currentScanFolder, scanItems);
  }

  hideScanHistory   = true;
  hideDuplicateZero = false;
  hideSkipped       = false;
  const chkHist = document.getElementById("chk-show-history");
  if (chkHist) chkHist.checked = false;
  const chkDup  = document.getElementById("chk-hide-zero-dup");
  if (chkDup)  chkDup.checked = false;
  const chkSkip = document.getElementById("chk-hide-skipped");
  if (chkSkip) chkSkip.checked = false;
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
  const n = Object.values(activeJobs).filter(j => ["pending", "running"].includes(j.status)).length;
  for (const id of ["badge-running", "badge-jobs-tab"]) {
    const el = document.getElementById(id);
    if (el) { el.textContent = n; el.classList.toggle("d-none", n === 0); }
  }
}

document.addEventListener("locale-changed", () => {
  document.querySelectorAll(".job-card").forEach(card => {
    const jid = card.dataset.jobId;
    if (activeJobs[jid]) applyStatus(card, activeJobs[jid].status);
    updateTmdbBadge(card);
  });
  refreshEmptyState();
});
