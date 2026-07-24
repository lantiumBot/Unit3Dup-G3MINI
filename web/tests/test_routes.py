"""Flask route tests — health, settings, jobs, bookmarks.

Uses monkeypatch + tmp_path to isolate file I/O and avoid pulling in
the unit3dup CLI stack (unit3dup.* imports, SocketIO threads, PTY, …).

On Windows the `pty` / `termios` modules don't exist.  Jobs-blueprint
tests are skipped there automatically.
"""
import sys
import os
import types
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock


# ── Stub pty/termios/tty so core.job can be imported on Windows ──────────────
def _stub_unix_modules():
    """Insert stub modules for Unix-only imports so Windows tests don't crash."""
    if sys.platform == "win32":
        for name in ("pty", "termios", "tty", "fcntl"):
            if name not in sys.modules:
                mod = types.ModuleType(name)
                # Provide minimal attributes used by core.job
                if name == "pty":
                    mod.openpty = lambda: (0, 0)  # type: ignore[attr-defined]
                sys.modules[name] = mod

_stub_unix_modules()


# ── Minimal fixture ───────────────────────────────────────────────────────────

@pytest.fixture()
def app_ctx(tmp_path):
    """Yields (flask_app, test_client, state_dict, has_jobs_bp) with deps stubbed.

    Patches are started before registration AND kept alive through the yield so
    route handlers see the mocks when the test client makes requests.
    """
    default_web = {
        "source_folder": str(tmp_path / "source"),
        "source_folder_bookmarks": ["/a", "/b"],
        "confirm_mode": False,
        "dry_run": False,
        "recursive_scan": False,
        "max_concurrent_jobs": 1,
        "duplicate_ask_pct": 0,
        "duplicate_cache_ttl_sec": 0,
        "auto_retry_on_error": False,
        "auto_retry_max": 1,
        "job_timeout_minutes": 0,
        "auto_scan": {"enabled": False, "interval_minutes": 60},
    }
    state = {"web": dict(default_web), "u3d": {}, "tags": []}

    from flask import Flask
    mini = Flask(__name__)
    mini.config["TESTING"]    = True
    mini.config["SECRET_KEY"] = "test"

    # ── Start patches (kept alive until the fixture teardown) ─────────────────
    active_patches = []

    def _patch(target, **kw):
        p = patch(target, **kw)
        p.start()
        active_patches.append(p)

    _patch("routes.settings.load_web_config",                 side_effect=lambda: dict(state["web"]))
    _patch("routes.settings.save_web_config",                 side_effect=lambda d: state["web"].update(d))
    _patch("routes.settings.load_unit3dbot",                  side_effect=lambda: dict(state["u3d"]))
    _patch("routes.settings.save_unit3dbot",                  side_effect=lambda d: state["u3d"].update(d))
    _patch("routes.settings.load_valid_tags",                 side_effect=lambda: list(state["tags"]))
    _patch("routes.settings.save_valid_tags",                 side_effect=lambda t: None)
    _patch("routes.settings._invalidate_if_tracker_changed",  return_value=None)

    from routes.settings import bp as settings_bp
    mini.register_blueprint(settings_bp)

    # health blueprint (no external deps)
    from routes.health import bp as health_bp
    mini.register_blueprint(health_bp)

    # jobs blueprint — needs shared._jobs / _jobs_lock; pty may be missing on Windows
    jobs_dict: dict = {}
    jobs_lock = threading.Lock()
    has_jobs_bp = False
    try:
        _patch("routes.jobs._jobs",      new=jobs_dict)
        _patch("routes.jobs._jobs_lock", new=jobs_lock)
        from routes.jobs import bp as jobs_bp
        mini.register_blueprint(jobs_bp)
        has_jobs_bp = True
    except Exception:
        pass

    client = mini.test_client()
    yield mini, client, state, has_jobs_bp

    # ── Teardown: stop all patches ────────────────────────────────────────────
    for p in reversed(active_patches):
        try:
            p.stop()
        except RuntimeError:
            pass


# ── /api/health ───────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_200(self, app_ctx):
        _, client, *_ = app_ctx
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_json_fields(self, app_ctx):
        _, client, *_ = app_ctx
        data = client.get("/api/health").get_json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "uptime" in data
        assert isinstance(data["uptime"], int)
        assert data["uptime"] >= 0

    def test_public_no_auth_needed(self, app_ctx):
        """Health endpoint is always public."""
        _, client, *_ = app_ctx
        r = client.get("/api/health")
        assert r.status_code == 200


# ── /api/settings GET ─────────────────────────────────────────────────────────

