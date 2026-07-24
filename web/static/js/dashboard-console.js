/* ── Console / xterm panel management ──────────────────────────────────────
 *
 * Depends on globals defined in dashboard.js:
 *   activeJobs, _dockXterms, _pendingOutput, _activeConsoleJobId, socket
 * Depends on: t() (i18n), showToast() (base.html)
 * ────────────────────────────────────────────────────────────────────────── */
"use strict";

// ── Low-level DOM helpers ─────────────────────────────────────────────────
function _activeDockEl() {
  return document.getElementById("active-console-dock");
}

function _panelEl(jobId) {
  return document.querySelector(`.active-console-panel[data-panel-job-id="${jobId}"]`);
}

function _panelTermEl(jobId) {
  return _panelEl(jobId)?.querySelector(".panel-terminal");
}

function _panelStdinEl(jobId) {
  return _panelEl(jobId)?.querySelector(".panel-stdin");
}

// ── Panel lifecycle ────────────────────────────────────────────────────────
function _createPanel(jobId) {
  const tpl = document.getElementById("tpl-console-panel");
  if (!tpl) return null;
  const clone = tpl.content.cloneNode(true);
  const panel = clone.querySelector(".active-console-panel");
  panel.dataset.panelJobId = jobId;

  const job = activeJobs[jobId];
  panel.querySelector(".panel-title").textContent = job?.name ?? "";
  panel.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  panel.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  panel.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });

  const stdinEl = panel.querySelector(".panel-stdin");
  const sendBtn = panel.querySelector(".panel-send-btn");
  sendBtn.addEventListener("click", () => sendStdinEl(jobId, stdinEl));
  stdinEl.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); sendStdinEl(jobId, stdinEl); }
  });
  stdinEl.addEventListener("focus", () => { _activeConsoleJobId = jobId; });

  _activeDockEl().appendChild(clone);
  return _panelEl(jobId);
}

function _destroyPanel(jobId) {
  const entry = _dockXterms.get(jobId);
  if (entry) {
    entry.resizeObserver?.disconnect();
    entry.xterm.dispose();
    _dockXterms.delete(jobId);
  }
  _panelEl(jobId)?.remove();
}

function _initPanelXterm(jobId) {
  if (_dockXterms.has(jobId)) return _dockXterms.get(jobId).xterm;
  const container = _panelTermEl(jobId);
  if (!container) return null;

  const xterm = new Terminal({
    convertEol:   true,
    scrollback:   5000,
    fontSize:     12,
    fontFamily:   '"Consolas", "Courier New", monospace',
    theme:        { background: "#0d1117", foreground: "#c9d1d9" },
    disableStdin: true,
    cursorBlink:  false,
  });
  const fitAddon = new FitAddon.FitAddon();
  xterm.loadAddon(fitAddon);
  xterm.open(container);
  // Double rAF: guarantees the browser has painted the container before fit() measures its height
  requestAnimationFrame(() => {
    requestAnimationFrame(() => { fitAddon.fit(); });
  });
  let resizeObserver = null;
  if (window.ResizeObserver) {
    resizeObserver = new ResizeObserver(() => { fitAddon.fit(); });
    resizeObserver.observe(container);
  }
  _dockXterms.set(jobId, { xterm, fitAddon, resizeObserver });
  return xterm;
}

function _setPanelStdin(jobId, on) {
  const panel = _panelEl(jobId);
  const inp   = panel?.querySelector(".panel-stdin");
  const btn   = panel?.querySelector(".panel-send-btn");
  const row   = inp?.closest(".input-row");
  if (inp) { inp.disabled = !on; if (!on) inp.value = ""; }
  if (btn) btn.disabled = !on;
  if (row) row.classList.toggle("opacity-50", !on);
}

