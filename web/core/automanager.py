"""AutoManager: automatic qBit seeding management (pause/resume/Gemini dead scan)."""
import threading
import time
import urllib.parse
import urllib.request
import json
from datetime import datetime

from core.conf import load_web_config, load_unit3dbot


class AutoManager:
    _SEEDING_STATES = {"uploading", "stalledUP", "forcedUP", "queuedUP"}
    _PAUSED_STATES  = {"pausedUP", "stoppedUP"}

    def __init__(self):
        self._log: list[dict] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="AutoManager").start()

    def _entry(self, msg: str, level: str = "info") -> dict:
        return {"ts": datetime.now().isoformat(), "msg": msg, "level": level}

    def _log_add(self, msg: str, level: str = "info"):
        with self._lock:
            self._log.append(self._entry(msg, level))
            if len(self._log) > 200:
                self._log = self._log[-200:]

    def get_log(self) -> list:
        with self._lock:
            return list(self._log)

    def _qbit_client(self):
        u3d = load_unit3dbot()
        cfg = u3d.get("TORRENT_CLIENT_CONFIG", {})
        import qbittorrentapi
        cl = qbittorrentapi.Client(
            host=cfg.get("QBIT_HOST", "http://localhost"),
            port=cfg.get("QBIT_PORT", 8080),
            username=cfg.get("QBIT_USER", "admin"),
            password=cfg.get("QBIT_PASS") or "",
            VERIFY_WEBUI_CERTIFICATE=False,
            REQUESTS_ARGS={"timeout": 8},
        )
        cl.auth_log_in()
        return cl, cfg.get("TAG", "ADDED TORRENTS")

    def _do_auto_remove(self, cfg: dict, cl, tag: str):
        after_secs  = int(cfg.get("after_days", 30)) * 86400
        min_seeders = int(cfg.get("min_seeders", 5))
        paused = []
        for t in cl.torrents_info(tag=tag):
            st = t.get("seeding_time", 0) or 0
            nc = t.get("num_complete", 0) or 0
            if t.state in self._SEEDING_STATES and st >= after_secs and nc >= min_seeders:
                paused.append(t.hash)
                self._log_add(f"PAUSE: {t.name[:60]} — {st//86400}j seedé, {nc} seeders", "warning")
        if paused:
            cl.torrents_pause(torrent_hashes=paused)
            self._log_add(f"{len(paused)} torrent(s) mis en pause", "warning")

    def _do_auto_reseed(self, cfg: dict, cl, tag: str):
        below   = int(cfg.get("below_seeders", 2))
        resumed = []
        for t in cl.torrents_info(tag=tag):
            nc = t.get("num_complete", 0) or 0
            if t.state in self._PAUSED_STATES and nc < below:
                resumed.append(t.hash)
                self._log_add(f"REPRISE: {t.name[:60]} — seulement {nc} seeders", "info")
        if resumed:
            cl.torrents_resume(torrent_hashes=resumed)
            self._log_add(f"{len(resumed)} torrent(s) relancé(s)", "info")

    def _do_gemini_scan(self, cfg: dict, cl, tag: str):
        u3d    = load_unit3dbot()
        tc     = u3d.get("TRACKER_CONFIG", {})
        url    = (tc.get("Gemini_URL") or "").rstrip("/")
        apikey = tc.get("Gemini_APIKEY") or ""
        if not url or not apikey:
            self._log_add("Gemini non configuré (URL/APIKEY manquants)", "error")
            return
        try:
            req = urllib.request.Request(
                f"{url}/api/torrents?api_token={apikey}&dead=1",
                headers={"Accept": "application/json", "User-Agent": "G3MINI-AutoManager/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = json.loads(r.read())
        except Exception as exc:
            self._log_add(f"Gemini scan erreur: {exc}", "error")
            return

        dead_names = set()
        for item in (raw.get("data") or []):
            n = item.get("attributes", {}).get("name") or item.get("name") or ""
            if n:
                dead_names.add(n.lower())

        if not dead_names:
            self._log_add("Gemini: aucune release dead trouvée")
            return

        self._log_add(f"Gemini: {len(dead_names)} release(s) dead détectées")
        resumed = []
        for t in cl.torrents_info(tag=tag):
            if t.name.lower() in dead_names and t.state in self._PAUSED_STATES:
                resumed.append(t.hash)
                self._log_add(f"GEMINI REPRISE: {t.name[:60]}", "info")
        if resumed:
            cl.torrents_resume(torrent_hashes=resumed)

    def _is_night_mode(self, am_cfg: dict) -> bool:
        nm = am_cfg.get("night_mode", {})
        if not nm.get("enabled"):
            return False
        h     = datetime.now().hour
        start = int(nm.get("start_hour", 0))
        end   = int(nm.get("end_hour",   7))
        if start <= end:
            return start <= h < end
        return h >= start or h < end  # spans midnight

    def _cycle(self):
        web_cfg = load_web_config()
        am_cfg  = web_cfg.get("auto_manage", {})
        if not am_cfg.get("enabled"):
            return
        self._log_add("── Cycle AutoManager ──")
        try:
            cl, tag = self._qbit_client()
        except Exception as exc:
            self._log_add(f"qBit connexion échouée: {exc}", "error")
            return
        try:
            night = self._is_night_mode(am_cfg)
            if night:
                self._log_add("Mode nuit actif — pause auto désactivée", "info")
            ar = am_cfg.get("auto_remove", {})
            if ar.get("enabled") and not night:
                self._do_auto_remove(ar, cl, tag)
            rs = am_cfg.get("auto_reseed", {})
            if rs.get("enabled"):
                self._do_auto_reseed(rs, cl, tag)
                if rs.get("gemini_dead_scan"):
                    self._do_gemini_scan(rs, cl, tag)
        finally:
            try:
                cl.auth_log_out()
            except Exception:
                pass
        self._log_add("── Fin cycle ──")

    def run_now(self):
        threading.Thread(target=self._cycle, daemon=True, name="AM-manual").start()

    def _loop(self):
        time.sleep(10)
        while True:
            web_cfg  = load_web_config()
            interval = int(web_cfg.get("auto_manage", {}).get("interval_minutes", 60)) * 60
            self._cycle()
            time.sleep(max(60, interval))


_auto_manager = AutoManager()
