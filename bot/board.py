"""Neues Dashboard im Stil des Videos (Nick, 21.10.2026) – fuer alle Bots gleich.

Jeder Bot liefert /api/board (Zahlen, Signal, Protokoll, Trades, Konto-Verlauf) und
/api/board/candles?symbol=... (Kerzen + Linien + Markierungen). Die Seite selbst (static/board.html)
ist fuer alle Bots dieselbe. Die alte, ausfuehrliche Seite bleibt unter /klassisch.
"""
import json
import math
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"


def board_html():
    return (STATIC / "board.html").read_text(encoding="utf-8")


def lwc_js():
    return (STATIC / "lwc.js").read_text(encoding="utf-8")


def iso_to_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def num(x, d=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, d) if math.isfinite(x) else None


# ---------------------------------------------------------------- Bitcoin kaufen & halten (Vergleich)
_btc_cache = {}
_btc_lock = threading.Lock()


def _klines(interval, start_ms, limit=1000):
    for host in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            u = f"{host}/api/v3/klines?symbol=BTCUSDT&interval={interval}&startTime={int(start_ms)}&limit={limit}"
            with urllib.request.urlopen(u, timeout=10) as r:
                return [(k[0] / 1000, float(k[1]), float(k[4])) for k in json.loads(r.read().decode())]
        except Exception:  # noqa: BLE001
            continue
    return []


def btc_path(start_ts):
    """[(ts, preis)] seit start_ts, Stundenkerzen (bis ~41 Tage) bzw. 4h-Kerzen. 10 Minuten zwischengespeichert."""
    key = int(start_ts // 60)
    with _btc_lock:
        hit = _btc_cache.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
    span_h = (time.time() - start_ts) / 3600
    interval = "1h" if span_h < 990 else "4h"
    rows = _klines(interval, start_ts * 1000)
    first = _klines("1m", start_ts * 1000, 1)
    path = []
    if first:
        path.append((start_ts, first[0][1]))           # Kurs genau zum Start
    path += [(t, c) for t, _, c in rows if t > start_ts]
    with _btc_lock:
        _btc_cache[key] = (time.time(), path)
    return path


def hold_curve(start_ts, start_value, btc_now=None):
    """Was haette 'Bitcoin kaufen & halten' mit dem Startkapital gebracht? -> ([(ts, wert)], wert_jetzt)."""
    if not start_ts or not start_value:
        return [], None
    path = btc_path(start_ts)
    if not path:
        return [], None
    p0 = path[0][1]
    curve = [(t, start_value * p / p0) for t, p in path]
    now_val = start_value * (btc_now or path[-1][1]) / p0
    curve.append((time.time(), now_val))
    return curve, now_val


# ---------------------------------------------------------------- Konto-Verlauf (alle 5 Minuten)
class EquityLog:
    """Merkt sich den Kontostand alle paar Minuten in einer kleinen JSON-Datei (fuer die Konto-Kurve)."""

    def __init__(self, path, every=300, keep=8000):
        self.path, self.every, self.keep = Path(path), every, keep
        self.points = []
        try:
            self.points = json.loads(self.path.read_text(encoding="utf-8")).get("points", [])
        except (OSError, ValueError):
            pass

    def add(self, equity, since_ts=None, ts=None):
        ts = ts or time.time()
        if since_ts:
            self.points = [p for p in self.points if p[0] >= since_ts]
        if self.points and ts - self.points[-1][0] < self.every:
            return
        if equity is None or not math.isfinite(float(equity)):
            return
        self.points.append([round(ts), round(float(equity), 2)])
        self.points = self.points[-self.keep:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"points": self.points}), encoding="utf-8")
        except OSError:
            pass


def merge_curve(trade_steps, logged, start_ts, start_value, now_equity):
    """Konto-Kurve aus abgeschlossenen Trades (Stufen) + mitgeschriebenen Punkten + jetzt."""
    pts = [(start_ts, start_value)] if start_ts else []
    pts += [(t, v) for t, v in trade_steps if t and (not start_ts or t >= start_ts)]
    pts += [(t, v) for t, v in logged if not start_ts or t >= start_ts]
    pts.append((time.time(), now_equity))
    pts.sort()
    out, last_t = [], None
    for t, v in pts:
        if last_t is not None and t <= last_t:
            t = last_t + 1
        out.append((t, v))
        last_t = t
    return out


def thin(points, max_points=600):
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)] + [points[-1]]


