#!/usr/bin/env python3
"""Unit3Dup Dashboard — application factory."""
import eventlet
eventlet.monkey_patch()
import logging
import os
import secrets
import threading
import time as _time
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

# ── Simple scan-endpoint rate-limiter (in-memory, per IP) ────────────────────
_rl_lock = threading.Lock()
_rl_hits: dict[str, list[float]] = {}

def _rl_blocked(ip: str, endpoint: str, max_req: int = 5, window: float = 60.0) -> bool:
    key = f"{ip}:{endpoint}"
    now = _time.time()
    with _rl_lock:
        hits = [h for h in _rl_hits.get(key, []) if now - h < window]
        if len(hits) >= max_req:
            _rl_hits[key] = hits
            return True
        hits.append(now)
        _rl_hits[key] = hits
        # Purge stale entries for all other keys to prevent unbounded dict growth
        stale = [k for k, v in _rl_hits.items() if k != key and not any(now - h < window for h in v)]
        for k in stale:
            del _rl_hits[k]
        return False

os.environ.setdefault("EVENTLET_TESTS", "1")

logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("socketio").setLevel(logging.WARNING)


def _configure_file_logging() -> None:
    from core.conf import LOGS_APP_DIR
    log_file = LOGS_APP_DIR / "app.log"
    handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    if root.level == logging.WARNING:
        root.setLevel(logging.INFO)
    root.addHandler(handler)


def _load_secret_key() -> str:
    key_file = Path(__file__).parent / ".secret_key"
    if key_file.exists():
        k = key_file.read_text().strip()
        if k:
            return k
    k = secrets.token_hex(32)
    try:
        key_file.write_text(k)
    except OSError:
        pass
    return k


