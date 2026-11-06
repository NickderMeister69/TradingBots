"""SMA-Kreuz-Bot – genau die Strategie aus Nicks Video (21.10.2026), als Spielgeld-Vergleich.

Regeln wie im Video:
- Bitcoin, 5-Minuten-Kerzen, schneller Durchschnitt SMA 9, langsamer SMA 21
- Kreuzt der schnelle den langsamen nach oben ("Golden Cross") -> mit allem Geld kaufen
- Kreuzt er nach unten ("Death Cross") -> alles verkaufen
- Prueft jede Minute, handelt nur auf abgeschlossenen Kerzen
- 0,1 % Gebuehr + 0,05 % Slippage pro Trade
Unterschied zum Video: Kurse von Binance (wie die anderen Bots) statt CoinGecko, Startgeld 5000 wie alle Bots.
"""
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import urllib.request

from .board import TF_NAME, TF_SEC, EquityLog, countdown, hold_curve, iso_to_ts, thin

DEFAULTS = {"symbol": "BTC/USDT", "timeframe": "5m", "fast": 9, "slow": 21, "fee_pct": 0.1, "slippage_pct": 0.05,
            "start": 5000.0, "check_seconds": 60}
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def de(x, d=2):
    """Zahl im deutschen Format: 83.193,07"""
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def sma(values, n):
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


