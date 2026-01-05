import logging
import os
import re
import threading
import time
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="[%(asctime)s] %(levelname)s %(message)s")
log = logging.getLogger("worldboss")

COOKIE = os.getenv("COOKIE", "").strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA}
if COOKIE:
    HEADERS["Cookie"] = COOKIE

DUMP_HTML_ON_FAILURE = os.getenv("DUMP_HTML_ON_FAILURE", "0") not in {"0", "false", "False", ""}
HTML_SNAPSHOT_PATH = os.getenv("HTML_SNAPSHOT_PATH", "/tmp/world_bosses.html")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
TELEGRAM_TEST_PING = os.getenv("TELEGRAM_TEST_PING", "0") not in {"0", "false", "False", ""}

EXPEDITION_URL = "https://web.simple-mmo.com/quests"

app = FastAPI()

def dump_html_snapshot(content: str, reason: str) -> None:
    if not DUMP_HTML_ON_FAILURE:
        return
    try:
        with open(HTML_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        log.info("Dump HTML -> %s (len=%s) reason=%s", HTML_SNAPSHOT_PATH, len(content), reason)
    except Exception:
        log.exception("Impossible d'écrire le snapshot HTML")

def absolutize(src: str | None) -> str | None:
    """Convertit un chemin relatif en URL absolue pour afficher correctement les icônes."""
    if not src:
        return None
    if src.startswith("http://") or src.startswith("https://"):
        return src
    return "https://web.simple-mmo.com" + src


def build_playwright_cookies(cookie_header: str) -> list[dict]:
    """Convertit un header Cookie en liste de cookies Playwright."""
    cookies = []
    if not cookie_header:
        return cookies
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if not name or not value:
            continue
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": "web.simple-mmo.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
        })
    return cookies


def extract_boss_id(link: str | None) -> str | None:
    """Extrait l'ID numérique du boss depuis une URL /worldboss/view/<id>."""
    if not link:
        return None
    m = re.search(r"worldboss/view/(\d+)", link)
    return m.group(1) if m else None


def fetch_boss_details(boss_id: str | None) -> dict:
    """Récupère les stats du boss (HP, STR, DEX, DEF) via la page dédiée."""
    if not boss_id:
        return {}
    url = f"https://web.simple-mmo.com/worldboss/view/{boss_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            log.debug("Boss %s detail HTTP %s", boss_id, r.status_code)
            return {}
        soup = BeautifulSoup(r.text, "lxml")

        def clean_num(val: str | None) -> int | None:
            if not val:
                return None
            digits = re.sub(r"[^0-9]", "", val)
            return int(digits) if digits else None

        # Try structured scrape first: dt/dd pairs in the stats grid
        stats = {}
        for dt in soup.select("dl dt"):
            label = dt.get_text(strip=True).lower()
            dd = dt.find_next_sibling("dd")
            num = clean_num(dd.get_text(strip=True) if dd else None)
            if label.startswith("health") or label.startswith("vie"):
                stats.setdefault("hp", num)
            elif label.startswith("strength") or label.startswith("force"):
                stats.setdefault("strength", num)
            elif label.startswith("dexterity") or label.startswith("dexter"):
                stats.setdefault("dexterity", num)
            elif label.startswith("defence") or label.startswith("defense"):
                stats.setdefault("defence", num)

        # Fallback: regex on page text
        text = soup.get_text(" ", strip=True)

        def grab(label: str) -> int | None:
            m = re.search(rf"{label}\s*:?\s*([0-9][0-9\s\u00A0'\.,]*)", text, re.IGNORECASE)
            return clean_num(m.group(1) if m else None)

        stats.setdefault("hp", grab("Health|Vie|HP"))
        stats.setdefault("strength", grab("Strength|Force|STR"))
        stats.setdefault("dexterity", grab("Dexterity|Dexterité|Dexterite|DEX"))
        stats.setdefault("defence", grab("Defence|Defense|DEF"))

        return stats
    except Exception:
        log.debug("Echec fetch boss detail %s", boss_id, exc_info=True)
        return {}


