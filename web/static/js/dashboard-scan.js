/* ── Scan folder, duplicates, preview table ─────────────────────────────────
 *
 * Depends on globals defined in dashboard.js:
 *   scanItems, hideScanHistory, hideDuplicateZero, hideSkipped, socket
 * Depends on: t(), showToast(), _notify(), escHtml()
 * ────────────────────────────────────────────────────────────────────────── */
"use strict";

// ── Sort state + cancel state ─────────────────────────────────────────────────
let _scanSortCol     = "";
let _scanSortAsc     = true;
let _currentScanTaskId = null;

function _setCancelBtnVisible(show) {
  const btn = document.getElementById("btn-cancel-scan");
  if (!btn) return;
  btn.classList.toggle("d-none", !show);
  btn.disabled = false;
}

async function cancelScan() {
  if (!_currentScanTaskId) return;
  const btn = document.getElementById("btn-cancel-scan");
  if (btn) btn.disabled = true;
  try {
    await fetch(`/api/scan/${_currentScanTaskId}/cancel`, { method: "POST" });
  } catch {}
}

function sortScan(col) {
  if (_scanSortCol === col) { _scanSortAsc = !_scanSortAsc; }
  else { _scanSortCol = col; _scanSortAsc = col !== "size_gb"; } // size desc by default
  // Update indicators
  document.querySelectorAll("[id^='sort-'][id$='-ind']").forEach(el => el.textContent = "");
  const ind = document.getElementById(`sort-${col}-ind`);
  if (ind) ind.textContent = _scanSortAsc ? " ▲" : " ▼";
  renderPreview(scanItems);
}

// ── Scan progress bar ─────────────────────────────────────────────────────
const _SCAN_PHASES = {
  inventory:  { label: () => t("scan.progress.inventory"), pct: 15 },
  scanning:   { label: () => t("scan.progress.scanning"),  pct: 45 },
  tmdb:       { label: () => t("scan.progress.tmdb"),      pct: 70 },
  duplicates: { label: () => t("scan.progress.duplicates"),pct: 90 },
  done:       { label: () => t("scan.progress.done"),      pct: 100 },
};

function _setScanProgress(phase, detail, event) {
  const wrap  = document.getElementById("scan-progress-wrap");
  const bar   = document.getElementById("scan-progress-bar");
  const label = document.getElementById("scan-progress-label");
  const detEl = document.getElementById("scan-progress-detail");
  if (!wrap) return;
  if (phase === "done") {
    bar.style.width = "100%";
    bar.classList.remove("progress-bar-animated");
    setTimeout(() => {
      wrap.classList.add("d-none");
      bar.style.width = "0%";
      bar.classList.add("progress-bar-animated");
    }, 600);
    return;
  }
  wrap.classList.remove("d-none");
  const cfg = _SCAN_PHASES[phase] || _SCAN_PHASES.inventory;

  // For TMDB phase, compute a smooth % between 45 and 70 based on progress
  let pct = cfg.pct;
  if (phase === "tmdb" && event?.total > 0) {
    pct = Math.round(45 + 25 * (event.done / event.total));
  }
  bar.style.width = pct + "%";
  if (label) label.textContent = cfg.label();
  if (detEl) {
    if (phase === "tmdb" && event?.total > 0) {
      detEl.textContent = `${event.done}/${event.total}`;
    } else {
      detEl.textContent = detail || "";
    }
  }
}

// ── Scan actions ──────────────────────────────────────────────────────────
function _showScanActions(visible) {
  const el = document.getElementById("scan-actions");
  if (el) el.classList.toggle("d-none", !visible);
}

