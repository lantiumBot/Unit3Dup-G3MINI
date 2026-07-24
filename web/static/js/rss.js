/* RSS feed management — rss.js */

// ── State ─────────────────────────────────────────────────────────────────────
let _rssFeeds            = [];
let _rssActiveFeedId     = null;
let _rssTorrentTimer     = null;
let _rssAutoRefresh      = true;
let _rssCatConfig        = {};   // rss_categories config loaded from /api/settings
let _rssCatEnabled       = false;
let _rssCurrentItems     = [];   // items currently displayed in the panel
let _rssHideIgnored      = false;

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await _rssLoadCatConfig();
  rssLoadFeeds();
  rssLoadTorrents();
  _rssTorrentTimer = setInterval(rssLoadTorrents, 10000);
});

async function _rssLoadCatConfig() {
  try {
    const r    = await fetch("/api/settings");
    const data = await r.json();
    const cfg  = data?.web?.rss_categories || {};
    _rssCatEnabled = !!cfg.enabled;
    _rssCatConfig  = cfg.categories || {};
  } catch (_) {}
}

// ── Feed loading ──────────────────────────────────────────────────────────────
async function rssLoadFeeds() {
  try {
    const r = await fetch("/api/rss/feeds");
    _rssFeeds = await r.json();
    _rssRenderFeeds(_rssFeeds);
  } catch (e) {
    showToast(t("rss.toast.error").replace("{0}", e), "danger");
  }
}

