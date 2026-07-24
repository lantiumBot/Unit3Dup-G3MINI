#!/usr/bin/env python3
"""
Unit3Dup Dashboard — Python launcher
Usage: python start_web.py [--host HOST] [--port PORT] [--daemon] [--stop]
"""
import argparse
import os
import signal
import sys
import time
from pathlib import Path


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse():
    p = argparse.ArgumentParser(
        description="Unit3Dup Dashboard — Flask + Socket.IO",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host",     metavar="ADDR",
                   default=os.environ.get("U3D_WEB_HOST") or os.environ.get("HOST", "0.0.0.0"),
                   help="Interface d'écoute (ex: 127.0.0.1)")
    p.add_argument("--port",     metavar="PORT", type=int,
                   default=int(os.environ.get("U3D_WEB_PORT") or os.environ.get("PORT", "5000")),
                   help="Port TCP d'écoute")
    p.add_argument("--daemon",   action="store_true",
                   help="Lancer en arrière-plan (daemon UNIX)")
    p.add_argument("--pid-file", default="/tmp/u3dup-web.pid", metavar="PATH",
                   help="Fichier PID (mode daemon)")
    p.add_argument("--log",      default="/tmp/u3dup-web.log", metavar="PATH",
                   help="Fichier log stdout/stderr (mode daemon)")
    p.add_argument("--stop",     action="store_true",
                   help="Arrêter le daemon en cours d'exécution")
    p.add_argument("--setup",    action="store_true",
                   help="Assistant de configuration : mot de passe + certificat TLS")
    p.add_argument("--tls",  action="store_true",
                   help="Activer HTTPS (TLS)")
    p.add_argument("--cert", metavar="PATH",
                   default=os.environ.get("U3D_TLS_CERT", ""),
                   help="Chemin vers le certificat PEM (défaut: web/.ssl/cert.pem)")
    p.add_argument("--key",  metavar="PATH",
                   default=os.environ.get("U3D_TLS_KEY", ""),
                   help="Chemin vers la clé privée PEM (défaut: web/.ssl/key.pem)")
    return p.parse_args()


# ── Stop daemon ───────────────────────────────────────────────────────────────
def _stop(pid_file: str):
    pf = Path(pid_file)
    if not pf.exists():
        print(f"[STOP] Fichier PID introuvable : {pid_file}")
        sys.exit(1)
    try:
        pid = int(pf.read_text().strip())
    except ValueError:
        print(f"[STOP] PID invalide dans {pid_file}")
        pf.unlink(missing_ok=True)
        sys.exit(1)

    # SIGTERM
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[STOP] SIGTERM → processus {pid}…")
    except ProcessLookupError:
        print(f"[STOP] Processus {pid} déjà terminé.")
        pf.unlink(missing_ok=True)
        return
    except PermissionError:
        print(f"[STOP] Permission refusée (pid={pid}).")
        sys.exit(1)

    # Wait up to 5 s for graceful shutdown
    for _ in range(20):
        time.sleep(0.25)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pf.unlink(missing_ok=True)
            print("[STOP] Daemon arrêté.")
            return
    else:
        # Escalate to SIGKILL
        print(f"[STOP] Processus {pid} ne répond pas — SIGKILL…")
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
        except ProcessLookupError:
            pass
        pf.unlink(missing_ok=True)
        print("[STOP] Daemon tué (SIGKILL).")


# ── Existing daemon guard ─────────────────────────────────────────────────────
def _check_existing(pid_file: str) -> None:
    """Abort if a daemon is already running; remove stale PID file otherwise."""
    pf = Path(pid_file)
    if not pf.exists():
        return
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)
        print(f"[DAEMON] Un daemon tourne déjà (PID {pid}).")
        print(f"[DAEMON] Arrêtez-le avec : python3 start_web.py --stop")
        sys.exit(1)
    except (ValueError, ProcessLookupError):
        pf.unlink(missing_ok=True)  # stale


