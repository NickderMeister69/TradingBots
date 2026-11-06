"""Startet den Copy-Bot (Hyperliquid, Spielgeld) als Teil des Trading-Projekts.

Gegenueber der Einzel-Version kommt hier dazu:
- gleicher Zugriffsschutz wie bei den anderen Bots (PC selbst frei, Handy nur ueber Tailscale mit Passwort)
- Telegram-Meldungen bei Einstieg, Ausstieg und Not-Aus (gleiche Einstellungen wie die anderen Bots)
- Anbindung an die Gesamt-Uebersicht (/alle) und die Knoepfe Neu laden / Pausieren / Weiter / Beenden
Aufruf: aus dem tradebot-Ordner mit start_copybot.bat
"""
import asyncio
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))      # zuerst die Copy-Bot-Module (config, engine, ...)
sys.path.append(str(ROOT))         # danach das Trading-Projekt (bot.dashboard, bot.notify)

from dotenv import load_dotenv   # noqa: E402

load_dotenv(ROOT / ".env")

import json  # noqa: E402

# Varianten (Nicks Wunsch 19.10.2026): gleicher Aufbau wie der Copy-Bot, eigene Seite, eigenes Lernen,
# nur anderes Risiko/Hebel. Sie bekommen Profi-Daten und Kurse vom Haupt-Copy-Bot (Port 8010),
# damit Hyperliquid nicht 6-mal so oft abgefragt wird.
VARIANTS = {
    "sicher": (8011, "Copy sicher", {"leverage": 0.5, "max_coin_pct": 0.10, "max_margin_pct": 0.40,
                                     "max_positions": 8, "stop_loss_pct": 0.05}),
    "vorsichtig": (8012, "Copy vorsichtig", {"leverage": 1.0, "max_coin_pct": 0.15, "max_margin_pct": 0.50,
                                             "max_positions": 10, "stop_loss_pct": 0.06}),
    "mutig": (8013, "Copy mutig", {"leverage": 2.5, "max_coin_pct": 0.20, "max_margin_pct": 0.60,
                                   "max_positions": 12, "stop_loss_pct": 0.04}),
    "riskant": (8014, "Copy riskant", {"leverage": 5.0, "max_coin_pct": 0.25, "max_margin_pct": 0.80,
                                       "max_positions": 15, "stop_loss_pct": 0.03}),
    "konzentriert": (8015, "Copy konzentriert", {"leverage": 1.5, "max_coin_pct": 0.20, "max_margin_pct": 0.60,
                                                 "max_positions": 5, "stop_loss_pct": 0.06}),
    # 21.10.2026 (Nick, nach dem Polymarket-Video): Gegenprobe. Gleiche Regeln wie der Copy-Bot (8010), aber
    # 100 ZUFAELLIGE aktive Hyperliquid-Konten statt der Top-100. Lernt nicht (kein Vertrauen, kein Aussortieren),
    # damit am Ende klar ist: bringt die Profi-Auswahl etwas – oder waere Zufall genauso gut?
    "zufall": (8016, "Copy Zufall", {"leverage": 3.0, "max_coin_pct": 0.20, "max_margin_pct": 0.60,
                                     "max_positions": 12, "stop_loss_pct": 0.06, "use_trust": False}),
}
RANDOM = "zufall"
VARIANT = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in VARIANTS else None
if VARIANT:
    _port, _name, _params = VARIANTS[VARIANT]
    os.environ["COPYBOT_PORT"] = str(_port)
    os.environ["COPYBOT_DATA"] = f"data_{VARIANT}"
    os.environ["COPYBOT_PARAMS"] = json.dumps(_params)
    if VARIANT != RANDOM:          # Zufall braucht eigene Trader -> fragt Hyperliquid selbst ab
        os.environ["COPYBOT_FEED"] = "http://127.0.0.1:8010/api/feed"

import uvicorn                                                        # noqa: E402
from fastapi import Request                                           # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response  # noqa: E402

