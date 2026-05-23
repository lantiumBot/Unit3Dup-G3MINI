/* ── Status page — service connectivity checks ───────────────────────────── */
"use strict";

const _SVC_ICON = {
  source:   "bi-folder2-open",
  unit3dup: "bi-terminal-fill",
  config:   "bi-file-earmark-code-fill",
  torrent:  "bi-hdd-network-fill",
  tracker:  "bi-broadcast",
  tmdb:     "bi-film",
};

const _SVC_COLOR = {
  source:   "text-info",
  unit3dup: "text-secondary",
  config:   "text-warning",
  torrent:  "text-success",
  tracker:  "text-primary",
  tmdb:     "text-danger",
};

const _BORDER = { ok: "border-success", warn: "border-warning", error: "border-danger" };
const _BADGE  = { ok: "success",        warn: "warning",        error: "danger"        };

function _badge(status) {
  const cls = _BADGE[status] || "secondary";
  const key = `status.${status}`;
  return `<span class="badge rounded-pill bg-${cls} px-2">${t(key)}</span>`;
}

function _msChip(ms) {
  if (!ms) return "";
  const cls = ms < 200 ? "text-success" : ms < 1000 ? "text-warning" : "text-danger";
  return `<span class="badge bg-body-secondary border font-mono ${cls}" style="font-size:.7rem">${ms} ms</span>`;
}

function renderCard(c) {
  const icon    = _SVC_ICON[c.id]  || "bi-question-circle";
  const color   = _SVC_COLOR[c.id] || "text-secondary";
  const border  = _BORDER[c.status] || "border-secondary";
  const label   = t(`status.service.${c.id}`);
  const detail  = c.detail
    ? `<div class="text-muted small text-truncate mt-1" title="${esc(c.detail)}">${esc(c.detail)}</div>`
    : "";
  const client  = c.client
    ? `<span class="badge bg-body-secondary text-body-secondary border ms-1" style="font-size:.65rem">${esc(c.client)}</span>`
    : "";

  return `
<div class="col-md-6 col-xl-4">
  <div class="card border-0 border-start border-3 ${border} shadow-sm h-100">
    <div class="card-body d-flex align-items-start gap-3 py-3">
      <div class="fs-2 ${color} pt-1 flex-shrink-0">
        <i class="bi ${icon}"></i>
      </div>
      <div class="flex-grow-1 min-w-0">
        <div class="fw-semibold mb-1">${esc(label)}${client}</div>
        <div class="d-flex align-items-center flex-wrap gap-1">
          ${_badge(c.status)}
          ${_msChip(c.ms)}
        </div>
        ${detail}
      </div>
    </div>
  </div>
</div>`;
}

async function refresh() {
  const grid = document.getElementById("status-grid");

  // spinner while loading
  grid.innerHTML = `
    <div class="col-12 text-center py-5 text-muted">
      <span class="spinner-border spinner-border-sm me-2"></span>
      <span data-i18n="status.loading">Vérification en cours…</span>
    </div>`;
  if (window.i18n?.applyLocale) i18n.applyLocale();

  try {
    const r = await fetch("/api/status");
    const d = await r.json();
    const cards = (d.checks || []).map(renderCard).join("");
    grid.innerHTML = cards || `<div class="col-12 text-muted text-center py-4">${t("status.empty")}</div>`;

    const ts = document.getElementById("status-ts");
    if (ts) ts.textContent = new Date().toLocaleTimeString(i18n?.dateLocale?.() || "fr-FR");
  } catch {
    grid.innerHTML = `
      <div class="col-12">
        <div class="alert alert-danger">${t("status.error_load")}</div>
      </div>`;
  }
}

function esc(s) {
  return String(s || "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.addEventListener("locale-changed", refresh);
document.addEventListener("DOMContentLoaded", refresh);