function _rssRenderFeeds(feeds) {
  const tbody = document.getElementById("rss-feeds-body");
  document.getElementById("rss-feed-count").textContent = feeds.length;
  if (!feeds.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="text-center text-muted py-4">${t("rss.empty")}</td></tr>`;
    return;
  }
  tbody.innerHTML = feeds.map(f => {
    const lastFetch = f.last_fetched ? new Date(f.last_fetched).toLocaleString() : t("rss.status.never");
    const errBadge  = f.last_error
      ? `<span class="badge bg-danger" title="${_esc(f.last_error)}">${t("rss.status.error")}</span>`
      : `<span class="badge bg-success">${t("rss.status.ok")}</span>`;
    const enabledBadge = f.enabled
      ? `<span class="badge bg-success">${t("common.enabled")}</span>`
      : `<span class="badge bg-secondary">Off</span>`;
    const clientLabel = f.torrent_client ? f.torrent_client : t("rss.client.default");
    const urlShort    = f.url.length > 55 ? f.url.slice(0, 52) + "…" : f.url;
    const isActive    = _rssActiveFeedId === f.id;
    return `<tr class="${isActive ? "table-active" : ""}">
      <td class="text-center">
        <button class="btn btn-sm btn-link p-0 text-decoration-none" onclick="rssToggleItems('${f.id}', '${_esc(f.name)}')"
                title="${t("rss.items.show_hint")}">
          <i class="bi bi-chevron-${isActive ? "up" : "down"} small"></i>
        </button>
      </td>
      <td class="fw-semibold">${_esc(f.name) || "<em class='text-muted'>—</em>"}</td>
      <td><span class="font-monospace small text-muted" title="${_esc(f.url)}">${_esc(urlShort)}</span></td>
      <td class="text-nowrap">${f.interval_minutes} min</td>
      <td>${_esc(clientLabel)}</td>
      <td class="text-nowrap small text-muted">${lastFetch}</td>
      <td>${f.item_count || 0}</td>
      <td class="text-center">
        <div class="form-check form-switch d-inline-block mb-0" title="${t("rss.modal.auto_download_hint")}">
          <input class="form-check-input" type="checkbox" role="switch"
                 id="rss-autodl-${f.id}" ${f.auto_download !== false ? "checked" : ""}
                 onchange="rssToggleAutoDownload('${f.id}', this.checked)">
        </div>
      </td>
      <td>${errBadge} ${enabledBadge}</td>
      <td class="text-nowrap">
        <button class="btn btn-sm btn-outline-info me-1" onclick="rssFetchNow('${f.id}')" title="${t("rss.fetch_btn")}">
          <i class="bi bi-arrow-repeat"></i>
        </button>
        <button class="btn btn-sm btn-outline-secondary me-1" onclick="rssOpenModal('${f.id}')" title="${t("rss.modal.edit_title")}">
          <i class="bi bi-pencil"></i>
        </button>
        <button class="btn btn-sm btn-outline-danger" onclick="rssDeleteFeed('${f.id}')" title="${t("rss.confirm.delete")}">
          <i class="bi bi-trash3"></i>
        </button>
      </td>
    </tr>`;
  }).join("");
}

// ── Items panel ───────────────────────────────────────────────────────────────
async function rssToggleItems(feedId, feedName) {
  if (_rssActiveFeedId === feedId) {
    rssCloseItems();
    return;
  }
  _rssActiveFeedId = feedId;
  _rssRenderFeeds(_rssFeeds);
  document.getElementById("rss-items-feed-name").textContent = feedName;
  document.getElementById("rss-items-card").classList.remove("d-none");
  document.getElementById("rss-items-body").innerHTML =
    `<tr><td colspan="7" class="text-center py-3">${t("common.loading")}</td></tr>`;
  document.getElementById("rss-items-card").scrollIntoView({ behavior: "smooth", block: "start" });
  await rssLoadItems(feedId);
  if (_rssCatEnabled) _rssEnrichItems(feedId);
}

function rssCloseItems() {
  _rssActiveFeedId = null;
  _rssHideIgnored  = false;
  document.getElementById("rss-items-card").classList.add("d-none");
  _rssRenderFeeds(_rssFeeds);
}

function rssToggleHideIgnored() {
  _rssHideIgnored = !_rssHideIgnored;
  _rssRenderItems(_rssCurrentItems);
}

async function rssLoadItems(feedId) {
  try {
    const r     = await fetch(`/api/rss/feeds/${feedId}/items`);
    const items = await r.json();
    _rssRenderItems(items);
  } catch (e) {
    document.getElementById("rss-items-body").innerHTML =
      `<tr><td colspan="5" class="text-center text-danger py-3">${_esc(String(e))}</td></tr>`;
  }
}

function _rssRenderItems(items) {
  _rssCurrentItems = items;
  const tbody = document.getElementById("rss-items-body");

  // Apply hide-ignored filter
  const visible = _rssHideIgnored ? items.filter(it => it.matched) : items;
  document.getElementById("rss-items-count").textContent = visible.length !== items.length
    ? `${visible.length}/${items.length}`
    : items.length;

  // Sync hide-ignored button appearance
  const hideBtn = document.getElementById("rss-hide-ignored-btn");
  if (hideBtn) {
    hideBtn.classList.toggle("active", _rssHideIgnored);
    hideBtn.classList.toggle("btn-secondary", _rssHideIgnored);
    hideBtn.classList.toggle("btn-outline-secondary", !_rssHideIgnored);
    const span = hideBtn.querySelector("span");
    if (span) span.textContent = _rssHideIgnored ? t("rss.items.show_ignored_btn") : t("rss.items.hide_ignored_btn");
    const icon = hideBtn.querySelector("i");
    if (icon) icon.className = _rssHideIgnored ? "bi bi-eye me-1" : "bi bi-eye-slash me-1";
  }

  if (!visible.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted py-3">${t("rss.items.empty")}</td></tr>`;
    return;
  }
  // Enable Doublons button if there is anything matched and not yet downloaded
  const canCheck = items.filter(it => it.matched && !it.downloaded).length;
  const dupBtn = document.getElementById("rss-dup-check-btn");
  if (dupBtn) dupBtn.disabled = canCheck === 0;

  tbody.innerHTML = visible.map(it => {
    const rssCatBadge = it.rss_category
      ? ` <span class="badge bg-dark border text-white-50 fw-normal" style="font-size:.65rem">${_esc(it.rss_category)}</span>`
      : "";
    const matchBadge = (it.matched
      ? `<span class="badge bg-success">${t("rss.matched")}</span>`
      : `<span class="badge bg-secondary">${t("rss.no_match")}</span>`) + rssCatBadge;

    // Duplicate badge
    let dupBadge = "";
    if (it.duplicate_status === "duplicate") {
      const names = (it.duplicate_info?.matches || []).map(m => m.name).join(", ") || "";
      dupBadge = `<span class="badge bg-danger" title="${_esc(names)}">${t("rss.dup.found")}</span>`;
    } else if (it.duplicate_status === "not_duplicate") {
      dupBadge = `<span class="badge bg-success">${t("rss.dup.ok")}</span>`;
    } else if (it.matched) {
      dupBadge = `<span class="badge bg-secondary opacity-50">${t("rss.dup.unchecked")}</span>`;
    }

    let dlBadge;
    if (it.downloaded) {
      const catLabel = it.download_category ? ` <small class="text-muted">(${_esc(it.download_category)})</small>` : "";
      dlBadge = `<span class="badge bg-success">${t("rss.downloaded")}</span>${catLabel}`;
    } else if (it.download_error) {
      dlBadge = `<span class="badge bg-danger" title="${_esc(it.download_error)}">${t("rss.status.error")}</span>`;
    } else {
      dlBadge = `<span class="badge bg-secondary">${t("rss.not_downloaded")}</span>`;
    }

    const dateStr = it.pub_date
      ? (it.pub_date.length > 20 ? it.pub_date.slice(0, 20) : it.pub_date)
      : "—";

    // Category cell
    const catCell = _rssCatEnabled
      ? _rssBuildCatSelect(it)
      : `<span class="text-muted small">—</span>`;

    // Disable DL button for confirmed duplicates
    const isDup   = it.duplicate_status === "duplicate";
    const guidSafe = _esc(it.guid.replace(/\\/g, "\\\\").replace(/'/g, "\\'"));
    const dlBtn = it.matched && !it.downloaded
      ? `<button class="btn btn-sm btn-outline-${isDup ? "secondary" : "success"}"
                 data-feed="${_esc(it.feed_id)}" data-guid="${_esc(it.guid)}"
                 ${isDup ? `disabled title="${t("rss.dup.skip_hint")}"` : `onclick="rssDownloadItem(this)" title="${t("rss.items.dl_btn")}"`}>
           <i class="bi bi-download"></i></button>`
      : "";
    const torrentLink = it.torrent_url
      ? `<a href="${_esc(it.torrent_url)}" target="_blank" class="btn btn-sm btn-outline-secondary ms-1" title="${t("rss.items.open_link")}"><i class="bi bi-box-arrow-up-right"></i></a>`
      : "";
    return `<tr class="${isDup ? "table-danger opacity-75" : ""}">
      <td class="small" title="${_esc(it.title)}">${_esc(it.title.length > 80 ? it.title.slice(0, 77) + "…" : it.title)}</td>
      <td class="small text-muted text-nowrap">${dateStr}</td>
      <td>${matchBadge}</td>
      <td>${dupBadge}</td>
      <td class="text-nowrap" id="rss-cat-cell-${_esc(it.guid.replace(/[^a-z0-9]/gi, "_"))}">${catCell}</td>
      <td>${dlBadge}</td>
      <td class="text-nowrap">${dlBtn}${torrentLink}</td>
    </tr>`;
  }).join("");
}

// ── Category cell helpers ─────────────────────────────────────────────────────

const _RSS_CAT_LABEL = {
  movies:             null,   // filled from i18n at call time
  series:             null,
  movies_animation:   null,
  series_animation:   null,
  movies_documentary: null,
  series_documentary: null,
};
const _RSS_CAT_I18N = {
  movies:             "config.rss_categories.cat.movies",
  series:             "config.rss_categories.cat.series",
  movies_animation:   "config.rss_categories.cat.movies_animation",
  series_animation:   "config.rss_categories.cat.series_animation",
  movies_documentary: "config.rss_categories.cat.movies_documentary",
  series_documentary: "config.rss_categories.cat.series_documentary",
};

function _rssCatLabel(key) {
  return t(_RSS_CAT_I18N[key] || key) || key;
}

function _rssEnabledCatKeys() {
  return Object.entries(_rssCatConfig)
    .filter(([, c]) => c?.enabled)
    .map(([k]) => k);
}

function _rssBuildCatSelect(it) {
  const enabledKeys = _rssEnabledCatKeys();
  if (!enabledKeys.length) return `<span class="text-muted small">${t("rss.items.category.no_cats")}</span>`;

  const suggested = it.suggested_category || "";
  const enriching = !it.tmdb_enriched && it.matched && !it.downloaded && it.duplicate_status !== "duplicate";
  const guidSafe  = it.guid.replace(/[^a-z0-9]/gi, "_");

  const opts = enabledKeys.map(k => {
    const qname  = _rssCatConfig[k]?.qbit_name || k;
    const isSug  = k === suggested;
    return `<option value="${_esc(qname)}" ${isSug ? "selected" : ""}>${_esc(_rssCatLabel(k))}${isSug ? " ✦" : ""}</option>`;
  }).join("");

  const spinner = enriching
    ? `<span class="spinner-border spinner-border-sm text-secondary ms-1" id="rss-cat-spin-${guidSafe}" style="width:.75rem;height:.75rem"></span>`
    : "";

  return `<select class="form-select form-select-sm" style="min-width:130px;font-size:.75rem"
                  id="rss-cat-sel-${guidSafe}">
            <option value="">${t("rss.items.category.none")}</option>
            ${opts}
          </select>${spinner}`;
}

function _rssGetSelectedCat(guid) {
  const guidSafe = guid.replace(/[^a-z0-9]/gi, "_");
  return document.getElementById(`rss-cat-sel-${guidSafe}`)?.value || null;
}

// ── Background enrichment ─────────────────────────────────────────────────────

async function _rssEnrichItems(feedId) {
  // Only enrich items that are matched, not downloaded, and not confirmed duplicates
  const eligible = _rssCurrentItems.filter(
    it => it.matched && !it.downloaded && it.duplicate_status !== "duplicate" && !it.tmdb_enriched
  );
  if (!eligible.length) return;
  try {
    const r = await fetch("/api/rss/items/enrich", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ feed_id: feedId, guids: eligible.map(it => it.guid) }),
    });
    if (!r.ok) return;
    const data = await r.json();
    if (!data.enriched) return;
    // Refresh items to show updated suggestions
    const itemsR = await fetch(`/api/rss/feeds/${feedId}/items`);
    const items  = await itemsR.json();
    _rssRenderItems(items);
  } catch (_) {}
}