def scrape_bosses():
    url = "https://web.simple-mmo.com/battle/world-bosses"
    r = requests.get(url, headers=HEADERS, timeout=10)

    if r.status_code != 200:
        log.error("HTTP %s sur %s", r.status_code, url)
        dump_html_snapshot(r.text, "http-status-" + str(r.status_code))
        return []

    log.debug("GET %s -> %s, taille=%s", url, r.status_code, len(r.text))

    soup = BeautifulSoup(r.text, "lxml")

    # Debug: vérifier si on est tombé sur une page de protection/login
    page_title = (soup.title.string or "").strip() if soup.title else ""
    if page_title:
        log.debug("page title: %s", page_title)
    if "Just a moment" in page_title or "Cloudflare" in page_title or "login" in page_title.lower():
        log.warning("La page semble être protégée (title=%s). Un cookie/session ou un autre UA peut être nécessaire.", page_title)

    bosses = []

    # Prochain boss : carte avec bordure indigo (pointer-events-auto)
    next_card = (
        soup.select_one("div.pointer-events-auto div.border-indigo-400")
        or soup.select_one("div.w-full.bg-white.border-2.border-indigo-400")
        or soup.select_one("div.w-full.bg-white.border-2")
        or soup.select_one("div.w-full.bg-white")
    )

    if next_card:
        next_name_el = next_card.select_one("p.text-xs.sm\\:text-sm.font-medium.text-gray-900")
        next_level_el = next_card.select_one("p.text-xs.sm\\:text-sm.text-gray-500")
        next_time_el = next_card.select_one("p.text-xs.sm\\:text-sm.text-gray-400")
        next_icon_el = next_card.select_one("img")
        next_link_el = next_card.select_one("a[href*='worldboss/view']")

        next_name = next_name_el.get_text(strip=True) if next_name_el else None
        next_level = next_level_el.get_text(strip=True) if next_level_el else None
        next_time = next_time_el.get_text(strip=True) if next_time_el else None
        next_icon = absolutize(next_icon_el["src"] if next_icon_el and next_icon_el.has_attr("src") else None)
    else:
        next_name = None
        next_level = None
        next_time = None
        next_icon = None

    log.debug(
        "next card found=%s name=%s level=%s time=%s icon=%s",
        bool(next_card),
        next_name,
        next_level,
        next_time,
        bool(next_icon),
    )

    next_id = extract_boss_id(next_link_el["href"] if next_link_el and next_link_el.has_attr("href") else None)
    next_stats = fetch_boss_details(next_id)
    next_eta_seconds = parse_eta_seconds(next_time)
    spawn_at = None
    if next_eta_seconds is not None:
        spawn_at = time.strftime("%H:%M:%S", time.localtime(time.time() + next_eta_seconds + 3600))

    bosses.append({
        "type": "next",
        "id": next_id,
        "name": next_name or "Inconnu",
        "level": next_level or "?",
        "time": next_time or "Actif",
        "spawn_at": spawn_at,
        "icon": next_icon,
        **next_stats,
    })

    # Autres bosses (6 entrées)
    other_rows = soup.select("div.divide-y div.flex.justify-between")
    other_names = []
    other_levels = []
    other_times = []
    other_icons = []
    other_details = []

    for row in other_rows:
        name_el = row.select_one("div.font-bold")
        lvl_el = row.select_one("div.text-gray-600.font-normal")
        time_el = row.select_one("div.text-xs.sm\\:text-sm.text-gray-500.font-normal")
        img_el = row.select_one("img")
        onclick = row.get("onclick", "")
        href_match = re.search(r"/worldboss/view/\d+", onclick)
        boss_link = href_match.group(0) if href_match else None
        other_id = extract_boss_id(boss_link)
        details = fetch_boss_details(other_id)
        eta_label = time_el.get_text(strip=True) if time_el else None
        eta_seconds = parse_eta_seconds(eta_label)
        spawn_at = None
        if eta_seconds is not None:
            spawn_at = time.strftime("%H:%M:%S", time.localtime(time.time() + eta_seconds + 3600))

        other_names.append(name_el.get_text(strip=True) if name_el else None)
        other_levels.append(lvl_el.get_text(strip=True) if lvl_el else None)
        other_times.append(eta_label)
        other_icons.append(absolutize(img_el["src"] if img_el and img_el.has_attr("src") else None))
        other_details.append({
            "id": other_id,
            "spawn_at": spawn_at,
            **details,
        })

    log.debug(
        "other rows=%s names=%s levels=%s times=%s icons=%s",
        len(other_rows),
        len([n for n in other_names if n]),
        len([l for l in other_levels if l]),
        len([t for t in other_times if t]),
        len([i for i in other_icons if i]),
    )

    count = min(len(other_rows), len(other_names), len(other_levels), len(other_times), len(other_icons), 6)

    if count == 0:
        log.warning(
            "Aucun boss 'other' trouvé (names=%s levels=%s times=%s)",
            len(other_names),
            len(other_levels),
            len(other_times),
        )
        dump_html_snapshot(r.text, "missing-other-nodes")

    for i in range(count):
        stats_payload = other_details[i] if i < len(other_details) else {}
        bosses.append({
            "type": "other",
            "name": other_names[i] or "Inconnu",
            "level": other_levels[i] or "?",
            "time": other_times[i] or "Actif",
            "icon": other_icons[i] if i < len(other_icons) else None,
            **stats_payload,
        })

    return bosses