class TestSettingsGet:
    def test_returns_200(self, app_ctx):
        _, client, *_ = app_ctx
        r = client.get("/api/settings")
        assert r.status_code == 200

    def test_has_required_sections(self, app_ctx):
        _, client, *_ = app_ctx
        data = client.get("/api/settings").get_json()
        assert "web" in data
        assert "unit3dbot" in data
        assert "valid_tags" in data

    def test_source_folder_in_web(self, app_ctx, tmp_path):
        _, client, *_ = app_ctx
        data = client.get("/api/settings").get_json()
        assert "source_folder" in data["web"]

    def test_new_keys_present(self, app_ctx):
        """auto_retry and job_timeout keys should be exposed."""
        _, client, *_ = app_ctx
        data = client.get("/api/settings").get_json()
        web = data["web"]
        assert "auto_retry_on_error" in web
        assert "auto_retry_max" in web
        assert "job_timeout_minutes" in web


# ── /api/settings POST ────────────────────────────────────────────────────────

class TestSettingsPost:
    def test_save_returns_ok(self, app_ctx):
        _, client, *_ = app_ctx
        r = client.post(
            "/api/settings",
            json={"web": {"source_folder": "/new/path"}},
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_save_updates_state(self, app_ctx):
        _, client, state, *_ = app_ctx
        client.post(
            "/api/settings",
            json={"web": {"source_folder": "/updated"}},
            content_type="application/json",
        )
        assert state["web"]["source_folder"] == "/updated"

    def test_empty_body_ok(self, app_ctx):
        _, client, *_ = app_ctx
        r = client.post("/api/settings", json={}, content_type="application/json")
        assert r.status_code == 200


# ── /api/settings/bookmarks ───────────────────────────────────────────────────

class TestBookmarksEndpoint:
    def test_add_bookmark(self, app_ctx):
        _, client, state, *_ = app_ctx
        state["web"]["source_folder_bookmarks"] = []
        r = client.post(
            "/api/settings/bookmarks",
            json={"action": "add", "path": "/mnt/Serie"},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "/mnt/Serie" in data.get("bookmarks", [])

    def test_remove_bookmark(self, app_ctx):
        _, client, state, *_ = app_ctx
        state["web"]["source_folder_bookmarks"] = ["/mnt/Serie", "/mnt/Films"]
        r = client.post(
            "/api/settings/bookmarks",
            json={"action": "remove", "path": "/mnt/Serie"},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "/mnt/Serie" not in data.get("bookmarks", [])
        assert "/mnt/Films" in data.get("bookmarks", [])

    def test_add_duplicate_not_duplicated(self, app_ctx):
        _, client, state, *_ = app_ctx
        state["web"]["source_folder_bookmarks"] = ["/mnt/Serie"]
        client.post(
            "/api/settings/bookmarks",
            json={"action": "add", "path": "/mnt/Serie"},
            content_type="application/json",
        )
        assert state["web"]["source_folder_bookmarks"].count("/mnt/Serie") == 1

    def test_invalid_action_returns_400(self, app_ctx):
        _, client, *_ = app_ctx
        r = client.post(
            "/api/settings/bookmarks",
            json={"action": "unknown", "path": "/x"},
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_missing_path_returns_400(self, app_ctx):
        _, client, *_ = app_ctx
        r = client.post(
            "/api/settings/bookmarks",
            json={"action": "add"},
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_max_20_bookmarks(self, app_ctx):
        """Adding more than 20 bookmarks should keep only 20 (most-recent first)."""
        _, client, state, *_ = app_ctx
        state["web"]["source_folder_bookmarks"] = [f"/p{i}" for i in range(20)]
        r = client.post(
            "/api/settings/bookmarks",
            json={"action": "add", "path": "/new"},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert len(data.get("bookmarks", [])) <= 20


# ── /api/jobs ─────────────────────────────────────────────────────────────────

class TestJobsEndpoint:
    def test_returns_list(self, app_ctx):
        _, client, _, has_jobs_bp = app_ctx
        if not has_jobs_bp:
            pytest.skip("jobs blueprint unavailable (pty missing on this platform)")
        with patch("routes.jobs._jobs", {}), \
             patch("routes.jobs._jobs_lock", threading.Lock()):
            r = client.get("/api/jobs")
        assert r.status_code == 200
        assert isinstance(r.get_json(), list)

    def test_clear_empty_ok(self, app_ctx):
        _, client, _, has_jobs_bp = app_ctx
        if not has_jobs_bp:
            pytest.skip("jobs blueprint unavailable (pty missing on this platform)")
        with patch("routes.jobs._jobs", {}), \
             patch("routes.jobs._jobs_lock", threading.Lock()), \
             patch("routes.jobs.socketio", MagicMock()):
            r = client.post("/api/jobs/clear", json={}, content_type="application/json")
        assert r.status_code == 200