# ── Daemonize (double-fork + pipe sync) ──────────────────────────────────────
def _daemonize(pid_file: str, log_file: str) -> bool:
    """Double-fork to background.

    Uses a pipe so the parent blocks until the grandchild has written the PID
    file — the "daemon started" banner is only printed after the PID is ready.

    Returns True in the daemon grandchild, False in the original parent.
    """
    r_fd, w_fd = os.pipe()

    # ── Fork #1 ──────────────────────────────────────────────────────────────
    try:
        pid = os.fork()
        if pid > 0:
            # Original parent: wait for grandchild ready signal, then return.
            os.close(w_fd)
            os.read(r_fd, 1)
            os.close(r_fd)
            return False
    except OSError as exc:
        sys.exit(f"[DAEMON] Fork #1 échoué : {exc}")

    # ── Intermediate child ───────────────────────────────────────────────────
    os.close(r_fd)
    os.setsid()
    os.umask(0o022)

    try:
        if os.fork() > 0:
            os._exit(0)  # intermediate exits — no atexit / buffer flush
    except OSError as exc:
        os._exit(1)

    # ── Grandchild: redirect streams ─────────────────────────────────────────
    try:
        log_fd  = os.open(log_file,   os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        null_fd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null_fd, 0)   # stdin  → /dev/null
        os.dup2(log_fd,  1)   # stdout → log file
        os.dup2(log_fd,  2)   # stderr → log file
        os.close(log_fd)
        os.close(null_fd)
    except OSError:
        os._exit(1)

    # Write PID file, then signal parent.
    Path(pid_file).write_text(str(os.getpid()))
    os.write(w_fd, b"1")
    os.close(w_fd)
    return True


# ── Venv bootstrap ────────────────────────────────────────────────────────────
def _req_hash(project_dir: Path) -> str:
    """Return a short hash of both requirements files (project + web)."""
    import hashlib
    h = hashlib.md5()
    for req in (project_dir / "requirements.txt", project_dir / "web" / "requirements.txt"):
        if req.exists():
            h.update(req.read_bytes())
    return h.hexdigest()


def _ensure_venv(project_dir: Path, *, force: bool = False) -> None:
    venv        = project_dir / ".venv"
    hash_file   = venv / ".req_hash"
    current     = _req_hash(project_dir)

    if not venv.exists():
        print("[SETUP] Création du virtualenv Python…")
        ret = os.system(f'{sys.executable} -m venv "{venv}"')
        if ret != 0:
            sys.exit("[SETUP] Échec de création du venv")
        force = True  # new venv — always install

    if not force and hash_file.exists() and hash_file.read_text().strip() == current:
        return  # requirements unchanged — skip pip

    pip = venv / "bin" / "pip"
    print("[SETUP] Installation des dépendances (unit3dup + web)…")
    os.system(f'"{pip}" install -e "{project_dir}" -q')
    os.system(f'"{pip}" install -r "{project_dir / "web" / "requirements.txt"}" -q')
    try:
        hash_file.write_text(current)
    except OSError:
        pass