boss_state = {
    "bosses": [],
    "last_update": None
}

notify_state = {
    "key": None,
    "sent": set(),
}

test_ping_state = {
    "last_min": None,
}

expedition_state = {
    "active": False,
    "thread": None,
    "last_error": None,
    "last_click": None,
}
expedition_lock = threading.Lock()

fetch_thread = None


def parse_eta_seconds(label: str | None) -> int | None:
    if not label:
        return None
    lower = label.strip().lower()
    norm = re.sub(r"\s+", " ", lower)
    if "actif" in lower or "active" in lower:
        return 0

    dhm = re.match(
        r"^(?:(\d+)\s*days?,\s*)?(?:(\d+)\s*hours?,\s*)?(?:(\d+)\s*mins?(?:ute)?s?)?$",
        norm,
    )
    if dhm:
        days = int(dhm.group(1) or 0)
        hours = int(dhm.group(2) or 0)
        minutes = int(dhm.group(3) or 0)
        total = days * 86400 + hours * 3600 + minutes * 60
        return total

    hms = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", lower)
    if hms:
        h, m, s = map(int, hms.groups())
        return h * 3600 + m * 60 + s

    ms = re.match(r"^(\d{1,2}):(\d{2})$", lower)
    if ms:
        m, s = map(int, ms.groups())
        return m * 60 + s

    num_min = re.match(r"^(\d+)\s*(minutes?|mins?|m)$", lower)
    if num_min:
        return int(num_min.group(1)) * 60

    num_h_m = re.match(r"^(\d+)\s*heures?\s*(\d+)?", lower)
    if num_h_m:
        hours = int(num_h_m.group(1))
        minutes = int(num_h_m.group(2) or 0)
        return hours * 3600 + minutes * 60

    return None


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_ENABLED:
        log.debug("Telegram désactivé (token ou chat_id manquant)")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=5,
        )
        if resp.status_code != 200:
            log.warning("Telegram HTTP %s: %s", resp.status_code, resp.text[:200])
        else:
            log.info("Notification Telegram envoyée: %s", text)
    except Exception:
        log.exception("Echec envoi Telegram")


def format_alert_message(boss: dict, label: str) -> str:
    name = boss.get("name") or "?"
    level = boss.get("level") or "?"
    eta = boss.get("time") or "?"
    return f"⚔️ Boss: {name}\n🏷️ Niveau: {level}\n⏳ Statut: {label}\n🕒 ETA: {eta}"


