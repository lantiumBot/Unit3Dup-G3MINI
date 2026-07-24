FROM python:3.11-slim

# ── Variables d'environnement ─────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UNIT3DUP_CONFIG_ROOT=/config \
    HOME=/tmp/unit3dup \
    U3D_WEB_HOST=0.0.0.0 \
    U3D_WEB_PORT=5000 \
    U3D_VERSION="0.8.21"

WORKDIR /app

# ── Dépendances système ───────────────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        mediainfo \
        libmediainfo0v5 \
        poppler-utils \
        p7zip-full \
        unrar-free \
        openssl \
        gosu && \ 
    rm -rf /var/lib/apt/lists/*

# ── Virtualenv + dépendances (layer cacheable séparément du code) ─────────────
# Copier uniquement les fichiers de dépendances en premier pour que Docker
# puisse réutiliser ce layer pip tant que requirements.txt ne change pas.
COPY requirements.txt pyproject.toml README.md ./
COPY web/requirements.txt ./web/requirements.txt

RUN python -m venv .venv

ENV PATH="/app/.venv/bin:$PATH"

RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install -r web/requirements.txt

# ── Code source (invalidé à chaque changement de code, pas de pip) ────────────
COPY common    ./common
COPY unit3dup  ./unit3dup
COPY view      ./view
COPY web       ./web
COPY start_web.py ./

RUN pip install --no-deps .

# ── Symlinks : données persistantes → volume /data ───────────────────────────
# Les fichiers runtime (web_config.json, history.db, .secret_key, logs/, .ssl/)
# pointent vers /data afin d'être conservés entre les redémarrages du conteneur.
RUN rm -f  web/web_config.json web/history.db web/.secret_key \
           web/rss_feeds.json web/rss_items.json && \
rm -rf web/logs web/.ssl && \
ln -sf /data/web_config.json  web/web_config.json  && \
ln -sf /data/history.db       web/history.db       && \
ln -sf /data/.secret_key      web/.secret_key      && \
ln -sf /data/logs             web/logs             && \
ln -sf /data/.ssl             web/.ssl             && \
ln -sf /data/rss_feeds.json   web/rss_feeds.json   && \
ln -sf /data/rss_items.json   web/rss_items.json   && \
ln -sf /data/valid_tags.json  /app/valid_tags.json

RUN groupadd -g 1000 unit3dup && \
    useradd -u 1000 -g 1000 -s /bin/sh -m unit3dup && \
    chown -R unit3dup:unit3dup /app

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 5000

VOLUME ["/config", "/data"]

ENTRYPOINT ["/docker-entrypoint.sh"]
# start_web.py détecte qu'il tourne dans le venv (PATH) et lance app.py directement
CMD ["python", "start_web.py"]
