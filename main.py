#!/usr/bin/env python3
"""
ربات بله فرمول ۱ — نمونهٔ آزمایشی (Demo) با داده‌ی عمومی

منبع داده: Jolpica F1 API (سازگار با Ergast) — https://api.jolpi.ca/ergast/f1
(قبل از استفادهٔ تجاری، شرایط استفادهٔ آن API را بخوانید.)

اجرا:  pip install requests  &&  python bale_f1_bot.py
توکن از فایل .env خوانده می‌شود (BOT_TOKEN=...).

امکانات: خانه (مسابقهٔ بعدی/آخرین)، تقویم فصل، برنامهٔ جلسات به وقت تهران،
نتیجهٔ مسابقه و کوالیفای، رده‌بندی رانندگان و تیم‌ها، کارت راننده، جستجو،
دنبال کردن راننده، اعلان خودکار نتیجهٔ مسابقه، تنظیمات اعلان.
"""
import datetime as dt
import hashlib
import json
import logging
import logging.handlers
import os
import re
import socket
import sqlite3
import time

import requests


# =============================== config ================================
def load_env() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, ".env"), os.path.join(os.getcwd(), ".env")):
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:]
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

VERSION = "f1-2026-11-23.1"
BALE_TOKEN = next(
    (os.environ[n] for n in ("BALE_TOKEN", "BOT_TOKEN", "BALE_BOT_TOKEN", "TOKEN") if os.environ.get(n)), ""
)
API = f"https://tapi.bale.ai/bot{BALE_TOKEN}"
F1_API = os.getenv("F1_API_BASE", "https://api.jolpi.ca/ergast/f1").rstrip("/")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "900"))
DB_PATH = os.getenv("DB_PATH", "f1bot.db")
LOG_FILE = os.getenv("LOG_FILE", "f1bot.log")
ADMIN_IDS = {int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "")) if x.strip().lstrip("-").isdigit()}
# TESTING ONLY: NOTIFY_ON_FIRST_SCAN=1 makes the first scan send an alert instead of staying silent
NOTIFY_FIRST_SCAN = os.getenv("NOTIFY_ON_FIRST_SCAN", "") == "1"

log = logging.getLogger("f1-bot")
STATS = {"started": time.time(), "scans": 0, "scan_errors": 0, "last_scan": 0.0, "sent": 0, "api_calls": 0}
_LOCK_SOCKET = None


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if LOG_FILE:
        fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def acquire_single_instance_lock() -> None:
    """Refuse to start when another copy with the same token already runs on this machine."""
    global _LOCK_SOCKET
    port = 20000 + int(hashlib.sha1(BALE_TOKEN.encode()).hexdigest()[:6], 16) % 40000
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit("ERROR: another instance of this bot is already running on this machine. Stop it first.")
    _LOCK_SOCKET = sock


# ====================== persian / date helpers =========================
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]  # index = date.weekday()
JMONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))  # Iran has no DST since 2022
RLM = "\u200f"
SEP = f"{RLM} · {RLM}"
MEDALS = ["🥇", "🥈", "🥉"]


def fa(value) -> str:
    return str(value).translate(_FA)