// ── Download ──────────────────────────────────────────────────────────────────

async function rssDownloadItem(btn) {
  const feedId   = btn.dataset.feed;
  const guid     = btn.dataset.guid;
  const category = _rssCatEnabled ? _rssGetSelectedCat(guid) : null;
  btn.disabled   = true;
  try {
    const r = await fetch("/api/rss/download", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ feed_id: feedId, guid, category }),
    });
    const d = await r.json();
    if (!r.ok) {
      showToast(t("rss.toast.dl_error").replace("{0}", d.error || r.status), "danger");
      btn.disabled = false;
    } else {
      showToast(t("rss.toast.dl_ok"), "success");
      await rssLoadItems(feedId);
    }
  } catch (e) {
    showToast(t("rss.toast.error").replace("{0}", e), "danger");
    btn.disabled = false;
  }
}

// ── Manual fetch ──────────────────────────────────────────────────────────────
async function rssFetchNow(feedId) {
  try {
    const r = await fetch(`/api/rss/feeds/${feedId}/fetch`, { method: "POST" });
    const d = await r.json();
    if (d.error) {
      showToast(t("rss.toast.dl_error").replace("{0}", d.error), "warning");
    } else {
      showToast(t("rss.toast.fetched").replace("{0}", d.items), "success");
    }
    await rssLoadFeeds();
    if (_rssActiveFeedId === feedId) {
      await rssLoadItems(feedId);
    }
  } catch (e) {
    showToast(t("rss.toast.error").replace("{0}", e), "danger");
  }
}

