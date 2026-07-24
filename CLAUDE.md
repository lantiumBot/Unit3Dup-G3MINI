# CLAUDE.md — Unit3Dup G3MINI

Contexte projet pour Claude Code. À lire en priorité avant toute modification.

---

## Vue d'ensemble

**Unit3Dup G3MINI** est un bot d'upload automatique pour trackers privés compatibles UNIT3D (type Gemini).
Il détecte des releases (films/séries), interroge le tracker et pousse les torrents via qBittorrent / Transmission / rTorrent.

Le dépôt contient deux surfaces :
- **CLI** (`unit3dup/`, `common/`) — logique métier Python (scan, TMDB, upload, watcher, AutoManager)
- **Dashboard web** (`web/`) — interface Flask + Socket.IO qui orchestre le CLI et expose une UI browser

---

## Architecture web (`web/`)

### Fichiers principaux

| Fichier | Rôle |
|---------|------|
| `app.py` | Application factory (`create_app()`), enregistre blueprints + socketio, rate-limiter scan |
| `extensions.py` | Singleton `SocketIO` (initialisé avant `init_app`) |
| `shared.py` | État partagé entre routes et scheduler : `_jobs`, `_jobs_lock`, `_job_queue` |
| `sockets.py` | Handlers Socket.IO (`connect`, `stdin`, `watcher_stdin`) |

### Sous-modules `core/`

| Module | Responsabilité |
|--------|---------------|
| `conf.py` | Constantes de chemins, I/O atomique JSON, `default_tracker_name()`, historique/transcripts (délègue à `db.py`), logs, persistance queue, `_rotate_upload_logs()` |
| `db.py` | Backend SQLite WAL (`history.db`) — `init_db()`, `db_history_query()`, `db_upsert_history_paths()`, `db_add_history_entries()`, `db_update_tracker_id_if_missing()`, `db_save_transcript()`, `db_get_transcript()`, etc. ; migration JSON legacy uniquement si `history.db` n'existait pas au démarrage (`_db_existed`) |
| `job.py` | `Job` (PTY subprocess), `JobResultCollector`, scheduler thread, restauration queue au démarrage ; `_max_concurrent()` avec cache TTL 5 s ; timeout watchdog (`job_timeout_minutes`) ; `_auto_retry()` après erreur (`auto_retry_on_error`, `auto_retry_max`) ; `job_output` émis avec `compress=True` |
| `scanner.py` | Scan dossier source — `_scan_dir()` interne + `scan_source()` publique (récursif optionnel) ; règle `collection` avec `collection_tags` + filtre extras/samples (`_EXTRA_RE`) ; `_is_season()` détecte les packs saison (`_SEASON_RE` = `\bS\d{2}\b`) **et** les dossiers épisode individuels (`_SE_RE` = `S\d{1,2}E\d{1,4}`) — les deux donnent `type="season"` ; `season_num`/`episode_num` extraits par `_parse_season_episode()` dans `scan_source()` |
| `duplicate.py` | Vérification doublons Gemini + cache SQLite TTL ; cache via `db_dup_cache_*` ; collection → vérif par fichier individuel ; pré-filtre historique local |
| `tmdb_search.py` | Recherche TMDB légère (sans stack CLI) — `search_tmdb_item(name, type)`, `enrich_items_with_tmdb(items)` ; credentials depuis `load_unit3dbot()` ; concurrence `ThreadPoolExecutor(4)` ; compteur de progression protégé par `threading.Lock` (`done_lock`) |
| `stream.py` | `ConsoleStream` — normalise la sortie PTY (ANSI, tqdm `\r`, backspace) |
| `checker.py` | Santé des services (cache 30 s, `_STATUS_LOCK` thread-safe) ; token API dans `Authorization: Bearer` |
| `torrent.py` | API qBittorrent (liste **tous** les torrents, sans filtre de tag) — cache 15 s avec `_QBIT_LOCK` ; `get_qbit_gemini_seeding_ids(gemini_url)` — détecte les torrents Gemini en seed via champ `comment` qBit (regex `/torrents/(\d+)`) → set d'IDs numériques ; cache 60 s `_SEEDING_LOCK` |
| `watcher.py` | `WatcherService` — PTY `unit3dup --watcher` |
| `automanager.py` | `AutoManager` — pause/reseed automatique qBit |
| `autoscan.py` | `_AutoScanState` — scan périodique en arrière-plan, émet `auto_scan_done` via Socket.IO |
| `gemini_inventory.py` | `sync_gemini_inventory()` — inventaire Gemini avec optimisation TTL + count check ; `trigger_background_inventory()` — sync asynchrone ; `get_inventory_status()` — état courant ; `should_refresh_inventory()` — décision TTL |
| `auth.py` | Auth session mono-utilisateur (bcrypt werkzeug), garde brute-force par IP ; `auth_enabled()` avec cache TTL 5 s (`_auth_cache_lock`) + `invalidate_auth_cache()` |

### Blueprints `routes/`

| Blueprint | Routes |
|-----------|--------|
| `pages.py` | HTML (index, history, settings, stats, status, rss, inventory) |
| `jobs.py` | `/api/scan`, `/api/scan/duplicates`, `/api/jobs`, `/api/jobs/clear`, `/api/jobs/<id>`, `/api/jobs/reorder`, `/api/jobs/<id>/retry` |
| `history.py` | `/api/history` (paginée + filtres), `/api/history/detail/<id>`, `/api/history/item`, `/api/history/export.csv`, `/api/history/recheck` (POST — vérifie chemins sur tracker) |
| `settings.py` | `/api/settings` (invalide `inventory_state.json` si credentials tracker changent) — GET retourne `{web, unit3dbot, valid_tags}` ; POST fusionne les sections entrantes dans la config existante ; `POST /api/settings/bookmarks` gère les favoris dossiers sources (`action: "add"\|"remove"`, max 20, plus récent en premier) ; `GET /api/settings/export` — télécharge `web_config.json` ; `POST /api/settings/web-import` — importe (deep-merge) un `web_config.json` |
| `stats.py` | `/api/stats`, `/api/torrents` |
| `status.py` | `/api/status` |
| `watcher_bp.py` | `/api/watcher/*` |
| `automanage.py` | `/api/automanage/*` |
| `auth_bp.py` | `/login`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/password`, `/api/auth/disable`, `/api/auth/status` |
| `health.py` | `/api/health` (public, toujours exempt d'auth) |
| `autoscan_bp.py` | `/api/autoscan/status` |
| `inventory_bp.py` | `GET /api/inventory/status`, `POST /api/inventory/sync {force: bool}`, `GET /api/inventory/items` (paginé, `?page=&limit=&search=&sort=name\|date&order=asc\|desc`), `GET /api/inventory/download/<tracker_id>` (proxy `.torrent`), `GET /api/inventory/seeding` (IDs Gemini en seed, cache 60 s) |

### Stockage

```
web/
├── web_config.json             # Config dashboard (source_folder, règles, doublons, recursive_scan,
│                               #   auto_manage, auto_scan, auth_enabled, auth_password_hash…)
├── history.db                  # SQLite WAL — 5 tables :
│                               #   history     (paths uploadés)
│                               #   transcripts (job_id → résultat)
│                               #   dup_cache   (cache TTL checks Gemini)
│                               #   job_queue   (jobs pending — survivent au redémarrage)
│                               #   app_state   (état inventory Gemini, clé "gemini_inventory")
│                               #   Remplace : history.json, history_transcripts.json,
│                               #   duplicate_check_cache.json, job_queue.json, inventory_state.json
│                               #   Migration automatique depuis les JSON legacy au premier démarrage
├── .secret_key                 # Clé secrète Flask (générée auto)
├── .ssl/                       # Certificats TLS (--tls ou --setup)
│   ├── cert.pem
│   └── key.pem
└── logs/
    ├── upload/<job_id>.json    # JSON complet par job (audit) — rotation 500 fichiers max
    └── app/app.log             # Logs Flask + Python (rotation 5 Mo × 3)