async function _doScan(extraParams = {}) {
  // E: use cached _currentScanFolder when available; only hit /api/settings on first call
  let src = _currentScanFolder || "";
  if (!src) {
    const settingsResp = await fetch("/api/settings");
    const { web } = await settingsResp.json();
    src = web?.source_folder || "";
  }
  if (!src) {
    showToast(t("jobs.toast.no_source"), "warning");
    return;
  }

  // B: disable scan button for the duration of the request
  const _btnScan = document.getElementById("btn-scan");
  if (_btnScan) _btnScan.disabled = true;

  showToast(t("jobs.toast.scanning"), "info");
  _currentScanFolder = src;
  clearScanCache(src);
  // Also clear server cache
  fetch("/api/scan/cache", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder: src, action: "clear" }),
  }).catch(() => {});
  _setScanProgress("inventory", "");
  try {
    const resp = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_folder: src,
        include_history: !!document.getElementById("chk-show-history")?.checked,
        ...extraParams,
      }),
    });
    const init = await resp.json();
    if (init.error) { showToast(init.error, "danger"); return; }
    // Async scan: wait for task to complete
    let data;
    if (init.task_id) {
      _currentScanTaskId = init.task_id;
      _setCancelBtnVisible(true);
      data = await _waitForScanResult(init.task_id);
    } else {
      // Legacy fallback (shouldn't happen)
      data = init;
    }
    _setScanProgress("done", "");
    if (data && data.__cancelled) {
      showToast(t("scan.toast.cancelled"), "secondary");
      return;
    }
    if (!data) { showToast(t("jobs.toast.scan_error") || "Erreur scan", "danger"); return; }
    if (data.error) { showToast(data.error, "danger"); return; }

    const skipped = data.skipped || [];
    if (skipped.length) showToast(t("scan.toast.skipped", skipped.length), "warning");

    const invBadge = document.getElementById("inventory-cache-badge");
    if (data.inventory_cached) {
      if (invBadge) {
        invBadge.textContent = t("scan.inventory.cached");
        invBadge.classList.remove("d-none");
        invBadge.title = t("scan.inventory.cached_hint");
      }
    } else {
      if (invBadge) invBadge.classList.add("d-none");
      const invAdded = data.inventory_added ?? 0;
      const invTotal = data.inventory_total ?? 0;
      if (invTotal > 0) {
        showToast(t("jobs.toast.inventory_done", invAdded, invTotal), "info");
      } else if (data.inventory_total !== undefined && invTotal === 0 && invAdded === 0) {
        showToast(t("jobs.toast.inventory_skipped"), "secondary");
      }
    }

    scanItems = data.items || [];

    // Persist scan cache in per-folder localStorage (24 h TTL) + server
    setScanCache(src, scanItems);
    fetch("/api/scan/cache", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: src, items: scanItems }),
    }).catch(() => {});

    hideScanHistory    = true;
    hideDuplicateZero  = false;
    hideSkipped        = false;
    _scanFilterText    = "";
    window._dupSkipTh  = data.duplicate_skip_threshold_pct ?? 0;

    const chkHist = document.getElementById("chk-show-history");
    if (chkHist) chkHist.checked = false;
    const chkDup  = document.getElementById("chk-hide-zero-dup");
    if (chkDup)  chkDup.checked = false;
    const chkSkip = document.getElementById("chk-hide-skipped");
    if (chkSkip) chkSkip.checked = false;
    const filterInp = document.getElementById("scan-filter-input");
    if (filterInp) filterInp.value = "";

    renderPreview(scanItems);
    updateScanSummary();
    _showScanActions(scanItems.length > 0);
    _updateIgnoreDupsBtn();

    if (data.rate_limited) {
      _startRateLimitCooldown(data.unchecked_paths || []);
    }

    const pending = visibleScanItems().filter(i => i.status === "pending").length;
    const dups    = scanItems.filter(i => i.status === "duplicate").length;
    const dupAsk  = visibleScanItems().filter(i => i.status === "duplicate_ask").length;
    if (dups)        showToast(t("jobs.toast.scan_done_dups", scanItems.length, pending, dups), "warning");
    else if (dupAsk) showToast(t("jobs.toast.scan_done_dup_ask", scanItems.length, pending, dupAsk), "warning");
    else             showToast(t("jobs.toast.scan_done", scanItems.length, pending), "success");

    const dupAskNotif = scanItems.filter(it => it.status === "duplicate_ask");
    if (dupAskNotif.length > 0) {
      _notify(t("notif.dup.title"), `${dupAskNotif.length} ${t("notif.dup.body")}`);
    }
  } catch (err) {
    _setScanProgress("done", "");
    showToast(t("jobs.toast.scan_error") || "Erreur réseau lors du scan", "danger");
  } finally {
    // B: always re-enable the scan button and hide cancel button
    if (_btnScan) _btnScan.disabled = false;
    _currentScanTaskId = null;
    _setCancelBtnVisible(false);
  }
}

function scanFolder()  { _doScan(); }

function rescanFolder() {
  _doScan({ force_inventory: true, include_history: true });
}

// ── Async scan result waiter (Feature 10) ─────────────────────────────────────
function _waitForScanResult(taskId) {
  return new Promise((resolve, reject) => {
    function onDone(ev) {
      if (ev.task_id !== taskId) return;
      socket.off("scan_done", onDone);
      clearInterval(pollTimer);
      if (ev.cancelled) { resolve({ __cancelled: true }); return; }
      fetch(`/api/scan/status/${taskId}`)
        .then(r => r.json())
        .then(d => {
          if (d.status === "done")      resolve(d.result);
          else if (d.status === "cancelled") resolve({ __cancelled: true });
          else reject(new Error(d.error || "Scan failed"));
        })
        .catch(reject);
    }
    socket.on("scan_done", onDone);
    // Fallback polling every 3s in case socket event is missed
    const pollTimer = setInterval(async () => {
      try {
        const r = await fetch(`/api/scan/status/${taskId}`);
        const d = await r.json();
        if (d.status === "done") {
          socket.off("scan_done", onDone);
          clearInterval(pollTimer);
          resolve(d.result);
        } else if (d.status === "cancelled") {
          socket.off("scan_done", onDone);
          clearInterval(pollTimer);
          resolve({ __cancelled: true });
        } else if (d.status === "error") {
          socket.off("scan_done", onDone);
          clearInterval(pollTimer);
          reject(new Error(d.error || "Scan failed"));
        } else if (d.status === "not_found") {
          socket.off("scan_done", onDone);
          clearInterval(pollTimer);
          reject(new Error("task not found"));
        }
      } catch {}
    }, 3000);
    // Timeout after 5 min
    setTimeout(() => {
      socket.off("scan_done", onDone);
      clearInterval(pollTimer);
      reject(new Error("timeout"));
    }, 300_000);
  });
}