// ── Delete feed ───────────────────────────────────────────────────────────────
async function rssDeleteFeed(feedId) {
  if (!confirm(t("rss.confirm.delete"))) return;
  try {
    await fetch(`/api/rss/feeds/${feedId}`, { method: "DELETE" });
    if (_rssActiveFeedId === feedId) rssCloseItems();
    showToast(t("rss.toast.deleted"), "info");
    await rssLoadFeeds();
  } catch (e) {
    showToast(t("rss.toast.error").replace("{0}", e), "danger");
  }
}

// ── Add / Edit modal ──────────────────────────────────────────────────────────
function rssOpenModal(feedId) {
  const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById("rssFeedModal"));
  document.getElementById("rss-filters-list").innerHTML = "";
  if (feedId) {
    const f = _rssFeeds.find(x => x.id === feedId);
    if (!f) return;
    document.getElementById("rssFeedModalTitle").textContent = t("rss.modal.edit_title");
    document.getElementById("rss-modal-id").value                   = f.id;
    document.getElementById("rss-modal-name").value                 = f.name;
    document.getElementById("rss-modal-url").value                  = f.url;
    document.getElementById("rss-modal-interval").value             = f.interval_minutes;
    document.getElementById("rss-modal-enabled").checked            = f.enabled;
    document.getElementById("rss-modal-auto-download").checked      = f.auto_download !== false;
    document.getElementById("rss-modal-save-path").value            = f.save_path || "";
    document.getElementById("rss-modal-tag").value                  = f.tag || "RSS";
    document.getElementById("rss-modal-client").value               = f.torrent_client || "";
    document.getElementById("rss-modal-match-all").checked          = !!f.filter_match_all;
    (f.filters || []).forEach(fi => rssAddFilter(fi));
  } else {
    document.getElementById("rssFeedModalTitle").textContent = t("rss.modal.add_title");
    document.getElementById("rss-modal-id").value                   = "";
    document.getElementById("rss-modal-name").value                 = "";
    document.getElementById("rss-modal-url").value                  = "";
    document.getElementById("rss-modal-interval").value             = "30";
    document.getElementById("rss-modal-enabled").checked            = true;
    document.getElementById("rss-modal-auto-download").checked      = true;
    document.getElementById("rss-modal-save-path").value            = "";
    document.getElementById("rss-modal-tag").value                  = "RSS";
    document.getElementById("rss-modal-client").value               = "";
    document.getElementById("rss-modal-match-all").checked          = false;
  }
  modal.show();
}