def expedition_loop():
    """Boucle Playwright : clique sur l'expédition initiale puis toutes les 30s."""
    while expedition_state["active"]:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                cookies = build_playwright_cookies(COOKIE)
                if cookies:
                    context.add_cookies(cookies)
                page = context.new_page()
                page.goto(EXPEDITION_URL, wait_until="networkidle")

                first_btn_selector = "button[x-on\\:click*=\"set-expedition-data\"]"
                first_btn = page.wait_for_selector(first_btn_selector, state="visible", timeout=15000)
                first_btn.click()
                log.info("Expédition : premier bouton cliqué")

                while expedition_state["active"]:
                    second_btn_selector = "button[x-on\\:click*=\"performExpedition\"]"
                    second_btn = page.wait_for_selector(second_btn_selector, state="visible", timeout=15000)
                    try:
                        second_btn.wait_for_element_state("enabled", timeout=5000)
                    except PlaywrightTimeoutError:
                        pass

                    try:
                        second_btn.click()
                        expedition_state["last_click"] = time.strftime("%H:%M:%S", time.localtime(time.time() + 3600))
                        log.info("Expédition : clic performExpedition")
                    except Exception as click_err:
                        log.warning("Expédition : clic impossible (%s)", click_err)

                    page.wait_for_timeout(299000)

        except Exception as exc:
            expedition_state["last_error"] = str(exc)
            log.exception("Boucle expédition en erreur, retry dans 5s")
            time.sleep(5)
        finally:
            try:
                if 'context' in locals():
                    context.close()
                if 'browser' in locals():
                    browser.close()
            except Exception:
                log.debug("Cleanup Playwright échoué", exc_info=True)


def start_expedition() -> bool:
    with expedition_lock:
        if expedition_state["active"]:
            return False
        expedition_state["active"] = True
        expedition_state["last_error"] = None
        t = threading.Thread(target=expedition_loop, daemon=True)
        expedition_state["thread"] = t
        t.start()
        log.info("Expédition activée")
        return True


def stop_expedition() -> bool:
    with expedition_lock:
        if not expedition_state["active"]:
            return False
        expedition_state["active"] = False
        log.info("Expédition désactivée")
        return True


def expedition_status() -> dict:
    return {
        "active": expedition_state["active"],
        "last_click": expedition_state["last_click"],
        "last_error": expedition_state["last_error"],
    }

