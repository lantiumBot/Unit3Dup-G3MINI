# Unit3Dup — Dashboard Web

Interface web pour piloter les uploads `unit3dup`, suivre les jobs en temps réel (console PTY + Socket.IO), consulter l'historique structuré, les statistiques, l'état des services, le watcher et la configuration.

---

## Prérequis

- **Python 3.10+** (`python3`, `python3-venv`, `pip`)
- **ffmpeg** (requis par `unit3dup`)
- Client torrent configuré dans `Unit3Dbot.json` (qBittorrent, Transmission ou rTorrent)
- Fichier de configuration `~/Unit3Dup_config/Unit3Dbot.json` (créé au premier lancement CLI)

```bash
sudo apt install ffmpeg python3 python3-pip python3-venv
```

---

## Installation

```bash
git clone https://github.com/lantiumBot/Unit3Dup-G3MINI.git unit3dup
cd unit3dup
chmod +x start_web.sh start_web.py upload.sh
```

Le lanceur crée automatiquement `.venv` et installe les dépendances (`unit3dup` + `web/requirements.txt`) au premier démarrage.

---

## Démarrage

### Assistant de configuration (premier lancement)

```bash
./start_web.sh --setup
```

L'assistant interactif guide la configuration initiale :

1. **Mot de passe** — définit (ou change) le mot de passe du dashboard, stocké dans `web/web_config.json` sous forme de hash PBKDF2-SHA256 (via werkzeug). Active automatiquement l'authentification.
2. **Certificat TLS** — génère un certificat auto-signé RSA 2048 bits dans `web/.ssl/` (valable 10 ans) via `openssl`. Utilisable immédiatement avec `--tls`.

> L'assistant requiert au minimum 6 caractères pour le mot de passe. Il peut être relancé à tout moment pour changer le mot de passe ou regénérer le certificat.

### Mode interactif (premier plan)

```bash
./start_web.sh
# ou
python3 start_web.py
```

Ouvrir : **http://127.0.0.1:5000** (ou l'IP/port choisis).

### Interface et port personnalisés

```bash
./start_web.sh --host 10.0.0.2 --port 8080
# variables d'environnement (surchargées par --host/--port explicites si passés après)
HOST=0.0.0.0 PORT=5000 ./start_web.sh
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--host` | `0.0.0.0` | Interface d'écoute (`127.0.0.1` = local uniquement) |
| `--port` | `5000` | Port TCP |

### HTTPS (TLS)

```bash
# Certificat auto-signé (généré dans web/.ssl/ si absent)
./start_web.sh --tls

# Certificat existant
./start_web.sh --tls --cert /chemin/cert.pem --key /chemin/key.pem

# Variables d'environnement
U3D_TLS_CERT=/chemin/cert.pem U3D_TLS_KEY=/chemin/key.pem ./start_web.sh --tls
```

Le certificat auto-signé est généré automatiquement dans `web/.ssl/` si les fichiers sont absents. Le navigateur affichera un avertissement de sécurité — accepter l'exception ou utiliser un certificat signé par une CA.

### Mode daemon (arrière-plan, Linux)

```bash
./start_web.sh --daemon --host 0.0.0.0 --port 5000
./start_web.sh --daemon --tls   # daemon HTTPS
```

| Fichier | Défaut | Rôle |
|---------|--------|------|
| PID | `/tmp/u3dup-web.pid` | Processus daemon |
| Logs | `/tmp/u3dup-web.log` | stdout/stderr |

Arrêt :

```bash
python3 start_web.py --stop
# ou
./start_web.sh --stop
```

Options daemon supplémentaires : `--pid-file`, `--log`.

### Lancement direct (sans wrapper)

```bash
cd web
python app.py --host 0.0.0.0 --port 5000
```

Variables d'environnement supportées : `U3D_WEB_HOST`, `U3D_WEB_PORT`, `U3D_TLS_CERT`, `U3D_TLS_KEY`.

---

## Configuration

### Emplacement des fichiers

| Fichier | Chemin par défaut | Contenu |
|---------|-------------------|---------|
| Unit3Dbot | `~/Unit3Dup_config/Unit3Dbot.json` | Tracker, client torrent, préférences, chemins watcher |
| Config web | `web/web_config.json` | Dossier source, règles de scan, doublons, AutoManager, auto-scan, auth |
| Tags valides | `valid_tags.json` (racine projet) | Tags autorisés pour les releases |
| **Base de données** | `web/history.db` | SQLite WAL — 6 tables : `history` (chemins uploadés + inventaire), `transcripts` (résultats jobs), `dup_cache` (cache TTL checks Gemini), `job_queue` (file persistée), `app_state` (état inventaire Gemini), `scan_cache` (résultats scan TTL 24 h). Remplace tous les anciens JSON (migration automatique au premier démarrage). |
| Logs upload | `web/logs/upload/<job_id>.json` | JSON complet par job (audit individuel) — rotation : max 500 fichiers + purge par âge (`log_retention_days`) |
| Logs app | `web/logs/app/app.log` | Logs Flask + application (rotation 5 Mo × 3) |
| Certificat TLS | `web/.ssl/cert.pem` + `key.pem` | Certificat auto-signé (généré par `--setup` ou `--tls`) |

Surcharger le dossier config :

```bash
export UNIT3DUP_CONFIG_ROOT=/chemin/vers/config
./start_web.sh
```

### Import depuis un fichier existant

Bouton **Importer Unit3Dbot.json** dans l'onglet Configuration : sélectionner un fichier `Unit3Dbot.json` existant pour pré-remplir la configuration (fusion, les champs absents du formulaire sont préservés).

### Configuration minimale (onglet **Configuration**)