def g2j(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def jdate(d: dt.date | None) -> str:
    if not d:
        return "—"
    _, jm, jd = g2j(d.year, d.month, d.day)
    return f"{WEEKDAYS[d.weekday()]} {fa(jd)} {JMONTHS[jm - 1]}"


def parse_date(s) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def parse_dt(date, time_=None) -> dt.datetime | None:
    """API date + 'HH:MM:SSZ' -> aware UTC datetime (None if the date is missing)."""
    if not date:
        return None
    t = (time_ or "").rstrip("Z") or "12:00:00"
    try:
        return dt.datetime.fromisoformat(f"{date}T{t}").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def when(date, time_=None) -> str:
    """Persian weekday/date, plus Tehran clock time when the API gives a time."""
    d = parse_dt(date, time_)
    if d is None:
        return "—"
    if not time_:
        return jdate(parse_date(date))
    t = d.astimezone(TEHRAN)
    return f"{jdate(t.date())}{SEP}{fa(t.strftime('%H:%M'))}"


def norm(s: str) -> str:
    s = (s or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", "").replace(" ", "")
    return s.lower()


def menu_key(s: str) -> str:
    """Letters only: Bale may strip the emoji from a pressed button's text."""
    return re.sub(r"[^\w]", "", norm(s))


def rtl(lines: list) -> str:
    out: list[str] = []
    for ln in lines:
        if ln.strip():
            out.append(RLM + ln)
        elif out and out[-1] != "":
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)[:4000]


# ------------- Persian names (fallback = English from the API) -------------
GP_FA = {
    "Bahrain": "بحرین", "Saudi Arabian": "عربستان", "Australian": "استرالیا", "Japanese": "ژاپن",
    "Chinese": "چین", "Miami": "میامی", "Emilia Romagna": "امیلیا رومانیا", "Monaco": "موناکو",
    "Canadian": "کانادا", "Spanish": "اسپانیا", "Austrian": "اتریش", "British": "بریتانیا",
    "Hungarian": "مجارستان", "Belgian": "بلژیک", "Dutch": "هلند", "Italian": "ایتالیا",
    "Azerbaijan": "آذربایجان", "Singapore": "سنگاپور", "United States": "ایالات متحده",
    "Mexico City": "مکزیکو سیتی", "Mexican": "مکزیک", "São Paulo": "سائوپائولو", "Sao Paulo": "سائوپائولو",
    "Brazilian": "برزیل", "Las Vegas": "لاس‌وگاس", "Qatar": "قطر", "Abu Dhabi": "ابوظبی", "Madrid": "مادرید",
    "Turkish": "ترکیه", "Portuguese": "پرتغال", "French": "فرانسه", "German": "آلمان", "Russian": "روسیه",
    "Styrian": "اشتایر", "70th Anniversary": "هفتادمین سالگرد", "Eifel": "آیفل", "Tuscan": "توسکانا",
}
COUNTRY_FA = {
    "Bahrain": "بحرین", "Saudi Arabia": "عربستان", "Australia": "استرالیا", "Japan": "ژاپن", "China": "چین",
    "USA": "آمریکا", "United States": "آمریکا", "Italy": "ایتالیا", "Monaco": "موناکو", "Canada": "کانادا",
    "Spain": "اسپانیا", "Austria": "اتریش", "UK": "بریتانیا", "Hungary": "مجارستان", "Belgium": "بلژیک",
    "Netherlands": "هلند", "Azerbaijan": "آذربایجان", "Singapore": "سنگاپور", "Mexico": "مکزیک",
    "Brazil": "برزیل", "Qatar": "قطر", "UAE": "امارات", "Turkey": "ترکیه", "Portugal": "پرتغال",
    "France": "فرانسه", "Germany": "آلمان", "Russia": "روسیه",
}
NATION_FA = {
    "Dutch": "هلندی", "British": "بریتانیایی", "Monegasque": "موناکویی", "Spanish": "اسپانیایی",
    "Australian": "استرالیایی", "Mexican": "مکزیکی", "German": "آلمانی", "French": "فرانسوی",
    "Finnish": "فنلاندی", "Thai": "تایلندی", "Japanese": "ژاپنی", "Canadian": "کانادایی",
    "Danish": "دانمارکی", "Chinese": "چینی", "Italian": "ایتالیایی", "Brazilian": "برزیلی",
    "Argentine": "آرژانتینی", "New Zealander": "نیوزیلندی", "American": "آمریکایی", "Swiss": "سوئیسی",
}
STATUS_FA = {
    "Finished": "پایان", "Retired": "انصراف", "Disqualified": "رد صلاحیت", "Accident": "تصادف",
    "Collision": "برخورد", "Engine": "موتور", "Gearbox": "گیربکس", "Did not start": "شروع نکرد",
    "Withdrew": "انصراف", "Brakes": "ترمز", "Hydraulics": "هیدرولیک", "Suspension": "تعلیق",
    "Power Unit": "پاور یونیت", "Mechanical": "مشکل فنی", "Puncture": "پنچری", "Overheating": "داغ کردن",
}


def gp_base(race: dict) -> str:
    name = race.get("raceName", "")
    return name[: -len(" Grand Prix")] if name.endswith(" Grand Prix") else name


def gp_short(race: dict) -> str:
    return GP_FA.get(gp_base(race), gp_base(race))


def gp_title(race: dict) -> str:
    return f"گرندپری {gp_short(race)}"


def status_fa(status: str) -> str:
    m = re.match(r"^\+(\d+) Laps?$", status or "")
    if m:
        return f"+{fa(m.group(1))} دور"
    return STATUS_FA.get(status, status or "—")


def dname(driver: dict) -> str:
    return f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip()


# ============================== API client =============================
session = requests.Session()
session.headers.update({"User-Agent": "bale-f1-demo-bot/1.0 (portfolio demo)"})
_cache: dict[str, tuple[float, dict]] = {}
_last_request = 0.0


def jget(path: str, ttl: int = 600, force: bool = False) -> dict:
    """GET an Ergast-style endpoint -> MRData dict. Cached; polite (<=3 req/s); serves stale data on errors."""
    global _last_request
    url = f"{F1_API}/{path.lstrip('/')}"
    hit = _cache.get(url)
    if hit and not force and time.time() - hit[0] < ttl:
        return hit[1]
    last_err = None
    for attempt in range(1, 4):
        wait = 0.35 - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        STATS["api_calls"] += 1
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 429:  # rate limited -> back off
                last_err = RuntimeError("HTTP 429 (rate limited)")
                time.sleep(2 * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()["MRData"]
            _cache[url] = (time.time(), data)
            return data
        except (requests.RequestException, ValueError, KeyError) as err:
            last_err = err
            log.warning("API call failed (%s/3) %s: %s", attempt, path, err)
            time.sleep(attempt)
    if hit:
        log.warning("serving stale data for %s", path)
        return hit[1]
    raise RuntimeError(f"could not load {path}: {last_err}")


def races(season="current", force=False) -> list[dict]:
    return jget(f"{season}.json?limit=100", ttl=6 * 3600, force=force)["RaceTable"]["Races"]


def current_season() -> str:
    rs = races("current")
    return rs[0]["season"] if rs else str(dt.date.today().year)


def get_race(season, rnd: int) -> dict | None:
    return next((r for r in races(season) if int(r["round"]) == rnd), None)


def race_results(season, rnd: int, force=False) -> list[dict]:
    rs = jget(f"{season}/{rnd}/results.json?limit=100", ttl=600, force=force)["RaceTable"]["Races"]
    return rs[0].get("Results", []) if rs else []


def race_quali(season, rnd: int) -> list[dict]:
    rs = jget(f"{season}/{rnd}/qualifying.json?limit=100", ttl=900)["RaceTable"]["Races"]
    return rs[0].get("QualifyingResults", []) if rs else []


def driver_standings(season, force=False) -> tuple[str, list[dict]]:
    lists = jget(f"{season}/driverstandings.json?limit=100", ttl=600, force=force)["StandingsTable"]["StandingsLists"]
    return (lists[0].get("round", ""), lists[0].get("DriverStandings", [])) if lists else ("", [])


def constructor_standings(season) -> tuple[str, list[dict]]:
    lists = jget(f"{season}/constructorstandings.json?limit=100", ttl=600)["StandingsTable"]["StandingsLists"]
    return (lists[0].get("round", ""), lists[0].get("ConstructorStandings", [])) if lists else ("", [])


def driver_season_results(season, driver_id: str) -> list[dict]:
    return jget(f"{season}/drivers/{driver_id}/results.json?limit=100", ttl=900)["RaceTable"]["Races"]


def race_done(race: dict) -> bool:
    d = parse_dt(race.get("date"), race.get("time"))
    return bool(d) and now_utc() > d + dt.timedelta(hours=3)


def next_race(season) -> dict | None:
    return next((r for r in races(season) if not race_done(r)), None)


def last_done_race(season) -> dict | None:
    done = [r for r in races(season) if race_done(r)]
    return done[-1] if done else None


# ============================== storage ================================
db = sqlite3.connect(DB_PATH)
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'followed', created REAL);
CREATE TABLE IF NOT EXISTS follows (chat_id INTEGER, driver_id TEXT, driver_name TEXT, PRIMARY KEY (chat_id, driver_id));
CREATE TABLE IF NOT EXISTS race_state (key TEXT PRIMARY KEY, state TEXT);
"""
)
db.commit()


def touch_user(chat_id: int) -> None:
    db.execute("INSERT OR IGNORE INTO users (chat_id, mode, created) VALUES (?, 'followed', ?)", (chat_id, time.time()))
    db.commit()


def remove_user(chat_id: int) -> None:
    db.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
    db.execute("DELETE FROM follows WHERE chat_id = ?", (chat_id,))
    db.commit()


def get_mode(chat_id: int) -> str:
    row = db.execute("SELECT mode FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
    return row[0] if row else "followed"


def set_mode(chat_id: int, mode: str) -> None:
    touch_user(chat_id)
    db.execute("UPDATE users SET mode = ? WHERE chat_id = ?", (mode, chat_id))
    db.commit()


def is_following(chat_id: int, driver_id: str) -> bool:
    return db.execute("SELECT 1 FROM follows WHERE chat_id=? AND driver_id=?", (chat_id, driver_id)).fetchone() is not None


def toggle_follow(chat_id: int, driver_id: str, name: str) -> bool:
    if is_following(chat_id, driver_id):
        db.execute("DELETE FROM follows WHERE chat_id=? AND driver_id=?", (chat_id, driver_id))
        db.commit()
        return False
    db.execute("INSERT OR IGNORE INTO follows VALUES (?,?,?)", (chat_id, driver_id, name))
    db.commit()
    return True


# ============================== Bale API ===============================
_ANSWER_CB_OK = True


def api(method: str, **params):
    resp = requests.post(f"{API}/{method}", json=params, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Bale API error in {method}: {data}")
    return data["result"]


def send(chat_id: int, text: str, markup: dict | None = None) -> bool:
    params = {"chat_id": chat_id, "text": text[:4000]}
    if markup:
        params["reply_markup"] = markup
    try:
        api("sendMessage", **params)
        STATS["sent"] += 1
        return True
    except requests.HTTPError as err:
        code = err.response.status_code if err.response is not None else None
        log.warning("send to %s failed (HTTP %s)", chat_id, code)
        if code == 403:
            remove_user(chat_id)
    except Exception:
        log.exception("send to %s failed", chat_id)
    return False


def edit_or_send(chat_id: int, message_id: int, text: str, markup: dict | None) -> None:
    params = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000]}
    if markup:
        params["reply_markup"] = markup
    try:
        api("editMessageText", **params)
    except Exception:
        log.warning("edit failed, sending a new message instead")
        send(chat_id, text, markup)


# ============================== keyboards ==============================
def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def chunk(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


BTN_HOME, BTN_STAND, BTN_MY, BTN_SEARCH, BTN_SET = "🏁 گرندپری‌ها", "🏆 رده‌بندی", "⭐ رانندگان من", "🔍 جستجو", "⚙️ تنظیمات"
MENU_CMD = {
    menu_key(BTN_HOME): "/start_home", menu_key(BTN_STAND): "/standings", menu_key(BTN_MY): "/my",
    menu_key(BTN_SEARCH): "/search", menu_key(BTN_SET): "/settings",
}
MAIN_KB = {
    "keyboard": [[{"text": BTN_HOME}, {"text": BTN_STAND}], [{"text": BTN_MY}, {"text": BTN_SEARCH}], [{"text": BTN_SET}]],
    "resize_keyboard": True,
}


# ================================ views ================================
SESSIONS = [
    ("FirstPractice", "تمرین ۱"), ("SecondPractice", "تمرین ۲"), ("ThirdPractice", "تمرین ۳"),
    ("SprintQualifying", "کوالیفای اسپرینت"), ("SprintShootout", "کوالیفای اسپرینت"),
    ("Sprint", "اسپرینت"), ("Qualifying", "کوالیفای"),
]


def view_home(force: bool = False):
    season = current_season()
    L = [f"🏎 فرمول ۱{SEP}فصل {fa(season)}", ""]
    try:
        nxt = next_race(season)
        if nxt:
            L += ["🔜 مسابقهٔ بعدی", f"{gp_title(nxt)}{SEP}{when(nxt.get('date'), nxt.get('time'))}", ""]
    except Exception:
        log.warning("home: next race unavailable")
    try:
        last = last_done_race(season)
        if last:
            res = race_results(season, int(last["round"]), force)
            win = dname(res[0]["Driver"]) if res else None
            L += ["🏁 آخرین مسابقه", gp_title(last) + (f"{SEP}🥇 {win}" if win else ""), ""]
    except Exception:
        log.warning("home: last race unavailable")
    L.append("منبع داده: Jolpica F1 API (نمونهٔ آزمایشی)")
    kb = [
        [btn("🔜 مسابقهٔ بعدی", "next"), btn("🏁 آخرین نتیجه", "last")],
        [btn("📅 تقویم فصل", f"cal:{season}:0")],
        [btn("🏆 رانندگان", "st:d"), btn("🏆 تیم‌ها", "st:c")],
        [btn("🔄 بروزرسانی", "home!")],
    ]
    return rtl(L), {"inline_keyboard": kb}


PAGE = 8


def view_calendar(season: str, page: int):
    rs = races(season)
    pages = max(1, (len(rs) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    part = rs[page * PAGE : (page + 1) * PAGE]
    L = [f"📅 تقویم فصل {fa(season)}", f"صفحهٔ {fa(page + 1)} از {fa(pages)}", ""]
    for r in part:
        L.append(f"{fa(r['round'])}. {gp_title(r)}" + ("  ✅" if race_done(r) else ""))
        L.append(when(r.get("date")))
    kb = chunk([btn(f"{fa(r['round'])} · {gp_short(r)}", f"g:{season}:{r['round']}") for r in part], 2)
    nav = []
    if page > 0:
        nav.append(btn("➡️ قبلی", f"cal:{season}:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("بعدی ⬅️", f"cal:{season}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([btn("🏠 خانه", "home")])
    return rtl(L), {"inline_keyboard": kb}


def session_list(race: dict) -> list[tuple[dt.datetime, str, dict]]:
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    items = []
    for key, label in SESSIONS:
        s = race.get(key)
        if s:
            items.append((parse_dt(s.get("date"), s.get("time")) or far, label, s))
    items.append((parse_dt(race.get("date"), race.get("time")) or far, "مسابقه", {"date": race.get("date"), "time": race.get("time")}))
    return sorted(items, key=lambda x: x[0])


def view_race(season: str, rnd: int, force: bool = False):
    race = get_race(season, rnd)
    if not race:
        return view_calendar(season, 0)
    c = race.get("Circuit", {})
    loc = c.get("Location", {})
    country = COUNTRY_FA.get(loc.get("country", ""), loc.get("country", ""))
    done = race_done(race)
    L = [f"🏁 {gp_title(race)}", f"دور {fa(rnd)} · فصل {fa(season)}" + ("  ✅ برگزار شد" if done else ""), ""]
    L.append(f"📍 {c.get('circuitName', '—')}")
    L.append(f"{loc.get('locality', '')}{SEP}{country}")
    L += ["", "🗓 برنامه (به وقت تهران)"]
    for _, label, s in session_list(race):
        L.append(f"{label}: {when(s.get('date'), s.get('time'))}")
    kb = []
    row = []
    if done:
        row.append(btn("🏁 نتیجهٔ مسابقه", f"res:{season}:{rnd}"))
    qd = parse_dt((race.get("Qualifying") or {}).get("date"), (race.get("Qualifying") or {}).get("time"))
    if qd and now_utc() > qd + dt.timedelta(hours=2):
        row.append(btn("⏱ کوالیفای", f"q:{season}:{rnd}"))
    if row:
        kb.append(row)
    nav = []
    if rnd > 1:
        nav.append(btn("➡️ دور قبل", f"g:{season}:{rnd - 1}"))
    if get_race(season, rnd + 1):
        nav.append(btn("دور بعد ⬅️", f"g:{season}:{rnd + 1}"))
    if nav:
        kb.append(nav)
    kb.append([btn("📅 تقویم", f"cal:{season}:{(rnd - 1) // PAGE}"), btn("🔄 بروزرسانی", f"g:{season}:{rnd}!")])
    return rtl(L), {"inline_keyboard": kb}


def gap_text(r: dict) -> str:
    t = (r.get("Time") or {}).get("time")
    return fa(t) if t else status_fa(r.get("status", ""))


def driver_buttons(items: list[dict], size: int = 5) -> list[list[dict]]:
    out = []
    for r in items:
        d = r.get("Driver") or {}
        if d.get("driverId"):
            out.append(btn(d.get("code") or d.get("familyName", "?")[:3].upper(), f"d:{d['driverId']}"))
    return chunk(out, size)


def result_lines(res: list[dict], limit: int | None = None) -> list[str]:
    lines = []
    for r in res[:limit]:
        pos = r.get("position", "")
        tag = MEDALS[int(pos) - 1] if pos.isdigit() and 1 <= int(pos) <= 3 else f"{fa(pos)}."
        line = f"{tag} {dname(r['Driver'])}{SEP}{gap_text(r)}"
        pts = r.get("points", "0")
        if pts not in ("0", "0.0", ""):
            line += f" ({fa(pts)})"
        lines.append(line)
    return lines


def view_results(season: str, rnd: int, force: bool = False):
    race = get_race(season, rnd)
    res = race_results(season, rnd, force)
    title = gp_title(race) if race else f"دور {fa(rnd)}"
    L = [f"🏁 نتیجهٔ {title}", when(race.get("date")) if race else "", ""]
    if not res:
        L.append("نتیجهٔ این مسابقه هنوز منتشر نشده است.")
    else:
        L += result_lines(res)
        fl = next((r for r in res if (r.get("FastestLap") or {}).get("rank") == "1"), None)
        if fl:
            L += ["", f"⚡ سریع‌ترین دور: {dname(fl['Driver'])}{SEP}{fa(fl['FastestLap'].get('Time', {}).get('time', ''))}"]
    kb = driver_buttons(res)
    kb.append([btn("⏱ کوالیفای", f"q:{season}:{rnd}"), btn("⬅️ گرندپری", f"g:{season}:{rnd}")])
    kb.append([btn("🔄 بروزرسانی", f"res:{season}:{rnd}!")])
    return rtl(L), {"inline_keyboard": kb}


def view_quali(season: str, rnd: int):
    race = get_race(season, rnd)
    q = race_quali(season, rnd)
    L = [f"⏱ کوالیفای {gp_title(race) if race else fa(rnd)}", ""]
    if not q:
        L.append("نتیجهٔ کوالیفای هنوز منتشر نشده است.")
    for r in q:
        best = r.get("Q3") or r.get("Q2") or r.get("Q1") or "—"
        L.append(f"{fa(r.get('position', ''))}. {dname(r['Driver'])}{SEP}{fa(best)}")
    kb = driver_buttons(q)
    kb.append([btn("🏁 نتیجهٔ مسابقه", f"res:{season}:{rnd}"), btn("⬅️ گرندپری", f"g:{season}:{rnd}")])
    return rtl(L), {"inline_keyboard": kb}


def view_standings(kind: str = "d", force: bool = False):
    season = current_season()
    if kind == "c":
        rnd, items = constructor_standings(season)
        L = [f"🏆 رده‌بندی تیم‌ها{SEP}فصل {fa(season)}" + (f"{SEP}پس از {fa(rnd)} دور" if rnd else ""), ""]
        for s in items:
            L.append(f"{fa(s['position'])}. {s['Constructor']['name']}{SEP}{fa(s['points'])} امتیاز")
        kb = [[btn("🏆 رانندگان", "st:d"), btn("🏠 خانه", "home")]]
        return rtl(L), {"inline_keyboard": kb}
    rnd, items = driver_standings(season, force)
    L = [f"🏆 رده‌بندی رانندگان{SEP}فصل {fa(season)}" + (f"{SEP}پس از {fa(rnd)} دور" if rnd else ""), ""]
    for s in items:
        L.append(f"{fa(s['position'])}. {dname(s['Driver'])}{SEP}{fa(s['points'])} امتیاز")
    kb = driver_buttons(items, 5) + [[btn("🏆 تیم‌ها", "st:c"), btn("🏠 خانه", "home")]]
    return rtl(L), {"inline_keyboard": kb}


def view_driver(chat_id: int, driver_id: str):
    season = current_season()
    rnd, items = driver_standings(season)
    s = next((x for x in items if x["Driver"]["driverId"] == driver_id), None)
    if not s:
        return "اطلاعات این راننده در فصل جاری پیدا نشد.", {"inline_keyboard": [[btn("🏆 رده‌بندی", "st:d")]]}
    d = s["Driver"]
    team = (s.get("Constructors") or [{}])[-1].get("name", "—")
    b = parse_date(d.get("dateOfBirth"))
    age = None
    if b:
        t = now_utc().date()
        age = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    following = is_following(chat_id, driver_id)
    head = f"🏎 {dname(d)}" + ("  ⭐" if following else "")
    sub = SEP.join(
        p for p in (NATION_FA.get(d.get("nationality", ""), d.get("nationality", "")),
                    f"{fa(age)} ساله" if age is not None else "",
                    f"شمارهٔ {fa(d['permanentNumber'])}" if d.get("permanentNumber") else "",
                    d.get("code", "")) if p
    )
    L = [head, sub, "", f"تیم: {team}", f"رده: {fa(s['position'])}{SEP}{fa(s['points'])} امتیاز{SEP}{fa(s.get('wins', 0))} برد"]
    try:
        rs = driver_season_results(season, driver_id)
        form = [(r.get("Results") or [{}])[0].get("positionText", "?") for r in rs][-8:]
        if form:
            L += ["", "نتایج اخیر: " + fa(" - ".join(reversed(form)))]
    except Exception:
        log.warning("driver form unavailable for %s", driver_id)
    kb = [
        [btn("✅ دنبال می‌شود · لغو" if following else "⭐ دنبال کردن راننده", f"f:{driver_id}")],
        [btn("🏆 رده‌بندی", "st:d"), btn("🏠 خانه", "home")],
    ]
    return rtl(L), {"inline_keyboard": kb}


def view_my(chat_id: int):
    rows = db.execute("SELECT driver_id, driver_name FROM follows WHERE chat_id=? ORDER BY rowid", (chat_id,)).fetchall()
    if not rows:
        return (
            rtl(["⭐ رانندگان من", "", "هنوز رانندهٔ را دنبال نکرده‌اید.",
                 "در کارت هر راننده «دنبال کردن» را بزنید تا نتیجهٔ مسابقه‌اش را برایتان بفرستم."]),
            {"inline_keyboard": [[btn("🏆 رده‌بندی رانندگان", "st:d")]]},
        )
    try:
        _, items = driver_standings(current_season())
        pos = {x["Driver"]["driverId"]: x for x in items}
    except Exception:
        pos = {}
    L, kb = [f"⭐ رانندگان من ({fa(len(rows))})", ""], []
    for did, name in rows:
        s = pos.get(did)
        L.append(f"🏎 {name}" + (f"{SEP}رده {fa(s['position'])}{SEP}{fa(s['points'])} امتیاز" if s else ""))
        kb.append([btn(f"🔎 {name}", f"d:{did}"), btn("❌", f"u:{did}")])
    return rtl(L), {"inline_keyboard": kb}


MODES = {"all": "🔔 نتیجهٔ همهٔ مسابقات", "followed": "⭐ فقط رانندگان دنبال‌شده", "off": "🔕 خاموش"}


def view_settings(chat_id: int):
    cur = get_mode(chat_id)
    L = ["⚙️ تنظیمات اعلان", "", "نوع اعلان‌هایی که می‌خواهید دریافت کنید:", "", f"وضعیت فعلی: {MODES[cur]}"]
    kb = [[btn(("✔️ " if k == cur else "") + label, f"m:{k}")] for k, label in MODES.items()]
    return rtl(L), {"inline_keyboard": kb}


def view_search(chat_id: int, query: str):
    q = norm(query)
    season = current_season()
    hits: list[tuple[str, str, str]] = []  # (kind, id, label)
    try:
        _, items = driver_standings(season)
        for s in items:
            d = s["Driver"]
            if q in norm(dname(d) + d.get("code", "") + d["driverId"]):
                hits.append(("d", d["driverId"], f"🏎 {dname(d)}"))
    except Exception:
        log.warning("search: standings unavailable")
    try:
        for r in races(season):
            c = r.get("Circuit", {})
            loc = c.get("Location", {})
            hay = norm(" ".join([r.get("raceName", ""), c.get("circuitName", ""), loc.get("locality", ""),
                                 loc.get("country", ""), COUNTRY_FA.get(loc.get("country", ""), ""), gp_title(r)]))
            if q in hay:
                hits.append(("g", f"{season}:{r['round']}", f"🏁 {gp_title(r)}"))
    except Exception:
        log.warning("search: calendar unavailable")
    if not hits:
        return rtl([f"🔍 «{query}»", "", "چیزی پیدا نشد.",
                    "نام راننده را به انگلیسی بنویسید (مثلاً Verstappen) و نام گرندپری را فارسی یا انگلیسی."]), None
    if len(hits) == 1:
        kind, ident, _ = hits[0]
        if kind == "d":
            return view_driver(chat_id, ident)
        s, r = ident.split(":")
        return view_race(s, int(r))
    kb = [[btn(label, f"d:{i}" if k == "d" else f"g:{i}")] for k, i, label in hits[:10]]
    return rtl([f"🔍 نتایج «{query}»", "", "یکی را انتخاب کنید:"]), {"inline_keyboard": kb}


# ============================== handlers ===============================
HELP = rtl([
    "🏎 ربات فرمول ۱ (نمونهٔ آزمایشی)", "",
    "🏁 گرندپری‌ها — مسابقهٔ بعدی، آخرین نتیجه و تقویم فصل",
    "🏆 رده‌بندی — رانندگان و تیم‌ها",
    "⭐ رانندگان من — رانندگان دنبال‌شده",
    "🔍 جستجو — نام راننده (انگلیسی) یا گرندپری",
    "⚙️ تنظیمات — نوع اعلان نتیجهٔ مسابقه", "",
    "منبع داده: Jolpica F1 API",
])


def handle_message(msg: dict) -> None:
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if chat_id is None or not text:
        return
    touch_user(chat_id)
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0] if text.startswith("/") else MENU_CMD.get(menu_key(text))
    arg = parts[1].strip() if text.startswith("/") and len(parts) > 1 else ""
    try:
        if cmd in ("/start", "/help"):
            name = (msg.get("from") or {}).get("first_name") or ""
            send(chat_id, (f"سلام {name} 👋\n\n" if cmd == "/start" and name else "") + HELP, MAIN_KB)
        elif cmd in ("/start_home", "/races"):
            send(chat_id, *view_home())
        elif cmd == "/standings":
            send(chat_id, *view_standings("d"))
        elif cmd == "/my":
            send(chat_id, *view_my(chat_id))
        elif cmd == "/settings":
            send(chat_id, *view_settings(chat_id))
        elif cmd == "/search":
            if arg:
                send(chat_id, *view_search(chat_id, arg))
            else:
                send(chat_id, "🔍 نام راننده یا گرندپری را بفرستید (مثلاً: Verstappen یا موناکو)")
        elif cmd == "/version":
            send(chat_id, f"نسخهٔ ربات: {VERSION}\nشناسهٔ فرایند: {os.getpid()}")
        elif cmd == "/id":
            send(chat_id, f"شناسهٔ چت شما: {chat_id}")
        elif cmd == "/stats" and chat_id in ADMIN_IDS:
            total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            follows = db.execute("SELECT COUNT(*) FROM follows").fetchone()[0]
            send(chat_id, f"📊 نسخه {VERSION}\nکاربران: {total}\nرانندگان دنبال‌شده: {follows}\n"
                          f"اسکن: {STATS['scans']} (خطا: {STATS['scan_errors']})\nفراخوانی API: {STATS['api_calls']}\n"
                          f"پیام‌های ارسالی: {STATS['sent']}")
        elif cmd is None:
            if len(text) < 2:
                send(chat_id, "برای جستجو حداقل دو حرف بفرستید.")
            else:
                send(chat_id, *view_search(chat_id, text))
    except Exception:
        log.exception("message handler failed")
        send(chat_id, "⚠️ دریافت اطلاعات با خطا مواجه شد. کمی بعد دوباره تلاش کنید.")


def handle_callback(cb: dict) -> None:
    global _ANSWER_CB_OK
    if _ANSWER_CB_OK:
        try:
            api("answerCallbackQuery", callback_query_id=cb["id"])
        except Exception:
            _ANSWER_CB_OK = False  # Bale does not need/accept it; stop calling (buttons still work)
            log.info("answerCallbackQuery not supported: disabled")
    msg = cb.get("message") or {}
    chat_id, message_id = (msg.get("chat") or {}).get("id"), msg.get("message_id")
    data = cb.get("data") or ""
    if chat_id is None or data == "noop":
        return
    touch_user(chat_id)
    force = data.endswith("!")
    p = data.rstrip("!").split(":")
    try:
        view = None
        season = current_season() if p[0] in ("next", "last") else None
        if p[0] == "home":
            view = view_home(force)
        elif p[0] == "next":
            n = next_race(season)
            view = view_race(season, int(n["round"])) if n else view_calendar(season, 0)
        elif p[0] == "last":
            n = last_done_race(season)
            view = view_results(season, int(n["round"]), True) if n else view_calendar(season, 0)
        elif p[0] == "cal" and len(p) == 3:
            view = view_calendar(p[1], int(p[2]))
        elif p[0] == "g" and len(p) == 3:
            view = view_race(p[1], int(p[2]), force)
        elif p[0] == "res" and len(p) == 3:
            view = view_results(p[1], int(p[2]), force)
        elif p[0] == "q" and len(p) == 3:
            view = view_quali(p[1], int(p[2]))
        elif p[0] == "st" and len(p) == 2:
            view = view_standings(p[1], force)
        elif p[0] == "d" and len(p) == 2:
            view = view_driver(chat_id, p[1])
        elif p[0] == "f" and len(p) == 2:
            _, items = driver_standings(current_season())
            s = next((x for x in items if x["Driver"]["driverId"] == p[1]), None)
            if s:
                toggle_follow(chat_id, p[1], dname(s["Driver"]))
            view = view_driver(chat_id, p[1])
        elif p[0] == "u" and len(p) == 2:
            db.execute("DELETE FROM follows WHERE chat_id=? AND driver_id=?", (chat_id, p[1]))
            db.commit()
            view = view_my(chat_id)
        elif p[0] == "m" and len(p) == 2 and p[1] in MODES:
            set_mode(chat_id, p[1])
            view = view_settings(chat_id)
        if view:
            edit_or_send(chat_id, message_id, view[0], view[1])
    except Exception:
        log.exception("callback failed")
        send(chat_id, "⚠️ دریافت اطلاعات با خطا مواجه شد.")


# ============================== notifier ===============================
def result_digest(res: list[dict]) -> str:
    state = [[r.get("position"), r["Driver"]["driverId"], r.get("status"), r.get("points")] for r in res]
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


def notify_results(race: dict, res: list[dict], corrected: bool) -> None:
    season, rnd = race["season"], int(race["round"])
    pos_by_driver = {r["Driver"]["driverId"]: r for r in res}
    follow_map: dict[int, list[str]] = {}
    ids = list(pos_by_driver)
    if ids:
        q = ",".join("?" * len(ids))
        for cid, did, name in db.execute(f"SELECT chat_id, driver_id, driver_name FROM follows WHERE driver_id IN ({q})", ids):
            r = pos_by_driver[did]
            follow_map.setdefault(cid, []).append(f"{name}{SEP}جایگاه {fa(r.get('position', '?'))}")
    head = f"🏁 نتیجهٔ {gp_title(race)} " + ("اصلاح شد" if corrected else "منتشر شد")
    body = rtl([head, ""] + result_lines(res, 3))
    kb = {"inline_keyboard": [[btn("📋 نتیجهٔ کامل", f"res:{season}:{rnd}")]]}
    sent = 0
    for cid, mode in db.execute("SELECT chat_id, mode FROM users WHERE mode != 'off'").fetchall():
        mine = follow_map.get(cid)
        if mode == "all" or mine:
            extra = ("\n\n" + rtl(["⭐ رانندگان دنبال‌شدهٔ شما"] + mine)) if mine else ""
            sent += send(cid, body + extra, kb)
            time.sleep(0.05)
    log.info("notified %d users about %s round %s", sent, season, rnd)


def scan_and_notify() -> None:
    STATS["scans"] += 1
    rs = jget("current/last/results.json?limit=100", ttl=0, force=True)["RaceTable"]["Races"]
    STATS["last_scan"] = time.time()
    if not rs or not rs[0].get("Results"):
        return
    race = rs[0]
    res = race["Results"]
    key, new = f"{race['season']}:{race['round']}", result_digest(res)
    first = db.execute("SELECT 1 FROM race_state LIMIT 1").fetchone() is None
    row = db.execute("SELECT state FROM race_state WHERE key = ?", (key,)).fetchone()
    if row and row[0] == new:
        return
    db.execute("INSERT OR REPLACE INTO race_state VALUES (?, ?)", (key, new))
    db.commit()
    if first and not NOTIFY_FIRST_SCAN:
        log.info("first scan: stored %s silently", key)
        return
    notify_results(race, res, corrected=row is not None)


# ================================ main =================================
def main() -> None:
    setup_logging()
    if not BALE_TOKEN:
        raise SystemExit("BALE_TOKEN is not set (put BOT_TOKEN=... in .env)")
    acquire_single_instance_lock()
    offset, last_scan = None, 0.0
    log.info("f1 bot started: version=%s pid=%s", VERSION, os.getpid())
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            for update in api("getUpdates", **params):
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(update["message"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
            if time.time() - last_scan >= SCAN_INTERVAL:
                last_scan = time.time()
                try:
                    scan_and_notify()
                except Exception:
                    STATS["scan_errors"] += 1
                    log.exception("scheduled scan failed")
        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("main loop error")
            time.sleep(5)


if __name__ == "__main__":
    main()
