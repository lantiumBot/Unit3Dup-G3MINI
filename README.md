# Unit3Dup — Fork G3MINI

Fork de [Unit3Dup](https://github.com/31December99/Unit3Dup) pour **G3MINI Tracker** : normalisation des noms de release, `personal_release` par tag d’équipe, nettoyage des `.nfo` orphelins (watcher).

---

## Démarrage rapide

### Prérequis

- **Python 3.10+** (`python3`, `python3-venv`, `pip`)
- **ffmpeg**
- **git**

```bash
sudo apt install ffmpeg python3 python3-pip python3-venv git
```

### Installation

```bash
git clone https://github.com/lantiumBot/Unit3Dup-G3MINI.git unit3dup
cd unit3dup
chmod +x upload.sh unit3dup-wrapper.sh
```

Au **premier** lancement, `upload.sh` crée `.venv` et exécute `pip install -e .` automatiquement si besoin.

### Configuration

Génère `~/Unit3Dup_config/Unit3Dbot.json` au premier lancement :

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/unit3dup --help
```

(`upload.sh` crée aussi le venv au besoin, mais il faut un chemin valide pour l’exécuter.)

Édite la config :

```bash
nano ~/Unit3Dup_config/Unit3Dbot.json
```

| Champ | Description |
|-------|-------------|
| `Gemini_URL` | URL du tracker |
| `Gemini_APIKEY` / `Gemini_PID` | Clé API et passkey du tracker |
| `TMDB_APIKEY` | [themoviedb.org](https://www.themoviedb.org/settings/api) |
| `IMGBB_KEY` | Screenshots ([imgbb.com](https://imgbb.com)) |
| `WATCHER_PATH` / `WATCHER_DESTINATION_PATH` | Mode watcher uniquement |
| `QBIT_*` ou `TRASM_*` | Client torrent pour le seed auto |

Section **`uploader_tag`** (à ajouter manuellement si besoin) :

```json
"uploader_tag": {
    "TAGS_TEAM": ["MONTEAM"]
}
```

Si la release se termine par `-MONTEAM`, `personal_release` est activé automatiquement à l’upload (selon ta config).

### Upload par lot (`upload.sh`)

Parcourt un dossier : **fichiers vidéo** → `-u`, **sous-dossiers** → `-f`.

```bash
./upload.sh /chemin/vers/releases
./upload.sh ./releases --confirm   # confirmation avant chaque -u
```

Le chemin est **obligatoire**. Pas de racine par défaut.

**Série intégrale** (plusieurs saisons dans un seul torrent) : passe le dossier dont le nom contient `INTEGRALE`, pas le parent `Series/` :

```text
/storage/Upload/Series/Serie.INTEGRALE.x265-TEAM/
├── Serie.S01.x265-TEAM/
├── Serie.S02.x265-TEAM/
└── Serie.S03.x265-TEAM/
```

```bash
# Intégrale seule (1 torrent, toutes les saisons)
./upload.sh "/storage/Upload/Series/Serie.INTEGRALE.x265-TEAM"

# Intégrale + chaque pack saison (S01, S02, S03 en torrents séparés)
./upload.sh "/storage/Upload/Series/Serie.INTEGRALE.x265-TEAM" --with-seasons
```

→ intégrale : un `unit3dup -f` sur le dossier parent ; avec `--with-seasons`, un `-f` supplémentaire par sous-dossier direct (`Serie.S01...`, etc.).  
Sans `INTEGRALE` dans le nom, `upload.sh` enverrait chaque sous-dossier séparément (sans upload du parent).

Équivalent manuel :

```bash
unit3dup -f ".../Serie.INTEGRALE.x265-TEAM"
unit3dup -f ".../Serie.INTEGRALE.x265-TEAM/Serie.S01.x265-TEAM"
unit3dup -f ".../Serie.INTEGRALE.x265-TEAM/Serie.S02.x265-TEAM"
```

---

## Commandes `unit3dup`

Après install du venv (manuel ou via `upload.sh`) :

```bash
.venv/bin/unit3dup -u /chemin/fichier.mkv
.venv/bin/unit3dup -f /chemin/dossier
.venv/bin/unit3dup -scan /chemin/dossier
.venv/bin/unit3dup -watcher
```

### Commande globale (optionnel)

Pour taper `unit3dup` depuis n’importe où :

```bash
chmod +x unit3dup-wrapper.sh
ln -sf "$(pwd)/unit3dup-wrapper.sh" ~/.local/bin/unit3dup
# ou : sudo ln -sf "$(pwd)/unit3dup-wrapper.sh" /usr/local/bin/unit3dup
```

Le wrapper active `.venv` et appelle `.venv/bin/unit3dup`.

### Install manuelle du venv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
unit3dup --help
```

Le mode `-e` permet un simple `git pull` sans réinstaller (sauf nouvelles dépendances).

---

## Mise à jour

```bash
cd unit3dup
git pull
source .venv/bin/activate && pip install -e .   # si requirements.txt a changé
```

---

## Docker / NAS

Stack à la racine : `Dockerfile`, `docker-compose.yml`. Config dans `/config` (`UNIT3DUP_CONFIG_ROOT`).

```bash
docker compose build
docker compose run --rm unit3dup --help    # crée Unit3Dbot.json
# éditer docker-data/config/Unit3Dbot.json (WATCHER_PATH=/watch, etc.)
docker compose up -d                       # -watcher
```

Upload manuel dans le conteneur :

```bash
docker compose run --rm unit3dup -u /data/fichier.mkv
```

Sur NAS, adapte les volumes (`/volume1/...`) et `PUID` / `PGID` dans le compose. Si aucun client torrent n’est joignable, utilise `-noseed` ou `-watcher -noseed` pour éviter les redémarrages en boucle.

---

## Projet original

Basé sur [Unit3Dup](https://github.com/31December99/Unit3Dup) — licence MIT.