async function recheckTmdb() {
  if (!scanItems.length) return;
  const btn = document.getElementById("btn-recheck-tmdb");
  if (btn) btn.disabled = true;
  _setScanProgress("tmdb", "", { phase: "tmdb", done: 0, total: 0 });
  try {
    const resp = await fetch("/api/scan/tmdb", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: scanItems, force: true }),
    });
    const data = await resp.json();
    _setScanProgress("done", "");
    if (data.error) { showToast(data.error, "danger"); return; }
    const byId = Object.fromEntries((data.items || []).map(i => [i.id, i]));
    // Merge tmdb results back into scanItems.
    // Locked items (user-edited ID): keep their tmdb_id, update title/year/origin from server.
    // Other items: full update from server.
    scanItems = scanItems.map(i => {
      const updated = byId[i.id];
      if (!updated) return i;
      // Propagate season_num from the server when it was just resolved
      // (absolute-episode anime OR text-based "Second Season" detection).
      const absEpFix = updated.season_num != null && !i.season_num
        ? { season_num: updated.season_num,
            ...(i.absolute_episode && updated.episode_num != null
                ? { episode_num: updated.episode_num } : {}) }
        : {};
      if (i.tmdb_id_locked) {
        // Server did an ID lookup for our locked ID — use fresh metadata, keep our ID.
        return {
          ...i,
          tmdb_title:  updated.tmdb_title  || i.tmdb_title  || "",
          tmdb_year:   updated.tmdb_year   || i.tmdb_year   || "",
          tmdb_origin: updated.tmdb_origin || i.tmdb_origin || "",
          ...absEpFix,
        };
      }
      return {
        ...i,
        tmdb_id:     updated.tmdb_id     || 0,
        tmdb_title:  updated.tmdb_title  || "",
        tmdb_year:   updated.tmdb_year   || i.tmdb_year   || "",
        tmdb_origin: updated.tmdb_origin || i.tmdb_origin || "",
        ...absEpFix,
      };
    });
    if (_currentScanFolder) setScanCache(_currentScanFolder, scanItems);
    renderPreview(scanItems);
    const found = scanItems.filter(i => i.tmdb_id > 0).length;
    showToast(t("jobs.toast.tmdb_done", found, scanItems.length), "success");

    // Phase 4: re-check duplicates using the (possibly updated) TMDB IDs.
    // Non-critical — TMDB metadata was already applied above.
    try {
      const resp2 = await fetch("/api/scan/recheck-tmdb-dup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: scanItems }),
      });
      if (resp2.ok) {
        const d2 = await resp2.json();
        if (!d2.error && Array.isArray(d2.items)) {
          const byId2 = Object.fromEntries(d2.items.map(i => [i.id, i]));
          scanItems = scanItems.map(i => {
            const upd = byId2[i.id];
            if (!upd) return i;
            return {
              ...i,
              status:           upd.status           ?? i.status,
              duplicate:        upd.duplicate        ?? i.duplicate,
              duplicate_source: upd.duplicate_source ?? i.duplicate_source,
              gemini_releases:  upd.gemini_releases  ?? i.gemini_releases,
            };
          });
          if (_currentScanFolder) setScanCache(_currentScanFolder, scanItems);
          renderPreview(scanItems);
          updateScanSummary();
        }
      }
    } catch { /* non-bloquant */ }
  } catch {
    _setScanProgress("done", "");
    showToast(t("jobs.toast.tmdb_error"), "danger");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function recheckDuplicates() {
  if (!scanItems.length) return;
  const btn = document.getElementById("btn-recheck-dup");
  if (btn) btn.disabled = true;
  let _rateLimited = false;
  _setScanProgress("duplicates", "");
  try {
    const resp = await fetch("/api/scan/duplicates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: scanItems }),
    });
    const data = await resp.json();
    _setScanProgress("done", "");
    if (data.error) { showToast(data.error, "danger"); return; }
    const byId = Object.fromEntries((data.items || []).map(i => [i.id, i]));
    scanItems = scanItems.map(i => byId[i.id] || i);
    window._dupSkipTh = data.duplicate_skip_threshold_pct ?? window._dupSkipTh ?? 0;
    // Persist updated duplicate results so they survive a page reload
    if (_currentScanFolder) setScanCache(_currentScanFolder, scanItems);
    renderPreview(scanItems);
    updateScanSummary();
    if (data.rate_limited) {
      _rateLimited = true;
      _startRateLimitCooldown(data.unchecked_paths || []);
      return;
    }
    const dups   = scanItems.filter(i => i.status === "duplicate").length;
    const dupAsk = visibleScanItems().filter(i => i.status === "duplicate_ask").length;
    showToast(
      dups + dupAsk > 0
        ? t("jobs.toast.scan_done_dups", scanItems.length,
            visibleScanItems().filter(i => i.status === "pending").length, dups + dupAsk)
        : t("jobs.toast.recheck_dup_done"),
      dups + dupAsk > 0 ? "warning" : "success"
    );
    const dupAskNotif = scanItems.filter(it => it.status === "duplicate_ask");
    if (dupAskNotif.length > 0) {
      _notify(t("notif.dup.title"), `${dupAskNotif.length} ${t("notif.dup.body")}`);
    }
  } catch {
    _setScanProgress("done", "");
    showToast(t("jobs.toast.dup_error") || "Erreur réseau lors de la vérification", "danger");
  } finally {
    // _startRateLimitCooldown manages btn.disabled itself via a timer — don't re-enable here
    if (btn && !_rateLimited) btn.disabled = false;
  }
}

function _startRateLimitCooldown(uncheckedPaths) {
  const btn   = document.getElementById("btn-recheck-dup");
  const label = document.getElementById("btn-recheck-dup-label");
  if (!btn) return;

  const names  = uncheckedPaths.map(p => p.split(/[\\/]/).pop()).filter(Boolean);
  const detail = names.length ? " — " + names.join(", ") : "";
  showToast(t("jobs.dup.rate_limited", names.length) + detail, "danger");

  let remaining = 60;
  btn.disabled  = true;
  const tick = () => {
    if (label) label.textContent = t("jobs.dup.cooldown", remaining);
  };
  tick();
  const timer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(timer);
      btn.disabled = false;
      if (label) {
        label.setAttribute("data-i18n", "jobs.recheck_dup_btn");
        label.textContent = t("jobs.recheck_dup_btn");
      }
    } else {
      tick();
    }
  }, 1000);
}