import app as A                                                       # noqa: E402  (Copy-Bot-Webseite)
import config as C                                                    # noqa: E402
import engine as E                                                    # noqa: E402
from bot.dashboard import FAIL_WINDOW, LOCK_SECONDS, MAX_FAILS, client_kind, password_ok  # noqa: E402
from bot.notify import Notifier                                       # noqa: E402

BOT_NAME = VARIANTS[VARIANT][1] if VARIANT else "Copy-Bot"
LOG_FILE = C.DATA_DIR / "bot.log"
TELEGRAM_TRADES = not VARIANT     # Varianten melden nur Not-Aus/Fehler, sonst kaemen 6x so viele Nachrichten
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
REMOTE = len(PASSWORD) >= 10
notify = Notifier(print, prefix=BOT_NAME)


# ---------------------------------------------------------------- Protokoll + Telegram
def _write_log(line):
    try:
        C.DATA_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5_000_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".old.log"))
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


_orig_log = getattr(E.Engine.log, "_orig", E.Engine.log)   # nie doppelt einwickeln


def _log(self, msg, level="info"):
    _orig_log(self, msg, level)
    _write_log(f"{datetime.now():%d.%m. %H:%M:%S} {msg}")
    # Telegram: Ein- und Ausstiege und Alarme. "Passt ... an" (kleine Anpassungen) nicht, sonst zu viele Nachrichten.
    if level == "trade" and TELEGRAM_TRADES and not msg.startswith("Passt"):
        icon = "✅ " if msg.startswith("Gewinn") else ("❌ " if msg.startswith("Verlust") else "📈 ")
        notify.send(icon + msg)
    elif level == "error":
        notify.send(msg, important=True)


_log._orig = _orig_log
E.Engine.log = _log


# ---------------------------------------------------------------- Daten vom Haupt-Copy-Bot (nur Varianten)
class FeedHyperliquid(E.Hyperliquid):
    """Wie der Hyperliquid-Zugang, liest Kurse und Profi-Positionen aber beim Haupt-Copy-Bot (Port 8010)."""

    def __init__(self):
        super().__init__()
        self.feed, self.feed_ts = None, 0.0

    async def get_feed(self, force=False):
        if force or self.feed is None or time.time() - self.feed_ts > 20:
            try:
                r = await self.client.get(C.FEED_URL, timeout=15.0)
                r.raise_for_status()
                self.feed, self.feed_ts = r.json(), time.time()
                self.calls += 1
            except Exception as e:  # noqa: BLE001
                self.errors += 1
                raise E.HLError(f"Haupt-Copy-Bot (8010) nicht erreichbar: {e}") from e
        return self.feed

    async def market(self):
        f = await self.get_feed(force=True)          # pro Runde einmal frisch holen
        if not f.get("market"):
            raise E.HLError("Haupt-Copy-Bot (8010) hat noch keine Kurse")
        return f["market"]

    async def positions(self, address):
        st = (await self.get_feed()).get("states", {}).get(address)
        if st is None:
            raise E.HLError("keine Daten fuer diesen Profi")
        return st


async def _feed_refresh_traders(self, blocking=False):
    """Varianten waehlen keine eigenen Profis, sondern uebernehmen die 100 des Haupt-Copy-Bots."""
    if self.refreshing:
        return
    self.refreshing = True
    try:
        f = await self.hl.get_feed(force=True)
        tr = f.get("traders") or []
        if not tr:
            if blocking and not self.traders:
                raise E.HLError("Haupt-Copy-Bot (8010) hat noch keine Profis")
            return
        now = time.time()
        old = {t["address"] for t in self.traders}
        prev = {t["address"]: t for t in self.traders}
        new_list = []
        for t in tr:
            t = dict(t)
            t["added_at"] = prev.get(t["address"], {}).get("added_at", now)
            self.trust.setdefault(t["address"], {"trust": 1.0, "obs": 0, "copy_ret": 0.0, "since": now, "hist": []})
            new_list.append(t)
        self.traders, self.reserves = new_list, []
        self.selection_info = {**(f.get("selection_info") or {}), "from_main": True}
        self.ts["last_lb"] = now
        new = {t["address"] for t in new_list}
        if new != old:
            self.log(f"Profi-Liste vom Haupt-Copy-Bot übernommen: {len(new_list)} Profis"
                     + (f", {len(new - old)} neu, {len(old - new)} raus." if old else "."))
            self.save()
    finally:
        self.refreshing = False