function rssAddFilter(fi) {
  const tpl  = document.getElementById("rss-filter-tpl").content.cloneNode(true);
  const row  = tpl.querySelector(".rss-filter-row");
  if (fi) {
    row.querySelector("[data-role=type]").value    = fi.type    || "include";
    row.querySelector("[data-role=mode]").value    = fi.mode    || "keyword";
    row.querySelector("[data-role=pattern]").value = fi.pattern || "";
  }
  // Apply i18n on the cloned node
  row.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.getAttribute("data-i18n")) || el.textContent;
  });
  document.getElementById("rss-filters-list").appendChild(tpl);
}

function _rssCollectFilters() {
  return Array.from(document.querySelectorAll(".rss-filter-row")).map(row => ({
    type:    row.querySelector("[data-role=type]").value,
    mode:    row.querySelector("[data-role=mode]").value,
    pattern: row.querySelector("[data-role=pattern]").value.trim(),
  })).filter(f => f.pattern);
}

async function rssSaveFeed() {
  const id       = document.getElementById("rss-modal-id").value.trim();
  const payload  = {
    name:             document.getElementById("rss-modal-name").value.trim(),
    url:              document.getElementById("rss-modal-url").value.trim(),
    interval_minutes: parseInt(document.getElementById("rss-modal-interval").value) || 30,
    enabled:          document.getElementById("rss-modal-enabled").checked,
    auto_download:    document.getElementById("rss-modal-auto-download").checked,
    save_path:        document.getElementById("rss-modal-save-path").value.trim(),
    tag:              document.getElementById("rss-modal-tag").value.trim() || "RSS",
    torrent_client:   document.getElementById("rss-modal-client").value,
    filter_match_all: document.getElementById("rss-modal-match-all").checked,
    filters:          _rssCollectFilters(),
  };
  if (!payload.url) {
    showToast(t("rss.toast.url_required"), "warning");
    return;
  }
  try {
    const r = await fetch(id ? `/api/rss/feeds/${id}` : "/api/rss/feeds", {
      method:  id ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) {
      showToast(t("rss.toast.error").replace("{0}", d.error || r.status), "danger");
      return;
    }
    bootstrap.Modal.getInstance(document.getElementById("rssFeedModal")).hide();
    showToast(id ? t("rss.toast.updated") : t("rss.toast.added"), "success");
    await rssLoadFeeds();
    // If this feed's items are currently displayed, refresh them so the new
    // filter rules are reflected immediately in the matched/ignored column.
    if (id && _rssActiveFeedId === id) {
      await rssLoadItems(id);
    }
  } catch (e) {
    showToast(t("rss.toast.error").replace("{0}", e), "danger");
  }
}

// ── Auto-download toggle ──────────────────────────────────────────────────────
async function rssToggleAutoDownload(feedId, enabled) {
  try {
    await fetch(`/api/rss/feeds/${feedId}`, {
      method:  "PUT",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ auto_download: enabled }),
    });
    const feed = _rssFeeds.find(f => f.id === feedId);
    if (feed) feed.auto_download = enabled;
  } catch (e) {
    showToast(t("rss.toast.error").replace("{0}", e), "danger");
    // Revert the checkbox visually on failure
    const cb = document.getElementById(`rss-autodl-${feedId}`);
    if (cb) cb.checked = !enabled;
  }
}

