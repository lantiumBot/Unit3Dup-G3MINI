#!/bin/sh
set -e

USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

groupadd -g ${GROUP_ID} unit3dup 2>/dev/null || true
useradd -u ${USER_ID} -g ${GROUP_ID} -s /bin/sh -m unit3dup 2>/dev/null || true

# ── Répertoires persistants ───────────────────────────────────────────────────
mkdir -p /data/logs/upload /data/logs/app /data/.ssl /tmp/unit3dup

# ── Initialisation des fichiers de données (premier démarrage) ───────────────
if [ ! -f /data/web_config.json ]; then echo '{}' > /data/web_config.json; fi
if [ ! -f /data/valid_tags.json ]; then echo '{"tags":[]}' > /data/valid_tags.json; fi
if [ ! -f /data/rss_feeds.json ];  then echo '[]' > /data/rss_feeds.json; fi
if [ ! -f /data/rss_items.json ];  then echo '[]' > /data/rss_items.json; fi

# ── Sauvegarde automatique lors d'une montée de version ──────────────────────
CURRENT_VERSION="${U3D_VERSION:-unknown}"
STORED_VERSION=""
if [ -f /data/.version ]; then
    STORED_VERSION=$(cat /data/.version)
fi

if [ -n "$STORED_VERSION" ] && [ "$STORED_VERSION" != "$CURRENT_VERSION" ]; then
    BACKUP="/data/web_config.json.bak.${STORED_VERSION}"
    if [ ! -f "$BACKUP" ] && [ -s /data/web_config.json ]; then
        cp /data/web_config.json "$BACKUP"
        echo "[entrypoint] Upgrade $STORED_VERSION → $CURRENT_VERSION : sauvegarde conservée dans $BACKUP"
    fi
fi
printf '%s' "$CURRENT_VERSION" > /data/.version

# ── Permissions ───────────────────────────────────────────────────────────────
chown -R ${USER_ID}:${GROUP_ID} /data /config /tmp/unit3dup /app

exec gosu unit3dup "$@"