def random_candidates(rows):
    """Gegenprobe: zufaellige Konten statt der besten. Nur die Mindest-Regeln, damit man sie ueberhaupt
    kopieren kann (genug Geld im Konto, diese Woche gehandelt) – keine Leistungs-Filter."""
    import random
    S = C.SELECTION
    pool = []
    for row in rows:
        try:
            av = float(row["accountValue"])
            w = {k: v for k, v in row["windowPerformances"]}
            week = {k: float(v) for k, v in w.get("week", {}).items()}
            month = {k: float(v) for k, v in w.get("month", {}).items()}
            allt = {k: float(v) for k, v in w.get("allTime", {}).items()}
        except (KeyError, ValueError, TypeError):
            continue
        if av < S["min_account_usd"] or week.get("vlm", 0) <= 0:
            continue
        pool.append({"address": row["ethAddress"].lower(), "name": row.get("displayName"), "lb_account": av,
                     "month_roi": month.get("roi", 0), "month_pnl": month.get("pnl", 0), "week_roi": week.get("roi", 0),
                     "alltime_pnl": allt.get("pnl", 0), "alltime_roi": allt.get("roi", 0),
                     "turnover": month.get("vlm", 0) / av if av else 0, "score": 0.0})
    random.Random(20261021).shuffle(pool)       # feste Auslosung: nach einem Neustart dieselben Konten
    return pool[: S["candidate_pool"]]


if VARIANT == RANDOM:
    E.candidates = random_candidates
    E.Engine.drop_weak_traders = lambda self, now: None   # keine Auslese nach Erfolg
    C.LEADERBOARD_REFRESH_HOURS = 24 * 365                 # Auslosung bleibt (nur neu, wenn keine Trader da)
    C.DEFAULT_SETTINGS["auto_learn"] = False               # keine Regeln von Schattenkonten uebernehmen
    E.L.coins_to_block = lambda *a, **k: []                 # keine Coins sperren

if C.FEED_URL:
    E.Hyperliquid = FeedHyperliquid
    E.Engine.refresh_traders = _feed_refresh_traders
    C.LEADERBOARD_REFRESH_HOURS = 0.1        # Profi-Liste alle 6 Minuten mit dem Haupt-Copy-Bot abgleichen


# ---------------------------------------------------------------- Zugriffsschutz
app = A.app
fails = {}


@app.middleware("http")
async def guard(request: Request, call_next):
    host = request.client.host if request.client else ""
    kind = client_kind(host)
    # Aenderungen nur mit JSON oder eigenem Kopf: fremde Webseiten koennen beides nicht unbemerkt schicken
    if request.method == "POST":
        ctype = request.headers.get("content-type", "")
        if not (ctype.startswith("application/json") or request.headers.get("x-bot-action") == "1"):
            return PlainTextResponse("Nicht erlaubt.", 403)
    if kind == "local":
        return await call_next(request)
    if kind != "tailscale" or not PASSWORD:
        return PlainTextResponse("Kein Zugriff. Der Copy-Bot ist nur ueber Tailscale mit Passwort erreichbar.", 403)
    now = time.time()
    recent = [t for t in fails.get(host, []) if now - t < FAIL_WINDOW]
    if len(recent) >= MAX_FAILS and now - recent[-1] < LOCK_SECONDS:
        return PlainTextResponse("Zu viele falsche Passwoerter. Bitte in 15 Minuten nochmal versuchen.", 429)
    if not password_ok(request.headers.get("authorization", ""), PASSWORD):
        if request.headers.get("authorization"):
            fails[host] = recent + [now]
            await asyncio.sleep(1)
        return Response("Passwort noetig", 401, headers={"WWW-Authenticate": 'Basic realm="Trading Bot", charset="UTF-8"'})
    fails.pop(host, None)
    return await call_next(request)