// ── Preview table ─────────────────────────────────────────────────────────
function _typeLabel(type) {
  return t({
    integrale:  "jobs.type.integrale",
    season:     "jobs.type.season",
    file:       "jobs.type.file",
    unknown:    "jobs.type.unknown",
    collection: "jobs.type.collection",
  }[type] || "jobs.type.unknown");
}

function _fmtGb(n) {
  if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
  return `${Number(n).toFixed(2)} Go`;
}

function _dupMatchLine(label, deltaPct, geminiGb) {
  return `${escHtml(label)} <span class="text-warning">Δ ${deltaPct}%</span>`
    + ` <span class="text-muted">·</span> ${t("jobs.duplicate.gemini_size", _fmtGb(geminiGb))}`;
}

function _dupDeltasHtml(duplicate) {
  if (!duplicate) return "";
  const localLine = duplicate.local_gb != null
    ? `<div class="small font-mono mb-1 text-info">${t("jobs.duplicate.local_size", _fmtGb(duplicate.local_gb))}</div>`
    : "";
  const _nameLine = name => name
    ? `<div class="text-muted lh-1 mb-1" style="font-size:.7rem;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(name)}">${escHtml(name)}</div>`
    : "";

  const byId = duplicate.deltas_by_id;
  if (Array.isArray(byId) && byId.length) {
    const parts = byId.map(row =>
      `<div>${_dupMatchLine(row.label, row.delta_pct, row.size_gb)}${_nameLine(row.name)}</div>`
    );
    return localLine + `<div class="small font-mono lh-sm">${parts.join("")}</div>`;
  }
  const matches = duplicate.matches;
  if (Array.isArray(matches) && matches.length) {
    const parts = matches.map(m => {
      const label = m.tmdb_id ? `TMDB ${m.tmdb_id}`
        : m.igdb_id ? `IGDB ${m.igdb_id}` : `#${m.tracker_id ?? "?"}`;
      return `<div>${_dupMatchLine(label, m.delta_pct, m.size_gb)}${_nameLine(m.name)}</div>`;
    });
    return localLine + `<div class="small font-mono lh-sm">${parts.join("")}</div>`;
  }
  if (duplicate.delta_pct != null) {
    const id    = duplicate.tmdb_id ? `TMDB ${duplicate.tmdb_id}`
      : duplicate.igdb_id ? `IGDB ${duplicate.igdb_id}` : "";
    const label    = id || duplicate.name || "?";
    const showName = id && duplicate.name ? _nameLine(duplicate.name) : "";
    return localLine
      + `<div class="small font-mono">${_dupMatchLine(label, duplicate.delta_pct, duplicate.size_gb)}${showName}</div>`;
  }
  return localLine;
}

function _duplicateTitle(duplicate) {
  if (!duplicate) return "";
  const local = duplicate.local_gb != null ? t("jobs.duplicate.local_size", _fmtGb(duplicate.local_gb)) : "";
  const byId  = duplicate.deltas_by_id;
  if (Array.isArray(byId) && byId.length) {
    const ids = byId.map(r =>
      `${r.label} Δ${r.delta_pct}% Gemini ${_fmtGb(r.size_gb)}${r.name ? " — " + r.name : ""}`
    ).join(" · ");
    return local ? `${local} — ${ids}` : ids;
  }
  if (duplicate.delta_pct != null) {
    const id      = duplicate.tmdb_id ? `TMDB ${duplicate.tmdb_id}` : duplicate.igdb_id ? `IGDB ${duplicate.igdb_id}` : "";
    const namePart = (id && duplicate.name) ? ` — ${duplicate.name}` : "";
    const bit      = `${id ? id + " " : ""}Δ${duplicate.delta_pct}% Gemini ${_fmtGb(duplicate.size_gb)}${namePart}`;
    return local ? `${local} — ${bit}` : bit;
  }
  return local || duplicate.name || "";
}

// ── Gemini releases column ────────────────────────────────────────────────────

/** Compact label for a resolution string from guessit (e.g. "1080p" → "1080p", "4K" → "4K"). */
function _resLabel(res) {
  if (!res) return "";
  // Normalize common guessit output: "2160p" → "4K", keep others as-is
  if (res === "2160p" || res === "4K UHD") return "4K";
  return res;
}

/** Compact label for a source string from guessit. */
function _srcLabel(src) {
  if (!src) return "";
  const map = {
    "Blu-ray": "BluRay", "BluRay": "BluRay",
    "WEB-DL": "WEB-DL", "WEBRip": "WEBRip", "WEB": "WEB",
    "HDTV": "HDTV", "DVD": "DVD", "UHD Blu-ray": "UHD BluRay",
  };
  return map[src] || src;
}

/** Compact label for a video_codec string from guessit. */
function _codecLabel(codec) {
  if (!codec) return "";
  const map = {
    "H.264": "H264", "H.265": "H265",
    "x264": "x264", "x265": "x265",
    "Xvid": "Xvid", "HEVC": "H265",
    "AVC": "H264", "VP9": "VP9",
  };
  return map[codec] || codec;
}

/**
 * Build HTML for the Gemini releases column.
 *
 * For each release in item.gemini_releases, shows a badge with resolution + tag.
 * Highlights in danger when BOTH the item's tag AND resolution match the release
 * (indicating the same group released the same quality — likely an exact duplicate).
 * Warning colour when only resolution matches.
 */