```

Config tracker/client : `~/Unit3Dup_config/Unit3Dbot.json` (surcharger via `UNIT3DUP_CONFIG_ROOT`).

Clés `web_config.json` ajoutées récemment :
- `auto_scan.enabled` / `auto_scan.interval_minutes` — scan périodique en fond
- `auth_enabled` / `auth_password_hash` — protection mot de passe dashboard
- `source_folder_bookmarks: []` — liste des dossiers sources favoris (max 20, plus récent en premier)
- `auto_retry_on_error: false` / `auto_retry_max: 1` — retry automatique en cas d'erreur job
- `job_timeout_minutes: 0` — timeout watchdog (0 = désactivé)
- `session_timeout_minutes: 0` — déconnexion automatique par inactivité (0 = désactivé)
- `auto_upload.enabled: false` / `auto_upload.max_per_run: 5` — création auto de jobs après chaque cycle auto-scan
- `webhook_url: ""` — URL POST notifiée à la fin de chaque job (vide = désactivé)
- `webhook_format: "raw"` — format du webhook : `"raw"` (JSON brut) ou `"discord"` (embed Discord coloré)
- `log_retention_days: 30` — purge automatique des logs upload plus anciens que N jours (0 = désactivé)
- `inventory_cache_ttl_hours: 24` — durée de validité du cache inventory Gemini ; 0 = count check à chaque connexion

---

## Comportements importants à connaître

### Scan et historique
- `scan_source()` exclut les chemins déjà dans `history.db` (sauf `include_history=True`)
- `scan_source()` accepte `recursive=True` : les dossiers classifiés `unknown` sont développés un niveau plus bas (ex : `Films/`, `Séries/` dans le dossier source)
- `apply_duplicate_checks()` pré-filtre les items "pending" qui seraient dans l'historique **avant** d'appeler l'API Gemini
- Items déjà uploadés → status `"history"`, jamais re-vérifiés côté tracker
- Tous les types (`file`, `season`, `integrale`, `collection`) sont filtrés par `valid_tags` si configuré ; tag invalide → `status: "pending"`, `tag_valid: false` (item décochable mais sélectionnable — pas un skip automatique)
- `PermissionError` sur un sous-dossier → ajouté à `skipped[]`, scan continue ; réponse `/api/scan` inclut `skipped: [...]`

### Inventaire Gemini (`gemini_inventory.py`)
- **Optimisation à deux niveaux** — découple le fetch Gemini du pipeline de scan pour ne jamais bloquer l'analyse :
  1. **TTL** (`inventory_cache_ttl_hours`, défaut 24 h) : si `last_checked_at` est récent → skip total, 0 appel réseau
  2. **Count check** : si TTL expiré → `GET /api/torrents/filter?uploader=<u>&perPage=1` lit `meta.total` (1 requête). Si total == `total_fetched` stocké → met à jour `last_checked_at`, retourne `cached=True`. Si total différent → re-fetch complet (delta via `INSERT OR IGNORE`)
- **Guard cache** : `_has_cache = last_total >= 0 and (not cached_username or cached_username == username)` — remplace l'ancien test `last_key == run_key` qui causait un re-fetch si `source_folder` différait entre BG et scan. Le TTL est maintenant indépendant du dossier source.
- **Sync en arrière-plan** : `trigger_background_inventory(source_folder, *, force)` — lance un thread daemon ; mutex `_bg_status_lock` évite les runs concurrents ; émet `inventory_started` puis `inventory_done` Socket.IO
- **`silent=True`** : `_fetch_all_user_torrents()` accepte `*, silent: bool = False` — supprime les émissions `scan_progress` quand la BG sync tourne en parallèle d'un scan (évite de polluer la barre de progression du scan)
- **Déclenchement automatique au connect** : `on_connect()` dans `sockets.py` appelle `should_refresh_inventory()` ; si TTL expiré → `trigger_background_inventory(..., silent=True)` silencieux
- **Endpoint manuel** : `POST /api/inventory/sync {force: bool}` → settings page ; `GET /api/inventory/status` → état courant
- **Page Inventaire** (`/inventory`) : liste tous les uploads Gemini depuis `app_state["torrents_list"]` (via `get_inventory_list()`) — **tous les torrents**, indépendamment de la présence locale des fichiers ; tri (name/date asc/desc) + filtre seed (client-side) + recherche (server-side) + pagination 50/100/200 ; badge total ; clic en-tête pour trier
- **Statut seeding** : `GET /api/inventory/seeding` → `get_qbit_gemini_seeding_ids(gemini_url)` dans `torrent.py` — pour chaque torrent qBit dont l'announce contient le domaine Gemini, lit `torrents_properties(hash).comment` (UNIT3D = URL de page, ex `https://gemini-tracker.org/torrents/5312`), extrait l'ID via regex `_COMMENT_ID_RE = re.compile(r"/torrents/(\d+)")` → set de strings ; comparaison `_seedingIds.has(String(row.tracker_id))` côté JS — match exact par ID numérique
- **Download** : `row.download_url = f"{gemini_url}/torrents/download/{tid}"` — lien direct tracker dans nouvel onglet (`target="_blank"`) ; le proxy `/api/inventory/download/<id>` existe encore (injecte `api_token`) mais n'est plus utilisé par l'UI
- Les champs `sortField=created_at&sortDirection=desc` sont injectés dans `torrent_obj.tracker.params` avant le premier appel ; le tri est propagé automatiquement sur toutes les pages
- `Tracker._get()` gère automatiquement le rate-limit HTTP 429 (backoff 60 s) ; pause 0,7 s entre pages (≈ 85 req/min)
- Username : `TRACKER_CONFIG.Gemini_USERNAME` (prioritaire) ou GET `/api/user` via `requests` ; mis en cache mémoire pour la session
- Pour chaque match local — **écriture additive dans SQLite, jamais de DELETE** :
  - Nouveau chemin → `db_add_history_entries()` (`INSERT OR IGNORE`) — n'écrase jamais une entrée existante
  - Chemin déjà présent → `db_update_tracker_id_if_missing()` (`UPDATE … WHERE tracker_id IS NULL`) — enrichit uniquement si manquant
- **État persistant** (`app_state` clé `"gemini_inventory"`) : `username` (ex `last_run_key`), `last_checked_at`, `total_fetched`, `last_run_at`, `added`, `source_folder`, `releases`, `torrents_list` (liste complète de tous les torrents Gemini pour la page Inventaire)
- `force=True` → ignore TTL + count check, force un re-fetch complet (bouton "Forcer la re-synchronisation" dans Settings)
- `invalidate_inventory_cache()` efface `username` + `last_checked_at` dans `app_state` + le cache API username ; appelé automatiquement quand `Gemini_URL`, `Gemini_APIKEY` ou `Gemini_USERNAME` changent dans les settings
- Dans `/api/scan`, la Phase 1 (inventory) est quasi-instantanée si TTL OK (0 appel réseau) ou 1 requête count si TTL expiré sans changement
- Settings → carte "Inventaire Gemini" : TTL configurable, bouton sync, bouton force, statut du dernier run
- `inventory_cache_ttl_hours: 24` dans `web_config.json` (0 = check du count à chaque connexion)
- **Notifications centre** : `socket.on("inventory_started")` et `socket.on("inventory_done")` dans `dashboard.js` → `addNotifToCenter(...)` avec message adapté (démarrage / mis à jour / inchangé / erreur)

### Pipeline `/api/scan` (ordre)
1. **inventory** — `sync_gemini_inventory()` (pré-filtre historique)
2. **scanning** — `scan_source()` (détection type, règles collection, etc.)
3. **tmdb** — `enrich_items_with_tmdb()` (recherche TMDB pour items `pending`/`duplicate_ask`)
4. **duplicates** — `apply_duplicate_checks()` (peut utiliser `tmdb_id` dans la clé de cache future)
5. **done**

La barre de progression Socket.IO (`scan_progress`) suit les 5 phases : 0 % → 15 → 45 → 70 → 90 → 100 %.

### TMDB scan automatique (`core/tmdb_search.py`)
- `search_tmdb_item(name, item_type)` → parse le nom avec `guessit`, appelle TMDB `/search/movie` ou `/search/tv` directement via `requests`
- Credentials : `TRACKER_CONFIG.TMDB_ACCESS_TOKEN` (Bearer) ou `TRACKER_CONFIG.TMDB_APIKEY` (query param)
- `enrich_items_with_tmdb(items, *, socketio)` : concurrence `ThreadPoolExecutor(max_workers=4)` ; émet `scan_progress {"phase":"tmdb","done":N,"total":N}` à chaque résultat ; ne modifie pas les items qui ont déjà un `tmdb_id`
- `tmdb_id` est stocké dans chaque scan item et propagé via `job.to_dict()["tmdb_id"]`
- Côté JS : `activeJobs[jobId].tmdbId` est initialisé depuis `j.tmdb_id` à la création des jobs (`startJobs()`, `syncJobsFromApi()`, `job_list` socket)
- Colonne éditable dans la table de scan : l'utilisateur peut saisir/corriger l'ID avant de lancer les jobs
- Injection automatique via stdin quand le CLI demande `"Please digit a valid TMDB ID"` (pattern `_TMDB_PROMPT_RE` dans `dashboard-console.js` → `checkTmdbPrompt` → `autoSendTmdb`)

