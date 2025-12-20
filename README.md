# simplemmo

FastAPI service that scrapes the SimpleMMO world-boss page, renders a small dashboard, and can send Telegram alerts when a boss is close/active.

## Repo layout
- [main.py](main.py) : scraping, background loop, Telegram alerts, HTML dashboard on `/`.
- [requirements.txt](requirements.txt) : Python deps.
- [Dockerfile](Dockerfile) : image build (uvicorn server).
- [docker-compose.yml](docker-compose.yml) : local orchestration, env handling, healthcheck.
- [.env.example](.env.example) : template to copy to `.env`.
- (Optionally delete or cleanse `lancement-docker.txt` before pushing; it currently contains real-looking tokens.)

## Prerequisites
- Docker + Docker Compose v2.
- A SimpleMMO session cookie for world-boss access (`COOKIE`).
- (Optional) Telegram bot token + chat id for notifications.

## Configuration (.env)
Copy `.env.example` to `.env` and fill:
- `COOKIE` (required): your SimpleMMO session cookie.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (optional): enable Telegram alerts.
- `LOG_LEVEL` (default INFO): logging level.
- `DUMP_HTML_ON_FAILURE` (1/0): save HTML when parsing fails.
- `HTML_SNAPSHOT_PATH`: where to dump snapshots.
- `TELEGRAM_TEST_PING` (1/0): send a test ping every minute (debug).

## Installation
-Récupérez votre cookie de session simplemmo:.
-sur simplemmo en étant connecté sur naviguateur > press f12 > Application > cookies > https://web.simple-mmo.com prenez les token des clé laravelsession et XSRF-TOKEN
-metez le sous la forme COOKIE="laravelsession=<TOKEN>; XSRF-TOKEN=<TOKEN>"
-Ensuite si vous voulez les notifications par telegram chercher le bot "BOTFATHER" sur telegram puis créer votre bot donner lui un nom et un pseudo, envoyé un message a votre bot puis dans votre naviguateur mettez cette url avec le token de votre bot https://api.telegram.org/bot<token_bot>/getUpdates
puis dans le JSON repérer ça 
"chat": {
  "id": 123456789
}
nottez l'ID c'est l'ID du chat.
Remplissez maintenant les 3 variables d'environnement dans [.env.example](.env.example)
```bash
docker compose up -d
# then open http://127.0.0.1:8000/
# logs: docker compose logs -f
```
Modifiez l'ip si vous voulez pouvoir accéder au site depuis un autre naviguateur du réseau
