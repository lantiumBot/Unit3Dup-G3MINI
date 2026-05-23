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

### Mode daemon (arrière-plan, Linux)

```bash
./start_web.sh --daemon --host 0.0.0.0 --port 5000
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

Variables d'environnement supportées : `U3D_WEB_HOST`, `U3D_WEB_PORT`.

---

## Configuration

### Emplacement des fichiers

| Fichier | Chemin par défaut | Contenu |
|---------|-------------------|---------|
| Unit3Dbot | `~/Unit3Dup_config/Unit3Dbot.json` | Tracker, client torrent, préférences, chemins watcher |
| Config web | `web/web_config.json` | Dossier source, règles de scan, doublons, AutoManager |
| Tags valides | `valid_tags.json` (racine projet) | Tags autorisés pour les releases |
| Historique | `web/history.json` | Chemins déjà traités (scan) |
| Transcriptions | `web/history_transcripts.json` | Résultat JSON + transcript console par job |
| Cache doublons | `web/duplicate_check_cache.json` | Résultats des checks Gemini au scan (TTL configurable) |

Surcharger le dossier config :

```bash
export UNIT3DUP_CONFIG_ROOT=/chemin/vers/config
./start_web.sh
```

### Configuration minimale (onglet **Configuration**)

Champs **obligatoires** (validation à l'enregistrement) :

**Tracker**
- `Gemini_URL`, `Gemini_APIKEY`, `Gemini_PID`, `TMDB_APIKEY`

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
| **Mode `-confirm`** | — | Ajoute `-confirm` à chaque commande `unit3dup` |
| **Dry-run** | — | Réservé (simulation) |
| **Doublons — seuil skip** | `duplicate_ask_pct` (%) | Si le delta taille vs release Gemini ≤ seuil → statut `duplicate` (ignoré). Entre seuil et `SIZE_TH` → `duplicate_ask` (confirmation à l'upload) |
| **Cache checks doublons** | `duplicate_cache_ttl_sec` (s) | `0` = toujours interroger le tracker ; `>0` = réutiliser `duplicate_check_cache.json` |

---

## Utilisation

### Onglet Jobs

1. Configurer le **dossier source** dans Configuration.
2. Cliquer sur **Analyser le dossier** : parcourt le dossier source (un niveau : fichiers vidéo + sous-dossiers).
3. Tableau de prévisualisation : type, tag, saisons, statut (dont doublons Gemini).
4. Sélectionner les éléments, puis **Démarrer les uploads**.

#### Types détectés au scan

| Type | Critère de nom | Commandes `unit3dup` exécutées (dans l'ordre) |
|------|----------------|-----------------------------------------------|
| **FICHIER** | `.mkv`, `.mp4`, `.avi`, `.m2ts` à la racine du dossier source | `-u` sur le fichier |
| **INTEGRALE** | Mot `INTEGRALE` (séparateurs `.`, `_`, `-`, espace) | Si **S01E01** trouvé : `-u` épisode → `-f` intégrale → `-f` chaque sous-dossier saison (si règle *upload saisons* activée) |
| **SAISON** | `COMPLETE` ou `Sxx` dans le nom + tag valide (si requis) | **S01 uniquement** : `-u` S01E01 (si trouvé) puis `-f` pack. **S02+** ou COMPLETE sans `S01` : `-f` pack seul |
| **Historique** | Chemin déjà dans `history.json` | Affiché en lecture seule (non sélectionnable pour upload) |
| **Ignoré** | Tag invalide, type inconnu, doublon certain | Non uploadable |

**S01E01** : seul le premier épisode de la **saison 1** est cherché dans le nom de fichier (`S01E01`, variantes `S1E1`, etc.). Recherche dans le dossier (profondeur 2). Pour une intégrale, recherche aussi dans un dossier voisin nommé **S01** si besoin.

#### Détection des doublons (Gemini)

Lors du scan, chaque élément éligible est comparé au tracker (logique `Duplicate` / delta de taille, seuil `SIZE_TH` côté bot).

- **`duplicate`** : skip automatique (delta ≤ `duplicate_ask_pct`).
- **`duplicate_ask`** : cochable ; confirmation navigateur avant upload.
- **`pending`** : pas de doublon bloquant.

Le tableau affiche les correspondances (TMDB/IGDB/torrent), deltas et tailles locales/Gemini. Le cache évite de re-scanner le tracker si `duplicate_cache_ttl_sec > 0`.

#### Console live et stdin

- Badge **Connecté / Déconnecté** (Socket.IO) dans la barre de navigation.
- **Console active** (dock en haut de page) : le job `running` y affiche sa sortie ; stdin centralisé pour TMDB, `y/n`, etc.
- Cartes jobs : statut, annulation, badge **TMDB** mémorisé par job.
- **TMDB** : détection du prompt `valid TMDB ID (0=skip)` ; envoi automatique si un ID a déjà été saisi pour ce job ; sinon saisie manuelle (une seule fois, pas de renvoi en boucle).
- Synchronisation au chargement / reconnexion : `GET /api/jobs` + événements `job_list`, `job_output`, `job_status`.

### Onglet Historique

- Liste des uploads passés : nom, type, tag, date, état qBittorrent, taille, ratio, vitesse.
- Bouton **JSON** : modal avec onglets **JSON** (résultat structuré `JobResultCollector` : commandes, TMDB, doublons, chemins watcher, codes de sortie) et **Console** (transcript PTY).
- Transcript reconstruit depuis les événements si la transcription fichier est vide.
- Suppression d'une entrée ou vidage complet.

### Onglet Stats

Statistiques d'upload et diagramme Sankey (flux intégrale / saison → résultats), données qBittorrent.

### Onglet Status

Vérification parallèle des services avec badge et latence :

| Service | Vérification |
|---------|----------------|
| Dossier source | Existence + droits lecture |
| unit3dup | Binaire `.venv` ou PATH |
| Configuration | Présence de `Unit3Dbot.json` |
| Client torrent | API qBittorrent ou socket Transmission/rTorrent |
| Tracker Gemini | Requête HTTP API |
| TMDB | Clé API valide |

États affichés :
- **Connecté** — service opérationnel
- **Déconnecté** — configuré mais inaccessible
- **Injoignable** — non configuré ou chemin manquant

Bouton **Actualiser** pour relancer les tests.

### Onglet Configuration

Sections (accordéon) :

- **Scan & Règles** — dossier source, intégrale/saisons, doublons, confirm/dry-run
- **Tags** — liste `valid_tags.json`
- **Tracker** — Gemini, TMDB, équipes
- **Client torrent** — qBit / Transmission / rTorrent
- **Préférences** — langue bot, chemins, etc.
- **Watcher & Chemins** — démarrage/arrêt du watcher `unit3dup`, état JSON live, console PTY, stdin
- **Gestion automatique (AutoManager)** — pause/reprise qBittorrent

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
| `/api/settings` | GET/POST | Config web + fusion Unit3Dbot |
| `/api/scan` | POST | Scan dossier source + checks doublons |
| `/api/jobs` | GET/POST | Liste / création jobs |
| `/api/jobs/<id>` | DELETE | Annulation |
| `/api/jobs/<id>/input` | POST | Stdin HTTP (alternative à Socket.IO) |
| `/api/jobs/clear` | POST | Retirer les jobs terminés de l'UI |
| `/api/history` | GET/DELETE | Historique |
| `/api/history/detail/<job_id>` | GET | Détail JSON job |
| `/api/history/transcript/<job_id>` | GET | Transcript console |
| `/api/torrents` | GET | Liste torrents qBit |
| `/api/stats` | GET | Agrégats + Sankey |
| `/api/status` | GET | Santé des services |
| `/api/watcher/*` | — | Watcher |
| `/api/automanage/*` | — | AutoManager |

### Socket.IO

| Événement | Direction | Rôle |
|-----------|-----------|------|
| `connect` | serveur → client | Envoi `job_list` initial |
| `job_output` | serveur → client | Chunk console (`text`, `op`: append/replace) |
| `job_status` | serveur → client | pending / running / done / error / cancelled |
| `jobs_cleared` | serveur → client | Cartes retirées |
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

### Sécurité

- En production, préférer `--host 127.0.0.1` + reverse proxy avec authentification.
- Ne pas exposer le dashboard sur Internet sans protection.
- Les mots de passe qBit/Transmission sont stockés en clair dans `Unit3Dbot.json` — restreindre les permissions du fichier (`chmod 600`).

---

## Dépannage

| Problème | Piste |
|----------|-------|
| Page Jobs « Déconnecté » en permanence | Erreur JavaScript (`dashboard.js`) : console F12 ; Ctrl+F5 ; vérifier que le serveur tourne |
| `write() before start_response` sur `/socket.io/…websocket` | Réinstaller les deps web (`pip install -r web/requirements.txt`) pour obtenir **eventlet** ; redémarrer le dashboard (`async_mode` eventlet, pas Werkzeug seul) |
| TMDB envoyé en boucle | Corrigé côté client : une seule réponse par prompt ; recharger `dashboard.js` |
| Pas de `-u` S01E01 | Vérifier qu'un fichier `*S01E01*` existe dans le pack (ou dossier S01 voisin pour intégrale) ; pack S02+ n'envoie pas d'épisode seul |
| Doublon non détecté | `duplicate_cache_ttl_sec = 0` ou attendre expiration du cache ; vérifier tracker dans Unit3Dbot |
| Client torrent rouge | Host/port/credentials ; onglet Status |
| Tracker injoignable | URL Gemini + clé API ; pare-feu |
| Config non sauvegardée | Champs obligatoires (astérisque rouge) |
| `unit3dup` introuvable | `./start_web.sh` une fois pour créer le venv |
| Daemon ne s'arrête pas | `python3 start_web.py --stop` ; vérifier `/tmp/u3dup-web.pid` |

Logs daemon : `tail -f /tmp/u3dup-web.log`

---

## Structure web

```
web/
├── app.py                      # Flask + Socket.IO + scan, jobs PTY, doublons, watcher, AutoManager
├── web_config.json             # Config dashboard
├── history.json                # Chemins traités
├── history_transcripts.json    # JSON résultat + transcripts par job_id
├── duplicate_check_cache.json  # Cache checks doublons (généré)
├── requirements.txt
├── templates/
│   ├── index.html              # Jobs (scan, dock console active)
│   ├── history.html            # Historique + modal JSON/Console
│   ├── settings.html           # Config, watcher, AutoManager
│   ├── stats.html
│   ├── status.html
│   └── base.html
└── static/
    ├── css/style.css
    ├── js/
    │   ├── dashboard.js        # Jobs, scan, doublons, console, TMDB
    │   ├── settings.js         # Config, watcher
    │   ├── history.js
    │   ├── stats.js
    │   ├── status.js
    │   └── i18n.js
    └── locales/                # fr, en, es, it, de
start_web.py                    # Lanceur (--host, --port, --daemon, --stop)
start_web.sh                    # Wrapper shell
```

---

## Dépendances Python (web)

```
flask>=3.0
flask-socketio>=5.3
simple-websocket>=1.0
eventlet>=0.35.0   # serveur WebSocket pour Jobs / watcher (évite erreurs Werkzeug)
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