function _geminiReleasesHtml(item) {
  const releases = item.gemini_releases;
  if (!Array.isArray(releases) || !releases.length) {
    return item.tmdb_id ? '<span class="text-muted small">—</span>' : '';
  }
  const itemTag = (item.tag || "").toUpperCase();
  const itemRes = (item.resolution || "").toLowerCase();
  const itemEp  = item.episode_num != null ? Number(item.episode_num) : null;
  const itemSn  = item.season_num  != null ? Number(item.season_num)  : null;

  // Keep only releases that match the local episode, season and resolution.
  // Releases that lack a parseable episode/season/resolution when the local
  // item has one are excluded — they cannot be confirmed as the same content.
  const filtered = releases.filter(r => {
    // Season-pack guard: local is a pack (season known, no episode) but
    // tracker release is an individual episode → cannot be the same content.
    if (itemEp === null && itemSn !== null && r.episode != null) return false;
    if (itemEp !== null) {
      const rEp = r.episode != null ? Number(r.episode) : null;
      if (rEp === null || rEp !== itemEp) return false;
    }
    if (itemSn !== null) {
      const rSn = r.season != null ? Number(r.season) : null;
      if (rSn === null || rSn !== itemSn) return false;
    }
    if (itemRes) {
      const rRes = (r.resolution || "").toLowerCase();
      if (!rRes || rRes !== itemRes) return false;
    }
    return true;
  });

  if (!filtered.length) {
    return item.tmdb_id ? '<span class="text-muted small">—</span>' : '';
  }

  return filtered.slice(0, 6).map(r => {
    const rTag = (r.tag || "").toUpperCase();
    const rRes = (r.resolution || "").toLowerCase();
    const tagMatch = itemTag && rTag && rTag === itemTag;
    const resMatch = itemRes && rRes && rRes === itemRes;
    let cls;
    if (tagMatch && resMatch) {
      cls = "bg-danger";
    } else if (resMatch) {
      cls = "bg-warning text-dark";
    } else if (tagMatch) {
      cls = "bg-info text-dark";
    } else {
      cls = "bg-secondary bg-opacity-50";
    }
    const parts = [_resLabel(r.resolution), r.tag].filter(Boolean);
    const label = parts.length ? escHtml(parts.join("·")) : "?";
    return `<span class="badge ${cls} me-1 mb-1 font-mono" style="font-size:.65rem"
                  title="${escHtml(r.name)}">${label}</span>`;
  }).join("") + (filtered.length > 6
    ? `<span class="text-muted small">+${filtered.length - 6}</span>` : "");
}

// Current text filter (lowercase) applied on top of checkbox filters
let _scanFilterText = "";

// Current folder — set when a scan starts; used for per-folder filter prefs (#14)
let _currentScanFolder = "";

function filterScanPreview(value) {
  _scanFilterText = (value || "").toLowerCase().trim();
  renderPreview(scanItems);
  updateScanSummary();
}

function visibleScanItems() {
  let items = hideScanHistory ? scanItems.filter(i => i.status !== "history") : scanItems;
  if (hideDuplicateZero) {
    items = items.filter(i => {
      if (i.status !== "duplicate") return true;
      const delta = i.duplicate?.delta_pct ?? i.duplicate?.deltas_by_id?.[0]?.delta_pct;
      return delta == null || delta > 0;
    });
  }
  if (hideSkipped) {
    items = items.filter(i => i.status !== "skip");
  }
  if (_scanFilterText) {
    items = items.filter(i =>
      (i.name  || "").toLowerCase().includes(_scanFilterText) ||
      (i.tag   || "").toLowerCase().includes(_scanFilterText) ||
      (i.type  || "").toLowerCase().includes(_scanFilterText)
    );
  }
  return items;
}

// ── Per-folder filter preferences (#14) ──────────────────────────────────────
const _FILTER_PREFS_GLOBAL_KEY = "u3d-scan-filter-prefs";

function _filterPrefsKey(folder) {
  try {
    return folder
      ? "u3d-scan-filter-prefs-" + btoa(encodeURIComponent(folder))
      : _FILTER_PREFS_GLOBAL_KEY;
  } catch { return _FILTER_PREFS_GLOBAL_KEY; }
}

function _saveFilterPrefs() {
  try {
    const data = JSON.stringify({ hideDuplicateZero, hideSkipped });
    localStorage.setItem(_filterPrefsKey(_currentScanFolder), data);
    // Also update global fallback
    localStorage.setItem(_FILTER_PREFS_GLOBAL_KEY, data);
  } catch {}
}

function _restoreFilterPrefs() {
  try {
    // Try folder-specific key first, fall back to global
    const raw = localStorage.getItem(_filterPrefsKey(_currentScanFolder))
              || localStorage.getItem(_FILTER_PREFS_GLOBAL_KEY);
    if (!raw) return;
    const p = JSON.parse(raw);
    if (p.hideDuplicateZero != null) {
      hideDuplicateZero = !!p.hideDuplicateZero;
      const el = document.getElementById("chk-hide-zero-dup");
      if (el) el.checked = hideDuplicateZero;
    }
    if (p.hideSkipped != null) {
      hideSkipped = !!p.hideSkipped;
      const el = document.getElementById("chk-hide-skipped");
      if (el) el.checked = hideSkipped;
    }
  } catch {}
}

function toggleHideZeroDup(chk) {
  hideDuplicateZero = chk.checked;
  _saveFilterPrefs();
  renderPreview(scanItems);
  updateScanSummary();
}

function toggleHideSkipped(chk) {
  hideSkipped = chk.checked;
  _saveFilterPrefs();
  renderPreview(scanItems);
  updateScanSummary();
}

