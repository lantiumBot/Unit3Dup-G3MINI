/* ── Stats page — ECharts Sankey + live torrent table ──────────────────── */
"use strict";

let sankeyChart = null;

function initChart() {
  const el = document.getElementById("sankey-chart");
  if (!el) return;
  sankeyChart = echarts.init(el, null, { renderer: "canvas" });
  window.addEventListener("resize", () => sankeyChart?.resize());
}

function renderSankey(nodes, links) {
  const empty   = document.getElementById("sankey-empty");
  const chartEl = document.getElementById("sankey-chart");

  if (!nodes.length || !links.length) {
    empty?.classList.remove("d-none");
    chartEl.style.display = "none";
    return;
  }
  empty?.classList.add("d-none");
  chartEl.style.display = "";

  const dark      = document.getElementById("html-root").getAttribute("data-bs-theme") === "dark";
  const textColor = dark ? "#c9d1d9" : "#333";

  const option = {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      formatter: p => p.dataType === "edge"
        ? `${p.data.source} → ${p.data.target} : <b>${p.data.value}</b>`
        : `${p.name}`,
    },
    series: [{
      type: "sankey",
      layoutIterations: 64,
      data: nodes,
      links: links,
      orient: "horizontal",
      draggable: true,
      nodeGap: 18,
      nodeWidth: 22,
      emphasis: { focus: "adjacency", lineStyle: { opacity: 0.9 } },
      label: { color: textColor, fontSize: 12, fontFamily: "Consolas, monospace" },
      lineStyle: { color: "gradient", curveness: 0.52, opacity: 0.5 },
      itemStyle: { borderRadius: 4 },
    }],
  };

  sankeyChart?.setOption(option, true);
  sankeyChart?.resize();
}

function renderTorrents(torrents) {
  const tbody = document.getElementById("torrent-body");
  if (!torrents.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted py-4">
      <i class="bi bi-inbox fs-2 d-block mb-2"></i>${t("stats.torrent.empty")}
    </td></tr>`;
    return;
  }
  tbody.innerHTML = torrents.map(tor => {
    const prog = tor.progress >= 100 ? "" :
      `<div class="progress" style="height:4px;min-width:60px">
         <div class="progress-bar bg-info" style="width:${tor.progress}%"></div>
       </div>`;
    return `<tr>
      <td class="font-mono small text-truncate" style="max-width:280px" title="${esc(tor.name)}">${esc(tor.name)}</td>
      <td><span class="badge bg-${tor.state_class}">${esc(tor.state_label)}</span></td>
      <td class="small text-nowrap">${tor.size_human}</td>
      <td class="small text-nowrap text-success">${tor.uploaded_human}</td>
      <td class="small fw-bold ${tor.ratio >= 1 ? 'text-success' : 'text-warning'}">${tor.ratio}</td>
      <td class="small text-nowrap font-mono text-warning">${tor.upload_speed > 0 ? tor.upload_speed_human : '—'}</td>
      <td class="small text-nowrap font-mono text-info">${tor.download_speed > 0 ? tor.download_speed_human : '—'}</td>
      <td style="min-width:70px">${tor.progress < 100 ? prog : '<span class="text-success small">100%</span>'}</td>
    </tr>`;
  }).join("");
}

async function refresh() {
  const resp = await fetch("/api/stats");
  const data = await resp.json();

  const { totals, history, sankey, qbit_error } = data;
  document.getElementById("c-torrents").textContent = totals.torrents || "0";
  document.getElementById("c-size").textContent     = totals.size_human || "—";
  document.getElementById("c-uploaded").textContent = totals.uploaded_human || "—";
  const ratioEl = document.getElementById("c-ratio");
  ratioEl.textContent = totals.ratio || "0.00";
  ratioEl.className   = `fs-3 fw-bold ${totals.ratio >= 1 ? "text-success" : "text-warning"}`;
  document.getElementById("c-upspd").textContent = totals.upload_speed_human || "—";
  document.getElementById("c-dlspd").textContent = totals.download_speed_human || "—";

  document.getElementById("sankey-info").textContent =
    t("stats.sankey_info", history.total, history.done, history.error);

  const errBanner = document.getElementById("qbit-error");
  if (qbit_error) {
    errBanner.classList.remove("d-none");
    document.getElementById("qbit-error-msg").textContent = qbit_error;
  } else {
    errBanner.classList.add("d-none");
  }

  renderSankey(sankey.nodes, sankey.links);

  const tresp = await fetch("/api/torrents");
  const tdata = await tresp.json();
  renderTorrents(tdata.torrents || []);
}

function esc(s) {
  return String(s || "").replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])
  );
}

// Redraw on theme or locale change
const _obs = new MutationObserver(() => refresh());
_obs.observe(document.getElementById("html-root"), { attributes: true, attributeFilter: ["data-bs-theme"] });
document.addEventListener("locale-changed", refresh);

document.addEventListener("DOMContentLoaded", () => {
  initChart();
  refresh();
  setInterval(refresh, 15000);
});