def countdown(tf_sec, now=None):
    now = now or time.time()
    return (math.floor(now / tf_sec) + 1) * tf_sec


# ---------------------------------------------------------------- Eigene Bots (bot/engine.py)
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
TF_NAME = {"5m": "5-Minuten", "15m": "15-Minuten", "1h": "1-Stunden", "4h": "4-Stunden", "1d": "Tages"}


def safe_text(x, quote="€"):
    """+12,30 €  ->  fuer 'sicher' / 'schlimmstenfalls'."""
    s = f"{x:+,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").replace("-", "−")
    return f"{s} {quote}".strip()


def safe_label(x):
    return "Save (so viel bleibt mindestens)" if x >= 0 else "Stoploss (so viel kostet es höchstens)"


def _pct(a, b):
    return (a / b - 1) * 100 if a and b else None


class EngineBoard:
    def __init__(self, engine):
        self.e = engine
        data_dir = Path(engine.cfg.get("data_dir", "data"))
        self.log = EquityLog(data_dir / "board_equity.json")
        self._candles = {}
        threading.Thread(target=self._record_loop, daemon=True).start()

    # ---- Hilfen
    def _btc_symbol(self):
        return f"BTC/{self.e.ex.quote}"

    def _price(self, sym):
        mv = (self.e.market_view.get(sym) or {}).get("price")
        if mv:
            return float(mv)
        try:
            return float(self.e.ex.price(sym))
        except Exception:  # noqa: BLE001
            return None

    def _record_loop(self):
        time.sleep(30)
        while True:
            try:
                o = self.e.overview()
                self.log.add(o.get("konto"), since_ts=iso_to_ts(o.get("since")))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)

    def _rules(self):
        c = self.e.cfg
        bp = {"entry_n": 55, "exit_m": 20, **(c.get("breakout_params") or {})}
        spot = self.e.ex.is_spot
        r = [f"Kaufen (Long), wenn eine Kerze über dem Hoch der letzten {bp['entry_n']} Kerzen schließt "
             f"und der lange Trend (EMA200) steigt."]
        if not spot:
            r.append(f"Short, wenn eine Kerze unter dem Tief der letzten {bp['entry_n']} Kerzen schließt und der Trend fällt.")
        r.append(f"Raus, wenn der Kurs das Tief (bei Short: das Hoch) der letzten {bp['exit_m']} Kerzen bricht – "
                 f"kein festes Gewinn-Ziel.")
        r.append("Verlust-Grenze: 2 × die normale Schwankung (ATR) vom Einstieg.")
        if c.get("btc_filter"):
            r.append("Nur in Bitcoins Richtung (Bitcoin über/unter seiner EMA200).")
        if bp.get("ichimoku"):
            r.append("Zusätzlich muss Ichimoku zustimmen (Kurs über/unter der Wolke).")
        if bp.get("skip_weekend"):
            r.append("Kein neuer Einstieg am Wochenende.")
        if bp.get("volume_mult"):
            r.append(f"Nur bei hohem Umsatz (mind. {bp['volume_mult']:g}× Schnitt).".replace(".", ",", 1))
        units = int((c.get("pyramid") or {}).get("max_units") or 1)
        r.append(f"Risiko pro Trade {c['risk_per_trade'] * 100:g} % vom Konto".replace(".", ",")
                 + (f", Nachkaufen bis {units} Teile." if units > 1 else "."))
        if c.get("order_type") == "limit":
            r.append("Limit-Orders (niedrigere Gebühr), Stops immer sofort.")
        return r

    # ---- /api/board
    def board(self, symbol=None):
        e = self.e
        o = e.overview()
        c = e.cfg
        quote = o.get("quote") or ""
        syms = e.active_symbols()
        btc = self._btc_symbol()
        sym = symbol if symbol in syms else (btc if btc in syms else (syms[0] if syms else btc))
        btc_now = self._price(btc)
        start, konto = float(o.get("start") or 0), float(o.get("konto") or 0)
        since_ts = iso_to_ts(o.get("since"))
        hcurve, hold_now = hold_curve(since_ts, start, btc_now)
        period = e._period_trades()
        fees = 0.0
        steps, run = [], start
        for t in period:
            run += float(t.get("pnl") or 0)
            steps.append((iso_to_ts(t.get("closed_at")), run))
            fees += float(t.get("entry", 0) or 0) * float(t.get("amount", 0) or 0) * float(c.get("fee_rate", 0.0005)) * 2
        curve = merge_curve(steps, [(p[0], p[1]) for p in self.log.points], since_ts, start, konto)
        pos = o.get("positions") or []
        if pos:
            longs = sum(1 for p in pos if p["side"] == "long")
            pos_text = f"{len(pos)} Trade{'s' if len(pos) != 1 else ''} offen"
            pos_sub = f"{longs} Long / {len(pos) - longs} Short · offen {o.get('unrealized', 0):+.2f} {quote}"
            if o.get("safe") is not None:
                pos_sub += f" · {'Save' if o['safe'] >= 0 else 'Stoploss'} {safe_text(o['safe'], quote)}"
        else:
            pos_text, pos_sub = "Kein Trade (Bargeld)", f"{konto:,.2f} {quote} bereit".replace(",", "X").replace(".", ",").replace("X", ".")
        n, wins = int(o.get("trades") or 0), int(o.get("wins") or 0)
        tf = c.get("timeframe", "1h")
        tf_sec = TF_SEC.get(tf, 3600)
        mv = e.market_view.get(sym) or {}
        price = mv.get("price") or self._price(sym)
        rows = [["Kurs", price]]
        if mv.get("bo_up"):
            rows.append(["Ausbruch nach oben ab", mv["bo_up"], _pct(mv["bo_up"], price)])
        if mv.get("bo_down"):
            rows.append(["Ausbruch nach unten ab", mv["bo_down"], _pct(mv["bo_down"], price)])
        if mv.get("trend_long"):
            rows.append(["Langer Trend (EMA200)", mv["trend_long"]])
        bt = None
        if c.get("btc_filter"):
            try:
                bt = e._btc_trend()          # pro Kerze zwischengespeichert, kostet kaum Abfragen
            except Exception:  # noqa: BLE001
                bt = (getattr(e, "_btc_cache", None) or {}).get("trend")
        if c.get("btc_filter"):
            rows.append(["Bitcoin-Filter", "nur Long" if bt == 1 else ("nur Short" if bt == -1 else "unbekannt – wartet")])
        open_here = next((p for p in pos if p["symbol"] == sym), None)
        if open_here:
            kind = "up" if open_here["side"] == "long" else "down"
            title = f"Im Trade: {'Long' if kind == 'up' else 'Short'} seit {str(open_here.get('opened_at') or '')[5:16].replace('T', ' ')}"
            text = (f"Einstieg {open_here['entry']:.6g}, gerade {open_here['pnl']:+.2f} {quote}. "
                    f"Raus, wenn eine Kerze jenseits der wandernden Linie schließt – oder sofort an der festen Verlust-Grenze.")
            if open_here.get("exit"):
                rows.insert(1, ["Raus spätestens bei (wandert mit)", open_here["exit"], _pct(open_here["exit"], price)])
            if open_here.get("stop"):
                rows.insert(2 if open_here.get("exit") else 1, ["Feste Verlust-Grenze", open_here["stop"], _pct(open_here["stop"], price)])
            if open_here.get("safe") is not None:
                rows.insert(1, [safe_label(open_here["safe"]), safe_text(open_here["safe"], quote)])
        else:
            kind = "flat"
            title = "Wartet auf einen Ausbruch"
            text = "Kein Trade in diesem Coin. Der Bot steigt ein, sobald eine Kerze außerhalb des Kanals schließt."
        last_t = period[-1] if period else None
        last = (f"{last_t['symbol']} {last_t['side']} {last_t['pnl']:+.2f} {quote} "
                f"({str(last_t['closed_at'])[5:16].replace('T', ' ')})") if last_t else "noch keiner"
        trades = [{"time": t.get("closed_at"), "symbol": t["symbol"], "side": t["side"], "entry": t["entry"],
                   "exit": t["exit"], "amount": t["amount"], "pnl": t["pnl"], "reason": t.get("reason")}
                  for t in period[-60:]][::-1]
        mode = c["mode"]
        return {
            "name": o.get("name"), "port": c.get("dashboard_port"),
            "badge": {"paper": "SPIELGELD", "testnet": "DEMO-KONTO", "live": "ECHTGELD"}.get(mode, mode.upper()),
            "subtitle": f"{e.ex.label} · {TF_NAME.get(tf, tf)}-Kerzen · {len(syms)} Coins",
            "status": o.get("status"), "paused": bool(e.paused), "quote": quote,
            "cards": {
                "price": {"label": f"{btc.split('/')[0]} Kurs", "value": btc_now},
                "equity": {"value": konto, "start": start, "pct": o.get("pct")},
                "hold": {"value": hold_now, "diff": (konto - hold_now) if hold_now else None},
                "position": {"text": pos_text, "sub": pos_sub},
                "closed": {"n": n, "wins": wins, "fees": round(fees, 2)},
            },
            "symbols": syms, "symbol": sym,
            "signal": {"title": title, "text": text, "kind": kind, "rows": rows,
                       "candle_close": countdown(tf_sec), "next_check": countdown(int(c.get("loop_seconds", 30))),
                       "last": last, "rules": self._rules(), "tf_sec": tf_sec,
                       "check_sec": int(c.get("loop_seconds", 30))},
            "curve": [[round(t), round(v, 2)] for t, v in thin(curve)],
            "hold_curve": [[round(t), round(v, 2)] for t, v in thin(hcurve)],
            "activity": [{"t": x.get("t"), "kind": x.get("kind"), "msg": x.get("msg")} for x in e.log[-80:]][::-1],
            "trades": trades,
            "settings": None, "details_url": "/klassisch", "all_actions": True,
        }

    # ---- /api/board/candles
    def candles(self, symbol):
        e = self.e
        syms = e.active_symbols()
        sym = symbol if symbol in syms else (self._btc_symbol())
        hit = self._candles.get(sym)
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        from .analysis import to_df
        from .strategy import add_breakout_levels
        tf = e.cfg.get("timeframe", "1h")
        try:
            raw = e.ex.ohlcv(sym, tf, limit=500)
        except Exception:  # noqa: BLE001
            raw = e.ex.ohlcv(sym, tf, limit=300)
        df = add_breakout_levels(to_df(raw), e.cfg.get("breakout_params"))
        ts = [int(t.timestamp()) for t in df["ts"]]
        candles = [{"time": t, "open": float(o), "high": float(h), "low": float(l), "close": float(cl)}
                   for t, o, h, l, cl in zip(ts, df["open"], df["high"], df["low"], df["close"])][-300:]
        keep = set(c["time"] for c in candles)

        def line(col):
            return [{"time": t, "value": float(v)} for t, v in zip(ts, df[col]) if t in keep and math.isfinite(float(v))]
        lines = [{"name": "Ausbruch oben", "color": "#16a34a", "dashed": True, "data": line("bo_hi")},
                 {"name": "Ausbruch unten", "color": "#dc2626", "dashed": True, "data": line("bo_lo")},
                 {"name": "EMA200", "color": "#f59e0b", "data": line("ema_long")}]
        if e.ex.is_spot:
            lines.pop(1)
        markers = []
        t0 = candles[0]["time"] if candles else 0
        tf_sec = TF_SEC.get(tf, 3600)
        for t in e._period_trades():
            if t["symbol"] != sym:
                continue
            for when, txt, is_open in ((t.get("opened_at"), "Kauf" if t["side"] == "long" else "Short", True),
                                       (t.get("closed_at"), f"raus {t['pnl']:+.0f}", False)):
                w = iso_to_ts(when)
                if not w or w < t0:
                    continue
                up = (t["side"] == "long") == is_open
                markers.append({"time": int(w // tf_sec * tf_sec), "position": "belowBar" if up else "aboveBar",
                                "color": "#16a34a" if up else "#dc2626", "shape": "arrowUp" if up else "arrowDown",
                                "text": txt})
        p_here = e.positions.get(sym)
        if p_here and candles:
            for name, val, col in (("Raus spätestens bei", p_here.get("exit_level"), "#7c3aed"),
                                   ("Feste Verlust-Grenze", p_here.get("stop"), "#dc2626")):
                if val:
                    lines.append({"name": name, "color": col, "dashed": name.startswith("Feste"), "width": 1,
                                  "data": [{"time": candles[0]["time"], "value": float(val)},
                                           {"time": candles[-1]["time"], "value": float(val)}]})
        for p in e.positions.values():
            if p["symbol"] == sym:
                w = iso_to_ts(p.get("opened_at"))
                if w and w >= t0:
                    up = p["side"] == "long"
                    markers.append({"time": int(w // tf_sec * tf_sec), "position": "belowBar" if up else "aboveBar",
                                    "color": "#2563eb", "shape": "arrowUp" if up else "arrowDown", "text": "offen"})
        markers.sort(key=lambda m: m["time"])
        out = {"symbol": sym, "timeframe": tf, "candles": candles, "lines": lines, "markers": markers}
        self._candles[sym] = (time.time(), out)
        return out