function updateScanSummary() {
  const el       = document.getElementById("scan-summary");
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
  const invN    = scanItems.filter(i => i.status === "inventory").length;
  let text = t("jobs.summary", pending, visible.length - pending, visible.length);
  if (hideScanHistory && histN > 0) {
    text += ` · ${t("jobs.summary_history_hidden", histN)}`;
  }
  if (invN > 0) {
    text += ` · ${t("jobs.summary_inventory", invN)}`;
  }
  el.textContent = text;
  if (btnStart) btnStart.disabled = pending + dupAsk === 0;
}

function toggleScanHistory(chk) {
  hideScanHistory = !chk.checked;
  renderPreview(scanItems);
  updateScanSummary();
}

const _LAZY_CHUNK = 60;   // rows per animation frame (#12)
let   _lazyTimer  = null; // handle for any in-progress lazy render

function _cancelLazy() {
  if (_lazyTimer != null) { cancelAnimationFrame(_lazyTimer); _lazyTimer = null; }
}

function renderPreview(items) {
  _cancelLazy();
  const body    = document.getElementById("preview-body");
  const preview = document.getElementById("scan-preview");
  if (!body || !preview) return;

  if (!items.length) {
    body.innerHTML = "";
    preview.classList.add("d-none");
    return;
  }

  const rows = visibleScanItems();
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="11" class="text-center text-muted py-3">${
      escHtml(t("jobs.preview_empty_visible"))
    }</td></tr>`;
    preview.classList.remove("d-none");
    return;
  }

  // ── Lazy-load: render the first chunk immediately, rest via rAF (#12) ──────
  if (rows.length > _LAZY_CHUNK) {
    body.innerHTML = _buildRows(rows.slice(0, _LAZY_CHUNK));
    preview.classList.remove("d-none");
    let offset = _LAZY_CHUNK;
    const renderChunk = () => {
      if (offset >= rows.length) { _lazyTimer = null; return; }
      body.insertAdjacentHTML("beforeend", _buildRows(rows.slice(offset, offset + _LAZY_CHUNK)));
      offset += _LAZY_CHUNK;
      _lazyTimer = requestAnimationFrame(renderChunk);
    };
    _lazyTimer = requestAnimationFrame(renderChunk);
    return;
  }

  body.innerHTML = _buildRows(rows);
  preview.classList.remove("d-none");
}

function _buildRows(rows) {
  // Apply sort
  let sorted = [...rows];
  if (_scanSortCol) {
    sorted.sort((a, b) => {
      let va = a[_scanSortCol] ?? "";
      let vb = b[_scanSortCol] ?? "";
      if (typeof va === "number" && typeof vb === "number") {
        return _scanSortAsc ? va - vb : vb - va;
      }
      va = String(va).toLowerCase(); vb = String(vb).toLowerCase();
      return _scanSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    });
  }
  rows = sorted;
  return rows.map(item => {
    const isSerieEp = (item.type === "file" || item.type === "season") && (
      item.episode_num != null || /\bS\d{1,2}E\d{1,2}\b/i.test(item.name)
    );
    const typeLabel = isSerieEp ? t("jobs.type.episode") : _typeLabel(item.type);
    const typeCls   = isSerieEp ? "bg-success" : ({
      integrale:  "bg-info text-dark",
      season:     "bg-success",
      file:       "bg-warning text-dark",
      unknown:    "bg-secondary",
      collection: "bg-primary",
    }[item.type] || "bg-secondary");
    const tagBadge  = item.tag
      ? `<span class="badge font-mono ${item.tag_valid ? "bg-success" : "bg-warning text-dark"}">${escHtml(item.tag)}</span>`
      : `<span class="text-muted">—</span>`;
    const dupTitle     = _duplicateTitle(item.duplicate);
    const statusBadge  = item.status === "pending"
      ? `<span class="badge bg-primary">${t("jobs.badge.to_upload")}</span>`
      : item.status === "duplicate"
      ? `<span class="badge bg-danger" title="${escHtml(dupTitle || item.duplicate?.name || "")}">${t("jobs.badge.duplicate")}</span>`
      : item.status === "duplicate_ask"
      ? `<span class="badge bg-warning text-dark" title="${escHtml(dupTitle || item.duplicate?.name || "")}">${t("jobs.badge.duplicate_ask")}</span>`
      : item.status === "inventory"
      ? (() => {
          let _h = `<span class="badge bg-success">${t("jobs.badge.inventory")}</span>`;
          if (item.tracker_url)
            _h += ` <a href="${escHtml(item.tracker_url)}" target="_blank" rel="noopener"
                        class="btn btn-sm btn-outline-success py-0 px-1 ms-1"
                        title="${escHtml(t("jobs.inv.open_tracker"))}"><i class="bi bi-box-arrow-up-right"></i></a>`;
          if (item.job_id)
            _h += ` <a href="/history?search=${encodeURIComponent(item.name)}" target="_blank"
                        class="btn btn-sm btn-outline-secondary py-0 px-1 ms-1"
                        title="${escHtml(t("jobs.inv.open_history"))}"><i class="bi bi-clock-history"></i></a>`;
          return _h;
        })()
      : item.status === "history"
      ? `<span class="badge bg-secondary">${t("jobs.badge.history")}</span>`
      : `<span class="badge bg-secondary">${t("jobs.badge.ignored")}</span>`;
    const dupDeltas = (item.status === "duplicate" || item.status === "duplicate_ask")
      ? _dupDeltasHtml(item.duplicate) : "";
    const selectable = item.status === "pending" || item.status === "duplicate_ask";
    const disabled   = selectable ? "" : "disabled";
    // tag_valid===false → item sélectionnable mais NON coché par défaut (avertissement tag)
    const checked    = (item.status === "pending" && item.tag_valid !== false) ? "checked" : "";
    const rowCls     = item.status === "duplicate_ask" ? "" : (selectable ? "" : "opacity-50");
    const dup        = item.duplicate;
    const dupHint    = (item.status === "duplicate" || item.status === "duplicate_ask") && dup
      ? ` title="${escHtml(t("jobs.duplicate.hint",
          dup.name || "",
          dup.delta_pct ?? 0,
          dup.size_th ?? 10,
          _fmtGb(dup.local_gb),
          _fmtGb(dup.size_gb)
        ) + (dupTitle ? " — " + dupTitle : ""))}"` : "";
    const tmdbVal   = item.tmdb_id ? String(item.tmdb_id) : "";
    const tmdbTitle = item.tmdb_title ? ` title="${escHtml(item.tmdb_title)}"` : "";

    // Media info columns
    const resBadge  = item.resolution
      ? `<span class="badge bg-dark border font-mono" style="font-size:.65rem">${escHtml(_resLabel(item.resolution))}</span>`
      : `<span class="text-muted small">—</span>`;
    const srcBadge  = item.source_type
      ? `<span class="badge bg-dark border font-mono" style="font-size:.65rem">${escHtml(_srcLabel(item.source_type))}</span>`
      : `<span class="text-muted small">—</span>`;
    const codecBadge = item.encoding
      ? `<span class="badge bg-dark border font-mono" style="font-size:.65rem">${escHtml(_codecLabel(item.encoding))}</span>`
      : `<span class="text-muted small">—</span>`;
    const geminiReleases = _geminiReleasesHtml(item);
    const sizeGb = item.size_gb;
    const sizeHtml = sizeGb > 0
      ? `<td class="small text-muted font-mono">${sizeGb < 1 ? (sizeGb * 1024).toFixed(0) + " Mo" : sizeGb.toFixed(2) + " Go"}</td>`
      : `<td class="text-muted small">—</td>`;

    return `<tr class="${rowCls}"${dupHint}>
      <td><input type="checkbox" class="item-chk scan-checkbox" data-id="${escHtml(item.id)}" data-item-id="${escHtml(item.id)}"
                 ${checked} ${disabled}></td>
      <td class="font-mono small" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          title="${escHtml(item.name)}">${escHtml(item.name)}</td>
      <td>${item.type === "unknown"
        ? `<select class="form-select form-select-sm font-mono p-0 px-1" style="width:7.5rem;font-size:.75rem"
                   data-item-id="${escHtml(item.id)}"
                   onchange="_scanTypeChanged(this.dataset.itemId, this.value)">
             <option value="unknown" selected>${t("jobs.type.unknown")}</option>
             <option value="file">${t("jobs.type.file")}</option>
             <option value="season">${t("jobs.type.season")}</option>
             <option value="integrale">${t("jobs.type.integrale")}</option>
             <option value="collection">${t("jobs.type.collection")}</option>
           </select>`
        : `<span class="badge ${typeCls}">${typeLabel}</span>`
      }</td>
      <td>${tagBadge}</td>
      <td class="text-center small font-mono">${item.season_num  != null ? item.season_num  : '<span class="text-muted">—</span>'}</td>
      <td class="text-center small font-mono">${item.episode_num != null ? item.episode_num : '<span class="text-muted">—</span>'}</td>
      <td class="text-center small font-mono">${item.tmdb_year   ? escHtml(String(item.tmdb_year)) : '<span class="text-muted">—</span>'}</td>
      <td class="text-center small font-mono">${item.tmdb_origin ? escHtml(item.tmdb_origin) : '<span class="text-muted">—</span>'}</td>
      <td>
        <input type="number" min="0" step="1"
               class="form-control form-control-sm font-mono p-0 px-1 tmdb-id-input"
               style="width:6.5rem"
               value="${escHtml(tmdbVal)}"
               placeholder="TMDB ID"${tmdbTitle}
               data-item-id="${escHtml(item.id)}"
               onchange="_scanTmdbChanged(this.dataset.itemId, this.value)">
      </td>
      <td class="text-center">${(() => {
        if (!item.tmdb_id) return '<span class="text-muted">—</span>';
        return `<button type="button"
                        class="btn btn-sm btn-outline-info py-0 px-1" style="font-size:.7rem"
                        title="Voir sur TMDB"
                        onclick="_openTmdbPage('${escHtml(item.id)}')">
                  <i class="bi bi-image"></i></button>`;
      })()}</td>
      <td>${resBadge}</td>
      <td>${srcBadge}</td>
      <td>${codecBadge}</td>
      <td style="max-width:12rem">${geminiReleases}</td>
      ${sizeHtml}
      <td>${statusBadge}${dupDeltas}</td>
    </tr>`;
  }).join("");
}

