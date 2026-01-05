# SimpleMMO World Boss Tracker

Service FastAPI qui surveille la page des "World Boss" de SimpleMMO, affiche un tableau de bord et envoie des alertes Telegram lorsqu'un boss est proche ou actif.

## 📂 Structure du projet

- `main.py` : Situé dans l'image docker, logique principale (scraping, tâche de fond, alertes Telegram, dashboard web).
- `requirements.txt` : Dépendances Python.
- `Dockerfile` : Fichier de construction de l'image (serveur uvicorn).
- `docker-compose.yml` : Orchestration locale, gestion des variables d'environnement et healthcheck.
- `.env` : Fichier de configuration à remplir.

## 🛠️ Prérequis

- **Docker** et **Docker Compose** installés.
- Un compte **SimpleMMO** actif.
- (Optionnel) Un bot **Telegram** pour les notifications.

## ⚙️ Configuration

1. Remplissez le fichier `.env` avec les informations suivantes :

| Variable | Description | Requis |
| :--- | :--- | :--- |
| `COOKIE` | Cookie de session SimpleMMO (voir instructions ci-dessous). | **Oui** |
| `TELEGRAM_BOT_TOKEN` | Token de votre bot Telegram. | Non |
| `TELEGRAM_CHAT_ID` | ID du chat pour recevoir les alertes. | Non |
| `LOG_LEVEL` | Niveau de log (défaut : `INFO` possibilité de mettre en `DEBUG`). | Non |
| `DUMP_HTML_ON_FAILURE` | Sauvegarder le HTML en cas d'erreur de parsing (`1` ou `0`). | Non |
| `HTML_SNAPSHOT_PATH` | Chemin de sauvegarde des snapshots HTML. | Non |
| `TELEGRAM_TEST_PING` | Envoi un ping de test chaque minute (`1` ou `0`). | Non |

### 🍪 Récupération du Cookie SimpleMMO
1. Connectez-vous à [SimpleMMO](https://web.simple-mmo.com) sur votre navigateur.
2. Ouvrez les outils de développement (F12) > Onglet **Application** > **Cookies**.
3. Sélectionnez `https://web.simple-mmo.com`.
4. Copiez les valeurs de `laravelsession` et `XSRF-TOKEN`.
5. Formatez la variable `COOKIE` dans votre fichier `.env` comme ceci :
   ```bash
   COOKIE="laravelsession=<VOTRE_TOKEN>; XSRF-TOKEN=<VOTRE_TOKEN>"
   ```

### 🤖 Configuration Telegram (Optionnel)
1. Créez un bot via [@BotFather](https://t.me/BotFather) sur Telegram pour obtenir le `TELEGRAM_BOT_TOKEN`.
2. Envoyez un message "Hello" à votre nouveau bot (pour initialiser la conversation).
3. Récupérez votre `chat_id` en visitant cette URL : `https://api.telegram.org/bot<VOTRE_TOKEN>/getUpdates`
4. Cherchez l'objet `"chat": { "id": 123456789 }` dans la réponse JSON.
5. Renseignez cet ID dans `TELEGRAM_CHAT_ID`.

## 🚀 Installation & Démarrage

Lancez le conteneur avec Docker Compose :

```bash
docker compose up -d
```

- **Accès au Dashboard :** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Voir les logs :** `docker compose logs -f`

> **Note :** Pour modifier le port ou autoriser l'accès depuis le réseau, modifiez le fichier `docker-compose.yml`.
> **Note :** Pour modifier le temps entre les utilisation de points de quête modifier le temps a la ligne 402 "page.wait_for_timeout(299000)" mettez le temps en ms.