### Collection — règle de classification (`core/scanner.py`)
- `_EXTRA_RE` filtre sample/trailer/featurette/bonus des vidéos avant de compter les "vrais" films
- `_real_movie_files(video_files)` → liste sans extras (si résultat vide, retourne la liste originale)
- `_is_collection_by_rule(tag, tag_valid, col_rule)` — priorité :
  1. `collection_tags` non-vide → seulement si tag dans la liste
  2. `require_valid_tag=True` → seulement si tag valide
  3. Défaut → tout dossier multi-vidéo (legacy)
- Config dans `web_config.json` → `rules.collection.{enabled, require_valid_tag, collection_tags}`
- UI dans Paramètres → section COLLECTION (carte avec 3 champs)

### Boutons post-scan
- **"Revérification"** (`rescanFolder()`) : appelle `/api/scan` avec `force_inventory=True` + `include_history=True`
- **"Doublons"** (`recheckDuplicates()`) : POST vers `/api/scan/duplicates` avec les items courants ; fusionne les résultats dans `scanItems` sans re-scanner le disque
- Les deux boutons n'apparaissent que si un scan a déjà produit des résultats (`_showScanActions(true)`)

### Scan asynchrone non-bloquant
- `/api/scan` (POST) : lance le scan dans un thread daemon, retourne immédiatement `{"task_id": "uuid"}`
- `GET /api/scan/status/<task_id>` : sonde l'état `{status: "running"|"done"|"error"|"cancelled", result?, error?}` ; nettoie l'entrée au premier retrait
- Frontend : `_waitForScanResult(taskId)` dans `dashboard-scan.js` écoute l'événement Socket.IO `scan_done` ET poll `/api/scan/status` toutes les 3 s en fallback ; timeout global 5 min
- `_scan_tasks` dict protégé par `_scan_task_lock` (threading.Lock) dans `routes/jobs.py`
- La progression `scan_progress` Socket.IO reste inchangée — émise depuis le thread de scan

