"""Webserver: Dashboard unter http://localhost:8010"""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config as C
import learning as L
from engine import MAIN, TZ, Engine, short
from storage import Storage

STATIC = C.BASE_DIR / "static"
engine: Engine = None


@asynccontextmanager
async def lifespan(app):
    global engine
    engine = Engine(Storage())
    task = asyncio.create_task(engine.run())
    daily = asyncio.create_task(daily_selftest())
    yield
    task.cancel()
    daily.cancel()
    engine.save()
    await engine.hl.close()


async def daily_selftest():
    await asyncio.sleep(120)
    while True:
        last = (engine.selftest_result or {}).get("ts", 0)
        if time.time() - last > 86400:
            try:
                await engine.selftest()
            except Exception as e:
                engine.log(f"Selbsttest fehlgeschlagen: {e}", "warn")
        await asyncio.sleep(600)


app = FastAPI(title="Copy-Trading Bot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ Hilfen
def _side_word(direction):
    return "steigend" if direction > 0 else "fallend"


def _fmt_eur(x):
    return f"{abs(x):,.0f}".replace(",", ".") + " €"


def coin_rows(e: Engine):
    pf = e.main
    cons = e.cons_w if pf.params.get("use_trust", True) else e.cons_u
    now = time.time()
    coins = set(cons) | set(pf.positions)
    rows = []
    for coin in coins:
        c = cons.get(coin, {"n": 0, "n_long": 0, "n_short": 0, "agreement": 0.0, "long_share": 0.5,
                            "avg_exposure": 0, "supporters": []})
        m = e.market.get(coin, {})
        pos = pf.positions.get(coin)
        ntl = pf.notional(coin, e.market) if pos else 0.0
        v = e.verdicts.get(coin, "few" if c["n"] < pf.params["min_traders"] else "split")
        direction = 1 if c["agreement"] > 0 else -1
        share = c["long_share"] if direction > 0 else 1 - c["long_share"]
        thr = round(50 + 50 * pf.params["min_agreement"])
        cd, bl = pf.cooldown.get(coin, 0), pf.blocked.get(coin, 0)
        if bl > now:
            text = f"Vom Lernen gesperrt bis {datetime.fromtimestamp(bl, TZ):%d.%m. %H:%M} – lief bisher schlecht für den Bot."
        elif cd > now and not pos:
            text = f"Nach Stop-Loss Pause bis {datetime.fromtimestamp(cd, TZ):%H:%M}."
        elif pos:
            side = "long (wettet auf steigend)" if ntl > 0 else "short (wettet auf fallend)"
            agree_txt = f"{share * 100:.0f} % der Profis sehen es genauso" if (ntl > 0) == (direction > 0) and c["n"] else "Profis sind gerade anders gestimmt"
            text = f"Bot ist {side} mit {_fmt_eur(abs(ntl))}. {agree_txt}."
        elif v in ("long", "short"):
            text = (f"{c['n']} Profis, {share * 100:.0f} % wetten auf {_side_word(direction)}. "
                    + ("Bot ist pausiert." if e.status != "running" else "Ziel zu klein für eine Order."))
        elif v == "few":
            text = f"Nur {c['n']} Profi{'s' if c['n'] != 1 else ''} drin – Bot braucht mind. {pf.params['min_traders']}."
        elif v == "weak":
            text = (f"{c['n']} Profis, {share * 100:.0f} % wetten auf {_side_word(direction)} – "
                    f"aber {pf.params['max_positions']} andere Coins haben stärkere Signale.")
        elif v == "exit":
            text = "Mehrheit ist weg – Bot steigt aus."
        else:
            text = f"Uneinig: {c['long_share'] * 100:.0f} % steigend. Bot wartet auf eine klare Mehrheit (ab {thr} %)."
        prev = m.get("prev_day")
        rows.append({
            "coin": coin, "price": m.get("mid"), "day_pct": ((m["mid"] / prev - 1) * 100) if prev and m.get("mid") else None,
            "n": c["n"], "n_long": c["n_long"], "n_short": c["n_short"], "long_share": c["long_share"],
            "agreement": c["agreement"], "avg_exposure": c["avg_exposure"], "verdict": v,
            "position": ntl, "upnl": (pos["qty"] * ((m.get("mark") or pos["last_px"]) - pos["entry"])) if pos else 0,
            "funding_h": m.get("funding"), "text": text, "locked": bl > now or cd > now,
        })
    rows.sort(key=lambda r: (r["position"] == 0, -r["n"]))
    return rows[:45]


def position_rows(e: Engine):
    pf = e.main
    cons = e.cons_w if pf.params.get("use_trust", True) else e.cons_u
    out = []
    for coin, p in pf.positions.items():
        m = e.market.get(coin, {})
        mark = m.get("mark") or p["last_px"]
        ntl = p["qty"] * mark
        upnl = p["qty"] * (mark - p["entry"])
        sgn = 1 if p["qty"] > 0 else -1
        c = cons.get(coin, {})
        stop = p["entry"] * (1 - sgn * pf.params["stop_loss_pct"])
        out.append({
            "coin": coin, "side": p["side"], "notional": abs(ntl), "entry": p["entry"], "mark": mark,
            "upnl": upnl, "upnl_margin_pct": upnl / (abs(ntl) / pf.params["leverage"]) * 100 if ntl else 0,
            "move_pct": (mark / p["entry"] - 1) * 100 * sgn, "stop": stop,
            "stop_dist_pct": abs(mark - stop) / mark * 100, "opened_at": p["opened_at"],
            "fees": p["fees"], "funding": p["funding"], "realized": p["realized"],
            "n_now": c.get("n", 0), "share_now": (c.get("long_share", 0.5) if sgn > 0 else 1 - c.get("long_share", 0.5)),
            "n_open": p.get("supporters", {}).get("n"), "adds": p["adds"],
        })
    out.sort(key=lambda r: -r["notional"])
    return out


def account_view(e: Engine):
    pf = e.main
    eq = pf.equity(e.market)
    u = pf.unrealized(e.market)
    tot = pf.totals
    day = pf.day
    gross = pf.gross(e.market)
    st = e.db.trade_stats(MAIN)
    today_start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    st_today = e.db.trade_stats(MAIN, today_start)
    return {
        "equity": eq, "cash": pf.cash, "unrealized": u, "gross": gross, "margin": gross / pf.params["leverage"],
        "peak": pf.peak, "dd_pct": (1 - eq / pf.peak) * 100 if pf.peak else 0, "start": pf.start,
        "started_at": pf.created_at,
        "total": {"pnl": eq - pf.start, "realized": tot["realized"], "fees": -tot["fees"], "funding": tot["funding"],
                  "open": u},
        "today": {"pnl": eq - day["equity"], "realized": tot["realized"] - day["realized"],
                  "fees": -(tot["fees"] - day["fees"]), "funding": tot["funding"] - day["funding"],
                  "trades": st_today["n"]},
        "trades": st,
    }


def variant_rows(e: Engine):
    stats = e.db.get("eval_stats", {}) or {}
    out = []
    best_score = None
    for pf in e.pfs.values():
        if pf.id.startswith("cmp_"):
            continue      # Vergleichs-Konten (Risiko/Hebel) stehen in der Gesamt-Uebersicht, nicht im Lern-Bereich
        eq = pf.equity(e.market)
        s = stats.get(pf.id, {})
        st = e.db.trade_stats(pf.id)
        row = {"id": pf.id, "name": pf.name, "equity": eq, "ret_pct": (eq / pf.start - 1) * 100,
               "trades": st["n"], "wins": st["wins"], "positions": len(pf.positions),
               "window": s, "rules": L.describe_params(pf.params), "note": pf.note,
               "age_h": (time.time() - pf.created_at) / 3600, "main": pf.id == MAIN,
               "same_as_main": pf.id != MAIN and all(pf.params.get(k) == e.main.params.get(k)
                                                     for k in list(L.LEARNABLE) + ["use_trust"])}
        out.append(row)
        if pf.id != MAIN and s and (best_score is None or s.get("score", -1e9) > best_score):
            best_score = s.get("score")
    for r in out:
        r["best"] = bool(r["window"]) and not r["main"] and r["window"].get("score") == best_score
    out.sort(key=lambda r: (not r["main"], -r["ret_pct"]))
    return out


# ------------------------------------------------------------------ API
@app.get("/api/state")
def api_state():
    e = engine
    if e is None:
        raise HTTPException(503, "startet")
    return JSONResponse({
        "now": time.time(), "status": e.status, "conn": e.conn, "refreshing": e.refreshing,
        "settings": e.settings, "params": e.main.params, "defaults": C.DEFAULT_PARAMS,
        "currency": C.CURRENCY, "start_capital": C.START_CAPITAL,
        "last_cycle": e.last_cycle, "cycle_ms": e.cycle_ms, "latency_ms": e.hl.last_latency_ms,
        "api_calls": e.hl.calls, "traders_count": len(e.traders),
        "account": account_view(e), "positions": position_rows(e), "coins": coin_rows(e),
        "log": e.db.recent_log(60), "events": e.db.recent_events(30), "learned": e.learned,
        "variants": variant_rows(e), "selection": e.selection_info, "selftest": e.selftest_result,
        "next_eval": e.ts.get("last_eval", 0) + C.EVALUATION_HOURS * 3600,
        "next_lb": e.ts.get("last_lb", 0) + C.LEADERBOARD_REFRESH_HOURS * 3600,
        "coin_stats": e.db.coin_stats(MAIN)[:30],
        "rules": {"switch_min_trades": C.SWITCH_MIN_TRADES, "switch_min_hours": C.SWITCH_MIN_HOURS,
                  "switch_margin": C.SWITCH_MARGIN_PCT, "selection": C.SELECTION, "top_n": C.TOP_N},
    })


@app.get("/api/traders")
def api_traders():
    e = engine
    out = []
    for t in e.traders:
        a = t["address"]
        st = e.states.get(a, {"account": t.get("account", 0), "positions": {}})
        tr = e.trust.get(a, {"trust": 1.0, "copy_ret": 0.0, "obs": 0})
        pos = sorted(st["positions"].items(), key=lambda kv: -abs(kv[1]["value"]))
        out.append({
            "rank": t.get("rank"), "address": a, "short": short(a), "name": t.get("name"),
            "account": st["account"], "month_roi": t.get("month_roi"), "week_roi": t.get("week_roi"),
            "alltime_pnl": t.get("alltime_pnl"), "month_pnl": t.get("month_pnl"), "score": t.get("score"),
            "trust": tr["trust"], "trust_24h": L.trust_change_24h(tr), "copy_ret": tr.get("copy_ret", 0.0),
            "obs": tr.get("obs", 0), "added_at": t.get("added_at"),
            "n_pos": len(pos),
            "lev": (sum(abs(p["value"]) for _, p in pos) / st["account"]) if st["account"] else 0,
            "positions": [{"coin": c, "side": "long" if p["szi"] > 0 else "short", "value": abs(p["value"]),
                           "upnl": p["upnl"], "entry": p["entry"], "lev": p["lev"]} for c, p in pos[:12]],
            "fresh": time.time() - e.state_ts.get(a, 0) < e.settings["cycle_seconds"] * 3,
        })
    out.sort(key=lambda r: -r["trust"])
    return {"traders": out, "selection": e.selection_info}


@app.get("/api/trader/{address}")
async def api_trader(address: str):
    try:
        hist = await engine.hl.portfolio_history(address.lower())
    except Exception as ex:
        return {"history": [], "error": str(ex)}
    return {"history": hist}


@app.get("/api/trades")
def api_trades(portfolio: str = MAIN, limit: int = 100):
    return {"trades": engine.db.trades(portfolio, min(limit, 500))}


@app.get("/api/equity")
def api_equity(portfolio: str = MAIN, range: str = "all"):
    since = {"day": 86400, "week": 7 * 86400, "month": 30 * 86400}.get(range)
    since = time.time() - since if since else 0
    pts = engine.db.equity_curve(portfolio, since)
    pf = engine.pfs.get(portfolio)
    if pf:
        pts.append((time.time(), pf.equity(engine.market)))
    return {"points": pts}


@app.post("/api/control")
async def api_control(body: dict = Body(...)):
    action = body.get("action")
    if action not in ("pause", "resume", "kill"):
        raise HTTPException(400, "unbekannte Aktion")
    async with engine.lock:
        engine.control(action)
    return {"ok": True, "status": engine.status}


@app.post("/api/settings")
async def api_settings(body: dict = Body(...)):
    async with engine.lock:
        changed = engine.update_params(body.get("params"), body.get("settings"))
    return {"ok": True, "changed": changed}


@app.post("/api/evaluate")
async def api_evaluate():
    async with engine.lock:
        notes = engine.evaluate(manual=True)
    return {"ok": True, "notes": notes}


@app.post("/api/selftest")
async def api_selftest():
    return await engine.selftest()


@app.post("/api/refresh_traders")
async def api_refresh():
    if engine.refreshing:
        return {"ok": False, "msg": "läuft schon"}
    asyncio.create_task(engine.refresh_traders())
    return {"ok": True}


@app.post("/api/adopt")
async def api_adopt(body: dict = Body(...)):
    async with engine.lock:
        ok = engine.adopt_variant(body.get("id"))
    return {"ok": ok}


@app.post("/api/reset")
async def api_reset(body: dict = Body(...)):
    if body.get("confirm") != "RESET":
        raise HTTPException(400, "Bestätigung fehlt")
    async with engine.lock:
        engine.reset(keep_learning=bool(body.get("keep_learning", True)))
    return {"ok": True}


if __name__ == "__main__":
    print(f"\n  Copy-Trading Bot (Spielgeld) läuft auf  http://localhost:{C.PORT}\n")
    uvicorn.run(app, host=C.HOST, port=C.PORT, log_level="warning")