function toggleAll(master) {
  document.querySelectorAll(".item-chk:not([disabled])").forEach(c => c.checked = master.checked);
}

/** Update scanItems when user edits a TMDB ID cell manually. */
function _scanTmdbChanged(itemId, rawValue) {
  const item = scanItems.find(i => i.id === itemId);
  if (!item) return;
  const n = parseInt(rawValue, 10);
  item.tmdb_id     = (Number.isFinite(n) && n > 0) ? n : 0;
  item.tmdb_title  = ""; // clear old title on manual override
  item.tmdb_id_locked = item.tmdb_id > 0; // protect from recheckTmdb overwrite
  // Persist in scan cache so the edit survives a re-render
  if (_currentScanFolder) setScanCache(_currentScanFolder, scanItems);
}

/** Update scanItems when user manually overrides the type of an unknown item. */
function _scanTypeChanged(itemId, newType) {
  const item = scanItems.find(i => i.id === itemId);
  if (!item || newType === "unknown") return;
  item.type    = newType;
  item.status  = "pending";
  item.tag_valid = true;
  if (_currentScanFolder) setScanCache(_currentScanFolder, scanItems);
  renderPreview(scanItems);
}

/** Open the TMDB page for the current (possibly unsaved) ID in the row's input. */
function _openTmdbPage(itemId) {
  // Read the live input value — may differ from item.tmdb_id if onchange hasn't fired yet
  const input = document.querySelector(`.tmdb-id-input[data-item-id="${CSS.escape(itemId)}"]`);
  const rawId = input ? input.value.trim() : "";
  const id    = parseInt(rawId, 10);
  if (!Number.isFinite(id) || id <= 0) return;
  // Commit the value to scanItems immediately (in case onchange hasn't fired)
  _scanTmdbChanged(itemId, rawId);
  const item = scanItems.find(i => i.id === itemId);
  if (!item) return;
  const isTv = item.type === "integrale" || item.type === "season"
    || (item.type === "file" && /\bS\d{1,2}E\d{1,2}\b/i.test(item.name));
  window.open(`https://www.themoviedb.org/${isTv ? "tv" : "movie"}/${id}`, "_blank", "noopener");
}

