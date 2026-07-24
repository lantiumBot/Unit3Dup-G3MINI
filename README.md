# Unit3Dup — Fork G3MINI

Fork de [Unit3Dup](https://github.com/31December99/Unit3Dup) adapté au tracker privé **G3MINI** (compatible UNIT3D).

Le projet détecte des releases (films/séries) dans un dossier, enrichit les métadonnées (TMDB, mediainfo, screenshots), crée le `.torrent` et pousse l'upload sur le tracker — avec seed automatique via qBittorrent, Transmission ou rTorrent.

**Ce fork est distribué exclusivement sous forme d'image Docker.** Pas d'installation Python manuelle : tout se pilote via le **dashboard web** (scan visuel, file de jobs en temps réel, historique, statistiques, inventaire Gemini, configuration) servi par le conteneur.

Spécificités de ce fork par rapport à l'upstream :
- Normalisation des noms de release adaptée à G3MINI
- `personal_release` automatique selon un tag d'équipe (`uploader_tag.TAGS_TEAM`)
- Nettoyage des `.nfo` orphelins en mode watcher
- Dashboard web complet (absent de l'upstream)
- Image Docker prête à l'emploi, seule méthode de déploiement supportée

---

## Sommaire

- [Prérequis](#prérequis)
- [Configuration avant build](#configuration-avant-build)
- [Build de l'image](#build-de-limage)
- [Lancement](#lancement)
- [Configuration du tracker](#configuration-du-tracker)
- [Dashboard Web](#dashboard-web)
- [HTTPS](#https)
- [Watcher (service optionnel)](#watcher-service-optionnel)
- [Commandes ponctuelles dans le conteneur](#commandes-ponctuelles-dans-le-conteneur)
- [Sur NAS](#sur-nas)
- [Logs et mise à jour](#logs-et-mise-à-jour)
- [Tests](#tests)

---

## Prérequis

- Docker Engine + plugin Docker Compose
- Un dossier média accessible sur l'hôte (source des releases)
- Un compte sur le tracker G3MINI (URL, clé API) et, optionnellement, une clé [TMDB](https://www.themoviedb.org/settings/api) et [imgbb](https://imgbb.com)

Aucune dépendance Python, `ffmpeg`, `mediainfo` etc. à installer sur l'hôte — tout est embarqué dans l'image.

---

## Configuration avant build

```bash
git clone https://github.com/lantiumBot/Unit3Dup-G3MINI.git unit3dup
cd unit3dup
cp .env.example .env
nano .env
```

| Variable | Défaut | Rôle |
|----------|--------|------|
| `PUID` / `PGID` | `1000` | UID/GID du processus dans le conteneur (doivent correspondre au propriétaire des fichiers média sur l'hôte) |
| `TZ` | `Europe/Paris` | Fuseau horaire |
| `WEB_PORT` | `5000` | Port HTTP exposé |
| `MEDIA_DIR` | à définir | Dossier média de l'hôte monté dans le conteneur |
| `U3D_CORS_ORIGINS` | _(auto)_ | Origines CORS Socket.IO autorisées (utile derrière un reverse proxy), séparées par des virgules |

Le `docker-compose.yml` fourni monte `/storage` en dur — adapter les volumes à votre arborescence média avant de lancer le build (voir [Lancement](#lancement)).

---

## Build de l'image

```bash
docker compose build
# ou directement :
docker build -t unit3dup:latest .
```

Ce que fait le `Dockerfile` :
- part de `python:3.11-slim`
- installe les dépendances système embarquées (`ffmpeg`, `mediainfo`, `p7zip-full`, `unrar-free`, `poppler-utils`, `openssl`, `gosu`)
- crée un venv isolé (`/app/.venv`) et installe `requirements.txt` + `web/requirements.txt` dans un layer séparé du code source (cache Docker efficace entre builds)
- installe le package `unit3dup` (CLI interne, utilisée par le dashboard)
- symlink les fichiers de données runtime (`web_config.json`, `history.db`, `.secret_key`, `logs/`, `.ssl/`, `rss_*.json`) vers `/data`, monté en volume, pour persister entre redémarrages/rebuilds
- expose le port `5000` et démarre via `docker-entrypoint.sh` → `python start_web.py`

`docker-entrypoint.sh` crée l'utilisateur `unit3dup` avec l'UID/GID fournis (`PUID`/`PGID`), initialise les fichiers de données au premier démarrage, sauvegarde automatiquement `web_config.json` lors d'une montée de version, ajuste les permissions puis relance le process via `gosu` (jamais en root).

---

## Lancement

```bash
docker compose up -d
```

Volumes utilisés par défaut (`docker-compose.yml`) :

| Hôte | Conteneur | Contenu |
|------|-----------|---------|
| `./docker-data/config` | `/config` | `Unit3Dbot.json` (créé au premier démarrage) |
| `./docker-data/web-data` | `/data` | Config web, `history.db`, logs, certificats TLS |
| `/storage` | `/storage` | Dossier(s) média source — **à adapter** à votre installation |

> Adapter le chemin `/storage` du `docker-compose.yml` (ou passer par `MEDIA_DIR` dans `.env` si vous personnalisez le montage) pour pointer vers votre médiathèque réelle.

---

## Configuration du tracker

Au premier démarrage, `docker-entrypoint.sh` crée les fichiers de données dans `/data` et le conteneur génère `Unit3Dbot.json` dans `/config`.

```bash
docker compose up -d
nano docker-data/config/Unit3Dbot.json
```

| Champ | Description |
|-------|-------------|
| `Gemini_URL` | URL du tracker |
| `Gemini_APIKEY` / `Gemini_PID` | Clé API et passkey du tracker |
| `TMDB_APIKEY` / `TMDB_ACCESS_TOKEN` | Clé TMDB (le token Bearer v4 est prioritaire s'il est renseigné) |
| `IMGBB_KEY` | Hébergement des captures d'écran |
| `QBIT_*` / `TRASM_*` / rTorrent | Client torrent utilisé pour le seed automatique après upload |

```bash
docker compose restart unit3dup-web
```

Section **`uploader_tag`** (à ajouter manuellement si besoin) :

```json
"uploader_tag": {
    "TAGS_TEAM": ["MONTEAM"]
}
```

Si le nom de la release se termine par `-MONTEAM`, `personal_release` est activé automatiquement à l'upload.

Le reste de la configuration (mot de passe dashboard, TLS, règles de scan, doublons, webhook…) se fait ensuite entièrement depuis l'interface web, sans réédition manuelle de JSON.

---

## Dashboard Web

Ouvrir `http://<ip-hôte>:5000` (ou le port choisi via `WEB_PORT`).

Pages disponibles : **Jobs** · **Historique** · **Stats** · **Status** · **RSS** · **Inventaire** · **Configuration**

Le dashboard permet de scanner le dossier média monté, sélectionner les releases à uploader, suivre chaque job en direct (console PTY), consulter l'historique des uploads, l'inventaire des torrents déjà sur le tracker, et régler toute la configuration (authentification, scan planifié, doublons, webhook, retry automatique…).

Documentation complète (architecture interne, API REST, Socket.IO, authentification…) : **[README_WEB.md](README_WEB.md)**.

---

## HTTPS

Le service `unit3dup-web` du `docker-compose.yml` fourni démarre avec `--tls` (certificat auto-signé généré au premier démarrage dans `/data/.ssl`, via le volume `./docker-data/web-data`). Retirer `--tls` de la commande du service dans `docker-compose.yml` pour rester en HTTP simple (par exemple derrière un reverse proxy qui gère déjà le TLS).

---

## Watcher (service optionnel)

Le mode watcher (surveillance continue d'un dossier, upload automatique) tourne comme service Docker séparé, désactivé par défaut :

```bash
docker compose --profile watcher up -d
```

Volumes dédiés : `./docker-data/watch` → `/watch`, `./docker-data/done` → `/done`.

---

## Commandes ponctuelles dans le conteneur

La CLI `unit3dup` reste utilisable ponctuellement à l'intérieur du conteneur, pour du dépannage ou un upload manuel hors dashboard :

```bash
docker compose run --rm unit3dup-web unit3dup -u /storage/fichier.mkv
docker compose exec unit3dup-web unit3dup --check
```

Elle n'est pas destinée à un usage régulier en dehors du conteneur — le dashboard web est le point d'entrée normal.

---

## Sur NAS

Adapter les chemins de volumes (`/volume1/...`) et `PUID`/`PGID` à l'utilisateur propriétaire des fichiers média. Si aucun client torrent n'est joignable depuis le conteneur, désactiver le seed automatique côté configuration (dashboard) ou lancer le watcher avec `-noseed` pour éviter les redémarrages en boucle causés par un client indisponible.

---

## Logs et mise à jour

```bash
docker compose logs -f unit3dup-web
git pull
docker compose build --no-cache
docker compose up -d
```

`web_config.json` est sauvegardé automatiquement (`web_config.json.bak.<ancienne_version>` dans `/data`) lors d'un changement de version d'image.

---

## Tests

La suite de tests du dashboard peut être lancée à l'intérieur du conteneur, sans rien installer sur l'hôte :

```bash
docker compose exec unit3dup-web python -m pytest web/tests/ -v
```

~278 tests côté dashboard web (aucun skip).

---

## Projet original

Basé sur [Unit3Dup](https://github.com/31December99/Unit3Dup) — licence MIT.