# ── Setup wizard ─────────────────────────────────────────────────────────────
def _setup_wizard(web_dir: Path) -> None:
    """Interactive first-run setup: password + TLS certificate."""
    import getpass
    import json

    print()
    print("=" * 60)
    print("  Unit3Dup Dashboard — Assistant de configuration")
    print("=" * 60)

    web_config = web_dir / "web_config.json"
    cfg: dict = {}
    if web_config.exists():
        try:
            cfg = json.loads(web_config.read_text())
        except Exception:
            pass

    # ── Password ──────────────────────────────────────────────────────────
    print()
    print("1. Authentification")
    already = cfg.get("auth_enabled") and cfg.get("auth_password_hash")
    if already:
        print("   Un mot de passe est déjà défini.")
    ans = input("   Définir / changer le mot de passe ? [O/n] : ").strip().lower()
    if ans in ("", "o", "y", "oui", "yes"):
        while True:
            pwd = getpass.getpass("   Nouveau mot de passe (min. 6 car.) : ")
            if len(pwd) < 6:
                print("   ✗ Trop court, recommencez.")
                continue
            pwd2 = getpass.getpass("   Confirmer le mot de passe      : ")
            if pwd != pwd2:
                print("   ✗ Les mots de passe ne correspondent pas.")
                continue
            break
        try:
            from werkzeug.security import generate_password_hash
            cfg["auth_password_hash"] = generate_password_hash(pwd, method="pbkdf2:sha256")
            cfg["auth_enabled"] = True
            print("   ✓ Mot de passe défini — authentification activée.")
        except ImportError:
            print("   ✗ werkzeug non disponible dans le venv — ignoré.")
    else:
        print("   → Authentification ignorée.")

    # ── TLS ───────────────────────────────────────────────────────────────
    print()
    print("2. Certificat TLS (HTTPS)")
    cert = web_dir / ".ssl" / "cert.pem"
    key  = web_dir / ".ssl" / "key.pem"
    if cert.exists() and key.exists():
        print(f"   Certificat existant dans {cert.parent}")
    ans = input("   Générer un certificat auto-signé ? [o/N] : ").strip().lower()
    if ans in ("o", "y", "oui", "yes"):
        if cert.exists() and key.exists():
            print("   → Certificat déjà présent — conservé.")
        else:
            try:
                _gen_self_signed(cert, key)
                print(f"   ✓ Certificat généré dans {cert.parent}")
                print("     Lancez avec : ./start_web.sh --tls")
            except SystemExit as exc:
                print(f"   ✗ Échec génération certificat : {exc}")
    else:
        print("   → TLS ignoré.")

    # ── Save config ───────────────────────────────────────────────────────
    print()
    try:
        tmp = web_config.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(web_config)
        print("   ✓ Configuration sauvegardée dans", web_config)
    except Exception as exc:
        print(f"   ✗ Impossible d'écrire {web_config} : {exc}")

    print()
    print("=" * 60)
    print("  Configuration terminée.")
    print("  Lancez le dashboard : ./start_web.sh")
    print("=" * 60)
    print()


# ── TLS helpers ───────────────────────────────────────────────────────────────
def _gen_self_signed(cert_path: Path, key_path: Path, bind_host: str = "") -> None:
    """Generate a self-signed certificate with SAN using openssl."""
    import socket
    import subprocess

    cert_path.parent.mkdir(parents=True, exist_ok=True)

    # Build SAN list: localhost + all local IPv4 addresses so the cert is
    # valid whether the dashboard is reached via 127.0.0.1 or a LAN IP.
    san_parts: list[str] = ["DNS:localhost", "IP:127.0.0.1"]

    # Include the explicit bind address (e.g. 10.0.0.2) so the cert is valid
    # when the dashboard is accessed via a specific LAN IP.
    if bind_host and bind_host not in ("0.0.0.0", "::", ""):
        import ipaddress
        try:
            ipaddress.ip_address(bind_host)
            entry = f"IP:{bind_host}"
        except ValueError:
            entry = f"DNS:{bind_host}"
        if entry not in san_parts:
            san_parts.append(entry)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ":" not in ip:          # skip IPv6
                entry = f"IP:{ip}"
                if entry not in san_parts:
                    san_parts.append(entry)
    except Exception:
        pass
    san = ",".join(san_parts)

    result = subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(key_path), "-out", str(cert_path),
        "-days", "3650", "-nodes",
        "-subj", "/CN=localhost",
        "-addext", f"subjectAltName={san}",
    ], capture_output=True)
    if result.returncode != 0:
        sys.exit(f"[TLS] Échec génération certificat : {result.stderr.decode()}")


