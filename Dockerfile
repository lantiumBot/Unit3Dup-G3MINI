FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UNIT3DUP_CONFIG_ROOT=/config \
    HOME=/tmp/unit3dup

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        mediainfo \
        libmediainfo0v5 \
        poppler-utils \
        p7zip-full \
        unrar-free && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md ./
COPY common ./common
COPY unit3dup ./unit3dup
COPY view ./view

RUN pip install --upgrade pip && \
    pip install .

RUN mkdir -p /config /watch /done /data "$HOME" && \
    chmod 777 "$HOME"

ENTRYPOINT ["unit3dup"]