// ── Ignore duplicates button ──────────────────────────────────────────────────

/** Show/hide the "Ignorer les doublons" button depending on whether there are any dup items. */
function _updateIgnoreDupsBtn() {
  const btn = document.getElementById("btn-ignore-dups");
  if (!btn) return;
  const hasDups = scanItems.some(i => i.status === "duplicate" || i.status === "duplicate_ask");
  btn.classList.toggle("d-none", !hasDups);
}

// ── Smart selection (Feature 2) ───────────────────────────────────────────────

function _updateStartBtn() {
  // Trigger updateScanSummary which also updates btn-start
  updateScanSummary();
}

function _selectByStatus(status) {
  document.querySelectorAll(".scan-checkbox").forEach(cb => {
    const id   = cb.dataset.itemId;
    const item = scanItems.find(i => i.id === id);
    if (item) cb.checked = item.status === status && !["history", "inventory", "skip"].includes(item.status);
  });
  _updateStartBtn();
}

function _selectByType(type) {
  document.querySelectorAll(".scan-checkbox").forEach(cb => {
    const id   = cb.dataset.itemId;
    const item = scanItems.find(i => i.id === id);
    if (item) cb.checked = item.type === type && !["history", "inventory", "skip"].includes(item.status);
  });
  _updateStartBtn();
}

function _invertSelection() {
  document.querySelectorAll(".scan-checkbox").forEach(cb => {
    const id   = cb.dataset.itemId;
    const item = scanItems.find(i => i.id === id);
    if (item && !["history", "inventory", "skip"].includes(item.status)) cb.checked = !cb.checked;
  });
  _updateStartBtn();
}

/** Save all checked scan items to history without launching any upload. */
async function ignoreSelectedReleases() {
  const selected = scanItems.filter(i => {
    if (["history", "skip"].includes(i.status)) return false;
    const cb = document.querySelector(`.scan-checkbox[data-item-id="${CSS.escape(i.id)}"]`);
    return cb && cb.checked;
  });
  if (!selected.length) {
    showToast(t("jobs.toast.no_selected"), "warning");
    return;
  }
  if (!confirm(t("jobs.confirm.ignore_selected", selected.length))) return;

  const btn = document.getElementById("btn-ignore-selected");
  if (btn) btn.disabled = true;
  try {
    await fetch("/api/jobs/ignore-selected", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: selected }),
    });
  } catch { /* ignore network errors */ }
  finally {
    if (btn) btn.disabled = false;
  }
  const ids = new Set(selected.map(i => i.id));
  scanItems = scanItems.filter(i => !ids.has(i.id));
  if (_currentScanFolder) setScanCache(_currentScanFolder, scanItems);
  renderPreview(scanItems);
  updateScanSummary();
  _updateIgnoreDupsBtn();
  _showScanActions(scanItems.length > 0);
  showToast(t("jobs.toast.ignore_selected", selected.length), "secondary");
}

/** Save all duplicate / duplicate_ask items to history without launching any upload. */
async function ignoreAllDuplicates() {
  const dups = scanItems.filter(i => i.status === "duplicate" || i.status === "duplicate_ask");
  if (!dups.length) return;
  if (!confirm(t("jobs.confirm.ignore_all_dups", dups.length))) return;

  // Disable the button for the duration of the fetch to prevent double-clicks
  const btn = document.getElementById("btn-ignore-dups");
  if (btn) btn.disabled = true;
  try {
    await fetch("/api/jobs/skip-duplicates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: dups }),
    });
  } catch { /* ignore network errors — items are removed from local list either way */ }
  finally {
    if (btn) btn.disabled = false;
  }
  // Remove them from the local list and refresh
  scanItems = scanItems.filter(i => i.status !== "duplicate" && i.status !== "duplicate_ask");
  if (_currentScanFolder) setScanCache(_currentScanFolder, scanItems);
  renderPreview(scanItems);
  updateScanSummary();
  _updateIgnoreDupsBtn();
  _showScanActions(scanItems.length > 0);
  showToast(t("jobs.toast.dup_ask_ignored", dups.length), "secondary");
}