Champs **obligatoires** (validation à l'enregistrement) :

**Tracker**
- `Gemini_URL`, `Gemini_APIKEY`, `Gemini_PID`, `TMDB_APIKEY` (clé API v3 legacy)
- `TMDB_ACCESS_TOKEN` (optionnel) : API Read Access Token TMDB (JWT Bearer — recommandé). S'il est renseigné, il est prioritaire sur `TMDB_APIKEY` ; fonctionne sur les endpoints v3 et v4.
- `Gemini_USERNAME` (optionnel) : nom d'utilisateur pour l'inventaire Gemini pré-scan ; si absent, résolu automatiquement via `GET /api/user`

**Client torrent** (selon le client sélectionné)
- qBittorrent : `QBIT_HOST`, `QBIT_PORT`, `QBIT_USER`, `QBIT_PASS`
- Transmission : `TRASM_HOST`, `TRASM_PORT`, `TRASM_USER`, `TRASM_PASS`
- rTorrent : `RTORR_HOST`, `RTORR_PORT`, `RTORR_USER`, `RTORR_PASS`

**Scan**
- Dossier source (section *Scan & Règles*)

### Règles de scan (`web_config.json`)

| Règle | Options | Effet |
|-------|---------|-------|
| **INTEGRALE** | Activée, *Uploader chaque sous-dossier saison* | Détecte les dossiers dont le nom contient `INTEGRALE` |
| **COMPLETE / Sxx** | Activée, *Exiger un tag valide* | Détecte `COMPLETE` ou `S01`…`S99` dans le nom |
| **COLLECTION** | Activée, *Tags collection*, *Exiger un tag valide* | Dossier multi-vidéo : classé collection si tag ∈ liste (vide = tous). Les extras (sample/trailer…) sont ignorés. |
| **Mode `-confirm`** | — | Ajoute `-confirm` à chaque commande `unit3dup` |
| **Dry-run** | — | Réservé (simulation) |
| **Doublons — seuil skip** | `duplicate_ask_pct` (%) | Si le delta taille vs release Gemini ≤ seuil → statut `duplicate` (ignoré). Entre seuil et `SIZE_TH` → `duplicate_ask` (confirmation à l'upload) |
| **Cache checks doublons** | `duplicate_cache_ttl_sec` (s) | `0` = toujours interroger le tracker ; `>0` = réutiliser le cache SQLite |
| **Cache inventaire Gemini** | `inventory_cache_ttl_hours` (h) | `0` = count check à chaque connexion ; `24` = défaut ; valeurs élevées → scan Phase 1 toujours instantané |

---

## Utilisation

### Onglet Jobs

1. Configurer le **dossier source** dans Configuration.
2. Cliquer sur **Analyser le dossier** : parcourt le dossier source (un niveau : fichiers vidéo + sous-dossiers).
3. Tableau de prévisualisation : type, tag, saisons, statut (dont doublons Gemini).
4. Sélectionner les éléments, puis **Démarrer les uploads**.

#### Types détectés au scan

| Type / Statut | Critère de détection | Badge UI | Commandes `unit3dup` / comportement |
|---------------|----------------------|----------|--------------------------------------|
| **FICHIER** | `.mkv`, `.mp4`, `.avi`, `.m2ts` à la racine du dossier source | Film | `-u` sur le fichier |
| **EPISODE** *(dossier)* | Dossier dont le nom contient `SxxExx` (ex. `NCIS.S23E07/`) | **Épisode** | `-f <dossier>` ; le CLI détecte `torrent_pack=False` (lookahead négatif sur `Exx`) → upload épisode avec `season=N`, `episode=N` |
| **INTEGRALE** | Mot `INTEGRALE` (séparateurs `.`, `_`, `-`, espace) | Intégrale | Si **S01E01** trouvé : `-u` épisode → `-f` intégrale → `-f` chaque sous-dossier saison (si règle *upload saisons* activée) |
| **SAISON** *(pack)* | `COMPLETE` ou `Sxx` (sans `Exx`) dans le nom + tag valide (si requis) | Saison | **S01 uniquement** : `-u` S01E01 (si trouvé) puis `-f` pack. **S02+** ou COMPLETE sans `S01` : `-f` pack seul |
| **Déjà UP** *(badge vert)* | Chemin présent dans l'inventaire Gemini (`source='gemini_inventory'`) | Déjà UP | Non sélectionnable — release déjà sur le tracker |
| **Historique** *(badge gris)* | Chemin dans `history.db` (source locale) | Historique | Affiché en lecture seule (non sélectionnable pour upload) |
| **Ignoré** | Type inconnu, doublon certain | — | Non uploadable |

> **Dossier épisode vs pack saison** : un dossier `NCIS.S23E07/` et un pack `NCIS.S23/` sont tous deux `type="season"` en backend. La différence se fait via `episode_num` : si défini → badge **Épisode**, sinon → badge **Saison**. Le CLI fait la même distinction côté `torrent_pack`.

> **Note TAG invalide** : si aucun tag n'est détecté ou si le tag ne correspond pas aux `valid_tags`, l'élément apparaît dans la liste avec la case décochée par défaut (statut `pending`, indicateur visuel `tag_valid: false`). Il reste sélectionnable manuellement — le scan ne l'exclut plus automatiquement.

**S01E01** : seul le premier épisode de la **saison 1** est cherché dans le nom de fichier (`S01E01`, variantes `S1E1`, etc.). Recherche dans le dossier (profondeur 2). Pour une intégrale, recherche aussi dans un dossier voisin nommé **S01** si besoin.

#### TMDB ID — colonne éditable et auto-remplissage

Après le scan de dossier, le dashboard interroge TMDB automatiquement pour chaque élément détecté et affiche l'ID trouvé dans une colonne **TMDB ID** éditable.

- L'ID est résolu à partir du nom de la release via `guessit` (titre + année) puis une recherche `GET /search/movie` ou `/search/tv`.
- Utilise `TMDB_ACCESS_TOKEN` (Bearer) ou `TMDB_APIKEY` selon la configuration.
- Saisie manuelle : cliquer dans la cellule pour corriger ou entrer un ID absent.
- L'ID est conservé dans la file de jobs et injecté automatiquement via stdin quand `unit3dup` affiche le prompt `"Please digit a valid TMDB ID (0=skip)"`.

> La recherche TMDB a lieu **avant** la vérification des doublons Gemini (ordre pipeline : inventaire → analyse → TMDB → doublons → terminé).

#### Collection — classification intelligente

Un dossier contenant plusieurs fichiers vidéo est classé **COLLECTION** selon des règles configurables (Paramètres → COLLECTION) :

- **`collection_tags`** (liste, virgule-séparée) : seuls les dossiers dont le tag correspond à cette liste sont classés collection. Exemple : `Collection, Pack, Saga`. Vide = tout dossier multi-vidéo (comportement legacy).
- **Exiger un tag valide** : classe en collection uniquement si le tag est dans `valid_tags.json`.
- Les fichiers *sample/trailer/featurette/bonus/extra* sont ignorés dans le comptage avant décision — un film + un trailer n'est **pas** une collection.

#### Barre de progression du scan

Pendant le scan, une barre Bootstrap indique la phase en cours :
*Inventaire Gemini* → *Analyse dossier* → *Recherche TMDB* → *Vérification doublons* → *Terminé*.

#### Scan récursif

Activer **Scan récursif** dans Configuration → Scan & Règles pour analyser aussi les sous-dossiers catégories (ex : `Films/`, `Séries/`) un niveau plus bas.

#### Cache de scan par dossier (localStorage)

Les résultats de chaque scan sont conservés par dossier source dans `localStorage` (TTL **24 heures**, clé base64 du chemin). À la réouverture ou au rechargement de la page, le tableau est restauré sans re-scanner le disque.

Le cache est invalidé automatiquement :
- à chaque nouveau scan du même dossier ;
- en cliquant sur **Revérification** (force aussi un re-fetch de l'inventaire Gemini) ;
- si le TTL de 24 h est dépassé.

En changeant de dossier source via les favoris, si un cache récent existe pour le nouveau dossier, les résultats sont restaurés instantanément sans scan réseau.

#### Favoris — dossiers sources

Un indicateur de dossier courant (icône 📂) apparaît au-dessus des boutons de scan, avec :
- **★ (bookmark-plus)** : ajoute le dossier courant aux favoris (`POST /api/settings/bookmarks`).
- **📌 (dropdown)** : liste les dossiers favoris (max 20, plus récent en premier) ; cliquer sur un dossier le sélectionne, met à jour le `source_folder` dans la config, et restaure son cache si disponible (sinon lance un nouveau scan).
- Les favoris sont aussi gérables dans **Configuration → Scan & Règles** (affichage sous forme de chips avec bouton de suppression).

#### Détection des doublons (Gemini) — pipeline 4 phases

Le scan exécute 4 phases successives de filtrage :

| Phase | Source | Statut assigné |
|-------|--------|----------------|
| **1 — Inventaire** | Chemins dans `history.db` avec `source='gemini_inventory'` | `inventory` *(Déjà UP — badge vert)* |
| **2 — Historique local** | Reste de l'historique `history.db` | `history` *(badge gris)* |
| **3 — Recherche Gemini par nom** | API tracker (threaded, cache TTL) | `duplicate` / `duplicate_ask` |
| **4 — Recherche par TMDB ID** | API tracker par `tmdbId` + comparaison taille | `duplicate` / `duplicate_ask` |

- **`duplicate`** : skip automatique (delta ≤ `duplicate_ask_pct`).
- **`duplicate_ask`** : cochable ; confirmation navigateur avant upload.
- **`pending`** : pas de doublon bloquant.

Le tableau affiche les correspondances Gemini avec code couleur : 🔴 tag + résolution identiques · 🟡 résolution seule · 🔵 tag seul · gris = aucune. Le cache évite de re-scanner le tracker si `duplicate_cache_ttl_sec > 0`.

Une **notification navigateur** est envoyée si des éléments `duplicate_ask` sont détectés (bouton 🔔 à activer si non encore autorisé).

##### Gestion du rate-limit (HTTP 429)

Si le tracker répond 429 pendant la vérification des doublons :
- Les résultats déjà obtenus sont conservés dans le tableau.
- Un toast indique les dossiers **non vérifiés**.
- Le bouton **Doublons** est verrouillé 60 secondes avec un décompte visible, puis se déverrouille automatiquement pour relancer uniquement les éléments manquants.

#### Inventaire Gemini pré-scan

Le dashboard maintient un inventaire local de vos uploads Gemini dans `history.db`. Cet inventaire est utilisé en Phase 1 de chaque scan pour éliminer immédiatement les releases déjà uploadées, sans interroger le tracker.

**Optimisation à deux niveaux** — le scan Phase 1 est quasi-instantané dans la grande majorité des cas :

| Cas | Requêtes API | Durée Phase 1 |
|-----|-------------|---------------|
| Cache TTL frais (inventaire récent) | **0** | < 1 ms |
| TTL expiré, aucun nouvel upload | **1** (count check `perPage=1`) | < 1 s |
| Nouveaux uploads détectés | N pages (re-fetch complet) | 30 s – quelques min |

**Déclenchement automatique** : à chaque connexion au dashboard, une sync silencieuse est lancée en arrière-plan si le TTL est expiré. L'analyse de dossier suivante trouvera l'inventaire déjà à jour.

**Synchronisation manuelle** (onglet Configuration → carte *Inventaire Gemini*) :
- **Synchroniser maintenant** : lance un cycle complet (count check d'abord, re-fetch si besoin).
- **Forcer la re-synchronisation** : ignore TTL et count, re-télécharge tout l'inventaire.
- **Durée du cache (heures)** : `inventory_cache_ttl_hours` — `0` = count check à chaque connexion ; défaut `24`.

**Notifications** : les syncs d'inventaire (en arrière-plan à la connexion ou manuelles) apparaissent dans le **centre de notifications** (cloche navbar) — démarrage, résultat (ajoutés / inchangé) et erreurs, même si l'utilisateur n'est pas dans les paramètres.

**Comportement** :
- L'inventaire ne supprime jamais d'entrées dans `history.db` (écritures `INSERT OR IGNORE`).
- `Gemini_USERNAME` dans les paramètres Tracker évite la résolution automatique via `GET /api/user`.
- **Revérification** (bouton post-scan) force un re-fetch complet avec `force=True`.
- `invalidate_inventory_cache()` est appelé automatiquement si les credentials tracker changent dans les settings.

**Page Inventaire** (`/inventory`) : voir la section [Page Inventaire](#page-inventaire) ci-dessous.

#### Auto-scan planifié

Le dashboard peut scanner automatiquement le dossier source à intervalle régulier sans intervention manuelle.

**Activation** : onglet Configuration → section *Auto-scan planifié* → cocher *Activer l'auto-scan* + définir l'intervalle (min 5 min, max 1440 min / 24 h).

**Comportement** :
- Le thread `AutoScan` démarre au lancement du dashboard et relit la config à chaque cycle.
- Le résultat est émis via l'événement Socket.IO `auto_scan_done` vers tous les clients connectés.
- Le tableau de prévisualisation est mis à jour automatiquement ; un toast indique le nombre d'éléments prêts à uploader.
- Le cache sessionStorage est mis à jour en même temps.
- Les éléments déjà en historique sont exclus (logique identique au scan manuel).

**API** : `GET /api/autoscan/status` → `{"enabled": bool, "interval_m": int, "last_run": "ISO8601|null", "last_count": int}`

#### Persistance de la file de jobs

La file d'attente est persistée dans `web/job_queue.json`. Au redémarrage du dashboard, les jobs en attente sont recréés et reprennent leur position dans la file. Les jobs terminés (done/error/cancelled) ne sont pas restaurés.

#### Réordonnancement et retry

- **Glisser-déposer** : les jobs en attente (`pending`) ont une poignée `⠿` — déplacer une carte change la priorité dans la file (appel `POST /api/jobs/reorder`).
- **Relancer** : les jobs terminés en erreur ou annulés affichent un bouton ↺. Un clic crée un nouveau job identique mis en file d'attente.

#### Console live et stdin

- Badge **Connecté / Déconnecté** (Socket.IO) dans la barre de navigation.
- **Console active** (dock en haut de page) : le job `running` y affiche sa sortie ; stdin centralisé pour TMDB, `y/n`, etc.
- Cartes jobs : statut, annulation, badge **TMDB** mémorisé par job.
- **TMDB** : détection du prompt `valid TMDB ID (0=skip)` ; envoi automatique si un ID a déjà été saisi pour ce job ; sinon saisie manuelle (une seule fois, pas de renvoi en boucle).
- Synchronisation au chargement / reconnexion : `GET /api/jobs` + événements `job_list`, `job_output`, `job_status`.

### Page Inventaire

Accessible via le lien **Inventaire** dans la navbar (`/inventory`). Affiche **tous** les uploads Gemini de l'utilisateur (stockés dans `app_state["torrents_list"]` après sync), indépendamment de la présence locale des fichiers.

| Colonne | Contenu |
|---------|---------|
| **Nom** | Nom de la release (depuis le tracker Gemini) |
| **Date d'upload** | Horodatage `created_at` Gemini (UTC) |
| **Lien Gemini** | Bouton *Ouvrir* → page tracker dans un nouvel onglet (`{Gemini_URL}/torrents/<id>`) |
| **Client** | Badge **En seed** (vert) si actif dans qBittorrent ; `—` sinon. Badge en haut du tableau : *N en seed* |
| **Télécharger** | Lien *Re-télécharger* → URL directe `{Gemini_URL}/torrents/download/<id>` (nouvel onglet) |

**Recherche** : champ texte en temps réel (debounce 300 ms) — filtrage côté serveur sur le nom.

**Tri** : sélecteur *Date ↓ / ↑* et *Nom A→Z / Z→A* + clic sur en-têtes des colonnes Nom / Date (bascule asc/desc).

**Filtre seeding** : sélecteur *Tous / En seed / Pas en seed* — appliqué côté client sur les rows de la page courante (nécessite que la détection qBit soit opérationnelle).

**Pagination** : 50 / 100 (défaut) / 200 entrées par page, navigation ‹ › en bas du tableau.

**Détection seeding** : `GET /api/inventory/seeding` → pour chaque torrent qBittorrent dont un des trackers configurés contient le domaine Gemini, lit le champ `comment` (UNIT3D inscrit l'URL de la page torrent, ex. `https://gemini-tracker.org/torrents/5312`), extrait l'ID via regex et compare avec `tracker_id` de chaque row — correspondance exacte par ID numérique. Résultat mis en cache 60 s. Si qBittorrent est inaccessible, le badge affiche *N/A*.

**API** :
- `GET /api/inventory/items?page=&limit=&search=&sort=name|date&order=asc|desc` — liste paginée ; retourne `{rows, total, page, pages, limit}` avec `tracker_url` et `download_url` par entrée.
- `GET /api/inventory/seeding` — set d'IDs Gemini en seed dans qBittorrent : `{ids: ["5312", …], count: N, error: null|"…"}`.
- `GET /api/inventory/download/<tracker_id>` — proxy téléchargement `.torrent` (injecte `?api_token=` sans l'exposer au client).

---

### Onglet Historique

- Liste des uploads passés : nom, type, tag, date, état qBittorrent, taille, ratio, vitesse.
- **Barre de filtres** : recherche textuelle (`#hist-search`), filtre par type (`file`/`season`/`integrale`) et par statut (`done`/`error`). Les filtres sont cumulatifs et appliqués côté serveur — l'URL est mise à jour pour permettre le partage.
- **Pagination** : 50 entrées par page (paramètres `?page=&limit=`), navigation ‹ / › en bas du tableau.
- **Export CSV** : bouton *Exporter CSV* — télécharge `history_export.csv` avec toutes les entrées correspondant aux filtres actifs (sans pagination).
- Bouton **JSON** : modal avec onglets **JSON** (résultat structuré `JobResultCollector` : commandes, TMDB, doublons, chemins watcher, codes de sortie) et **Console** (transcript PTY).
- Transcript reconstruit depuis les événements si la transcription fichier est vide.
- Suppression d'une entrée ou vidage complet.

### Onglet Stats

Statistiques d'upload et diagramme Sankey (flux intégrale / saison → résultats), données qBittorrent.

**Tableau torrents actifs** :
- Affiche **tous** les torrents du client torrent (sans filtre de tag) — depuis la migration, le filtre `TAG` a été retiré de l'appel `torrents_info()`.
- **Filtrage par état** : sélecteur *Tous / uploading / seeding / paused / stalled* au-dessus du tableau.
- **Tri par colonne** : cliquer sur l'entête de colonne (Nom, Taille, Ratio, Vitesse up, État) — bascule ascendant/descendant, indicateur ▲/▼.
- Compteur d'éléments affichés mis à jour dynamiquement.

### Onglet Status

Vérification parallèle des services avec badge et latence :

| Service | Vérification |
|---------|----------------|
| Dossier source | Existence + droits lecture |
| unit3dup | Binaire `.venv` ou PATH |
| Configuration | Présence de `Unit3Dbot.json` |
| Client torrent | API qBittorrent ou socket Transmission/rTorrent |
| Tracker Gemini | Requête HTTP API |
| TMDB | Clé API v3 valide **ou** Bearer token (`TMDB_ACCESS_TOKEN`) |

États affichés :
- **Connecté** — service opérationnel
- **Déconnecté** — configuré mais inaccessible
- **Injoignable** — non configuré ou chemin manquant

Bouton **Actualiser** pour relancer les tests.

### Onglet Configuration

Sections (accordéon) :

- **Scan & Règles** — dossier source, intégrale/saisons, doublons, confirm/dry-run
- **Inventaire Gemini** — durée du cache TTL, boutons sync / force re-sync, statut du dernier run
- **Tags** — liste `valid_tags.json`
- **Tracker** — Gemini, TMDB (v3 key + Bearer token optionnel), YouTube, IGDB, image hosting
- **Client torrent** — qBit / Transmission / rTorrent
- **Préférences** — langue bot, chemins, etc.
- **Auto-scan planifié** — activation, intervalle (minutes), dernière exécution et nombre d'éléments trouvés
- **Watcher & Chemins** — démarrage/arrêt du watcher `unit3dup`, état JSON live, console PTY, stdin
- **Gestion automatique (AutoManager)** — pause/reprise qBittorrent ; **Mode nuit** : plage horaire pendant laquelle la pause auto est désactivée (reseed reste actif)

### Notifications navigateur

Un bouton 🔔 apparaît dans la barre si la permission n'est pas encore accordée. Une fois accordée :
- Une notification système est envoyée à chaque fin de job (succès ou erreur).
- Une notification est envoyée si des **doublons `duplicate_ask`** sont détectés lors d'un scan ou d'une vérification de doublons.

### Langue et thème

- Sélecteur de langue (**FR, EN, ES, IT, DE**) — fichiers `web/static/locales/*.json`, préférence `localStorage`.
- Bouton soleil/lune : thème clair/sombre.

---

## Watcher (Configuration)

Le watcher surveille un dossier (`USER_PREFERENCES.WATCHER_PATH`) et traite les releases vers la destination configurée.

| Action | API |
|--------|-----|
| État | `GET /api/watcher/status` |
| Démarrer | `POST /api/watcher/start` |
| Arrêter | `POST /api/watcher/stop` |
| Stdin | `POST /api/watcher/input` |

Événements Socket.IO : `watcher_state`, `watcher_output`, `watcher_console_sync`, `watcher_stdin`.

Interface : badge de phase, file d'attente, JSON d'état, console live (comme les jobs).

---

## AutoManager

Section *Gestion automatique* dans Configuration :

- **Suppression auto** : met en pause les torrents en seeding depuis X jours si le swarm a ≥ Y seeders.
- **Reseed auto** : reprend les torrents en pause si seeders < Z ou si la release est marquée *dead* sur Gemini (scan API).

Intervalle configurable ; bouton **Exécuter maintenant** (`POST /api/automanage/run`). Journal visible dans la même section (`GET /api/automanage/status`).

---

## API REST (résumé)

| Route | Méthode | Description |
|-------|---------|-------------|
| `/api/health` | GET | Santé serveur (toujours public) — `{"status":"ok","version":"…","uptime":N}` |
| `/api/settings` | GET/POST | Config web + fusion Unit3Dbot |
| `/api/settings/bookmarks` | POST | Gestion favoris dossiers (`{"action":"add"\|"remove","path":"…"}`) |
| `/api/scan` | POST | Scan dossier source + checks doublons |
| `/api/scan/duplicates` | POST | Re-vérification doublons sur items existants |
| `/api/jobs` | GET/POST | Liste / création jobs |
| `/api/jobs/<id>` | DELETE | Annulation |
| `/api/jobs/<id>/input` | POST | Stdin HTTP (alternative à Socket.IO) |
| `/api/jobs/<id>/retry` | POST | Relancer un job en erreur/annulé |
| `/api/jobs/reorder` | POST | Réordonner la file d'attente (`{"ids": [...]}`) |
| `/api/jobs/clear` | POST | Retirer les jobs terminés de l'UI |
| `/api/history` | GET/DELETE | Historique paginé (`?page=&limit=&search=&type=&status=`) |
| `/api/history/export.csv` | GET | Export CSV (filtres `?search=&type=&status=` appliqués) |
| `/api/history/detail/<job_id>` | GET | Détail JSON job |
| `/api/history/transcript/<job_id>` | GET | Transcript console |
| `/api/history/item` | DELETE | Suppression d'une entrée |
| `/api/torrents` | GET | Liste torrents qBit |
| `/api/stats` | GET | Agrégats + Sankey |
| `/api/status` | GET | Santé des services |
| `/api/autoscan/status` | GET | État thread auto-scan |
| `/api/inventory/status` | GET | État inventaire Gemini (running, last_run_at, total, added) |
| `/api/inventory/sync` | POST | Lance/force sync inventaire — `{"force": bool}` |
| `/api/inventory/items` | GET | Liste paginée de l'inventaire (`?page=&limit=&search=&sort=name\|date&order=asc\|desc`) — avec `tracker_url` et `download_url` |
| `/api/inventory/seeding` | GET | Set d'IDs Gemini en seed dans qBittorrent (via champ `comment`, cache 60 s) — `{ids, count, error}` |
| `/api/inventory/download/<id>` | GET | Proxy téléchargement `.torrent` (injecte `api_token`) |
| `/api/watcher/*` | — | Watcher |
| `/api/automanage/*` | — | AutoManager |

> **Rate-limit** : les endpoints `/api/scan` (POST) sont limités à **5 requêtes par 60 secondes par IP**. Un dépassement retourne HTTP 429 avec `{"error": "rate_limit", "retry_after": N}`.

### Socket.IO

| Événement | Direction | Rôle |
|-----------|-----------|------|
| `connect` | serveur → client | Envoi `job_list` initial |
| `job_output` | serveur → client | Chunk console (`text`, `op`: append/replace) |
| `job_status` | serveur → client | pending / running / done / error / cancelled |
| `jobs_cleared` | serveur → client | Cartes retirées |
| `scan_progress` | serveur → client | Progression scan : `{phase: "inventory"\|"scanning"\|"tmdb"\|"duplicates"\|"done", page?, fetched?, done?, total?}` |
| `auto_scan_done` | serveur → client | Résultat auto-scan : `{items, skipped, ran_at, to_upload}` — met à jour le tableau de prévisualisation |
| `inventory_started` | serveur → client | Début sync inventaire : `{force: bool}` — alimente le centre de notifications |
| `inventory_done` | serveur → client | Fin sync inventaire background : `{added, total, cached}` ou `{error}` — alimente le centre de notifications |
| `stdin` | client → serveur | Réponse interactive (`id`, `text`) |
| `watcher_*` | — | État et console watcher |

---

## Déploiement production

### Derrière un reverse proxy (nginx)

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

WebSocket requis pour l'onglet Jobs (Socket.IO) et le watcher en live.

### systemd (exemple)

```ini
[Unit]
Description=Unit3Dup Dashboard
After=network.target

[Service]
Type=simple
User=unit3dup
WorkingDirectory=/opt/unit3dup
ExecStart=/opt/unit3dup/.venv/bin/python /opt/unit3dup/start_web.py --host 127.0.0.1 --port 5000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Pour HTTPS via systemd :

```ini
ExecStart=/opt/unit3dup/.venv/bin/python /opt/unit3dup/start_web.py \
    --host 127.0.0.1 --port 5000 --tls \
    --cert /opt/unit3dup/web/.ssl/cert.pem \
    --key  /opt/unit3dup/web/.ssl/key.pem
```

### Sécurité

- En production, préférer `--host 127.0.0.1` + reverse proxy avec authentification.
- Ne pas exposer le dashboard sur Internet sans protection.
- Utiliser `./start_web.sh --setup` pour définir un mot de passe avant toute exposition réseau.
- `SECRET_KEY` Flask : depuis `U3D_SECRET_KEY` (env) ou `web/.secret_key` (généré automatiquement). Garder ce fichier privé.
- Les mots de passe qBit/Transmission sont stockés en clair dans `Unit3Dbot.json` — restreindre les permissions du fichier (`chmod 600`).
- Le rate-limit `/api/scan` (5 req/60 s/IP) protège contre les appels API abusifs, pas contre les attaques par force brute — le dashboard doit être derrière un reverse proxy ou VPN en production.

---

## Dépannage

| Problème | Piste |
|----------|-------|
| Page Jobs « Déconnecté » en permanence | Erreur JavaScript (`dashboard.js`) : console F12 ; Ctrl+F5 ; vérifier que le serveur tourne |
| `write() before start_response` sur `/socket.io/…websocket` | Réinstaller les deps web (`pip install -r web/requirements.txt`) pour obtenir **eventlet** ; redémarrer le dashboard (`async_mode` eventlet, pas Werkzeug seul) |
| TMDB envoyé en boucle | Corrigé côté client : une seule réponse par prompt ; recharger `dashboard.js` |
| Pas de `-u` S01E01 | Vérifier qu'un fichier `*S01E01*` existe dans le pack (ou dossier S01 voisin pour intégrale) ; pack S02+ n'envoie pas d'épisode seul |
| TMDB : erreur 401 / recherche échoue | Vérifier `TMDB_APIKEY` (v3) ou `TMDB_ACCESS_TOKEN` (Bearer) dans Configuration › Tracker. Le Bearer token est prioritaire si renseigné — le générer sur https://www.themoviedb.org/settings/api (champ "API Read Access Token") |
| Doublon non détecté | `duplicate_cache_ttl_sec = 0` ou attendre expiration du cache ; vérifier tracker dans Unit3Dbot |
| Bouton Doublons verrouillé | Rate-limit 429 du tracker — attendre la fin du décompte (60 s) puis recliquer |
| Inventaire Gemini bloqué / 0 correspondance | Vérifier `Gemini_USERNAME` dans Configuration › Tracker ; consulter `web/logs/app/app.log` (lignes `Inventory :`) pour le diagnostic |
| Badge "En seed" affiche N/A ou 0 | qBittorrent inaccessible, ou `TORRENT_CLIENT` ≠ `qbittorrent`, ou torrents Gemini sans champ `comment` (vérifier dans qBit : propriétés d'un torrent Gemini → commentaire = URL de la page tracker) |
| Filtre "En seed" / "Pas en seed" inactif | Détection seeding non opérationnelle — voir ligne ci-dessus ; le filtre ne s'applique que sur la page courante (not sur la totalité de l'inventaire) |
| Scan lent malgré cache inventory | Le TTL est peut-être expiré et le count a changé → re-fetch déclenché. Augmenter `inventory_cache_ttl_hours` dans Configuration → Inventaire Gemini |
| Bouton "Synchroniser" ne démarre pas | Une sync est déjà en cours (badge "En cours…" dans la carte) ; attendre la fin |
| Nom de torrent préfixé "MMULTI" | Bug corrigé (step 8a `release_normalizer.py`) — mettre à jour et relancer |
| Client torrent rouge | Host/port/credentials ; onglet Status |
| Tracker injoignable | URL Gemini + clé API ; pare-feu |
| Config non sauvegardée | Champs obligatoires (astérisque rouge) |
| `unit3dup` introuvable | `./start_web.sh` une fois pour créer le venv |
| Daemon ne s'arrête pas | `python3 start_web.py --stop` ; après 5 s SIGTERM, SIGKILL automatique ; vérifier `/tmp/u3dup-web.pid` |
| Erreur "daemon tourne déjà" au démarrage | Un process tient encore le port — `--stop` puis relancer ; ou supprimer `/tmp/u3dup-web.pid` si le PID est périmé |
| `--setup` : werkzeug manquant | Lancer `./start_web.sh` une fois sans `--setup` pour créer le venv, puis relancer avec `--setup` |
| Avertissement certificat TLS dans le navigateur | Certificat auto-signé — accepter l'exception ou utiliser un certificat signé par une CA (Let's Encrypt, etc.) |
| Erreur TLS `[SSL: WRONG_VERSION_NUMBER]` | Vérifier que le client accède bien en `https://` et non `http://` |
| Auto-scan ne se déclenche pas | Vérifier que *Activer l'auto-scan* est coché dans Configuration et enregistré ; consulter `web/logs/app/app.log` (thread `AutoScan`) |
| Jobs perdus après redémarrage | Normalement restaurés depuis `job_queue.json` ; si le fichier est corrompu, le supprimer pour repartir d'une file vide |
| Historique vide / erreur SQLite | `history.db` corrompu — arrêter le dashboard, supprimer `web/history.db` et redémarrer. Si `history.json` + `history_transcripts.json` existent encore, la migration recrée automatiquement la base. Sinon l'historique repart de zéro. |
| Rate-limit 429 sur `/api/scan` | 5 appels max par 60 s par IP — attendre ou utiliser `/api/health` pour vérifier que le serveur répond |
| Tableau historique vide après filtrage | Le filtre est appliqué côté serveur — vider les champs recherche/type/statut pour afficher tout l'historique |
| Notification doublon ne s'affiche pas | Vérifier que la permission navigateur est accordée (bouton 🔔) ; les notifications sont bloquées en `http://` sur Chrome sauf `localhost` |

Logs daemon : `tail -f /tmp/u3dup-web.log`

Logs application (Flask + erreurs Python) : `tail -f web/logs/app/app.log`

---

## Structure web

```
web/
├── app.py                      # Application factory (Flask + Socket.IO, rate-limit)
├── extensions.py               # Singleton SocketIO
├── shared.py                   # État partagé : _jobs, _jobs_lock, _job_queue
├── sockets.py                  # Handlers Socket.IO (@socketio.on)
├── web_config.json             # Config dashboard
├── history.db                  # SQLite WAL : historique chemins + transcripts jobs
│                               #   (remplace history.json + history_transcripts.json)
├── job_queue.json              # File d'attente persistée (restaurée au redémarrage)
├── duplicate_check_cache.json  # Cache checks doublons (généré — migré SQLite au 1er démarrage)
├── .ssl/                       # Certificat TLS (cert.pem + key.pem) généré par --setup/--tls
├── .secret_key                 # SECRET_KEY Flask (généré au premier lancement)
├── requirements.txt
├── core/
│   ├── conf.py                 # Constantes de chemins, I/O config/logs, default_tracker_name(), queue helpers
│   ├── db.py                   # SQLite WAL : init_db(), CRUD historique + transcripts, migration JSON legacy
│   ├── job.py                  # Job, JobResultCollector, scheduler thread, _restore_queue()
│   ├── scanner.py              # Scan dossier source, détection type/tag/épisode, règles collection
│   ├── duplicate.py            # Détection doublons Gemini + cache SQLite TTL
│   ├── tmdb_search.py          # Recherche TMDB légère (sans stack CLI), enrich_items_with_tmdb()
│   ├── stream.py               # ConsoleStream (normalisation sortie PTY)
│   ├── checker.py              # Vérification santé services (status page, thread-safe)
│   ├── torrent.py              # Requêtes qBittorrent (cache 15 s) + détection seeding Gemini via comment (cache 60 s)
│   ├── watcher.py              # WatcherService (unit3dup --watcher PTY)
│   ├── automanager.py          # AutoManager (pause/reseed auto qBit)
│   ├── autoscan.py             # Thread auto-scan planifié + émission auto_scan_done
│   └── gemini_inventory.py     # Inventaire Gemini : TTL cache + count check + sync BG
├── routes/
│   ├── pages.py                # Pages HTML (index, history, settings, stats, status, inventory)
│   ├── jobs.py                 # /api/scan, /api/jobs, /api/jobs/clear, /api/normalize
│   ├── history.py              # /api/history (filtres, pagination), /api/history/export.csv
│   ├── settings.py             # /api/settings, /api/settings/bookmarks, export/import
│   ├── stats.py                # /api/stats, /api/torrents
│   ├── status.py               # /api/status
│   ├── health.py               # /api/health (toujours public, exempt auth)
│   ├── autoscan_bp.py          # /api/autoscan/status
│   ├── inventory_bp.py         # /api/inventory/status, /sync, /items, /seeding, /download/<id>
│   ├── watcher_bp.py           # /api/watcher/*
│   └── automanage.py           # /api/automanage/*
├── logs/
│   ├── upload/                 # Un fichier JSON par job : <job_id>.json
│   └── app/                    # Logs Flask/app (app.log, rotation 5 Mo × 3)
├── templates/
│   ├── base.html               # Layout commun, navbar (Jobs/Historique/Stats/Status/RSS/Inventaire/Config)
│   ├── index.html              # Jobs (scan, dock console active)
│   ├── history.html            # Historique + filtres + export CSV + modal JSON/Console
│   ├── inventory.html          # Inventaire Gemini (liste, recherche, pagination, qBit status, re-download)
│   ├── settings.html           # Config, auto-scan, watcher, AutoManager
│   ├── stats.html              # Sankey + tableau torrents triable/filtrable
│   └── status.html
└── static/
    ├── css/style.css
    ├── js/
    │   ├── dashboard.js              # État partagé, Socket.IO, DOMContentLoaded, cache scan localStorage, favoris
    │   ├── dashboard-notifications.js # requestNotifPermission(), _notify()
    │   ├── dashboard-console.js      # Panel xterm, dock console, TMDB prompt, stdin
    │   ├── dashboard-scan.js         # Scan, preview, doublons, rate-limit cooldown
    │   ├── dashboard-jobs.js         # Cycle de vie jobs, renderJob(), SortableJS, badges
    │   ├── settings.js               # Config, auto-scan, watcher, AutoManager
    │   ├── history.js
    │   ├── stats.js                  # Tri/filtre torrents
    │   ├── status.js
    │   └── i18n.js
    └── locales/                # fr.json, en.json, es.json, it.json, de.json (+ clés inventory.*)
├── tests/
│   ├── test_conf.py            # _atomic_write_json, _safe_read_json, default_tracker_name, _rotate_upload_logs
│   ├── test_db.py              # CRUD historique SQLite, transcripts, pagination, filtres
│   ├── test_duplicate_cache.py # _prune_duplicate_cache, apply_duplicate_checks guards
│   ├── test_scanner.py         # Parsing noms, _scan_dir, scan_source (récursif, tag, historique)
│   ├── test_stream.py          # strip_ansi, ConsoleStream, normalize_transcript
│   └── test_i18n.py            # Cohérence locales (JSON valide, parité des clés, couverture HTML/JS)
start_web.py                    # Lanceur (--host, --port, --daemon, --stop, --setup, --tls, --cert, --key)
start_web.sh                    # Wrapper shell
```

---

## Tests

```bash
# Depuis la racine du projet (venv activé)
cd web && python -m pytest tests/ -v
```

Les tests couvrent les modules Python du dashboard (`core/conf.py`, `core/db.py`, `core/duplicate.py`, `core/scanner.py`, `core/stream.py`) et la cohérence i18n (`test_i18n.py` — parité de clés entre les 5 locales, couverture HTML/JS). Chaque test utilise des répertoires temporaires (`tmp_path`) et `monkeypatch` pour isoler le filesystem — aucun fichier réel n'est modifié.

---

## Dépendances Python (web)

```
flask>=3.0
flask-socketio>=5.3
simple-websocket>=1.0
eventlet>=0.35.0   # serveur WebSocket pour Jobs / watcher (évite erreurs Werkzeug)
werkzeug>=3.0      # hash mot de passe (pbkdf2:sha256)
qbittorrent-api   # installé avec unit3dup si client qBit
```

Installées automatiquement par `start_web.py` / `start_web.sh`.

---

## Schéma — séquence d'upload par type

```
FICHIER     →  unit3dup -u <fichier>

SAISON S01  →  unit3dup -u <S01E01.mkv>  (si trouvé)
            →  unit3dup -f <pack S01>

SAISON S02+ →  unit3dup -f <pack>

INTEGRALE   →  unit3dup -u <S01E01>      (si trouvé)
            →  unit3dup -f <dossier intégrale>
            →  unit3dup -f <S01> …        (si « upload saisons » activé)
```

Les jobs s'exécutent **séquentiellement** dans une file ; chaque job enchaîne ses commandes dans l'ordre ci-dessus (arrêt sur code de sortie ≠ 0).
