/* ── Auto-logout: inactivity timeout + server-restart detection ───────────────
 *
 * Loaded at the bottom of base.html (after page scripts) — active on every page.
 *
 * Feature A — Inactivity timeout
 *   Reads session_timeout_minutes from GET /api/auth/status.
 *   Tracks user activity (mouse / keyboard / scroll / touch).
 *   Checks idle time every 30 s; auto-logs out when the limit is reached.
 *   Shows a warning toast 60 s before expiry.
 *   Setting = 0 → feature disabled.
 *
 * Feature B — Server-restart session invalidation
 *   Pings GET /api/auth/status every 15 seconds.
 *   If auth is still enabled but logged_in = false → the server restarted
 *   (or the session was otherwise revoked) → auto-logout.
 *   Also exposed as window._u3d_ping_auth() so dashboard.js can trigger an
 *   immediate check on socket connect_error (new epoch rejected by server).
 *   This avoids patching window.fetch() which would interfere with other code.
 *
 * Both paths redirect to /login?reason=timeout|restart so the login page
 * can display a contextual message.
 * ────────────────────────────────────────────────────────────────────────── */
"use strict";

(function () {
  const _WARN_BEFORE_MS  = 60_000;   // warn 60 s before expiry
  const _CHECK_INTERVAL  = 30_000;   // inactivity poll interval
  const _PING_INTERVAL   = 15_000;   // auth-status ping interval (restart detection)

  let _timeoutMs        = 0;         // 0 = inactivity feature disabled
  let _lastActivity     = Date.now();
  let _warned           = false;     // warning toast already shown this cycle
  let _inactivityTimer  = null;
  let _pingTimer        = null;
  let _authEnabled      = false;
  let _logoutInProgress = false;

  // ── Activity tracking ─────────────────────────────────────────────────────
  function _resetActivity() {
    _lastActivity = Date.now();
    _warned       = false;
  }

  ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "click"].forEach(ev => {
    document.addEventListener(ev, _resetActivity, { passive: true });
  });

  // ── Logout helper ─────────────────────────────────────────────────────────
  async function _autoLogout(reason) {
    if (_logoutInProgress) return;
    _logoutInProgress = true;
    clearInterval(_inactivityTimer);
    clearInterval(_pingTimer);
    try { await fetch("/api/auth/logout", { method: "POST" }); } catch (_) {}
    window.location.href = "/login?reason=" + encodeURIComponent(reason);
  }

  // ── Inactivity check (runs every _CHECK_INTERVAL) ─────────────────────────
  function _checkInactivity() {
    if (!_authEnabled || _timeoutMs <= 0 || _logoutInProgress) return;
    const idle      = Date.now() - _lastActivity;
    const remaining = _timeoutMs - idle;

    if (remaining <= 0) {
      _autoLogout("timeout");
      return;
    }

    if (remaining <= _WARN_BEFORE_MS && !_warned) {
      _warned = true;
      // Format remaining time: show minutes if ≥ 60 s, otherwise seconds
      const secs   = Math.ceil(remaining / 1000);
      const label  = secs >= 60
        ? `${Math.ceil(secs / 60)} min`
        : `${secs} s`;
      if (typeof showToast === "function") {
        showToast(t("config.security.session_expiry_warning", label), "warning");
      }
    }
  }

  // ── Auth status ping (runs every _PING_INTERVAL) ──────────────────────────
  // Detects server restarts: after a restart the epoch changes → session is
  // cleared server-side → logged_in becomes false → we redirect to /login.
  async function _pingAuthStatus() {
    if (_logoutInProgress) return;
    try {
      const r = await fetch("/api/auth/status");
      if (!r.ok) return;                           // network glitch — retry next cycle
      const d = await r.json();
      if (d.auth_enabled && !d.logged_in) {
        _autoLogout("restart");
      }
    } catch (_) {}
  }

  // ── Init: read config + start timers ─────────────────────────────────────
  (async function _init() {
    try {
      const r = await fetch("/api/auth/status");
      if (!r.ok) return;
      const d = await r.json();
      _authEnabled = d.auth_enabled === true;
      if (!_authEnabled) return;                   // auth off — nothing to do

      // Feature A: inactivity timer
      const mins = d.session_timeout_minutes ?? 0;
      if (mins > 0) {
        _timeoutMs      = mins * 60_000;
        _inactivityTimer = setInterval(_checkInactivity, _CHECK_INTERVAL);
      }

      // Feature B: restart-detection ping (always active when auth is on).
      // Exposed on window so dashboard.js can trigger an immediate check on
      // socket connect_error (epoch mismatch detected by server).
      window._u3d_ping_auth = _pingAuthStatus;
      _pingTimer = setInterval(_pingAuthStatus, _PING_INTERVAL);
    } catch (_) {}
  })();
})();