def _cert_covers_host(cert_path: Path, host: str) -> bool:
    """Return True if the certificate SAN covers the given host/IP."""
    import subprocess
    if host in ("0.0.0.0", "::", ""):
        return True
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(cert_path), "-text", "-noout"],
            capture_output=True, text=True,
        )
        san_block = result.stdout
        import ipaddress
        try:
            ipaddress.ip_address(host)
            return f"IP Address:{host}" in san_block
        except ValueError:
            return f"DNS:{host}" in san_block
    except Exception:
        return True  # can't check — assume valid


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args        = _parse()
    project_dir = Path(__file__).parent.resolve()
    web_dir     = project_dir / "web"
    app_py      = web_dir / "app.py"

    if not app_py.exists():
        sys.exit(f"[ERREUR] {app_py} introuvable — vérifiez la structure du projet")

    # ── --stop ────────────────────────────────────────────────────────────────
    if args.stop:
        _stop(args.pid_file)
        return

    # ── Venv bootstrap ────────────────────────────────────────────────────────
    in_venv = sys.prefix != sys.base_prefix
    venv_py = project_dir / ".venv" / "bin" / "python"
    if not in_venv:
        _ensure_venv(project_dir)
        # Re-exec inside the venv so all packages are available.
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)

    # ── Setup wizard ──────────────────────────────────────────────────────────
    if args.setup:
        _setup_wizard(web_dir)
        return

    # ── Daemon mode ───────────────────────────────────────────────────────────
    if args.daemon:
        _check_existing(args.pid_file)
        is_child = _daemonize(args.pid_file, args.log)
        if not is_child:
            # Parent: grandchild is ready (pipe unblocked), print banner, exit.
            print()
            print("=" * 54)
            print("  Unit3Dup Dashboard — mode daemon démarré")
            print(f"  URL     : {'https' if args.tls else 'http'}://{args.host}:{args.port}")
            print(f"  PID     : {args.pid_file}")
            print(f"  Logs    : {args.log}")
            print(f"  Arrêt   : python3 start_web.py --stop")
            print("=" * 54)
            print()
            sys.stdout.flush()
            os._exit(0)
        # Daemon grandchild falls through to Flask launch below.

    else:
        print()
        print("=" * 54)
        print("  Unit3Dup Dashboard")
        print(f"  URL    : {'https' if args.tls else 'http'}://{args.host}:{args.port}")
        print("  Arrêt  : Ctrl+C")
        print("=" * 54)
        print()

    # ── TLS ────────────────────────────────────────────────────────────────────
    if args.tls:
        cert = Path(args.cert) if args.cert else web_dir / ".ssl" / "cert.pem"
        key  = Path(args.key)  if args.key  else web_dir / ".ssl" / "key.pem"
        # Regenerate if cert exists but doesn't cover the bind address (e.g. a
        # LAN IP passed via --host that was not in the SAN when first generated).
        if cert.exists() and key.exists() and not _cert_covers_host(cert, args.host):
            print(f"[TLS] Certificat existant ne couvre pas {args.host}, régénération…")
            cert.unlink(missing_ok=True)
            key.unlink(missing_ok=True)
        if not cert.exists() or not key.exists():
            print(f"[TLS] Génération d'un certificat auto-signé dans {cert.parent}…")
            _gen_self_signed(cert, key, bind_host=args.host)
            print(f"[TLS] Certificat généré : {cert}")
        os.environ["U3D_TLS_CERT"] = str(cert)
        os.environ["U3D_TLS_KEY"]  = str(key)

    # ── Launch Flask (replace current process) ────────────────────────────────
    os.chdir(str(web_dir))
    os.environ["U3D_WEB_HOST"]    = args.host
    os.environ["U3D_WEB_PORT"]    = str(args.port)
    os.environ.setdefault("EVENTLET_TESTS", "1")
    os.execv(sys.executable, [
        sys.executable, str(app_py),
        "--host", args.host,
        "--port", str(args.port),
    ])


if __name__ == "__main__":
    main()
