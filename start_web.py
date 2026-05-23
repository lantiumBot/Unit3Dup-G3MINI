#!/usr/bin/env python3
"""
Unit3Dup Dashboard — Python launcher
Usage: python start_web.py [--host HOST] [--port PORT] [--daemon] [--stop]
"""
import argparse
import os
import sys
import time
from pathlib import Path


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse():
    p = argparse.ArgumentParser(
        description="Unit3Dup Dashboard — Flask + Socket.IO",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host",     default="0.0.0.0",           metavar="ADDR",
                   help="Interface d'écoute (ex: 127.0.0.1)")
    p.add_argument("--port",     default=5000, type=int,       metavar="PORT",
                   help="Port TCP d'écoute")
    p.add_argument("--daemon",   action="store_true",
                   help="Lancer en arrière-plan (daemon UNIX)")
    p.add_argument("--pid-file", default="/tmp/u3dup-web.pid", metavar="PATH",
                   help="Fichier PID (mode daemon)")
    p.add_argument("--log",      default="/tmp/u3dup-web.log", metavar="PATH",
                   help="Fichier log stdout/stderr (mode daemon)")
    p.add_argument("--stop",     action="store_true",
                   help="Arrêter le daemon en cours d'exécution")
    return p.parse_args()


# ── Stop daemon ───────────────────────────────────────────────────────────────
def _stop(pid_file: str):
    import signal
    pf = Path(pid_file)
    if not pf.exists():
        print(f"[STOP] Fichier PID introuvable: {pid_file}")
        sys.exit(1)
    try:
        pid = int(pf.read_text().strip())
    except ValueError:
        print(f"[STOP] PID invalide dans {pid_file}")
        sys.exit(1)
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[STOP] Signal SIGTERM envoyé au processus {pid}.")
        # Wait briefly for clean shutdown
        for _ in range(20):
            time.sleep(0.25)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        pf.unlink(missing_ok=True)
        print("[STOP] Daemon arrêté.")
    except ProcessLookupError:
        print(f"[STOP] Processus {pid} déjà terminé.")
        pf.unlink(missing_ok=True)
    except PermissionError:
        print(f"[STOP] Permission refusée pour SIGTERM pid={pid}")
        sys.exit(1)


# ── Daemonize (double-fork, UNIX only) ───────────────────────────────────────
def _daemonize(pid_file: str, log_file: str) -> bool:
    """Fork to background. Returns True in daemon child, False in parent."""
    # First fork — detach from terminal
    try:
        pid = os.fork()
        if pid > 0:
            return False  # original parent: caller will print info and exit
    except OSError as exc:
        sys.exit(f"[DAEMON] Fork #1 échoué: {exc}")

    os.setsid()   # new session, no controlling terminal
    os.umask(0o022)

    # Second fork — prevent acquiring a new terminal
    try:
        if os.fork() > 0:
            sys.exit(0)   # intermediate parent exits silently
    except OSError as exc:
        sys.exit(f"[DAEMON] Fork #2 échoué: {exc}")

    # Redirect standard streams
    log_fd  = os.open(log_file,  os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    null_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null_fd, sys.stdin.fileno())
    os.dup2(log_fd,  sys.stdout.fileno())
    os.dup2(log_fd,  sys.stderr.fileno())
    os.close(log_fd)
    os.close(null_fd)

    # Write PID file
    Path(pid_file).write_text(str(os.getpid()))
    return True  # daemon grandchild: continue to run Flask


# ── Venv bootstrap ────────────────────────────────────────────────────────────
def _ensure_venv(project_dir: Path) -> Path:
    venv = project_dir / ".venv"
    if not venv.exists():
        print("[SETUP] Création du virtualenv Python…")
        ret = os.system(f'{sys.executable} -m venv "{venv}"')
        if ret != 0:
            sys.exit("[SETUP] Échec de création du venv")

    pip = venv / "bin" / "pip"
    print("[SETUP] Installation des dépendances (unit3dup + web)…")
    os.system(f'"{pip}" install -e "{project_dir}" -q')
    os.system(f'"{pip}" install -r "{project_dir / "web" / "requirements.txt"}" -q')
    return venv / "bin" / "python"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args        = _parse()
    project_dir = Path(__file__).parent.resolve()
    web_dir     = project_dir / "web"
    app_py      = web_dir / "app.py"

    if not app_py.exists():
        sys.exit(f"[ERREUR] {app_py} introuvable — vérifiez la structure du projet")

    # ── --stop ─────────────────────────────────────────────────────────────
    if args.stop:
        _stop(args.pid_file)
        return

    # ── Venv bootstrap ──────────────────────────────────────────────────────
    in_venv   = sys.prefix != sys.base_prefix
    venv_py   = project_dir / ".venv" / "bin" / "python"
    if not in_venv:
        if not venv_py.exists():
            _ensure_venv(project_dir)
        # Re-exec with the venv interpreter so all packages are available
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)

    # ── Daemon mode ─────────────────────────────────────────────────────────
    if args.daemon:
        is_child = _daemonize(args.pid_file, args.log)
        if not is_child:
            # Parent: print startup info and exit
            print()
            print("=" * 54)
            print("  Unit3Dup Dashboard — mode daemon démarré")
            print(f"  URL     : http://{args.host}:{args.port}")
            print(f"  PID     : {args.pid_file}")
            print(f"  Logs    : {args.log}")
            print(f"  Arrêt   : python start_web.py --stop")
            print("=" * 54)
            print()
            sys.exit(0)
        # Daemon child falls through to the exec below

    else:
        # Foreground: print startup banner
        print()
        print("=" * 54)
        print("  Unit3Dup Dashboard")
        print(f"  URL    : http://{args.host}:{args.port}")
        print("  Arrêt  : Ctrl+C")
        print("=" * 54)
        print()

    # ── Launch Flask (replace current process) ──────────────────────────────
    os.chdir(str(web_dir))
    os.environ["U3D_WEB_HOST"] = args.host
    os.environ["U3D_WEB_PORT"] = str(args.port)
    # Masque l'avertissement EventletDeprecationWarning au boot (eventlet/__init__.py)
    os.environ["EVENTLET_TESTS"] = "1"
    os.execv(sys.executable, [
        sys.executable, str(app_py),
        "--host", args.host,
        "--port", str(args.port),
    ])


if __name__ == "__main__":
    main()