function updateActiveDockTmdb(jobId) {
  const el = _panelEl(jobId)?.querySelector(".panel-tmdb");
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

// ── Dock show / hide ───────────────────────────────────────────────────────
function showActiveConsoleDock(jobId) {
  const dock = _activeDockEl();
  if (!dock) return;

  const alreadyOpen = !!_panelEl(jobId);

  // Set _activeConsoleJobId FIRST so job_output events are routed here even before card exists
  _activeConsoleJobId = jobId;

  if (!alreadyOpen) {
    _createPanel(jobId);
  }

  dock.classList.remove("d-none");

  if (!alreadyOpen) {
    const job   = activeJobs[jobId];
    const xterm = _initPanelXterm(jobId);
    if (xterm) {
      xterm.reset();
      const snapshot = (job?.lines || []).join("");
      if (snapshot) { xterm.write(snapshot); xterm.scrollToBottom(); }
    }
    const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
    if (card) collapseJobConsole(card);
  }

  _setPanelStdin(jobId, true);
  updateActiveDockTmdb(jobId);
  reorderJobCards();
}

function hideActiveConsoleDock(jobId) {
  const id = jobId || _activeConsoleJobId;
  if (!id) return;

  const card = document.querySelector(`.job-card[data-job-id="${id}"]`);
  if (card) {
    const j = activeJobs[id];
    // Only transfer output to the card terminal and expand the inline console
    // when the job is still running.  For finished jobs, applyStatus() has
    // already called collapseJobConsole(); re-expanding here would undo that
    // and show the raw terminal text unexpectedly.
    const isFinished = ["done", "error", "cancelled"].includes(j?.status);
    if (!isFinished) {
      const cardTerm = card.querySelector(".terminal");
      if (cardTerm && j?.lines?.length) {
        cardTerm.textContent = j.lines.join("");
        cardTerm.scrollTop = cardTerm.scrollHeight;
      }
      expandJobConsole(card);
    }
  }

  if (_activeConsoleJobId === id) {
    const remaining = [..._dockXterms.keys()].filter(k => k !== id);
    _activeConsoleJobId = remaining[0] ?? null;
  }
  _destroyPanel(id);

  if (_dockXterms.size === 0) {
    _activeDockEl()?.classList.add("d-none");
  }
}

function _consoleContext(jobId) {
  const useDock = _dockXterms.has(jobId);
  const card    = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
  return {
    card,
    primary: useDock ? _panelTermEl(jobId) : card?.querySelector(".terminal"),
    useDock,
  };
}

// ── Terminal I/O ───────────────────────────────────────────────────────────
const _ANSI_RE = /\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g;
function _stripAnsi(s) { return s ? s.replace(_ANSI_RE, "") : s; }

function _applyTerminalChunk(term, text, op) {
  if (!term || text == null || text === "") return;
  const atBottom = term.scrollTop + term.clientHeight >= term.scrollHeight - 10;
  if (op === "replace") {
    const prev = term._progressNode;
    if (prev && prev.parentNode === term) {
      prev.nodeValue = text;
    } else {
      const node = document.createTextNode(text);
      term.appendChild(node);
      term._progressNode = node;
    }
  } else {
    term._progressNode = null;
    term.appendChild(document.createTextNode(text));
  }
  if (atBottom) term.scrollTop = term.scrollHeight;
}

function writeTerminal(jobId, text) {
  if (text == null || text === "") return;
  const { card, useDock } = _consoleContext(jobId);
  const clean = _stripAnsi(text);

  if (useDock && _dockXterms.has(jobId)) {
    const xt = _dockXterms.get(jobId).xterm;
    xt.write(text, () => xt.scrollToBottom());
    checkTmdbPrompt(jobId, clean);
    checkDupPrompt(jobId, clean);
    return;
  }

  const cardTerm = card?.querySelector(".terminal");
  if (!cardTerm) return;
  if (card) {
    const st = activeJobs[jobId]?.status;
    if (!["done", "error", "cancelled"].includes(st) &&
        card.querySelector(".job-console-body")?.classList.contains("d-none")) {
      expandJobConsole(card);
    }
  }
  _applyTerminalChunk(cardTerm, clean, "append");
  checkTmdbPrompt(jobId, clean);
  checkDupPrompt(jobId, clean);
}

function appendTerminal(jobId, text) {
  writeTerminal(jobId, text);
}

// ── TMDB prompt detection ─────────────────────────────────────────────────
const _TMDB_PROMPT_RE  = /valid\s+TMDB\s+ID\s*\(\s*0\s*=\s*skip\s*\)/i;
const _DUP_PROMPT_RE   = /Press\s*\(C\)\s*to\s*continue.*\(S\)\s*to\s*SKIP/i;
const _tmdbAutoSent    = new Set();
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
  const panelInp = _panelStdinEl(jobId);
  if (panelInp && !panelInp.disabled) return panelInp;
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

// ── stdin helpers ─────────────────────────────────────────────────────────
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
      showToast(t("jobs.toast.stdin_failed", data.error || r.status), "warning");
      return false;
    }
    return true;
  } catch {
    showToast(t("jobs.toast.stdin_failed", "network"), "danger");
    return false;
  }
}

function sendStdin(btn) {
  const card = btn.closest(".job-card");
  if (card) sendStdinEl(card.dataset.jobId, card.querySelector(".job-stdin"));
}

function sendActiveDockStdin() {
  if (_activeConsoleJobId) sendStdinEl(_activeConsoleJobId, _panelStdinEl(_activeConsoleJobId));
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