### Annulation de scan
- `POST /api/scan/<task_id>/cancel` : positionne `cancel_ev` (threading.Event) sur la tâche courante → retourne `{"ok": true}` (404 si la tâche n'est pas en cours)
- Le thread de scan vérifie `cancel_ev.is_set()` entre chaque phase (inventory / scanning / tmdb) ; si positionné → status `"cancelled"`, émet `scan_done {cancelled: true}`
- Chaque tâche dans `_scan_tasks` embarque son propre `"cancel": threading.Event()` créé dans `api_scan()`
- Frontend :
  - `_currentScanTaskId` (module-level) stocke l'id de la tâche en cours
  - `#btn-cancel-scan` : affiché pendant le scan (dans `#scan-progress-wrap`), caché dans le bloc `finally`
  - `cancelScan()` : `POST /api/scan/<_currentScanTaskId>/cancel` ; désactive le bouton immédiatement
  - `_waitForScanResult()` résout avec `{__cancelled: true}` si `ev.cancelled` (socket) ou `status: "cancelled"` (poll)
  - `_doScan()` détecte `data.__cancelled` et affiche un toast "Analyse annulée" (clé `scan.toast.cancelled`)
- i18n : `scan.btn.cancel`, `scan.toast.cancelled` dans les 5 locales

### Colonne Taille + tri des colonnes de scan
- `_item_size_gb(path, is_file)` dans `core/scanner.py` — calcule la taille locale en Go (stat() pour fichiers, rglob pour dossiers) ; champ `size_gb` ajouté à chaque item de `_scan_dir()`
- Tri côté client : `_scanSortCol` / `_scanSortAsc` dans `dashboard-scan.js` ; `sortScan(col)` bascule asc/desc et appelle `renderPreview()` ; tri appliqué dans `_buildRows()` avant rendu
- Colonnes triables : `name`, `size_gb` (et tout champ présent dans les items)
- Affichage taille : `< 1 Go` → en Mo, sinon en Go

### Sélection intelligente dans la table de scan
- `_selectByStatus(status)` — coche uniquement les items du statut donné (ex: `"pending"`)
- `_selectByType(type)` — coche les items d'un type (`"file"`, `"season"`, `"collection"`)
- `_invertSelection()` — inverse les coches des items sélectionnables (hors `history` / `inventory` / `skip`)
- Boutons dans `index.html` : groupe btn-group avec Pending / Films / Séries / Collections / Inverser

### ETA global de la file de jobs
- `updateQueueEta()` dans `dashboard-jobs.js` : calcule `pending × avg_duration` → affiche dans `#queue-eta-label` au-dessus de la liste de jobs ; mis à jour à chaque `_recordJobDuration()` et `_updateAllEtaBadges()`

### Filtre par date dans l'historique
- `db_history_query()` accepte `date_from` et `date_to` (str ISO date `YYYY-MM-DD`) → clause `WHERE processed_at BETWEEN ? AND ?T23:59:59`
- Deux `<input type="date">` dans `history.html` ; passés en query params `date_from`/`date_to`

### Preview transcript inline dans l'historique
- Chaque ligne de l'historique (si `job_id` présent) a un `data-job-id` + est cliquable (`cursor:pointer`)
- Clic → affiche un `<tr id="expand-{job_id}" class="transcript-row">` collapsible sous la ligne ; charge le transcript via `GET /api/history/detail/<job_id>` à la demande (lazy load)
- Plusieurs lignes ne peuvent pas être ouvertes simultanément (fermeture des autres à l'ouverture)

### Cache scan côté serveur SQLite
- Table `scan_cache` dans `history.db` : clé = chemin dossier, valeur = JSON items, TTL 24 h
- `db_scan_cache_get(folder)` / `db_scan_cache_set(folder, items)` / `db_scan_cache_clear(folder)` dans `core/db.py`
- `GET /api/scan/cache?folder=<path>` et `POST /api/scan/cache` (`action: "set"|"clear"`) dans `routes/jobs.py`
- Frontend : `getScanCacheWithFallback(folder)` essaie localStorage d'abord, puis `/api/scan/cache` ; `setScanCacheWithServer(folder, items)` écrit dans les deux

### Auto-purge des logs upload par âge
- `log_retention_days: 30` dans `web_config.json` (0 = désactivé)
- `_rotate_upload_logs()` dans `conf.py` : purge d'abord par âge (`mtime < cutoff`), puis par count (max 500)
- Configurable dans Paramètres → section Scan → "Rétention logs (jours)"

### Webhook Discord embed
- `webhook_format: "raw"|"discord"` dans `web_config.json`
- Si `"discord"` : `_fire_webhook()` dans `job.py` construit un embed coloré (vert=done, rouge=error, orange=cancelled) avec champs name/statut/type/tag/retry ; compatible Slack basic webhook aussi
- Configurable dans Paramètres → section Webhook → "Format"

### Graphiques Stats (Chart.js)
- `db_history_chart_data(days)` dans `core/db.py` : agrège les uploads par jour (`GROUP BY substr(processed_at,1,10), status`) sur les N derniers jours
- `GET /api/stats/chart?days=N` dans `routes/stats.py`
- Chart.js 4.4.0 (CDN jsdelivr) dans `stats.html` ; graphique barres empilées done/error ; boutons 7j / 30j / 90j ; `_uploadChart` destroy+recreate à chaque changement de période

### Modal de vérification des noms d'upload (`#P`)
- Intercalé entre la sélection des items et la création effective des jobs
- Déclenché par `startJobs()` après les guards de confirmation existants (bulk, duplicate_ask)
- Flux : `startJobs()` → `_openVerifyModal(toSend)` → POST `/api/normalize` → modal → `_confirmVerify()` → `_doStartJobs(items)`
- **`POST /api/normalize`** (dans `routes/jobs.py`) : accepte `{items: [{id, name, type}]}` ; appelle `unit3dup.release_normalizer.normalize_release_name(name, torrent_pack=True/False)` ; retourne `{items: [{id, name, normalized}]}` ; `torrent_pack=True` pour les types `season`/`integrale`/`collection` ; import `release_normalizer` avec dégradation gracieuse si non disponible
- **Modal** `#modal-verify` dans `index.html` : Bootstrap xl + scrollable ; tableau 3 colonnes (n°, nom source, nom upload éditable) ; état chargement (`#verify-loading`) ; entête sticky ; boutons "Annuler" + "Lancer les jobs"
- **`_openVerifyModal(items)`** dans `dashboard-jobs.js` : affiche d'abord l'état de chargement, appelle `/api/normalize`, peuple `<tbody id="verify-tbody">` avec les `<input>` éditables pré-remplis du nom normalisé, stocke l'original dans `data-original` pour détecter les modifications
- **`_onVerifyInput(input)`** : classe `is-valid` + `border-warning` si la valeur diffère de `data-original` (feedback visuel de modification)
- **`_confirmVerify()`** : collecte les noms édités (stockés dans `item.custom_name`), ferme le modal, appelle `_doStartJobs()`
- **`_doStartJobs(items)`** : logique de création jobs extraite de `startJobs()` (POST `/api/jobs`, rendu cards, sync, navigation onglet)
- `item.custom_name` est transmis dans l'item au job ; `web/core/job.py` l'injecte comme variable d'environnement `U3D_CUSTOM_RELEASE_NAME` dans le subprocess PTY ; `unit3dup/upload.py:normalize_release_name()` la lit en priorité absolue (court-circuit de toute normalisation automatique)
- i18n (5 locales) : `verify.modal.title`, `verify.col.source`, `verify.col.upload_name`, `verify.btn.confirm`, `verify.hint`, `verify.loading`, `verify.count`

### Scan récursif
- Activé via checkbox "Scan récursif" dans Paramètres → stocké `web_config.json.recursive_scan`
- Transmissible aussi via paramètre `recursive` dans le body de `/api/scan`
- Implémentation : `_scan_dir()` est la fonction interne (un niveau) ; `scan_source()` l'appelle et, si `recursive=True`, rappelle `_scan_dir()` pour chaque item `unknown` qui est un dossier

### Progression du scan (Socket.IO)
- L'endpoint `/api/scan` émet des événements `scan_progress` : `{phase: "inventory"|"scanning"|"duplicates"|"done", page?, fetched?}`
- `gemini_inventory._fetch_all_user_torrents()` émet aussi `scan_progress` à chaque page API
- Frontend : `_setScanProgress(phase, detail)` pilote la barre Bootstrap (6px, striped+animated, transition 100% + masquage à `done`)

### Auto-scan planifié (`autoscan.py`)
- Thread daemon `AutoScan` démarré à l'import du module dans `create_app()` (ligne `import core.autoscan`)
- Lit `web_config.json.auto_scan` à chaque cycle : `enabled`, `interval_minutes` (min 5)
- Si activé : appelle `scan_source()` en fond (sans inventory Gemini, sans check doublons)
- Émet `auto_scan_done` via Socket.IO : `{items, skipped, ran_at, to_upload}` — les clients connectés mettent à jour leur table de scan en temps réel
- État consultable via `GET /api/autoscan/status` : `{enabled, interval_m, last_run, last_count}`
- Configurable dans l'onglet Configuration › "Scan planifié"
- **Interruptible** : utilise `threading.Event.wait()` au lieu de `time.sleep()` — la config prend effet immédiatement après un save (appel à `wakeup()` depuis `routes/settings.py`)
- **Auto-upload (`auto_upload.enabled`)** : si activé, crée automatiquement des jobs pour les items `pending` après chaque cycle ; `auto_upload.max_per_run` (défaut 5) limite le nombre de jobs créés par cycle ; sinon fournit seulement la liste à l'utilisateur

### Cache scan côté client (localStorage, par dossier)
- Après un scan réussi, `scanItems` est sauvegardé dans `localStorage` **par dossier source** (clé = `"u3d_scan_folder_" + btoa(encodeURIComponent(folder))`) avec timestamp, TTL **24 heures**
- Fonctions dans `dashboard.js` : `getScanCache(folder)`, `setScanCache(folder, items)`, `clearScanCache(folder)`, `_getCacheAge(folder)` (retourne l'âge lisible pour le dropdown)
- Au chargement de la page (`DOMContentLoaded`) : GET `/api/settings` → récupère `source_folder`, charge le cache de ce dossier s'il existe et est valide
- Basculer de dossier via les favoris (`_switchFolder`) restaure le cache de ce dossier si disponible ; sinon lance un scan
- Le cache du dossier courant est effacé dès qu'un nouveau scan démarre

### Préférences filtres par dossier (`#14`)
- Clé de préférence filtre : `"u3d-scan-filter-prefs-" + btoa(encodeURIComponent(folder))` (par dossier) avec fallback clé globale `"u3d-scan-filter-prefs"`
- `_saveFilterPrefs()` écrit dans la clé dossier ET la clé globale ; `_restoreFilterPrefs()` essaie d'abord la clé dossier, puis la globale
- `_currentScanFolder` mis à jour avant chaque `clearScanCache()` dans `_doScan()` pour que les prefs soient associées au bon dossier

### Table preview lazy-load (`#12`)
- `renderPreview(items)` orchestre le rendu par chunks de `_LAZY_CHUNK = 60` lignes via `requestAnimationFrame`
- `_buildRows(rows)` → retourne une string HTML ; ne touche pas au DOM (séparation des responsabilités)
- `_cancelLazy()` annule un rendu en cours si `renderPreview` est appelé à nouveau
- Les 60 premières lignes s'affichent immédiatement (`body.innerHTML = _buildRows(...)`) ; les chunks suivants s'ajoutent via `body.insertAdjacentHTML("beforeend", ...)`

### Favoris dossiers source
- Stockés dans `web_config.json` → `source_folder_bookmarks: []` (max 20, plus récent en premier), gérés via `POST /api/settings/bookmarks` (`action: "add"|"remove"`)
- Interface : indicateur de dossier courant + dropdown bookmarks dans `index.html` ; section chips dans `settings.html`
- `_renderBookmarks(bookmarks, currentFolder)` dans `dashboard.js` — construit le menu dropdown ; coche le dossier actif ; les boutons utilisent `data-switch-folder` + `addEventListener` (pas `onclick=""` inline, évite les problèmes d'échappement HTML sur les chemins avec caractères spéciaux)
  - **Cache timestamp** (`#15`) : `_getCacheAge(folder)` lit `localStorage` directement pour afficher l'âge du cache (`< 1 min`, `Xm`, `Xh`, `Xj`) dans un badge sur chaque item du dropdown
  - **Badge count** (`#16`) : `<span id="folder-bookmarks-count">` sur le bouton dropdown, mis à jour par `_renderBookmarks()` avec le nombre de favoris
- `_switchFolder(newFolder)` — PATCH settings + restauration cache ou scan ; `_addCurrentFolderBookmark()` — ajoute le dossier courant
- `_renderSettingsBookmarks(bookmarks)` dans `settings.js` — chips avec icône grip + drag handle + bouton suppression
  - **Drag-and-drop** (`#17`) : SortableJS initialisé via `_initBookmarksSortable()` après rendu ; `onEnd` POSTe le nouvel ordre à `/api/settings` (clé `source_folder_bookmarks`)
  - SortableJS CDN ajouté à `settings.html` (`sortablejs@1.15.2`) ; `_bookmarksSortable` détouit + réinitialisé à chaque re-rendu
- i18n : `jobs.no_source_folder`, `jobs.bookmark.add`, `jobs.bookmark.added`, `jobs.bookmark.switched`, `config.scan.bookmarks_label`, `config.scan.bookmarks_empty`, `config.scan.auto_retry_label`, `config.scan.auto_retry_max_label`, `config.scan.job_timeout_label`, `config.scan.job_timeout_hint`

### Jobs
- Les jobs restent dans `_jobs` avec leur statut final (`done`, `error`, `cancelled`) jusqu'à ce que l'utilisateur clique "Supprimer les terminés" → `POST /api/jobs/clear`
- `_finalize()` ne supprime PAS automatiquement le job de `_jobs`
- Chaque job est un processus PTY distinct (`pty.openpty()` + `subprocess.Popen`)
- Le scheduler est un thread daemon unique qui bloque sur `_queue_cv.wait_for(lambda: bool(_job_queue))`
- `_job_queue` est une **liste ordonnée** (pas un `queue.Queue`) protégée par `_queue_cv` (Condition indépendante de `_jobs_lock`) — supporte le réordonnancement
- Retry manuel : `POST /api/jobs/<id>/retry` — crée un nouveau job avec un nouveau UUID à partir de l'item du job en erreur/annulé
- **Auto-retry** (`#8`) : `_auto_retry(job)` est appelé dans `_run_job_in_thread()` après libération du slot ; crée un nouveau `Job` avec `_retry_count+1` si `auto_retry_on_error=true` et `retry_count < auto_retry_max` ; émis via `socketio.emit("job_list", ...)` ; badge `↺ N` affiché sur la carte si `retry_count > 0`
- **Timeout watchdog** (`#9`) : `_get_job_timeout_minutes()` lit `job_timeout_minutes` (cache TTL 30 s) ; dans `_run_pty()` un `deadline` est calculé une fois au début ; à chaque tour de boucle, si `_time.time() > deadline` → `SIGTERM` + message `[TIMEOUT Xmin — job annulé automatiquement]` + break
- **Compression Socket.IO** : `socketio.emit("job_output", ..., compress=True)` — réduit la bande passante pour les sorties longues
- `to_dict(include_lines=True)` expose `retry_count: int` (0 si jamais retenté) ; `include_lines=False` omet le champ `lines` pour alléger les polls API
- `GET /api/jobs?fields=no_lines` utilise `include_lines=False` — le poll de `syncJobsFromApi()` utilise ce paramètre, les lignes arrivent par socket `job_output`
- **Webhook (`webhook_url`)** : `_fire_webhook(job)` est appelé dans `_finalize()` après chaque job ; POST JSON `{job_id, name, path, type, tag, status, started_at, ended_at, retry_count}` ; timeout 10 s ; erreur loggée en WARNING dans `app.log`
- **ETA (durée estimée)** : `_recentDurations` (dernier 10 jobs `done`) dans `dashboard-jobs.js` ; `_recordJobDuration(sec)` appelé dans le handler `job_status` de `dashboard.js` ; `_updateAllEtaBadges()` met à jour tous les badges `.job-eta-badge` sur les cartes `pending` ; badge retiré si aucune donnée
- **Confirmation de démarrage en masse** : `_BULK_CONFIRM_THRESHOLD = 10` dans `dashboard-jobs.js` — `startJobs()` demande confirmation si ≥ 10 items sélectionnés (`jobs.confirm.bulk_start`)

### Persistance de la queue jobs (SQLite `job_queue`)
- À la création de chaque job (y compris retry) : `add_to_queue_state(job_id, item)` persiste dans la table SQLite `job_queue` (délègue à `db_queue_add`)
- À la fin de chaque job (`_finalize()`) : `remove_from_queue_state(job_id)` retire l'entrée (délègue à `db_queue_remove`)
- Au démarrage du module `job.py` : `_restore_queue()` relit la table via `db_queue_load()`, reconstruit les `Job` objects et les ajoute à `_jobs` + `_job_queue` — les jobs `pending` survivent donc à un redémarrage Flask
- Fonctions de façade dans `conf.py` : `load_queue_state()`, `add_to_queue_state()`, `remove_from_queue_state()` — délèguent toutes à `core.db`
- Les constantes de chemin JSON legacy (`QUEUE_JSON`, `INVENTORY_STATE_JSON`, `DUPLICATE_CACHE_JSON`…) ont été déplacées dans `db.py` (uniquement utilisées par `_migrate_json()` au premier démarrage)

### Console live (Socket.IO)
- `_activeConsoleJobId` est positionné **avant** de vérifier si `activeJobs[jobId]` existe (évite la race condition entre `job_status: running` et `renderJob()`)
- `flushPendingOutput(id)` est appelé après `showActiveConsoleDock` pour vider le buffer de chunks reçus avant création du DOM
- `createTextNode + appendChild` (pas `textContent +=`) pour les performances
- **Fermeture automatique du dock** : `hideActiveConsoleDock(jobId)` est appelé dans deux chemins :
  1. `applyStatus()` dans `dashboard-jobs.js` — quand `job_status` socket reçoit `done|error|cancelled`
  2. `syncJobsFromApi()` — après chaque poll API, ferme les panneaux ouverts pour des jobs non-running (`[..._dockXterms.keys()].forEach(...)`)

### Colonnes media info dans le scan (`scanner.py`)
- `_parse_media_info(name: str) -> dict` — parse la résolution, le type de source et le codec vidéo depuis le nom de la release via `guessit` ; retourne `{"resolution": str, "source_type": str, "encoding": str}` (chaînes vides si inconnu)
- Chaque item produit par `_scan_dir()` contient maintenant ces 3 champs
- Colonnes dans la table de scan : **Rés.** (`resolution`), **Source** (`source_type`), **Codec** (`encoding`)
- Fonctions JS de normalisation dans `dashboard-scan.js` : `_resLabel(res)` ("2160p" → "4K"), `_srcLabel(src)`, `_codecLabel(codec)` — badge dark border dans chaque cellule
- `guessit` utilise `screen_size` / `source` / `video_codec` comme clés

### Badge Type épisode vs saison (`dashboard-scan.js`)
- `isSerieEp` dans `_buildRows()` : `(item.type === "file" || item.type === "season") && (item.episode_num != null || /\bS\d{1,2}E\d{1,2}\b/i.test(item.name))` → badge "Episode" (`jobs.type.episode`) au lieu de "Saison"
- Couvre deux cas : fichier épisode isolé (`type="file"`) **et** dossier épisode individuel (`type="season"` avec `episode_num` défini, ex. `NCIS.S23E07/`)
- Un dossier `SxxExx` seul avec une vidéo est `type="season"` côté backend (même code de détection que les packs) mais `episode_num != null` → badge "Episode" côté UI
- L'upload reste `unit3dup -f <dossier>` ; le CLI CLI détecte `torrent_pack=False` via son propre regex (lookahead négatif sur `E\d+`) → upload comme épisode individuel avec `season=N`, `episode=N`

### Doublons Gemini — pipeline 4 phases (`core/duplicate.py`)
- `apply_duplicate_checks()` exécute désormais 4 phases successives :
  1. **Phase 1 — Inventaire** : items dont le chemin est dans `history.db` avec `source='gemini_inventory'` → `status="inventory"` *(badge vert "Déjà UP" — distinct de `"history"`)*,  `duplicate_source="inventory"` ; fonction DB : `db_history_paths_by_source("gemini_inventory")` ; non sélectionnable (exclu de `_selectByStatus`, `_selectByType`, `_invertSelection`)
  2. **Phase 2 — Historique local** : chemins restants dans le reste de l'historique → `status="history"`, `duplicate_source="local_history"`
  3. **Phase 3 — Recherche Gemini par nom** : comportement existant (threaded, cache TTL) ; `duplicate_source="name_search"` sur les matches
  4. **Phase 4 — Recherche Gemini par TMDB ID + colonne Releases** : `_phase4_enrich_releases()` ; pour chaque item ayant un `tmdb_id`, appelle `gemini_releases_for_tmdb_id()` en parallèle (groupé par tmdb_id unique) → peuple `item["gemini_releases"]` (liste de releases) ; pour les items encore `pending`, compare la taille locale vs Gemini — mark `duplicate`/`duplicate_ask` si delta ≤ `SIZE_TH`, `duplicate_source="tmdb_id"`
- `gemini_duplicate_check()` inchangé (Phase 3)
- `gemini_releases_for_tmdb_id(tmdb_id, tracker_name)` : `GET /api/torrents/filter?tmdbId=<id>&perPage=100` ; parse `(resolution, tag)` via `_parse_release_info()` (guessit + fallback) ; retourne list[dict] avec `{tracker_id, name, resolution, tag, size_gb}`
- `_get_local_size_gb(path_str)` : calcule la taille locale en Go sans la stack CLI (rglob pour les dossiers)
- `_parse_release_info(name)` : retourne `(resolution, tag)` depuis guessit ; fallback : `name.rsplit("-",1)[-1]`
- Rate-limit Phase 4 : un `phase4_abort` flag propre (indépendant de l'abort_flag Phase 3) ; un 429 en Phase 4 logge un WARNING et saute silencieusement, sans affecter les résultats des phases 1-3
- `db_history_paths_by_source(source)` ajouté dans `core/db.py` : `SELECT path FROM history WHERE source=?`
- `duplicate_source` ajouté aux items pour traçabilité : `"inventory"`, `"local_history"`, `"name_search"`, `"tmdb_id"`
- `apply_duplicate_checks()` conserve les 3 gardes existantes et la signature de retour `(items, rate_limited, unchecked_paths)`

### Colonne Gemini releases dans la table de scan
- `item["gemini_releases"]` : liste de dicts `{tracker_id, name, resolution, tag, size_gb}` ajoutée par la Phase 4
- `_geminiReleasesHtml(item)` dans `dashboard-scan.js` : affiche jusqu'à 6 releases sous forme de badges compacts ; code couleur :
  - 🔴 `bg-danger` : TAG **et** résolution correspondent à l'item local (doublon probable exact)
  - 🟡 `bg-warning text-dark` : résolution seule correspond (différent groupe)
  - 🔵 `bg-info text-dark` : TAG seul correspond (même groupe, autre résolution)
  - Gris : aucune correspondance
- Tooltip = nom complet de la release Gemini ; `+N` si >6 releases
- Nouvelle colonne `<th data-i18n="jobs.col.gemini_releases">` dans `index.html` ; colspan vide → 11 colonnes
- Clés i18n : `jobs.col.resolution`, `jobs.col.source_type`, `jobs.col.encoding`, `jobs.col.gemini_releases` (5 locales)

### Statut "Déjà UP" (`status="inventory"`) dans la table de scan
- Badge vert `bg-success` avec label `jobs.badge.inventory` ("Déjà UP") — distinct de `"history"` (gris) et `"duplicate"` (rouge)
- Assigné en Phase 1 de `apply_duplicate_checks()` quand le chemin est dans `history.db` avec `source='gemini_inventory'`
- Non sélectionnable : exclu de `_selectByStatus()`, `_selectByType()`, `_invertSelection()` (même traitement que `"history"` et `"skip"`)
- Visible même quand le filtre "masquer l'historique" est actif (il s'agit d'un statut distinct)
- `updateScanSummary()` affiche ` · N déjà sur le tracker` si `invN > 0` (clé `jobs.summary_inventory`)

- `gemini_duplicate_check()` appelle l'API du tracker — coûteux
- **Utilise `requests.get()` directement** (bypass `Tracker._get()`) pour détecter HTTP 429 immédiatement sans le sleep 60s natif ; lève `RateLimitError` sur 429
- Cache TTL dans table SQLite `dup_cache` (configurable via `duplicate_cache_ttl_sec`)
- Items déjà dans `history.db` → jamais passés à l'API Gemini (Phase 1+2)
- Clé de cache : `tracker|path|mtime:size|hash_config` — le hash (MD5 tronqué à 8 chars) de l'URL Gemini + APIKEY invalide automatiquement le cache si le tracker change
- `apply_duplicate_checks()` retourne `(items, rate_limited: bool, unchecked_paths: list[str])`
- `apply_duplicate_checks()` a trois gardes préalables qui loggent en WARNING (visibles dans `app.log`) avant de retourner sans vérifier :
  1. `MULTI_TRACKER` absent ou vide dans `Unit3Dbot.json`
  2. `DUPLICATE_ON=false` dans les préférences
  3. `Load()` a échoué (exception loggée avec le message d'erreur)
- **Gestion 429** : un `abort_flag` partagé entre threads arrête les vérifications dès le premier 429 ; `as_completed()` collecte les résultats partiels ; `unchecked_paths` liste les chemins non vérifiés
- Frontend : `_startRateLimitCooldown(uncheckedPaths)` verrouille `#btn-recheck-dup` 60 s (décompte visible sur le label), affiche un toast danger avec les noms de dossiers manquants, déverrouille automatiquement après 60 s
- **Notification navigateur** : si des `duplicate_ask` sont détectés au scan, `_notify()` est appelé (en plus du toast)

### Rate-limit API scan (`app.py`)
- `before_request` hook `_scan_rate_limit` : 5 requêtes POST / 60 s par IP sur `/api/scan`, `/api/scan/duplicates` et `/api/scan/tmdb` (frozenset `_RATE_LIMITED_SCAN_PATHS`)
- Compteur en mémoire (`_rl_hits`) — réinitialisé au redémarrage
- `_rl_blocked()` purge les IPs inactives à chaque écriture (évite croissance non bornée du dict)
- Répond `429 {"error": "Too many requests — retry in 60 s"}` si dépassé
- Distinct du rate-limit 429 Gemini (tracker) géré dans `duplicate.py`

### Historique
- Backend SQLite (`history.db`) — `db_history_query()` fait un SQL `WHERE … LIKE … LIMIT … OFFSET` — aucun chargement complet en mémoire
- `/api/history` est paginée : paramètres `?page=`, `?limit=` (défaut 50, max 200)
- Filtres disponibles : `?search=` (nom/tag), `?type=` (file/season/integrale), `?status=` (done/error)
- Réponse : `{"rows": [...], "total": N, "page": P, "pages": N, "limit": L}` — `total` reflète la taille après filtrage
- Export CSV : `GET /api/history/export.csv` (colonnes : name, type, tag, processed_at, status, path)
- Frontend : barre de filtres (debounce 300 ms), total badge utilise `_histTotal` (total filtré, pas taille de page)
- **Chips de filtre rapide (`#M`)** : boutons chips au-dessus de la barre de filtres dans `history.html` ; `_setChip(field, value, btn)` met à jour le `<select>` correspondant et déclenche `_histFilterChanged()` ; `_syncChips()` resynchronise les chips quand le select change directement
- **Re-check tracker (`#O`)** : `POST /api/history/recheck` + bouton <i class="bi bi-cloud-check"></i> dans chaque ligne ; appelle `gemini_duplicate_check()` depuis `core/duplicate.py` ; résultat : `{results: [{path, status: "duplicate"|"ok"|"error", detail}]}`

### Statistiques — table torrents
- `_allTorrents` stocke la liste complète reçue de `/api/torrents`
- `applyTorrentFilter()` filtre par état (select `#torrent-state-filter`) et trie selon `_torrentSort`
- `sortTorrents(col)` bascule asc/desc ; indicateurs visuels ▲/▼ dans les en-têtes
- Tri par défaut : `ratio DESC`
- `/api/torrents` retourne **tous** les torrents qBittorrent (plus de filtre `tag`) — `get_qbit_torrents()` appelle `cl.torrents_info()` sans argument

### Endpoint `/api/health`
- Toujours public (exempt de `_auth_guard` et de `_scan_rate_limit`)
- Répond `{"status": "ok", "version": "0.8.21", "uptime": <secondes>}`
- `_start_time` capturé à l'import du module `routes/health.py`

### AutoManager
- Mode nuit (`night_mode.enabled`, `start_hour`, `end_hour`) : pendant la plage configurée, `auto_remove` est désactivé, `auto_reseed` reste actif
- Supporte les plages à cheval sur minuit (ex: 23h→6h)

### Notifications navigateur
- `requestNotifPermission()` dans `dashboard-notifications.js` — bouton `#btn-notif` affiché si permission non accordée
- `_notify(title, body)` — notification à chaque fin de job (`done` ou `error`) **et** quand des doublons `duplicate_ask` sont détectés au scan

### Centre de notifications (`#J`)
- Cloche `#notif-bell-btn` dans la navbar (toutes pages via `base.html`) avec badge rouge comptant les non-lus
- Dropdown `#notif-dropdown` : derniers 20 toasts (`_NOTIF_MAX`) avec icône colorée, message tronqué et heure
- `addNotifToCenter(msg, type)` appelé automatiquement dans `showToast()` (inline script `base.html`)
- `clearNotifCenter()` vide l'historique et remet le compteur à zéro
- Compteur d'non-lus remis à 0 en ouvrant le dropdown ; `_notifUnread` en mémoire (réinitialisé au rechargement de la page)

### Export / Import config web (`#I`)
- `GET /api/settings/export` → télécharge `web_config.json` courant comme fichier `application/json`
- `POST /api/settings/web-import` → deep-merge du JSON importé dans la config ; le champ `auth_password_hash` est ignoré à l'import (sécurité)

### Authentification (`core/auth.py`, `routes/auth_bp.py`)
- Activée via `web_config.json` : `auth_enabled=true` + `auth_password_hash` (pbkdf2:sha256 werkzeug)
- `before_request` `_auth_guard` dans `create_app()` : protège toutes les routes sauf `/login`, `/api/auth/*`, `/static/*`, `/api/health`
- Socket.IO : `on_connect()` retourne `False` si auth activée et non connecté **ou** si l'epoch de session est périmée (`session["u3d_epoch"] != _SERVER_EPOCH` → session antérieure à un redémarrage)
- **Garde brute-force avec backoff exponentiel** (`#19`) : `_fail_times[ip]` (timestamps) + `_fail_count[ip]` (compteur) ; window : 60 s pour ≤ 5 échecs, puis `60 × 2^(n-5)` s → max 3 600 s (1 h) ; état en mémoire (réinitialisé au redémarrage — acceptable pour un dashboard local)
- Session permanente 30 jours si "Se souvenir de moi"
- `POST /api/auth/password` (change/crée) ; `POST /api/auth/disable` ; `GET /api/auth/status`
- `GET /api/auth/status` retourne aussi `session_timeout_minutes` (lu depuis `web_config.json`)
- Configuration UI dans l'onglet Configuration › "Sécurité"
- **Auto-logout inactivité** : `session_timeout_minutes` dans `web_config.json` (0 = désactivé, défaut 0) ; géré côté client par `static/js/dashboard-inactivity.js` (chargé dans `base.html`) ; `setInterval` 30 s ; toast d'avertissement 60 s avant ; POST `/api/auth/logout` + redirect `/login?reason=timeout`
- **Déconnexion au redémarrage** : `_SERVER_EPOCH = uuid4()` généré à l'import de `core/auth.py` (= nouveau UUID à chaque démarrage du processus) ; écrit dans `session["u3d_epoch"]` à la connexion ; `_auth_guard` et `on_connect()` comparent avec `_SERVER_EPOCH` courant — si différent → `session.clear()` → 401/redirect ; côté client, **double mécanisme** :
  1. `_pingAuthStatus()` dans `dashboard-inactivity.js` interroge `GET /api/auth/status` toutes les **15 secondes** (`_PING_INTERVAL = 15_000`) — si `auth_enabled && !logged_in` → redirect `/login?reason=restart` ; exposée comme `window._u3d_ping_auth` pour une invocation externe
  2. `socket.on("connect_error", ...)` dans `dashboard.js` — `on_connect()` retourne `False` quand l'epoch est périmée → Socket.IO émet `connect_error` → appel immédiat à `window._u3d_ping_auth()` → redirect en quelques secondes sans attendre le prochain cycle de 15 s
- La page `/login` affiche un message contextuel selon `?reason=timeout|restart` (clés i18n `config.security.session_expired_timeout|restart`)

### En-têtes de sécurité (`app.py` `#18`)
- `@app.after_request` `_security_headers` ajoute (via `setdefault` — non-écrasant) :
  - `Content-Security-Policy` : `default-src 'self'` ; `script-src` + `style-src` autorisent CDN `cdn.jsdelivr.net` et `'unsafe-inline'` (requis pour les handlers `onclick=""` inline) ; `connect-src` autorise `ws:`/`wss:` pour Socket.IO ; `worker-src blob:` pour xterm
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: SAMEORIGIN`
  - `Referrer-Policy: same-origin`

### TLS / HTTPS (`start_web.py`)
- `--tls` active HTTPS ; `--cert` / `--key` pour apporter ses propres fichiers PEM
- Si `--tls` sans certificat existant : génère un cert auto-signé via `openssl req -x509 -newkey rsa:2048` dans `web/.ssl/`
- Transmet les chemins via env vars `U3D_TLS_CERT` / `U3D_TLS_KEY` → `app.py` `__main__` les récupère et passe `certfile`/`keyfile` à `socketio.run()`

### Wizard de configuration (`--setup`)
- `./start_web.sh --setup` lance un assistant interactif (après activation du venv)
- Étape 1 : définir/changer le mot de passe dashboard → écrit `auth_password_hash` + `auth_enabled=true` dans `web_config.json`
- Étape 2 : générer un certificat TLS auto-signé (optionnel) → `web/.ssl/cert.pem` + `key.pem`
- Sauvegarde atomique de `web_config.json` via fichier `.tmp`

### Page Inventaire (`/inventory`)
- Route `GET /inventory` dans `routes/pages.py` → `inventory.html`
- Lien dans `base.html` navbar (icône `bi-collection-fill`) entre RSS et Config
- Template `inventory.html` : JavaScript inline (IIFE), pas de module séparé
  - `invLoad(page)` : `GET /api/inventory/items?page=&limit=&search=&sort=name|date&order=asc|desc` → rendu table ; lit les contrôles tri/limite depuis les selects à chaque appel
  - `_loadQbit()` : `GET /api/inventory/seeding` → `_seedingIds = new Set(ids.map(String))` ; badge count "N en seed" ; `_qbitLoaded = true` après résolution
  - Tri : sélecteur + clic sur en-têtes `th-name` / `th-date` → `_invSortCol(col)` bascule asc/desc, met à jour select, appelle `invLoad(1)`
  - Filtre seed : sélecteur `#inv-seed-filter` → `_invSeedFilterChange()` → `_applyQbitFilter(rows)` appliqué côté client sur la page courante
  - Debounce recherche 300 ms ; pagination `‹ ›` ; badge total ; sélecteur 50/100/200 par page (défaut 100)
  - Téléchargement via `<a href="{row.download_url}" target="_blank">` — URL directe tracker (pas de proxy)
- **`GET /api/inventory/items`** : lit `get_inventory_list()` → `app_state["torrents_list"]` (tous les torrents Gemini, indépendamment des fichiers locaux) ; filtre search côté Python ; tri Python (`sort`/`order`) ; pagination ; construit `tracker_url = {url}/torrents/{id}` et `download_url = {url}/torrents/download/{id}` par row
- **`GET /api/inventory/seeding`** : appelle `get_qbit_gemini_seeding_ids(gemini_url)` dans `torrent.py` ; détection via `torrents_trackers(hash)` (tous les trackers, pas seulement l'actif) + `torrents_properties(hash).comment` → regex `_COMMENT_ID_RE = re.compile(r"/torrents/(\d+)")` ; retourne `{ids: [...], count: N, error: str|null}`
- **`GET /api/inventory/download/<tracker_id>`** : proxy `requests.get({Gemini_URL}/torrents/{id}/download?api_token=...)` → `Response(stream_with_context(...), content_type="application/x-bittorrent")` — conservé pour usage API mais pas utilisé par l'UI (lien direct préféré)
- `db_inventory_query()` dans `core/db.py` : `SELECT path, name, processed_at, tracker_id FROM history WHERE source='gemini_inventory'` — conservé pour usage futur mais n'est plus le source de la page Inventaire
- i18n : clés `nav.inventory`, `inventory.title`, `inventory.col.*`, `inventory.search_placeholder`, `inventory.no_results`, `inventory.btn_open`, `inventory.btn_download`, `inventory.seeding`, `inventory.not_seeding`, `inventory.total`, `inventory.page_of` dans les 5 locales

---

## Conventions de code

- Blueprints Flask : `bp = Blueprint("name", __name__)`
- I/O JSON : toujours via `_safe_read_json` / `_atomic_write_json` (écriture atomique via `.tmp`)
- Historique + transcripts : passer par `core/db.py` — ne pas lire/écrire `history.db` directement
- **Écriture historique — choisir la bonne fonction** :
  - `db_add_history_entries(dict)` → `INSERT OR IGNORE` — pour les ajouts (inventory, scan) ; **ne supprime rien**
  - `db_upsert_history_paths(paths, proto)` → `INSERT OR REPLACE` — pour les jobs (remplace si existe)
  - `db_save_history({})` → `DELETE FROM history` + re-insert — réservé à l'effacement total (`/api/history DELETE`)
  - **Ne jamais appeler** `load_history()` + `save_history(full_dict)` pour des ajouts partiels — c'est destructif
- **Migration SQLite** : `_migrate_json()` ne s'exécute que si `_db_existed = False` (fichier absent au démarrage) ; si `history.db` existe → migration ignorée même si vide — prévient la ré-importation du JSON legacy après un `clear_history`
- `default_tracker_name()` : source unique dans `conf.py` — ne pas dupliquer dans `duplicate.py` ou `gemini_inventory.py`
- Locks : `_dup_cache_lock` pour le cache doublons, `_jobs_lock` pour `_jobs`, `_queue_cv` (Condition) pour `_job_queue`, `_lock` (threading.Lock) dans `gemini_inventory` pour l'état persistant, `_rl_lock` pour le rate-limiter scan, `_STATUS_LOCK` dans `checker.py`, `_QBIT_LOCK` dans `torrent.py`, `_SEEDING_LOCK` dans `torrent.py` (cache 60 s des IDs Gemini en seed), `_DB_LOCK` dans `db.py`, `_auth_cache_lock` pour le cache TTL de `auth_enabled()`, `_cfg_cache_lock` pour le cache TTL de `load_web_config()`
- Ne pas appeler `save_duplicate_cache()` depuis l'intérieur d'un bloc `with _dup_cache_lock` (deadlock)
- Ne jamais mixer `_jobs_lock` et `_queue_cv` dans le même bloc (locks indépendants)
- JS frontend : `dashboard.js` ne contient que l'état partagé + Socket.IO ; les modules (`dashboard-notifications.js`, `dashboard-console.js`, `dashboard-scan.js`, `dashboard-jobs.js`) sont chargés **après** dans `index.html` — ils accèdent aux globaux définis dans `dashboard.js`
- Ne pas utiliser `import`/`export` ES6 dans ces fichiers (les `onclick=""` inline n'ont pas accès aux modules) ; pour les éléments créés dynamiquement (ex. bookmarks), préférer `data-*` + `addEventListener` aux `onclick=""` inline afin d'éviter les problèmes d'échappement
- `load_web_config()` est mise en cache 5 s (TTL `_CFG_CACHE_TTL`, lock `_cfg_cache_lock`) ; `save_web_config()` invalide le cache immédiatement après l'écriture via `_invalidate_cfg_cache()`
- `_deep_merge(base, override)` dans `conf.py` — fusionne récursivement les dicts, les clés non-dict dans `override` écrasent `base` ; utilisée par `load_web_config()` (defaults + disque) et `save_web_config()` (config actuelle + delta entrant)
- i18n frontend : `t("key")` dans le JS, clés dans `static/locales/*.json` (5 locales : fr, en, es, it, de) — `t(key)` retourne la clé elle-même si absente → toutes les clés `data-i18n="config.*"` présentes dans `settings.html` **doivent** exister dans les 5 fichiers locales ; une clé manquante affiche le nom brut dans l'UI sans aucune erreur visible — clés critiques également dans `login.html` : `auth.error.wrong_password`, `auth.error.too_many_attempts` (doivent exister dans les 5 locales)

## Intégration TMDB (`common/external_services/theMovieDB/`)

- `TmdbAPI` (`core/api.py`) est instancié avec `config_settings.TRACKER_CONFIG`
- **Authentification prioritaire** : si `TRACKER_CONFIG.TMDB_ACCESS_TOKEN` est défini, non vide et différent de `"no_key"` → `Authorization: Bearer <token>` dans les headers, aucun `api_key` dans les params. Sinon → `api_key=<TMDB_APIKEY>` en query param (v3 legacy). Rétrocompatible — les configs sans token continuent de fonctionner.
- `TMDB_ACCESS_TOKEN` : présent dans `TrackerConfig` (`common/settings.py`) avec `str | None = None` ; ajouté dans `_OPTIONAL` → pas d'erreur de validation si absent du JSON.
- `TmdbAPI.params` : **instance-level** (pas class-level) — construit dans `__init__` avec `{**self._auth_params, "language": self._TMDB_LANGUAGE}` ; la méthode `request()` utilise `self.params`.
- `_TMDB_LANGUAGE = "fr-FR"` : constante de classe (remplace l'ancien `"it-IT"` hardcodé).
- Recherche **films** : paramètre `primary_release_year` (filtre strict sur la sortie theatrale, plus précis que `year`).
- Recherche **séries** : paramètre `first_air_date_year`.
- Modèles Pydantic nullable (l'API TMDB peut retourner `null`) : `TVShowDetails.poster_path: str | None = None`, `Network.logo_path: str | None = None`, `Alternative.type: str | None = None`.

## Stack CLI réutilisable depuis le web

Le code web (`web/core/`) peut importer directement le CLI. Toujours appeler `Load()` avant tout accès à `config_settings` ou instanciation de classes CLI :

```python
from common.settings import Load
Load()  # singleton — sans effet si déjà appelé
from unit3dup.pvtTracker import Unit3d
from unit3dup.torrent import Torrent
```

- `trackers_api_data` dans `common/trackers/data.py` est peuplé à l'import depuis `config_settings` → les credentials reflètent l'état JSON au démarrage du processus (pas en temps réel)
- `Torrent(tracker_name)` expose `.tracker` (instance `Unit3d`) ; méthodes utiles : `get_by_uploader(username)`, `search(keyword)`, `tracker.next(url)` pour la pagination
- `Tracker._get()` gère HTTP 429 automatiquement (60 s de backoff) — utilisé par `gemini_inventory` ; **ne pas utiliser** pour les checks doublons (utiliser `requests.get()` direct + `RateLimitError` pour détecter le 429 sans bloquer)
- Pour les appels hors `filterAPI` (ex : `/api/user`, checks doublons), utiliser `requests` directement avec `tracker.base_url`, `tracker.api_token`, `tracker.headers`, `tracker.filter_url`, `tracker.params`

---

## Tests

```bash
# Depuis web/ avec le venv :
..\. venv\Scripts\python -m pytest tests/ -v        # Windows
../.venv/bin/python -m pytest tests/ -v             # Linux/macOS

# Ou depuis la racine :
.venv\Scripts\python -m pytest web/tests/ -v
```

> **Pré-requis** : Flask + dépendances dans le **venv du projet** (`.venv/`).
> Le venv est créé par `start_web.sh` au premier lancement.
> Installation manuelle : `.venv/Scripts/python -m ensurepip && .venv/Scripts/python -m pip install flask flask-socketio simple-websocket eventlet requests guessit pytest`
> Avec Flask installé dans le venv → **278 tests, 0 skip**.

Couverture actuelle (`web/tests/`) :

| Fichier | Sujets |
|---------|--------|
| `test_conf.py` | `_atomic_write_json`, `_safe_read_json`, `default_tracker_name` ; `_rotate_upload_logs` (count **et âge**) ; `_deep_merge` (récursif, sibling keys) ; `load_web_config` (defaults, merge disque, cache TTL) |
| `test_db.py` | CRUD historique SQLite, transcripts, pagination, filtres ; `db_add_history_entries` (INSERT OR IGNORE), `db_update_tracker_id_if_missing` ; garde migration `_db_existed` ; **scan_cache** (set/get/clear/TTL/blank key) ; **`db_history_chart_data`** (structure, longueur, agrégation quotidienne) ; **filtre date** `db_history_query(date_from/date_to)` ; `db_history_paths_set` ; `db_history_paths_by_source` ; `db_history_stats` |
| `test_duplicate_cache.py` | Cache SQLite dup (store/lookup/prune/TTL/skip_th) ; guards `apply_duplicate_checks` |
| `test_scanner.py` | Parsers noms ; `_scan_dir` (18 cas) ; `scan_source` (récursif) ; **`_item_size_gb`** ; **`_parse_media_info`** ; **`_filename_is_s01e01`** ; `_has_video_files` / `_list_video_files` ; **`_is_extra_file`** / **`_real_movie_files`** ; **`_is_collection_by_rule`** (3 niveaux) ; **`find_s01e01_upload_file`** (root/nested/sibling) ; **`episode_upload_for_item`** ; champs `size_gb` + media info présents ; **`test_season_detected_sxxexx`** (`NCIS.S23E11`, `Show.S01E01`, `Series.S02E05`) |
| `test_tmdb_search.py` | **`_parse_title_year`** (guessit + fallback) ; **`_tmdb_auth`** (Bearer/api_key/no-creds/`no_key`) ; **`search_tmdb_item`** (TV vs movie, empty, error, mocked) ; **`enrich_items_with_tmdb`** (no creds, already has id, history skip, enrichissement mocked) |
| `test_checker.py` | **`_ms`** ; **`_check_source`** (warn/ok/error) ; **`_check_config`** (ok/error) ; branches "non configuré" pour torrent/tracker/tmdb ; **cache TTL** `get_status_checks` (hit / force / expired) |
| `test_stream.py` | `strip_ansi`, `ConsoleStream` (ANSI, `\r`, backspace), `normalize_transcript` |
| `test_i18n.py` | Validité JSON, parité des clés entre 5 locales, couverture HTML + JS, clés orphelines |
| `test_routes.py` | `/api/health`, `/api/settings` GET/POST, `/api/settings/bookmarks`, `/api/jobs` |
| `test_auth.py` | `auth_enabled()` + cache TTL ; `_SERVER_EPOCH` ; `_backoff_window()` ; brute-force guard |
| `test_job.py` | Queue SQLite (add/load/remove/idempotent) ; `Job.to_dict()` ; `_restore_queue()` |

Chaque test utilise des fixtures `tmp_path` / `monkeypatch` — aucun fichier réel modifié.

**Note `test_i18n.py`** : clés Jinja2 dynamiques exclues ; orphelines émettent un `UserWarning` (pas un échec).

**Note `test_routes.py` / `test_auth.py`** : nécessitent Flask (dans le venv). Modules Unix-only stubbés pour Windows.

---

## Lancement

```bash
./start_web.sh --setup          # assistant premier démarrage (mot de passe + TLS)
./start_web.sh                  # interactif (installe .venv au premier lancement)
./start_web.sh --tls            # HTTPS avec cert auto-signé dans web/.ssl/
./start_web.sh --daemon         # mode daemon
python3 start_web.py --stop     # arrêt daemon
cd web && python app.py         # lancement direct
```

Port par défaut : 5000. Variables : `U3D_WEB_HOST`, `U3D_WEB_PORT`, `U3D_SECRET_KEY`, `U3D_TLS_CERT`, `U3D_TLS_KEY`.