// ── Download all matched ──────────────────────────────────────────────────────
async function rssDownloadAllMatched() {
  const feedId = _rssActiveFeedId;
  if (!feedId) return;
  const btn = document.getElementById("rss-dl-all-btn");
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/rss/feeds/${feedId}/download-matched`, { method: "POST" });
    const d = await r.json();
    if (!r.ok) {
      showToast(t("rss.toast.dl_error").replace("{0}", d.error || r.status), "danger");
    } else if (d.launched === 0 && d.errors === 0) {
      showToast(t("rss.toast.dl_all_none"), "info");
    } else {
      showToast(t("rss.toast.dl_all_done").replace("{0}", d.launched).replace("{1}", d.errors), "success");
      await rssLoadItems(feedId);
    }
  } catch (e) {
    showToast(t("rss.toast.error").replace("{0}", e), "danger");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Gemini duplicate check ────────────────────────────────────────────────────
async function rssCheckDuplicates() {
  const feedId = _rssActiveFeedId;
  if (!feedId) return;
  const btn = document.getElementById("rss-dup-check-btn");
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${t("rss.dup.checking")}`; }
  try {
    const r = await fetch(`/api/rss/feeds/${feedId}/check-duplicates`, { method: "POST" });
    const d = await r.json();
    if (!r.ok) {
      showToast(t("rss.toast.error").replace("{0}", d.error || r.status), "danger");
    } else if (d.checked === 0) {
      showToast(t("rss.dup.nothing_to_check"), "info");
    } else {
      const msg = t("rss.dup.result")
        .replace("{0}", d.checked)
        .replace("{1}", d.duplicates);
      showToast(msg, d.duplicates > 0 ? "warning" : "success");
      if (d.rate_limited) showToast(t("rss.dup.rate_limited"), "danger");
      await rssLoadItems(feedId);
    }
  } catch (e) {
    showToast(t("rss.toast.error").replace("{0}", e), "danger");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<i class="bi bi-search me-1"></i>${t("rss.items.dup_btn")}`;
    }
  }
}

// ── Torrent progress ──────────────────────────────────────────────────────────
async function rssLoadTorrents() {
  if (!_rssAutoRefresh) return;
  try {
    const r    = await fetch("/api/rss/torrents");
    const data = await r.json();
    _rssRenderTorrents(data.torrents || [], data.error);
    const upd = document.getElementById("rss-torrent-updated");
    if (upd) upd.textContent = new Date().toLocaleTimeString();
  } catch (_) {}
}

function _rssRenderTorrents(torrents, err) {
  const errDiv = document.getElementById("rss-torrent-error");
  const errMsg = document.getElementById("rss-torrent-error-msg");
  const tbody  = document.getElementById("rss-torrent-body");
  const cnt    = document.getElementById("rss-torrent-count");

  if (err) {
    errDiv.classList.remove("d-none");
    errMsg.textContent = err;
  } else {
    errDiv.classList.add("d-none");
  }

  cnt.textContent = torrents.length || "";

  if (!torrents.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted py-4">${t("rss.progress.empty")}</td></tr>`;
    return;
  }

  tbody.innerHTML = torrents.map(t_ => {
    const pct    = Math.min(100, Math.max(0, t_.progress || 0));
    const barCls = pct >= 100 ? "success" : "info";
    return `<tr>
      <td class="small" title="${_esc(t_.name)}">${_esc(t_.name.length > 60 ? t_.name.slice(0, 57) + "…" : t_.name)}</td>
      <td><span class="badge bg-${t_.state_class || "secondary"}">${_esc(t_.state_label || t_.state)}</span></td>
      <td class="text-nowrap small">${t_.size_human}</td>
      <td style="min-width:120px">
        <div class="d-flex align-items-center gap-1">
          <div class="progress flex-grow-1" style="height:6px">
            <div class="progress-bar bg-${barCls}" style="width:${pct}%"></div>
          </div>
          <small class="text-muted" style="min-width:38px">${pct}%</small>
        </div>
      </td>
      <td class="text-nowrap small">${t_.download_speed_human}</td>
      <td class="text-nowrap small">${t_.upload_speed_human}</td>
      <td class="small">${t_.ratio}</td>
    </tr>`;
  }).join("");
}

function rssToggleAutoRefresh(enabled) {
  _rssAutoRefresh = enabled;
  if (enabled) {
    rssLoadTorrents();
    _rssTorrentTimer = setInterval(rssLoadTorrents, 10000);
  } else {
    clearInterval(_rssTorrentTimer);
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function _esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