def fetch_boss_loop():
    while True:
        try:
            bosses = scrape_bosses()
            boss_state["bosses"] = bosses
            boss_state["last_update"] = time.strftime("%H:%M:%S", time.localtime(time.time() + 3600))
            log.info("Bosses mis à jour (%s)", len(bosses))

            if TELEGRAM_ENABLED and TELEGRAM_TEST_PING:
                now_min = int(time.time() // 60)
                if test_ping_state["last_min"] != now_min:
                    send_telegram_message(f"[TEST] Ping {time.strftime('%H:%M:%S')}")
                    test_ping_state["last_min"] = now_min

            next_boss = bosses[0] if bosses else None
            if next_boss:
                eta_seconds = parse_eta_seconds(next_boss.get("time"))
                key = f"{next_boss.get('name')}-{next_boss.get('level')}"

                log.debug("Prochain boss=%s level=%s eta_label=%s eta_seconds=%s telegram=%s", next_boss.get("name"), next_boss.get("level"), next_boss.get("time"), eta_seconds, TELEGRAM_ENABLED)

                if notify_state["key"] != key:
                    notify_state["key"] = key
                    notify_state["sent"] = set()

                if eta_seconds is not None:
                    checkpoints = [
                        (3600, "1 heure"),
                        (900, "15 minutes"),
                        (120, "2 minutes"),
                        (0, "Actif"),
                    ]
                    for threshold, label in checkpoints:
                        if eta_seconds <= threshold and threshold not in notify_state["sent"]:
                            send_telegram_message(format_alert_message(next_boss, label))
                            notify_state["sent"].add(threshold)
                else:
                    log.debug("ETA non parsé, pas de notif Telegram")
        except Exception as e:
            log.exception("Erreur scraping")

        time.sleep(30)


@app.on_event("startup")
def start_background_fetch():
    """Démarre la boucle de scrap en arrière-plan au lancement du serveur."""
    global fetch_thread
    if fetch_thread is None or not fetch_thread.is_alive():
        fetch_thread = threading.Thread(target=fetch_boss_loop, daemon=True)
        fetch_thread.start()
        log.info("Thread fetch_boss_loop démarré")


@app.post("/scraping/start")
def scraping_start():
    started = start_expedition()
    return {"started": started, **expedition_status()}


@app.post("/scraping/stop")
def scraping_stop():
    stopped = stop_expedition()
    return {"stopped": stopped, **expedition_status()}


@app.get("/scraping/status")
def scraping_status():
    return expedition_status()

@app.get("/", response_class=HTMLResponse)
def homepage():
    if not boss_state["bosses"]:
        return "<h1>Chargement des boss…</h1>"

    def img_tag(icon: str | None) -> str:
        src = icon or "https://web.simple-mmo.com/img/sprites/3.png"
        return f"<div class='avatar'><img src='{src}' alt='icon'></div>"

    def fmt_num(val) -> str:
        return f"{val:,}".replace(",", " ") if isinstance(val, int) else (val or "?")

    next_boss = boss_state["bosses"][0]
    other_bosses = boss_state["bosses"][1:]

    def stats_html(b: dict) -> str:
        hp = fmt_num(b.get("hp"))
        st = fmt_num(b.get("strength"))
        dx = fmt_num(b.get("dexterity"))
        df = fmt_num(b.get("defence"))
        return f"HP {hp} · STR {st} · DEX {dx} · DEF {df}"

    other_cards_html = "".join(
        f"""
                <div class='card'>
                    {img_tag(b.get('icon'))}
                    <div>
                        <div class='name' style='font-size:16px'>{b.get('name') or 'Inconnu'}</div>
                        <div class='meta'>
                            <span class='pill'>Niveau {b.get('level') or '?'}</span>
                            <span class='time'>ETA {b.get('time') or 'Actif'}</span>
                        </div>
                        <div class='stats'>{stats_html(b)}</div>
                        <div class='spawn'>Spawn prévu : {b.get('spawn_at') or '?'} (ETA)</div>
                    </div>
                </div>
        """
        for b in other_bosses
    )

    style = """
            html, body { min-height: 100vh; height: 100%; overflow: hidden; }
            :root {
                --bg: #0f172a;
                --card: #111827;
                --card-2: #0b1224;
                --text: #e5e7eb;
                --muted: #9ca3af;
                --accent: #7c3aed;
                --accent-2: #22d3ee;
            }
            * { box-sizing: border-box; }
            body { margin:0; font-family: 'Segoe UI', sans-serif; background: radial-gradient(circle at 20% 20%, #111827 0, #0b1224 35%, #0f172a 100%); color: var(--text); min-height: 100vh; height: 100%; overflow: hidden; }
            .page { max-width: 1080px; margin: 0 auto; padding: 24px 16px 32px; min-height: 100vh; box-sizing: border-box; display: flex; flex-direction: column; gap: 16px; }
            h1 { margin: 0 0 16px; font-size: 28px; letter-spacing: 0.3px; }
            h2 { margin: 0 0 12px; font-size: 18px; color: var(--muted); font-weight: 600; }
            .next { border: 1px solid rgba(124,58,237,.4); background: linear-gradient(135deg, rgba(124,58,237,.12), rgba(34,211,238,.08)); border-radius: 14px; padding: 18px; display: grid; grid-template-columns: auto 1fr auto; gap: 14px; align-items: center; box-shadow: 0 10px 30px rgba(0,0,0,.35); }
            .avatar img { width: 72px; height: 72px; object-fit: contain; filter: drop-shadow(0 4px 8px rgba(0,0,0,.45)); }
            .name { font-size: 20px; font-weight: 700; }
            .meta { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 4px; color: var(--muted); font-size: 14px; }
            .pill { padding: 4px 10px; border-radius: 999px; background: rgba(124,58,237,.15); color: #c4b5fd; border: 1px solid rgba(124,58,237,.35); font-size: 13px; font-weight: 600; }
            .time { color: #a5f3fc; font-weight: 600; font-size: 14px; }
            .stats { margin-top: 4px; color: var(--muted); font-size: 13px; }
            .spawn { margin-top: 4px; color: #c7d2fe; font-size: 13px; }
            .grid { margin-top: 18px; display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
            .card { background: var(--card); border: 1px solid rgba(255,255,255,.05); border-radius: 12px; padding: 14px; display: grid; grid-template-columns: auto 1fr; gap: 12px; align-items: center; box-shadow: 0 8px 24px rgba(0,0,0,.28); }
            .card .avatar img { width: 52px; height: 52px; }
            .foot { margin-top: 18px; color: var(--muted); font-size: 13px; text-align: right; }
            .tag { display: inline-block; padding: 3px 8px; border-radius: 8px; background: rgba(34,211,238,.12); color: #67e8f9; border: 1px solid rgba(34,211,238,.35); font-size: 12px; font-weight: 600; }
            .controls { display: flex; gap: 10px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
            .btn { background: var(--accent); color: #fff; border: none; padding: 8px 14px; border-radius: 10px; cursor: pointer; font-weight: 700; box-shadow: 0 6px 18px rgba(124,58,237,.35); transition: transform .08s ease, box-shadow .08s ease; }
            .btn:hover { transform: translateY(-1px); box-shadow: 0 10px 24px rgba(124,58,237,.45); }
            .btn.secondary { background: #1f2937; color: var(--text); border: 1px solid rgba(255,255,255,.12); box-shadow: none; }
            .status { font-size: 13px; color: var(--muted); }
    """

    script = """
        <script>
        async function updateStatus() {
            try {
                const res = await fetch('/scraping/status');
                const data = await res.json();
                const statusEl = document.getElementById('exp-status');
                const active = data.active ? 'Active' : 'Inactive';
                const last = data.last_click ? `Dernier clic : ${data.last_click}` : '';
                const err = data.last_error ? `Erreur : ${data.last_error}` : '';
                statusEl.textContent = `Statut : ${active} ${last} ${err}`.trim();
            } catch (e) {
                document.getElementById('exp-status').textContent = 'Statut : erreur';
            }
        }

        async function callEndpoint(url) {
            await fetch(url, { method: 'POST' });
            await updateStatus();
        }

        document.getElementById('btn-start').addEventListener('click', () => callEndpoint('/scraping/start'));
        document.getElementById('btn-stop').addEventListener('click', () => callEndpoint('/scraping/stop'));

        updateStatus();
        setInterval(updateStatus, 15000);
        </script>
    """

    html = f"""
    <!doctype html>
    <html lang='fr'>
    <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>World Bosses</title>
        <link rel='icon' href='https://web.simple-mmo.com/img/simplemmo-trans.png'>
        <style>
{style}
        </style>
    </head>
    <body>
        <div class='page'>
            <h1>World Bosses</h1>
            <div class='next'>
                {img_tag(next_boss.get('icon'))}
                <div>
                    <div class='name'>{next_boss.get('name') or 'Inconnu'}</div>
                    <div class='meta'>
                        <span class='pill'>Niveau {next_boss.get('level') or '?'}</span>
                        <span class='time'>ETA {next_boss.get('time') or 'Actif'}</span>
                    </div>
                    <div class='stats'>{stats_html(next_boss)}</div>
                    <div class='spawn'>Spawn prévu : {next_boss.get('spawn_at') or 'En cours'} (ETA)</div>
                </div>
                <div><span class='tag'>Prochain</span></div>
            </div>

            <div class='controls'>
                <button class='btn' id='btn-start'>Activer la fonction</button>
                <button class='btn secondary' id='btn-stop'>Désactiver la fonction</button>
                <span class='status' id='exp-status'>Statut : chargement…</span>
            </div>
            <div>
                <span>Ceci est la fonction pour les quêtes automatiques.</span>
            </div>
            <h2>Autres boss</h2>
            <div class='grid'>
                {other_cards_html}
            </div>
            <div class='foot'>Dernière mise à jour (+1h) : {boss_state.get('last_update') or '...'}</div>
        </div>
        {script}
    </body>
    </html>
    """

    return html