# ---------------------------------------------------------------- Gesamt-Uebersicht
STATUS = {"running": "läuft", "paused": "Pause", "stopped": "Notbremse"}


def _pf_view(e, pf, name, meta, status):
    mk = e.market
    eq = pf.equity(mk)
    st = e.db.trade_stats(pf.id)
    today_start = datetime.now(E.TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    pos = []
    for coin, p in pf.positions.items():
        mark = (mk.get(coin) or {}).get("mark") or p["last_px"]
        upnl = p["qty"] * (mark - p["entry"])
        risk = abs(p["qty"]) * p["entry"] * pf.params["stop_loss_pct"]
        pos.append({"symbol": f"{coin}/USD", "side": "long" if p["qty"] > 0 else "short", "entry": p["entry"],
                    "price": mark, "pnl": upnl, "r": (upnl / risk) if risk else None, "units": 1, "max_units": 1,
                    "opened_at": datetime.fromtimestamp(p["opened_at"]).isoformat() if p.get("opened_at") else None})
    pos.sort(key=lambda x: x["opened_at"] or "")
    return {
        "name": name, "port": C.PORT, "mode": "paper", "exchange": "Hyperliquid (nur lesen)", "quote": "€",
        "timeframe": f"{e.settings['cycle_seconds']} s", "risk": pf.params["stop_loss_pct"], "units": 1, "meta": meta,
        "start": pf.start, "deposits": 0.0, "realized": pf.totals["realized"], "unrealized": pf.unrealized(mk),
        "konto": eq, "gewinn": eq - pf.start, "pct": (eq / pf.start - 1) * 100 if pf.start else 0,
        "trades": st["n"], "wins": st["wins"], "today": eq - pf.day["equity"],
        "status": status, "since": datetime.fromtimestamp(pf.created_at).isoformat(), "positions": pos,
        "last": [], "updated": datetime.fromtimestamp(e.last_cycle).strftime("%H:%M:%S") if e.last_cycle else None,
        "today_trades": e.db.trade_stats(pf.id, today_start)["n"],
    }


def overview():
    e = A.engine
    if e is None:
        return {"name": BOT_NAME, "port": C.PORT, "status": "startet", "konto": C.START_CAPITAL, "gewinn": 0.0,
                "pct": 0.0, "start": C.START_CAPITAL, "positions": [], "trades": 0, "wins": 0, "today": 0.0,
                "unrealized": 0.0, "quote": "€", "meta": "Hyperliquid-Profis kopieren", "extra": []}
    status = STATUS.get(e.status, e.status)
    if not e.traders:
        status = "sucht Profis"
    pm = e.main.params
    who = "Zufalls-Trader" if VARIANT == RANDOM else "Profis"
    meta = (f"{C.PORT} · {'Variante · ' if VARIANT else ''}{len(e.traders)} {who} · Hebel {pm['leverage']:g} · "
            f"Stop {pm['stop_loss_pct'] * 100:g} %" + (f" · max. {int(pm['max_positions'])} Coins" if VARIANT else ""))
    main = _pf_view(e, e.main, BOT_NAME, meta, status)
    extra = []
    for pid, name, _own in E.CMP_ACCOUNTS:
        pf = e.pfs.get(pid)
        if not pf:
            continue
        p = pf.params
        st = "Notbremse" if p.get("cmp_stopped") else status
        meta = (f"{C.PORT} · Vergleich · Hebel {p['leverage']:g} · Stop {p['stop_loss_pct'] * 100:g} % · "
                f"max. {int(p['max_positions'])} Coins")
        extra.append(_pf_view(e, pf, name, meta, st))
    main["extra"] = extra
    return main


@app.get("/api/kurz")
def kurz():
    return JSONResponse(overview())


# ---------------------------------------------------------------- Neues Dashboard (wie bei allen Bots)
from bot import board as BD   # noqa: E402

_routes = [r for r in app.router.routes if getattr(r, "path", None) == "/" and "GET" in getattr(r, "methods", set())]
for _r in _routes:
    app.router.routes.remove(_r)        # "/" zeigt jetzt das neue Dashboard, die alte Seite liegt unter /klassisch
_candle_cache = {}
COPY_RULES = [
    "Kopiert die Positionen von 100 Hyperliquid-Tradern (gewichtete Mehrheit pro Coin).",
    "Einstieg, wenn genug Trader im Coin sind und klar die Mehrheit auf einer Seite liegt.",
    "Raus, wenn die Mehrheit weg ist, die Trader drehen oder ein Coin mit stärkerem Signal den Platz braucht.",
    "Verlust-Grenze pro Coin, Not-Aus bei −30 % vom Höchststand.",
    "Lernt mit Schattenkonten und übernimmt bessere Regeln nach mind. 48 h und 20 Trades.",
]


@app.get("/")
def board_page():
    return HTMLResponse(BD.board_html(), headers={"Cache-Control": "no-cache"})


@app.get("/klassisch")
def klassisch_page():
    return FileResponse(A.STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/board/lwc.js")
def board_lwc():
    return Response(BD.lwc_js(), media_type="application/javascript", headers={"Cache-Control": "max-age=86400"})


async def _board(symbol=None):
    e = A.engine
    if e is None or not e.market:
        return {"name": BOT_NAME, "badge": "SPIELGELD", "subtitle": "startet …", "status": "startet", "paused": False,
                "quote": "€", "cards": {"price": {"label": "BTC Kurs", "value": None}, "equity": {"value": C.START_CAPITAL,
                "start": C.START_CAPITAL, "pct": 0}, "hold": {"value": None, "diff": None},
                "position": {"text": "–", "sub": ""}, "closed": {"n": 0, "wins": 0, "fees": 0}},
                "symbols": ["BTC"], "symbol": "BTC", "signal": {"title": "Startet …", "text": "", "kind": "flat", "rows": [],
                "rules": COPY_RULES}, "curve": [], "hold_curve": [], "activity": [], "trades": [], "settings": None,
                "details_url": "/klassisch"}
    pf, mk = e.main, e.market
    btc = (mk.get("BTC") or {})
    btc_now, prev = btc.get("mark"), btc.get("prev_day")
    eq = pf.equity(mk)
    hcurve, hold_now = await asyncio.to_thread(BD.hold_curve, pf.created_at, pf.start, btc_now)
    curve = e.db.equity_curve(E.MAIN, pf.created_at, max_points=600) + [(time.time(), eq)]
    st = e.db.trade_stats(E.MAIN)
    coins = sorted(pf.positions)
    longs = sum(1 for p in pf.positions.values() if p["qty"] > 0)
    gross = pf.gross(mk)
    status = STATUS.get(e.status, e.status)
    lw = sum(c.get("long_w", 0) for c in e.cons_w.values()) if e.cons_w else 0
    sw = sum(c.get("short_w", 0) for c in e.cons_w.values()) if e.cons_w else 0
    share = lw / (lw + sw) * 100 if lw + sw else None
    busy = sum(1 for t in e.traders if e.states.get(t["address"], {}).get("positions"))
    who = "Zufalls-Trader" if VARIANT == RANDOM else "Profis"
    kind = "flat" if share is None else ("up" if share > 55 else "down" if share < 45 else "flat")
    title = ("Trader gerade gemischt" if kind == "flat" else
             f"{who} gerade überwiegend {'long' if kind == 'up' else 'short'}")
    text = (f"{share:.0f} % des Gewichts liegt auf Long, {100 - share:.0f} % auf Short." if share is not None else "")
    pm = pf.params
    rows = [[f"{who} kopiert", str(len(e.traders))], ["davon mit offenen Positionen", str(busy)],
            ["Coins mit Signal", str(len(e.cons_w or {}))], ["Bot hält", f"{len(coins)} Coins ({longs} long / {len(coins) - longs} short)"],
            ["Einsatz gesamt", f"{gross:,.0f} €".replace(",", ".")], ["Hebel / Stop", f"{pm['leverage']:g} / {pm['stop_loss_pct'] * 100:g} %".replace(".", ",")]]
    trades = e.db.trades(E.MAIN, limit=60)
    last = trades[0] if trades else None
    sym = symbol if symbol in (["BTC"] + coins) else "BTC"
    return {
        "name": BOT_NAME, "badge": "SPIELGELD", "subtitle": f"Hyperliquid · kopiert {len(e.traders)} {who} · alle {e.settings['cycle_seconds']} s",
        "status": status, "paused": e.status == "paused", "quote": "€",
        "cards": {"price": {"label": "BTC Kurs", "value": btc_now, "change": BD._pct(btc_now, prev)},
                  "equity": {"value": eq, "start": pf.start, "pct": (eq / pf.start - 1) * 100 if pf.start else 0},
                  "hold": {"value": hold_now, "diff": (eq - hold_now) if hold_now else None},
                  "position": {"text": f"{len(coins)} Coins offen" if coins else "Kein Trade (Bargeld)",
                               "sub": f"{longs} Long / {len(coins) - longs} Short · offen {pf.unrealized(mk):+.2f} €" if coins else ""},
                  "closed": {"n": st["n"], "wins": st["wins"], "fees": round(pf.totals["fees"], 2)}},
        "symbols": ["BTC"] + coins, "symbol": sym,
        "signal": {"title": title, "text": text, "kind": kind, "rows": rows,
                   "next_check": (e.last_cycle or time.time()) + e.settings["cycle_seconds"],
                   "check_sec": e.settings["cycle_seconds"],
                   "last": (f"{last['coin']} {last['side']} {last['net']:+.2f} € ({datetime.fromtimestamp(last['closed_at']):%d.%m %H:%M})"
                            if last else "noch keiner"), "rules": COPY_RULES},
        "curve": [[round(t), round(v, 2)] for t, v in curve],
        "hold_curve": [[round(t), round(v, 2)] for t, v in BD.thin(hcurve)],
        "activity": [{"t": datetime.fromtimestamp(x["ts"]).strftime("%d.%m %H:%M"), "kind": x["level"], "msg": x["msg"]}
                     for x in e.db.recent_log(80)],
        "trades": [{"time": datetime.fromtimestamp(t["closed_at"]).isoformat(), "symbol": t["coin"], "side": t["side"],
                    "entry": t["entry"], "exit": t["exit"], "amount": t["qty"], "pnl": t["net"], "reason": t["reason"]}
                   for t in trades],
        "settings": None, "details_url": "/klassisch",
    }


@app.get("/api/board")
async def api_board(symbol: str = ""):
    return JSONResponse(await _board(symbol or None))


@app.get("/api/board/candles")
async def api_board_candles(symbol: str = ""):
    e = A.engine
    coin = (symbol or "BTC").split("/")[0]
    hit = _candle_cache.get(coin)
    if hit and time.time() - hit[0] < 60:
        return JSONResponse(hit[1])
    now = int(time.time() * 1000)
    rows = []
    if e is not None:
        try:
            rows = await e.hl.info({"type": "candleSnapshot",
                                    "req": {"coin": coin, "interval": "15m", "startTime": now - 300 * 900_000, "endTime": now}})
        except Exception:  # noqa: BLE001
            rows = []
    candles = [{"time": int(k["t"] // 1000), "open": float(k["o"]), "high": float(k["h"]), "low": float(k["l"]),
                "close": float(k["c"])} for k in rows or []]
    lines, markers = [], []
    if e is not None and candles:
        p = e.main.positions.get(coin)
        if p:
            lines.append({"name": "Einstieg Bot", "color": "#2563eb", "dashed": True,
                          "data": [{"time": candles[0]["time"], "value": p["entry"]}, {"time": candles[-1]["time"], "value": p["entry"]}]})
        t0 = candles[0]["time"]
        for t in e.db.trades(E.MAIN, limit=200):
            if t["coin"] != coin:
                continue
            for when, is_open in ((t["opened_at"], True), (t["closed_at"], False)):
                if when and when >= t0:
                    up = (t["side"] == "long") == is_open
                    markers.append({"time": int(when // 900 * 900), "position": "belowBar" if up else "aboveBar",
                                    "color": "#16a34a" if up else "#dc2626", "shape": "arrowUp" if up else "arrowDown",
                                    "text": ("rein" if is_open else f"raus {t['net']:+.0f}")})
        markers.sort(key=lambda m: m["time"])
    out = {"symbol": coin, "timeframe": "15m", "candles": candles, "lines": lines, "markers": markers}
    _candle_cache[coin] = (time.time(), out)
    return JSONResponse(out)


@app.get("/api/feed")
def feed(request: Request):
    """Fuer die Copy-Varianten auf diesem PC: aktuelle Kurse, Profis und ihre Positionen (nur lokal)."""
    if client_kind(request.client.host if request.client else "") != "local":
        return JSONResponse({"ok": False}, 403)
    e = A.engine
    if e is None or not e.market:
        return {"ts": 0, "market": {}, "states": {}, "traders": []}
    return {"ts": e.last_cycle, "market": e.market, "states": e.states, "traders": e.traders,
            "selection_info": e.selection_info}


@app.get("/alle")
def alle_page(request: Request):
    # Die Gesamt-Uebersicht liefert der Hauptbot; dorthin weiterleiten
    host = (request.headers.get("host") or "localhost").split(":")[0]
    return Response(status_code=307, headers={"Location": f"http://{host}:8000/alle"})


def _exit_soon(reason, code):
    """1 = start_copybot.bat startet nach 30 s neu, 3 = bleibt aus (wieder starten nur am PC)."""
    e = A.engine
    if e is not None:
        e.log(reason, "warn")
        try:
            e.save()
        except Exception:
            pass

    def bye():
        time.sleep(1.5)
        os._exit(code)
    threading.Thread(target=bye, daemon=True).start()


def _ok(request):
    return request.headers.get("x-bot-action") == "1"


@app.post("/api/neu_laden")
def neu_laden(request: Request):
    if not _ok(request):
        return JSONResponse({"ok": False}, 403)
    _exit_soon("Neu laden über die Übersicht. Der Copy-Bot ist in ca. 1 Minute wieder da.", 1)
    return {"ok": True}


@app.post("/api/beenden")
def beenden(request: Request):
    if not _ok(request):
        return JSONResponse({"ok": False}, 403)
    _exit_soon("Beendet über die Übersicht. Wieder starten nur am PC (alle_starten.bat).", 3)
    return {"ok": True}


async def _control(request, action):
    if not _ok(request):
        return JSONResponse({"ok": False}, 403)
    e = A.engine
    if e is None:
        return {"ok": False}
    async with e.lock:
        if action == "resume" and e.status == "stopped":
            return {"ok": False, "msg": "Nach Not-Aus bitte auf der Copy-Bot-Seite selbst wieder starten."}
        e.control(action)
    return {"ok": True}


@app.post("/api/pause")
async def pause(request: Request):
    return await _control(request, "pause")


@app.post("/api/resume")
async def resume(request: Request):
    return await _control(request, "resume")


if __name__ == "__main__":
    host = "0.0.0.0" if REMOTE else "127.0.0.1"
    print(f"\n  {BOT_NAME} (Hyperliquid, Spielgeld): http://localhost:{C.PORT}")
    if VARIANT:
        print("  Daten kommen vom Haupt-Copy-Bot (Port 8010) - der muss laufen.")
    if REMOTE:
        print(f"  Vom Handy (Tailscale): http://<Tailscale-IP>:{C.PORT}  (nur mit Passwort)")
    print("  Telegram: " + ("an" if notify.enabled else "aus (telegram_einrichten.py)") + "\n")
    _write_log(f"{datetime.now():%d.%m. %H:%M:%S} {BOT_NAME} gestartet (Port {C.PORT}, Telegram {'an' if notify.enabled else 'aus'}).")
    uvicorn.run(app, host=host, port=C.PORT, log_level="warning")