class SmaBot:
    def __init__(self, data_dir="data_sma", notify=None, port=8017, name="SMA-Kreuz (Video)"):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.notify, self.port, self.name = notify, port, name
        self.lock = threading.RLock()
        self.cfg = {**DEFAULTS, **self._read("settings.json", {})}
        st = self._read("state.json", {})
        self.cash = float(st.get("cash", self.cfg["start"]))
        self.qty = float(st.get("qty", 0.0))
        self.cost = float(st.get("cost", 0.0))           # was der offene Kauf gekostet hat (inkl. Gebuehr)
        self.opened_at = st.get("opened_at")
        self.entry_px = st.get("entry_px")
        self.trades = st.get("trades", [])
        self.log = st.get("log", [])
        self.since = st.get("since") or now_iso()
        self.last_candle = st.get("last_candle")
        self.last_cross = st.get("last_cross")
        self.paused = bool(st.get("paused", False))
        self.fees = float(st.get("fees", 0.0))
        self.price = None
        self.candles = []
        self.last_check = 0.0
        self.error = None
        self.eqlog = EquityLog(self.dir / "board_equity.json")
        if not st:
            self._log(f"Bot gestartet mit {self.cfg['start']:,.0f} USDT Spielgeld. Wartet auf das nächste Kreuz.".replace(",", "."))
            self.save()

    # ---- Dateien
    def _read(self, name, default):
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def save(self):
        st = {"cash": self.cash, "qty": self.qty, "cost": self.cost, "opened_at": self.opened_at, "entry_px": self.entry_px,
              "trades": self.trades[-500:], "log": self.log[-300:], "since": self.since, "last_candle": self.last_candle,
              "last_cross": self.last_cross, "paused": self.paused, "fees": self.fees}
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / "state.json")
        (self.dir / "settings.json").write_text(json.dumps(self.cfg, indent=1), encoding="utf-8")

    def _log(self, msg, kind="info"):
        entry = {"t": datetime.now().strftime("%H:%M:%S"), "kind": kind, "msg": msg}
        self.log = (self.log + [entry])[-300:]
        try:
            print(entry["t"], msg, flush=True)
        except (OSError, ValueError):
            pass
        if kind == "trade" and self.notify:
            self.notify.send(msg)

    # ---- Rechnen
    def equity(self):
        return self.cash + self.qty * (self.price or self.entry_px or 0)

    def _fetch(self):
        tf = self.cfg["timeframe"]
        sym = self.cfg["symbol"].replace("/", "")
        raw = [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
               for k in self._get(f"/api/v3/klines?symbol={sym}&interval={tf}&limit={max(300, self.cfg['slow'] + 50)}")]
        tf_ms = TF_SEC[tf] * 1000
        now_ms = time.time() * 1000
        closed = [k for k in raw if k[0] + tf_ms <= now_ms]       # nur abgeschlossene Kerzen
        self.candles = closed
        t = self._get(f"/api/v3/ticker/24hr?symbol={sym}")
        self.price = float(t["lastPrice"])
        self.change24 = float(t.get("priceChangePercent") or 0.0)
        return closed

    @staticmethod
    def _get(path):
        """Oeffentliche Binance-Kurse (ohne Konto). Zweite Adresse als Ersatz."""
        err = None
        for host in ("https://api.binance.com", "https://data-api.binance.vision"):
            try:
                with urllib.request.urlopen(host + path, timeout=15) as r:
                    return json.loads(r.read().decode())
            except Exception as e:  # noqa: BLE001
                err = e
        raise RuntimeError(f"Binance nicht erreichbar ({err})")

    def tick(self):
        with self.lock:
            closed = self._fetch()
            self.last_check = time.time()
            if len(closed) < self.cfg["slow"] + 2:
                return
            closes = [k[4] for k in closed]
            f, s = sma(closes, self.cfg["fast"]), sma(closes, self.cfg["slow"])
            last_ts = closed[-1][0]
            if self.last_candle == last_ts:
                return                                         # diese Kerze schon ausgewertet
            first_run = self.last_candle is None
            self.last_candle = last_ts
            up_now, up_before = f[-1] > s[-1], f[-2] > s[-2]
            if up_now == up_before or first_run:
                self.save()
                return
            kind = "golden" if up_now else "death"
            px = closes[-1]
            self.last_cross = {"kind": kind, "ts": last_ts / 1000, "price": px}
            fee, slip = self.cfg["fee_pct"] / 100, self.cfg["slippage_pct"] / 100
            name = "Golden Cross" if up_now else "Death Cross"
            if self.paused:
                self._log(f"{name}: schneller SMA({self.cfg['fast']}) hat den langsamen SMA({self.cfg['slow']}) "
                          f"{'nach oben' if up_now else 'nach unten'} gekreuzt – Handel ist pausiert, nichts gemacht.", "warn")
            elif up_now and self.qty == 0 and self.cash > 1:
                buy_px = self.price * (1 + slip)
                f_paid = self.cash * fee
                self.qty = (self.cash - f_paid) / buy_px
                self.cost, self.cash, self.entry_px, self.opened_at = self.cash, 0.0, buy_px, now_iso()
                self.fees += f_paid
                self._log(f"📈 {name}: SMA({self.cfg['fast']}) kreuzt SMA({self.cfg['slow']}) nach oben. "
                          f"KAUF {de(self.qty, 6)} BTC zu {de(buy_px)} (Gebühr {de(f_paid)}).", "trade")
            elif (not up_now) and self.qty > 0:
                sell_px = self.price * (1 - slip)
                gross = self.qty * sell_px
                f_paid = gross * fee
                self.cash = gross - f_paid
                pnl = self.cash - self.cost
                self.fees += f_paid
                self.trades.append({"symbol": self.cfg["symbol"], "side": "long", "entry": self.entry_px, "exit": sell_px,
                                    "amount": self.qty, "pnl": round(pnl, 2), "reason": name,
                                    "opened_at": self.opened_at, "closed_at": now_iso()})
                self._log(f"{'✅' if pnl >= 0 else '❌'} {name}: SMA({self.cfg['fast']}) kreuzt SMA({self.cfg['slow']}) nach unten. "
                          f"VERKAUF zu {de(sell_px)}. Ergebnis {'+' if pnl >= 0 else ''}{de(pnl)} USDT.", "trade")
                self.qty, self.cost, self.entry_px, self.opened_at = 0.0, 0.0, None, None
            else:
                self._log(f"{name} – passt zur aktuellen Position, nichts zu tun.")
            self.save()

    def run(self):
        self._log(f"Prüft alle {self.cfg['check_seconds']} s die {TF_NAME.get(self.cfg['timeframe'], self.cfg['timeframe'])}-Kerzen "
                  f"von {self.cfg['symbol']} (SMA {self.cfg['fast']}/{self.cfg['slow']}).")
        while True:
            try:
                self.tick()
                self.error = None
                self.eqlog.add(self.equity(), since_ts=iso_to_ts(self.since))
            except Exception as e:  # noqa: BLE001
                if str(e) != self.error:
                    self._log(f"Kurse holen hat nicht geklappt: {e}", "error")
                self.error = str(e)
            time.sleep(self.cfg["check_seconds"])

    # ---- Steuerung
    def pause(self):
        with self.lock:
            self.paused = True
            self._log("Handel pausiert (über das Dashboard).", "warn")
            self.save()

    def resume(self):
        with self.lock:
            self.paused = False
            self._log("Handel läuft wieder.")
            self.save()

    def update_settings(self, d):
        with self.lock:
            ch = []
            if d.get("timeframe") in TIMEFRAMES and d["timeframe"] != self.cfg["timeframe"]:
                self.cfg["timeframe"] = d["timeframe"]
                ch.append(f"Kerzen {d['timeframe']}")
            for k, lo, hi, kind in (("fast", 2, 200, int), ("slow", 3, 400, int), ("fee_pct", 0, 1, float), ("slippage_pct", 0, 1, float)):
                if k in d:
                    try:
                        v = kind(d[k])
                    except (TypeError, ValueError):
                        continue
                    v = max(lo, min(hi, v))
                    if v != self.cfg[k]:
                        self.cfg[k] = v
                        ch.append(f"{k}={v}")
            if self.cfg["fast"] >= self.cfg["slow"]:
                self.cfg["fast"] = max(2, self.cfg["slow"] - 1)
            if ch:
                self.last_candle = None                    # mit neuen Einstellungen frisch anfangen
                self._log("Einstellungen geändert: " + ", ".join(ch), "warn")
                self.save()
            return ch

    def reset(self, start):
        with self.lock:
            start = max(100.0, min(1_000_000.0, float(start or self.cfg["start"])))
            self.cfg["start"], self.cash, self.qty, self.cost = start, start, 0.0, 0.0
            self.entry_px = self.opened_at = None
            self.trades, self.fees, self.since, self.last_cross = [], 0.0, now_iso(), None
            self.eqlog.points = []
            self._log(f"Spielgeld-Konto zurückgesetzt auf {start:,.0f} USDT.".replace(",", "."), "warn")
            self.save()

    # ---- Anzeige
    def overview(self):
        with self.lock:
            eq, start = self.equity(), self.cfg["start"]
            pos = []
            if self.qty > 0:
                u = self.qty * (self.price or 0) - self.cost
                pos.append({"symbol": self.cfg["symbol"], "side": "long", "entry": self.entry_px, "price": self.price,
                            "pnl": u, "r": None, "units": 1, "max_units": 1, "opened_at": self.opened_at})
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return {"name": self.name, "port": self.port, "mode": "paper", "exchange": "Binance-Kurse", "quote": "USDT",
                    "timeframe": self.cfg["timeframe"], "risk": 1.0, "units": 1,
                    "meta": f"{self.port} · Video-Strategie · SMA {self.cfg['fast']}/{self.cfg['slow']} · {self.cfg['timeframe']} · alles rein/raus",
                    "start": start, "deposits": 0.0, "realized": sum(t["pnl"] for t in self.trades),
                    "unrealized": pos[0]["pnl"] if pos else 0.0, "konto": eq, "gewinn": eq - start,
                    "pct": (eq / start - 1) * 100 if start else 0, "trades": len(self.trades),
                    "wins": sum(1 for t in self.trades if t["pnl"] > 0),
                    "today": sum(t["pnl"] for t in self.trades if str(t["closed_at"])[:10] == today),
                    "status": "Pause" if self.paused else ("Fehler" if self.error else "läuft"), "since": self.since,
                    "positions": pos, "last": [], "updated": datetime.now().strftime("%H:%M:%S")}

    def board(self, symbol=None):
        with self.lock:
            o = self.overview()
            c = self.cfg
            eq, start = o["konto"], c["start"]
            since_ts = iso_to_ts(self.since)
            hcurve, hold_now = hold_curve(since_ts, start, self.price)
            closes = [k[4] for k in self.candles]
            f = sma(closes, c["fast"])[-1] if closes else None
            s = sma(closes, c["slow"])[-1] if closes else None
            up = f is not None and s is not None and f > s
            if self.qty > 0:
                title, text, kind = "Gekauft – wartet auf das Death Cross", "Verkauft alles, sobald der schnelle SMA unter den langsamen fällt.", "up"
            elif up:
                title, text, kind = "Schneller über langsamem (Aufwärts)", "Im Bargeld. Kauft erst beim nächsten Golden Cross.", "up"
            else:
                title, text, kind = "Schneller unter langsamem (Abwärts)", "Im Bargeld. Kauft, wenn der schnelle SMA den langsamen nach oben kreuzt.", "down"
            gap = (f / s - 1) * 100 if f and s else None
            lc = self.last_cross
            last_cross = (f"{'Golden Cross ▲' if lc['kind'] == 'golden' else 'Death Cross ▼'} · "
                          f"{datetime.fromtimestamp(lc['ts']):%d.%m %H:%M} · {lc['price']:,.0f}".replace(",", ".")) if lc else "noch keins"
            rows = [[f"Schneller SMA ({c['fast']})", f], [f"Langsamer SMA ({c['slow']})", s],
                    ["Abstand", f"{gap:+.3f} %".replace(".", ",") if gap is not None else "–"], ["Letztes Kreuz", last_cross]]
            tf_sec = TF_SEC[c["timeframe"]]
            curve = [(p[0], p[1]) for p in self.eqlog.points]
            steps, run = [], start
            for t in self.trades:
                run += t["pnl"]
                steps.append((iso_to_ts(t["closed_at"]), run))
            from .board import merge_curve
            curve = merge_curve(steps, curve, since_ts, start, eq)
            n, wins = o["trades"], o["wins"]
            if self.qty > 0:
                ptext, psub = f"{self.qty:.6f} BTC", f"gekauft zu {self.entry_px:,.2f} · offen {o['unrealized']:+.2f} USDT".replace(",", "X").replace(".", ",").replace("X", ".")
            else:
                ptext, psub = "Bargeld (flat)", f"{self.cash:,.2f} USDT bereit zum Kauf".replace(",", "X").replace(".", ",").replace("X", ".")
            last_t = self.trades[-1] if self.trades else None
            return {
                "name": self.name, "port": self.port, "badge": "SPIELGELD",
                "subtitle": f"{c['symbol']} · Binance-Kurse · {TF_NAME.get(c['timeframe'], c['timeframe'])}-Kerzen · Strategie aus dem Video",
                "note": "Genau die Strategie aus dem Video. Achtung: Im Backtest mit 5-Minuten-Kerzen haben die Gebühren das Konto stark aufgefressen.",
                "status": o["status"], "paused": self.paused, "quote": "USDT",
                "cards": {"price": {"label": "BTC Kurs", "value": self.price, "change": getattr(self, "change24", None)},
                          "equity": {"value": eq, "start": start, "pct": o["pct"]},
                          "hold": {"value": hold_now, "diff": (eq - hold_now) if hold_now else None},
                          "position": {"text": ptext, "sub": psub},
                          "closed": {"n": n, "wins": wins, "fees": round(self.fees, 2)}},
                "symbols": [c["symbol"]], "symbol": c["symbol"],
                "signal": {"title": title, "text": text, "kind": kind, "rows": rows,
                           "candle_close": countdown(tf_sec), "next_check": (self.last_check or time.time()) + c["check_seconds"],
                           "tf_sec": tf_sec, "check_sec": c["check_seconds"],
                           "last": (f"{last_t['pnl']:+.2f} USDT ({str(last_t['closed_at'])[5:16].replace('T', ' ')})" if last_t else "noch keiner"),
                           "rules": [f"Kaufen (alles), wenn der schnelle SMA({c['fast']}) den langsamen SMA({c['slow']}) nach oben kreuzt.",
                                     "Alles verkaufen, wenn er wieder nach unten kreuzt.",
                                     f"Prüft alle {c['check_seconds']} s, entscheidet nur bei abgeschlossenen Kerzen.",
                                     f"Jeder Kauf/Verkauf kostet {str(c['fee_pct']).replace('.', ',')} % Gebühr + {str(c['slippage_pct']).replace('.', ',')} % Slippage.",
                                     "Kein Stop-Loss, kein Short – genau wie im Video."]},
                "curve": [[round(t), round(v, 2)] for t, v in thin(curve)],
                "hold_curve": [[round(t), round(v, 2)] for t, v in thin(hcurve)],
                "activity": list(reversed(self.log[-80:])),
                "trades": [{"time": t["closed_at"], "symbol": t["symbol"], "side": "long", "entry": t["entry"], "exit": t["exit"],
                            "amount": t["amount"], "pnl": t["pnl"], "reason": t["reason"]} for t in self.trades[-60:]][::-1],
                "settings": [{"key": "timeframe", "label": "Kerzen-Größe", "value": c["timeframe"], "options": TIMEFRAMES},
                             {"key": "fast", "label": "Schneller SMA", "value": c["fast"], "step": 1},
                             {"key": "slow", "label": "Langsamer SMA", "value": c["slow"], "step": 1},
                             {"key": "fee_pct", "label": "Gebühr %", "value": c["fee_pct"], "step": 0.01},
                             {"key": "slippage_pct", "label": "Slippage %", "value": c["slippage_pct"], "step": 0.01}],
                "can_reset": True, "details_url": None,
            }

    def candle_data(self):
        with self.lock:
            k = list(self.candles)
            c = self.cfg
        if not k:
            return {"symbol": c["symbol"], "timeframe": c["timeframe"], "candles": [], "lines": [], "markers": []}
        closes = [x[4] for x in k]
        f, s = sma(closes, c["fast"]), sma(closes, c["slow"])
        ts = [int(x[0] // 1000) for x in k]
        keep = ts[-300:]
        t0 = keep[0]
        candles = [{"time": int(x[0] // 1000), "open": x[1], "high": x[2], "low": x[3], "close": x[4]} for x in k[-300:]]
        lines = [{"name": f"Schneller SMA ({c['fast']})", "color": "#2563eb", "data": [{"time": t, "value": v} for t, v in zip(ts, f) if v and t >= t0]},
                 {"name": f"Langsamer SMA ({c['slow']})", "color": "#f59e0b", "data": [{"time": t, "value": v} for t, v in zip(ts, s) if v and t >= t0]}]
        markers = []
        tf_sec = TF_SEC[c["timeframe"]]
        for i in range(1, len(ts)):
            if ts[i] < t0 or None in (f[i], s[i], f[i - 1], s[i - 1]):
                continue
            a, b = f[i] > s[i], f[i - 1] > s[i - 1]
            if a != b:
                markers.append({"time": ts[i], "position": "belowBar" if a else "aboveBar", "color": "#16a34a" if a else "#dc2626",
                                "shape": "arrowUp" if a else "arrowDown"})
        for t in self.trades:
            for when, txt, up in ((t["opened_at"], "KAUF", True), (t["closed_at"], f"VERKAUF {t['pnl']:+.0f}", False)):
                w = iso_to_ts(when)
                if w and w >= t0:
                    markers.append({"time": int(w // tf_sec * tf_sec), "position": "belowBar" if up else "aboveBar",
                                    "color": "#7c3aed", "shape": "circle", "text": txt})
        if self.opened_at:
            w = iso_to_ts(self.opened_at)
            if w and w >= t0:
                markers.append({"time": int(w // tf_sec * tf_sec), "position": "belowBar", "color": "#7c3aed", "shape": "circle", "text": "KAUF"})
        markers.sort(key=lambda m: m["time"])
        return {"symbol": c["symbol"], "timeframe": c["timeframe"], "candles": candles, "lines": lines, "markers": markers}