def create_app() -> "Flask":
    from flask import Flask
    from extensions import socketio

    _configure_file_logging()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.environ.get("U3D_SECRET_KEY") or _load_secret_key()
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

    try:
        import eventlet
        async_mode = "eventlet"
    except ImportError:
        async_mode = "threading"

    # Socket.IO CORS — résolution des origines autorisées (ordre de priorité) :
    #   1. U3D_CORS_ORIGINS=https://mondomaine.com,https://autre.com  (override manuel)
    #   2. U3D_WEB_HOST=0.0.0.0 → "*" (wildcard — cas Docker standard)
    #   3. U3D_WEB_HOST=<IP>    → liste d'origines explicites pour cette IP
    # Le dashboard dispose de sa propre couche d'auth ; wildcard est acceptable.
    _cors_env = os.environ.get("U3D_CORS_ORIGINS", "").strip()
    _host = os.environ.get("U3D_WEB_HOST", "0.0.0.0")
    _port = int(os.environ.get("U3D_WEB_PORT", "5000"))
    if _cors_env:
        # Override manuel : liste séparée par des virgules
        _cors_origins: list | str = [o.strip() for o in _cors_env.split(",") if o.strip()]
    elif _host in ("0.0.0.0", ""):
        # Binding all interfaces — on ne connaît pas l'IP publique au démarrage.
        _cors_origins = "*"
    else:
        # Bound to a specific IP: allow that IP + loopback explicitly.
        _cors_origins = [
            f"http://localhost:{_port}",
            f"http://127.0.0.1:{_port}",
            f"https://localhost:{_port}",
            f"https://127.0.0.1:{_port}",
            f"http://{_host}:{_port}",
            f"https://{_host}:{_port}",
        ]

    socketio.init_app(
        app,
        cors_allowed_origins=_cors_origins,
        async_mode=async_mode,
    )

    # ── Blueprints ────────────────────────────────────────────────────────────
    from routes.pages      import bp as pages_bp
    from routes.jobs       import bp as jobs_bp
    from routes.history    import bp as history_bp
    from routes.settings   import bp as settings_bp
    from routes.stats      import bp as stats_bp
    from routes.status     import bp as status_bp
    from routes.watcher_bp import bp as watcher_bp
    from routes.automanage import bp as automanage_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(watcher_bp)
    app.register_blueprint(automanage_bp)

    from routes.auth_bp import bp as auth_bp
    app.register_blueprint(auth_bp)

    from routes.health        import bp as health_bp
    from routes.autoscan_bp   import bp as autoscan_bp
    from routes.rss_bp        import bp as rss_bp
    from routes.inventory_bp  import bp as inventory_bp
    app.register_blueprint(health_bp)
    app.register_blueprint(autoscan_bp)
    app.register_blueprint(rss_bp)
    app.register_blueprint(inventory_bp)

    # ── Background services ───────────────────────────────────────────────────
    import core.autoscan  # noqa: F401 — starts auto-scan background thread
    from core.rss import start_rss_poller
    start_rss_poller()

    # ── Socket.IO handlers ────────────────────────────────────────────────────
    import sockets  # noqa: F401 — registers @socketio.on decorators

    from flask import request as _req

    _RATE_LIMITED_SCAN_PATHS = frozenset({"/api/scan", "/api/scan/duplicates", "/api/scan/tmdb"})

    @app.before_request
    def _scan_rate_limit():
        if _req.path in _RATE_LIMITED_SCAN_PATHS and _req.method == "POST":
            ip = (_req.headers.get("X-Forwarded-For", _req.remote_addr) or "").split(",")[0].strip()
            if _rl_blocked(ip, _req.path):
                from flask import jsonify
                return jsonify({"error": "Too many requests — retry in 60 s"}), 429

    @app.before_request
    def _auth_guard():
        from flask import session
        from core.auth import auth_enabled, is_logged_in, _SERVER_EPOCH
        if not auth_enabled():
            return
        if is_logged_in():
            # Epoch guard: if the server restarted, _SERVER_EPOCH changed.
            # Stale sessions are cleared so the browser falls through to /login.
            if session.get("u3d_epoch") != _SERVER_EPOCH:
                session.clear()
                # fall through to the 401 / redirect logic below
            else:
                return
        path = _req.path
        # Always allow: login page, health endpoint, auth API, static assets,
        # and Socket.IO transport (auth is enforced in on_connect handler)
        if (path in ("/login", "/api/health")
                or path.startswith("/api/auth/")
                or path.startswith("/static/")
                or path.startswith("/socket.io/")):
            return
        # API / JSON → 401
        if path.startswith("/api/") or _req.is_json or "application/json" in _req.headers.get("Accept", ""):
            from flask import jsonify
            return jsonify({"error": "Unauthorized"}), 401
        # Pages → redirect to login
        from flask import redirect, url_for
        return redirect(url_for("auth.login_page", next=path))

    # ── Jinja2 context: server-side auth state injected into every template ──
    @app.context_processor
    def _auth_ctx():
        from core.auth import auth_enabled as _ae, is_logged_in as _il
        # _auth_guard already enforces the epoch check and redirects stale sessions
        # to /login before any page is rendered — the epoch check here is redundant
        # and can hide the button if there is any timing discrepancy between the two
        # evaluations.  Simple rule: button visible ↔ auth is on AND user is logged in.
        show = _ae() and _il()
        return {"show_logout_btn": show}

    # ── Security headers (#18 CSP) ────────────────────────────────────────────
    @app.after_request
    def _security_headers(response):
        # Content-Security-Policy — allow inline scripts (onclick="") and CDN assets
        csp = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' ws: wss:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "worker-src blob:;"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    return app


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Unit3Dup Dashboard")
    ap.add_argument("--host",  default=os.environ.get("U3D_WEB_HOST", "0.0.0.0"))
    ap.add_argument("--port",  default=int(os.environ.get("U3D_WEB_PORT", "5000")), type=int)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--cert", default=os.environ.get("U3D_TLS_CERT", ""))
    ap.add_argument("--key",  default=os.environ.get("U3D_TLS_KEY", ""))
    args = ap.parse_args()

    from extensions import socketio
    application = create_app()

    try:
        import eventlet
        async_mode = "eventlet"
    except ImportError:
        async_mode = "threading"

    run_kwargs: dict = {
        "host":                  args.host,
        "port":                  args.port,
        "debug":                 args.debug,
        "use_reloader":          False,
        "allow_unsafe_werkzeug": (async_mode == "threading"),
    }
    if args.cert and args.key:
        print(f"[TLS] Mode HTTPS — cert={args.cert}  key={args.key}  async={async_mode}", flush=True)
        if async_mode == "eventlet":
            # eventlet.wrap_ssl() expects certfile/keyfile
            run_kwargs["certfile"] = args.cert
            run_kwargs["keyfile"]  = args.key
            # Python 3.13 raises ssl.SSLError(errno.ENOTCONN, "Closed before TLS handshake
            # with data in recv buffer") when a client closes before the handshake completes.
            # eventlet does not include ENOTCONN in ACCEPT_ERRNO so it re-raises and crashes
            # the server. Wrap eventlet.wsgi.server so accept() retries on these transient errors.
            import errno as _errno
            import ssl as _ssl
            import eventlet.wsgi as _ewsgi
            _orig_wsgi_server = _ewsgi.server
            def _tls_resilient_server(sock, *a, **kw):
                class _SafeAccept:
                    def __init__(self, s): self.__dict__["_s"] = s
                    def accept(self):
                        while True:
                            try:
                                return self._s.accept()
                            except _ssl.SSLError as e:
                                if getattr(e, "errno", None) not in (_errno.ENOTCONN, _errno.ECONNRESET):
                                    raise
                    def __getattr__(self, n): return getattr(self._s, n)
                    def __setattr__(self, n, v): setattr(self._s, n, v)
                return _orig_wsgi_server(_SafeAccept(sock), *a, **kw)
            _ewsgi.server = _tls_resilient_server
            
            _orig_handle = _ewsgi.HttpProtocol.handle
            def _safe_handle(self):
                try:
                    _orig_handle(self)
                except _ssl.SSLError:
                    pass
            _ewsgi.HttpProtocol.handle = _safe_handle
        else:
            # Werkzeug/threading expects ssl_context as a (cert, key) tuple
            run_kwargs["ssl_context"] = (args.cert, args.key)
    else:
        print(f"[TLS] Mode HTTP (cert='{args.cert}' key='{args.key}') — TLS désactivé", flush=True)
    socketio.run(application, **run_kwargs)
