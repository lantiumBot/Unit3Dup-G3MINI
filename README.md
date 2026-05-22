# Unit3Dup — Fork G3MINI

Fork de [Unit3Dup](https://github.com/31December99/Unit3Dup) adapté pour **G3MINI Tracker**.

Ce fork ajoute la normalisation automatique des noms de release selon les conventions du tracker, la détection du flag `personal_release` par tag d'équipe, et le nettoyage automatique des fichiers `.nfo` orphelins.

---

## Fonctionnalités ajoutées

- **Normalisation des noms de release** : les noms sont automatiquement reformatés selon les conventions G3MINI (`Titre.Année.Langue.Résolution.HDR.Source.Audio.Codec-TEAM`)
- **Détection `personal_release`** : si le tag de la release (ex: `-KFL`) correspond à un tag configuré dans `TAGS_TEAM`, le champ `personal_release` est automatiquement coché à l'upload
- **Nettoyage des `.nfo` orphelins** : le watcher supprime automatiquement les fichiers `.nfo` isolés après traitement

---

## Installation

### Prérequis

```bash
sudo apt install ffmpeg python3 python3-pip python3-venv git
```

Il vous faut la version 3.13.5 de Python3

### Cloner le repo

```bash
git clone https://github.com/lantiumBot/Unit3Dup-G3MINI ~/unit3dup
cd ~/unit3dup
```

### Créer un environnement virtuel et installer

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

L'option `-e` (editable) permet de recevoir les mises à jour du fork simplement avec un `git pull`, sans réinstaller.

### Vérifier l'installation

```bash
unit3dup --help
```

Si la commande n'est pas trouvée, active d'abord le venv manuellement :

```bash
source .venv/bin/activate
unit3dup --help
```

---

### Wrapper (optionnel mais recommandé)

Le wrapper permet d'utiliser `unit3dup` depuis n'importe où **sans activer le venv manuellement** à chaque fois. Il détecte automatiquement son emplacement — peu importe où tu as cloné le repo.

Rends-le exécutable et crée le symlink :

```bash
chmod +x ~/unit3dup/unit3dup-wrapper.sh
sudo ln -s ~/unit3dup/unit3dup-wrapper.sh /usr/local/bin/unit3dup
```

Vérifie que ça fonctionne :

```bash
which unit3dup
unit3dup --help
```

---

## Configuration

### Étape 1 — Générer la configuration initiale

Au premier lancement, unit3dup crée automatiquement le dossier `~/Unit3Dup_config/` avec un fichier `Unit3Dbot.json` pré-rempli :

```bash
unit3dup --help
```

### Étape 2 — Remplir la configuration

```bash
nano ~/Unit3Dup_config/Unit3Dbot.json
```

Les champs essentiels à renseigner :

| Champ | Description | Requis |
|---|---|---|
| `Gemini_URL` | URL de G3MINI | ✅ |
| `Gemini_APIKEY` | Clé API (profil G3MINI) | ✅ |
| `Gemini_PID` | Ton passkey | ✅ |
| `TMDB_APIKEY` | Clé gratuite sur [themoviedb.org](https://www.themoviedb.org/settings/api) | ✅ |
| `WATCHER_PATH` | Chemin vers ton dossier de watch (la où sont les releases à upload) | ✅ |
| `WATCHER_DESTINATION_PATH` | Chemin de destination des releases après l'upload | ✅ |
| `IMGBB_KEY` | Clé gratuite sur [imgbb.com](https://imgbb.com) pour les screenshots | ✅ |

> **Permissions :** Assure-toi que l'utilisateur qui lance unit3dup a bien les droits en lecture sur `WATCHER_PATH` et en écriture sur `WATCHER_DESTINATION_PATH`. Si ces dossiers sont sur un montage NFS ou un partage réseau, vérifie aussi que le montage est actif avant de lancer le watcher.

### Étape 3 — Ajouter tes tags d'équipe

La section `uploader_tag` n'est **pas générée automatiquement**, il faut l'ajouter manuellement dans le JSON :

```json
"uploader_tag": {
    "TAGS_TEAM": ["MONTAG"]
}
```

Si ta release se termine par `-MONTAG`, le champ `personal_release` sera automatiquement activé à l'upload. Tu peux mettre plusieurs tags dans le tableau.

---

## Utilisation

```bash
# Uploader un fichier
unit3dup -u /chemin/vers/fichier.mkv

# Uploader un dossier entier
unit3dup -f /chemin/vers/dossier

# Scanner un dossier
unit3dup -scan /chemin/vers/dossier
```

---

## Mise à jour

```bash
cd ~/unit3dup
git pull
```

Pas besoin de réinstaller grâce au mode `-e`. Si des nouvelles dépendances ont été ajoutées :

```bash
source .venv/bin/activate
pip install -e .
```

---

## Projet original

Ce fork est basé sur [Unit3Dup](https://github.com/31December99/Unit3Dup) — licence MIT.

---

## Docker / NAS

Une stack Docker prête à l'emploi est disponible à la racine du dépôt avec :

- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

Le conteneur utilise ce fork localement et stocke sa configuration dans `/config` via la variable d'environnement `UNIT3DUP_CONFIG_ROOT`.

### Volumes par défaut

Le `docker-compose.yml` monte ces dossiers :

- `./docker-data/config` -> `/config`
- `./docker-data/watch` -> `/watch`
- `./docker-data/done` -> `/done`
- `./docker-data/media` -> `/data`

Sur un NAS, remplace de préférence ces chemins par tes partages absolus, par exemple :

```yaml
volumes:
  - /volume1/docker/unit3dup/config:/config
  - /volume1/torrents/watch:/watch
  - /volume1/torrents/done:/done
  - /volume1/media:/data
```

### 1. Construire l'image

```bash
docker compose build
```

### 2. Générer la configuration initiale

Lance une première fois l'outil pour créer `/config/Unit3Dbot.json` :

```bash
docker compose run --rm unit3dup --help
```

### 3. Éditer la configuration

Modifie ensuite `Unit3Dbot.json` dans ton dossier `config` et adapte au minimum :

```json
"WATCHER_PATH": "/watch",
"WATCHER_DESTINATION_PATH": "/done"
```

Renseigne aussi :

- `Gemini_URL`
- `Gemini_APIKEY`
- `Gemini_PID`
- `TMDB_APIKEY`
- `IMGBB_KEY`

Si tu utilises un client torrent externe sur le NAS, pense également à corriger :

- `QBIT_HOST` / `QBIT_PORT`
- ou `TRASM_HOST` / `TRASM_PORT`
- ou `RTORR_HOST` / `RTORR_PORT`

### 4. Lancer le watcher

```bash
docker compose up -d
```

Le service démarre avec la commande `-watcher`.

> **Note importante :**
> Le mode `-watcher` utilisé par le compose par défaut suppose qu'un client torrent configuré soit accessible si `-noseed` n'est pas utilisé.
> Si `qBittorrent`, `Transmission` ou `rTorrent` n'est pas encore joignable depuis le conteneur, le service peut redémarrer en boucle au lancement.
>
> Pour un premier test Docker sans client torrent, tu peux par exemple lancer :
>
> ```bash
> docker compose run --rm unit3dup -scan /watch -noseed -noup
> ```
>
> Ou modifier temporairement la commande du service en :
>
> ```yaml
> command: ["-watcher", "-noseed"]
> ```

### 5. Lancer un upload manuel

Pour envoyer un fichier déjà présent dans le volume `/data` :

```bash
docker compose run --rm unit3dup -u /data/mon_fichier.mkv
```

Pour scanner un dossier :

```bash
docker compose run --rm unit3dup -scan /data/mon_dossier
```

### Permissions NAS

Le compose utilise :

```yaml
user: "${PUID:-1000}:${PGID:-1000}"
```

Adapte `PUID` et `PGID` à l'utilisateur de ton NAS si besoin pour éviter les problèmes d'accès sur les partages.
