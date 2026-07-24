/* ── Browser notifications ──────────────────────────────────────────────── */
"use strict";

function requestNotifPermission() {
  if (!("Notification" in window)) return;
  Notification.requestPermission().then(perm => {
    const btn = document.getElementById("btn-notif");
    if (btn) btn.style.display = "none";
    if (perm === "granted") showToast(t("notif.permission.granted"), "success");
  });
}

function _notify(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(title, { body, icon: "/static/favicon.ico" }); } catch {}
}
