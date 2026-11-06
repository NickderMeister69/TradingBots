"""Lernen aus Erfahrung.

Der Bot wertet seine eigenen Trades und Backtests aus und macht daraus VORSCHLAEGE.
Er aendert NIE selbst etwas: Erst wenn du im Dashboard auf "Uebernehmen" klickst,
wird eine Regel aktiv. Jede Regel laesst sich einzeln zuruecknehmen.

Drei Quellen:
  1. Merkmale eigener Trades   (z. B. "Short-Trades bei Trendfolge verlieren")
  2. Coin-Gedaechtnis          (z. B. "AKE: 2 Verluste in Folge -> 3 Tage meiden")
  3. Parameter-Test            (Backtest-Varianten, gepruefte auf ungesehenen Daten)

Bremsen gegen Zufallstreffer:
  - Eine Regel wird erst ab `min_trades` Trades pro Merkmal vorgeschlagen.
  - Der Durchschnitt wird mit `prior_trades` "Phantom-Trades" mit 0 R verduennt
    (Shrinkage). Wenige Ausreisser koennen so keine Regel erzwingen.
  - Parameter werden nur vorgeschlagen, wenn sie im Trainings- UND im
    Testzeitraum (den die Optimierung nie gesehen hat) besser sind.
"""
from __future__ import annotations

import hashlib
import json
import random
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analysis import add_indicators, to_df
from .backtest import run_backtest
from .strategy import DEFAULT_PARAMS, params_from

LEARN_FILE = Path("data/learning.json")

DEFAULTS = {
    "auto_apply": True,          # Vorschlaege automatisch uebernehmen (im Dashboard abschaltbar)
    "min_trades": 20,            # Trades pro Merkmal, bevor eine Regel vorgeschlagen wird
    "prior_trades": 10,          # Skepsis: so viele 0-R-Trades werden gedanklich dazugerechnet
    "block_below_r": -0.25,      # Ø R darunter -> "Setup sperren" vorschlagen
    "reduce_below_r": -0.10,     # Ø R darunter -> "Risiko halbieren" vorschlagen
    "boost_above_r": 0.40,       # Ø R darueber -> "Risiko x1.25" vorschlagen
    "coin_losses_in_row": 2,     # so viele Verluste in Folge auf einem Coin ...
    "coin_lookback_days": 7,     # ... innerhalb dieser Tage ...
    "coin_cooldown_hours": 72,   # ... -> Coin so lange meiden (Vorschlag)
    "tune_every_hours": 24,      # Parameter-Test einmal pro Tag (passt Einstellungen an die aktuelle Marktphase an)
    "tune_days": 30,             # Kursdaten fuer den Parameter-Test
    "tune_test_share": 0.34,     # letzter Anteil = Testzeitraum (ungesehen)
    "tune_max_coins": 6,
    "tune_combos": 40,           # zufaellige Varianten pro Lauf
    "tune_min_test_trades": 20,
    "tune_min_gain_r": 3.0,      # Variante muss im Test mind. so viele R mehr bringen
}

DIM_LABELS = {
    "setup": "Strategie & Richtung",
    "regime": "Marktlage beim Einstieg",
    "adx": "Trendstaerke (ADX)",
    "vol": "Volatilitaet pro Kerze",
    "session": "Tageszeit (UTC)",
    "hebel": "Hebel",
    "toptrader": "Top-Trader (Binance)",
    "crowd": "Alle Trader (Masse)",
}
REGIME_LABELS = {"trend_up": "Aufwaertstrend", "trend_down": "Abwaertstrend", "range": "Seitwaerts",
                 "chaos": "Zu volatil", "neutral": "Unklar"}

TUNE_GRID = {
    "trend_adx_min": [20.0, 25.0, 30.0],
    "trend_stop_atr": [1.5, 2.0, 2.5, 3.0],
    "trend_tp_atr": [2.0, 3.0, 4.0, 5.0],
    "range_stop_atr": [1.0, 1.5, 2.0],
    "breakeven_at_r": [0.75, 1.0, 1.5],
    "trail_at_r": [1.5, 2.0, 3.0],
}
PARAM_LABELS = {
    "trend_adx_min": "Trendfolge ab ADX", "trend_stop_atr": "Stop Trendfolge (ATR)",
    "trend_tp_atr": "Ziel Trendfolge (ATR)", "range_stop_atr": "Stop Scalping (ATR)",
    "breakeven_at_r": "Break-even ab (R)", "trail_at_r": "Trailing ab (R)",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def settings(cfg: dict) -> dict:
    s = dict(DEFAULTS)
    s.update(cfg.get("learning") or {})
    return s


# =====================================================================
#  Merkmale eines Trades
# =====================================================================
def entry_context(row, regime: str, leverage: int, scanner_row: dict | None, ratio: dict | None = None) -> dict:
    """Wird beim Einstieg gespeichert, damit spaeter ausgewertet werden kann, WAS funktioniert."""
    ctx = {
        "regime": regime,
        "adx": round(float(row["adx"]), 1),
        "rsi": round(float(row["rsi"]), 1),
        "vol_pct": round(float(row["vol_pct"]), 5),
        "leverage": int(leverage),
    }
    if scanner_row:
        ctx["change24"] = scanner_row.get("change24")
        ctx["scan_score"] = scanner_row.get("score")
    if ratio:
        ctx["top_long"] = ratio.get("top_long")
        ctx["crowd_long"] = ratio.get("crowd_long")
    return ctx


def _alignment(long_share: float, side: str, with_label: str, against_label: str, mixed_label: str) -> str:
    """Wie viel % stehen auf UNSERER Seite?"""
    ours = long_share if side == "long" else 1 - long_share
    return with_label if ours >= 0.55 else against_label if ours <= 0.45 else mixed_label


def features(t: dict) -> dict:
    """Merkmal -> Auspraegung. Fuer alte Trades ohne ctx nur die ableitbaren Merkmale."""
    f = {}
    strat = "Trendfolge" if str(t.get("strategy", "")).startswith("Trend") else "Scalping"
    f["setup"] = f"{strat} {'Long' if t.get('side') == 'long' else 'Short'}"
    try:
        h = int(str(t["opened_at"])[11:13])
        f["session"] = ("Asien 0-7" if h < 7 else "Europa 7-13" if h < 13 else "USA 13-21" if h < 21 else "Spaet 21-24")
    except (KeyError, ValueError):
        pass
    lev = int(t.get("leverage") or 1)
    f["hebel"] = "1x" if lev <= 1 else "2-3x" if lev <= 3 else "4x+"
    ctx = t.get("ctx") or {}
    if "regime" in ctx:
        f["regime"] = REGIME_LABELS.get(ctx["regime"], ctx["regime"])
    if "adx" in ctx:
        a = ctx["adx"]
        f["adx"] = "unter 20" if a < 20 else "20-25" if a < 25 else "25-35" if a < 35 else "ueber 35"
    if "vol_pct" in ctx:
        v = ctx["vol_pct"] * 100
        f["vol"] = "unter 0.5 %" if v < 0.5 else "0.5-1 %" if v < 1 else "1-2 %" if v < 2 else "ueber 2 %"
    side = t.get("side", "long")
    if ctx.get("top_long") is not None:
        f["toptrader"] = _alignment(ctx["top_long"], side, "mit Top-Tradern", "gegen Top-Trader", "Top-Trader gemischt")
    if ctx.get("crowd_long") is not None:
        f["crowd"] = _alignment(ctx["crowd_long"], side, "mit der Masse", "gegen die Masse", "Masse gemischt")
    return f


def r_multiple(t: dict, fallback_risk: float) -> float:
    """Ergebnis in R (1 R = das geplante Risiko des Trades). Macht Trades vergleichbar."""
    if t.get("r_mult") is not None:
        return float(t["r_mult"])
    risk = t.get("risk_usdt") or fallback_risk
    return float(t["pnl"]) / risk if risk else 0.0


def fallback_risk(trades: list[dict], cfg: dict, equity: float | None) -> float:
    """Fuer alte Trades ohne gespeichertes Risiko: typischer Verlust bei 'Stop erreicht'."""
    stops = [abs(t["pnl"]) for t in trades if t.get("reason") == "Stop erreicht" and t["pnl"] < 0]
    if len(stops) >= 3:
        return statistics.median(stops)
    return max(1.0, (equity or 1000.0) * cfg.get("risk_per_trade", 0.01))


# =====================================================================
#  Statistik
# =====================================================================
def feature_stats(trades: list[dict], cfg: dict, equity: float | None) -> list[dict]:
    s = settings(cfg)
    fb = fallback_risk(trades, cfg, equity)
    buckets: dict[tuple, dict] = {}
    for t in trades:
        r = r_multiple(t, fb)
        for dim, val in features(t).items():
            b = buckets.setdefault((dim, val), {"dim": dim, "value": val, "n": 0, "wins": 0, "pnl": 0.0, "sum_r": 0.0})
            b["n"] += 1
            b["wins"] += t["pnl"] > 0
            b["pnl"] += t["pnl"]
            b["sum_r"] += r
    out = []
    for b in buckets.values():
        b["avg_r"] = b["sum_r"] / b["n"]
        b["shrunk_r"] = b["sum_r"] / (b["n"] + s["prior_trades"])   # skeptischer Wert
        b["win_rate"] = b["wins"] / b["n"] * 100
        b["ready"] = b["n"] >= s["min_trades"]
        b["dim_label"] = DIM_LABELS.get(b["dim"], b["dim"])
        b["pnl"] = round(b["pnl"], 2)
        out.append(b)
    order = list(DIM_LABELS)
    out.sort(key=lambda b: (order.index(b["dim"]) if b["dim"] in order else 99, -b["n"]))
    return out


# =====================================================================
#  Regeln anwenden (nur die, die DU uebernommen hast)
# =====================================================================
def learned(cfg: dict) -> dict:
    lr = cfg.setdefault("learned", {})
    lr.setdefault("rules", [])
    lr.setdefault("coin_cooldown", {})
    return lr


def coin_blocked_until(cfg: dict, sym: str, now: datetime | None = None) -> str | None:
    until = learned(cfg)["coin_cooldown"].get(sym)
    if until and datetime.fromisoformat(until) > (now or now_utc()):
        return until
    return None


def entry_check(cfg: dict, trade_like: dict) -> tuple[bool, float, list[str]]:
    """Prueft einen geplanten Einstieg gegen die uebernommenen Regeln.
    Gibt (erlaubt, Risiko-Faktor, Begruendungen) zurueck."""
    f = features(trade_like)
    mult, notes = 1.0, []
    amp = learned(cfg).get("ampel") or {}
    if amp.get("mult", 1.0) != 1.0:
        mult *= float(amp["mult"])
        notes.append(f"Risiko x{amp['mult']} (Strategie-Ampel {amp.get('status')})")
    for rule in learned(cfg)["rules"]:
        if f.get(rule["dim"]) != rule["value"]:
            continue
        label = f"{DIM_LABELS.get(rule['dim'], rule['dim'])} = {rule['value']}"
        if rule["action"] == "block":
            return False, 0.0, [f"gesperrt durch gelernte Regel ({label})"]
        mult *= float(rule.get("mult", 1.0))
        notes.append(f"Risiko x{rule['mult']} ({label})")
    return True, max(0.25, min(1.5, mult)), notes


# =====================================================================
#  Vorschlaege erzeugen
# =====================================================================
def _sid(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def analyze(trades: list[dict], cfg: dict, equity: float | None, now: datetime | None = None) -> list[dict]:
    now = now or now_utc()
    s = settings(cfg)
    sugg: list[dict] = []
    active = {(r["dim"], r["value"]): r for r in learned(cfg)["rules"]}

    for b in feature_stats(trades, cfg, equity):
        key = (b["dim"], b["value"])
        label = f"{b['dim_label']}: {b['value']}"
        evidence = (f"{b['n']} Trades, Trefferquote {b['win_rate']:.0f} %, Ø {b['avg_r']:+.2f} R, "
                    f"zusammen {b['pnl']:+.2f} (vorsichtig bewertet: {b['shrunk_r']:+.2f} R)")
        rule = active.get(key)
        if rule:
            # Bestehende Risiko-Regel passt nicht mehr zu den Daten -> Ruecknahme vorschlagen
            if rule["action"] == "risk" and ((rule["mult"] < 1 and b["shrunk_r"] > 0.05) or
                                             (rule["mult"] > 1 and b["shrunk_r"] < 0.15)):
                sugg.append({"id": _sid("undo", *key, b["n"]), "kind": "undo_rule", "rule_id": rule["id"],
                             "title": f"Regel zuruecknehmen: {label}",
                             "why": "Die neueren Trades bestaetigen die Regel nicht mehr.", "evidence": evidence})
            continue
        if not b["ready"]:
            continue
        if b["shrunk_r"] <= s["block_below_r"]:
            action, mult, title, why = "block", 0.0, f"Sperren: {label}", "Diese Einstiege verlieren deutlich und dauerhaft."
        elif b["shrunk_r"] <= s["reduce_below_r"]:
            action, mult, title, why = "risk", 0.5, f"Risiko halbieren: {label}", "Diese Einstiege laufen leicht negativ."
        elif b["shrunk_r"] >= s["boost_above_r"]:
            action, mult, title, why = "risk", 1.25, f"Risiko x1.25: {label}", "Diese Einstiege laufen klar besser als der Rest."
        else:
            continue
        sugg.append({"id": _sid("rule", *key, action, mult), "kind": "rule", "title": title, "why": why,
                     "evidence": evidence,
                     "rule": {"id": _sid(*key), "dim": b["dim"], "value": b["value"], "action": action, "mult": mult}})

    # --- Coin-Gedaechtnis -------------------------------------------------
    since = now - timedelta(days=s["coin_lookback_days"])
    by_coin: dict[str, list[dict]] = {}
    for t in trades:
        try:
            closed = datetime.fromisoformat(t["closed_at"])
        except (KeyError, ValueError):
            continue
        if closed >= since:
            by_coin.setdefault(t["symbol"], []).append(t)
    for sym, ts in by_coin.items():
        ts.sort(key=lambda t: t["closed_at"])
        streak = 0
        for t in reversed(ts):
            if t["pnl"] < 0:
                streak += 1
            else:
                break
        if streak >= s["coin_losses_in_row"] and not coin_blocked_until(cfg, sym, now):
            loss = sum(t["pnl"] for t in ts[-streak:])
            sugg.append({"id": _sid("coin", sym, ts[-1]["closed_at"]), "kind": "coin", "symbol": sym,
                         "hours": s["coin_cooldown_hours"],
                         "title": f"{sym} fuer {s['coin_cooldown_hours'] // 24} Tage meiden",
                         "why": f"{streak} Verluste in Folge auf diesem Coin.",
                         "evidence": f"zusammen {loss:+.2f} seit {ts[-streak]['opened_at'][:10]}"})
    return sugg


# =====================================================================
#  Parameter-Test (Walk-Forward)
# =====================================================================
def _evaluate(datasets, cfg, is_spot, params, part):
    c = dict(cfg)
    c["strategy_params"] = {k: v for k, v in params.items() if k in DEFAULT_PARAMS}
    c["breakeven_at_r"] = params["breakeven_at_r"]
    c["trail_at_r"] = params["trail_at_r"]
    total_r, trades, worst_dd = 0.0, 0, 0.0
    for _sym, df, split in datasets:
        a, b = (60, split) if part == "train" else (split, len(df))
        res = run_backtest(None, c, is_spot, df=df, start_index=a, end_index=b)
        if "error" in res:
            continue
        total_r += res["total_r"]
        trades += res["trades"]
        worst_dd = max(worst_dd, res["max_drawdown_pct"])
    return {"total_r": round(total_r, 2), "trades": trades, "max_dd": round(worst_dd, 1)}


def current_params(cfg: dict) -> dict:
    p = params_from(cfg)
    p["breakeven_at_r"] = float(cfg.get("breakeven_at_r", 1.0))
    p["trail_at_r"] = float(cfg.get("trail_at_r", 2.0))
    return p


def tune(history_fn, cfg: dict, is_spot: bool, symbols: list[str], seed: int | None = None,
         progress=lambda msg: None) -> dict:
    """history_fn(symbol, timeframe, days) -> ohlcv. Gibt Ergebnis + ggf. Vorschlag zurueck."""
    s = settings(cfg)
    datasets = []
    for sym in symbols[: s["tune_max_coins"]]:
        try:
            candles = history_fn(sym, cfg["timeframe"], s["tune_days"])
        except Exception as e:  # noqa: BLE001
            progress(f"Parameter-Test: {sym} uebersprungen ({e})")
            continue
        if len(candles) < 300:
            continue
        df = add_indicators(to_df(candles))
        datasets.append((sym, df, int(len(df) * (1 - s["tune_test_share"]))))
    result = {"at": iso(now_utc()), "coins": [d[0] for d in datasets], "suggestion": None}
    if not datasets:
        result["summary"] = "Selbsttest: Es konnten keine alten Kurse geladen werden."
        return result

    cur = current_params(cfg)
    rng = random.Random(seed)
    combos, seen = [], {tuple(sorted(cur.items()))}
    for _ in range(s["tune_combos"] * 5):
        if len(combos) >= s["tune_combos"]:
            break
        c = {k: rng.choice(v) for k, v in TUNE_GRID.items()}
        if c["trail_at_r"] <= c["breakeven_at_r"]:
            continue
        key = tuple(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            combos.append(c)

    cur_train = _evaluate(datasets, cfg, is_spot, cur, "train")
    progress(f"Parameter-Test: {len(combos)} Varianten auf {len(datasets)} Coins (Training)")
    scored = []
    for c in combos:
        r = _evaluate(datasets, cfg, is_spot, c, "train")
        if r["trades"] >= 10:
            scored.append((r["total_r"], c, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    finalists = scored[:5]

    cur_test = _evaluate(datasets, cfg, is_spot, cur, "test")
    best = None
    for _tr, c, rtrain in finalists:
        rtest = _evaluate(datasets, cfg, is_spot, c, "test")
        if best is None or rtest["total_r"] > best[2]["total_r"]:
            best = (c, rtrain, rtest)

    result.update({"current_train": cur_train, "current_test": cur_test})
    if not best:
        result["summary"] = "Selbsttest: Zu wenige Trades in den alten Kursen, alles bleibt, wie es ist."
        return result
    c, rtrain, rtest = best
    result.update({"best_train": rtrain, "best_test": rtest})
    gain = rtest["total_r"] - cur_test["total_r"]
    ok = (rtest["trades"] >= s["tune_min_test_trades"] and rtest["total_r"] > 0
          and rtrain["total_r"] > cur_train["total_r"] and gain >= s["tune_min_gain_r"]
          and rtest["max_dd"] <= max(cur_test["max_dd"] + 2, 10))
    changes = {k: v for k, v in c.items() if abs(float(v) - float(cur[k])) > 1e-9}
    if not ok or not changes:
        result["summary"] = (f"Selbsttest: Keine andere Einstellung war klar besser, alles bleibt, wie es ist "
                             f"(aktuell {cur_test['total_r']:+.1f} R, beste Alternative {rtest['total_r']:+.1f} R).")
        return result
    diff = ", ".join(f"{PARAM_LABELS[k]} {cur[k]:g} -> {v:g}" for k, v in changes.items())
    result["summary"] = f"Selbsttest: Bessere Einstellungen gefunden ({gain:+.1f} R mehr in den Test-Tagen)."
    result["suggestion"] = {
        "id": _sid("params", *sorted(changes.items())), "kind": "params", "params": c, "previous": cur,
        "title": "Strategie-Einstellungen anpassen",
        "why": diff,
        "evidence": (f"Training: {rtrain['total_r']:+.1f} R ({rtrain['trades']} Trades) vs. aktuell {cur_train['total_r']:+.1f} R. "
                     f"Test (ungesehen): {rtest['total_r']:+.1f} R ({rtest['trades']} Trades) vs. aktuell "
                     f"{cur_test['total_r']:+.1f} R. Coins: {', '.join(result['coins'])}."),
    }
    return result


# =====================================================================
#  Strategie-Ampel: passt die Strategie gerade zum Markt?
# =====================================================================
AMPEL = {"gruen": 1.0, "gelb": 0.75, "rot": 0.5, "grau": 1.0}


def ampel_from(res: dict, params_applied: bool = False) -> dict:
    """Bewertet den letzten Parameter-Test (Testzeitraum = Daten, die die Optimierung nicht kannte).
    gruen = aktuelle Einstellungen verdienen  -> volles Risiko
    gelb  = nur eine andere Variante verdient -> Risiko x0.75
    rot   = keine Variante verdient           -> Risiko x0.5 (Strategie passt gerade nicht zum Markt)
    grau  = zu wenig Daten                    -> unveraendert"""
    cur = (res.get("best_test") if params_applied else res.get("current_test")) or {}
    best = res.get("best_test") or cur
    r, n = cur.get("total_r"), cur.get("trades", 0)
    if r is None or n < 10:
        status, why = "grau", "zu wenig Trades im Test"
    elif r > 0:
        status, why = "gruen", f"Strategie verdient im Test ({r:+.1f} R bei {n} Trades)"
    elif (best.get("total_r") or 0) > 0:
        status, why = "gelb", f"aktuell {r:+.1f} R, eine andere Einstellung waere besser"
    else:
        status, why = "rot", f"keine getestete Variante verdient gerade ({r:+.1f} R bei {n} Trades)"
    return {"status": status, "mult": AMPEL[status], "why": why, "at": iso(now_utc())}


# =====================================================================
#  Speicher fuer Vorschlaege / Verlauf
# =====================================================================
class LearningStore:
    def __init__(self, path: Path = LEARN_FILE):
        self.path = path
        d = json.loads(path.read_text()) if path.exists() else {}
        self.suggestions: list[dict] = d.get("suggestions", [])
        self.dismissed: dict[str, str] = d.get("dismissed", {})
        self.history: list[dict] = d.get("history", [])
        self.last_analyze: str | None = d.get("last_analyze")
        self.last_tune: str | None = d.get("last_tune")
        self.tune_result: dict | None = d.get("tune_result")
        self.tuning = False

    def save(self):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps({
            "suggestions": self.suggestions, "dismissed": self.dismissed, "history": self.history[-200:],
            "last_analyze": self.last_analyze, "last_tune": self.last_tune, "tune_result": self.tune_result,
        }, indent=1))

    def note(self, text: str):
        self.history.append({"t": iso(now_utc()), "text": text})

    def set_suggestions(self, new: list[dict], now: datetime | None = None):
        now = now or now_utc()
        # verworfene Vorschlaege 7 Tage nicht wieder anzeigen
        self.dismissed = {k: v for k, v in self.dismissed.items()
                          if datetime.fromisoformat(v) > now - timedelta(days=7)}
        keep_params = [x for x in self.suggestions if x["kind"] == "params"]
        merged = [x for x in new if x["id"] not in self.dismissed]
        merged += [x for x in keep_params if x["id"] not in self.dismissed and x["id"] not in {m["id"] for m in merged}]
        for m in merged:
            m.setdefault("created_at", iso(now))
        self.suggestions = merged

    def pop(self, sid: str) -> dict | None:
        for i, x in enumerate(self.suggestions):
            if x["id"] == sid:
                return self.suggestions.pop(i)
        return None

    def dismiss(self, sid: str) -> bool:
        x = self.pop(sid)
        if x:
            self.dismissed[sid] = iso(now_utc())
            self.note(f"Verworfen: {x['title']}")
        return bool(x)

    def tune_due(self, cfg: dict, now: datetime | None = None) -> bool:
        if self.tuning:
            return False
        if not self.last_tune:
            return True
        return (now or now_utc()) - datetime.fromisoformat(self.last_tune) >= timedelta(hours=settings(cfg)["tune_every_hours"])


def apply_suggestion(cfg: dict, sug: dict, now: datetime | None = None) -> str:
    """Setzt einen Vorschlag in der Config um. Gibt Text fuers Protokoll zurueck."""
    now = now or now_utc()
    lr = learned(cfg)
    if sug["kind"] == "rule":
        rule = dict(sug["rule"], since=iso(now), title=sug["title"])
        lr["rules"] = [r for r in lr["rules"] if not (r["dim"] == rule["dim"] and r["value"] == rule["value"])] + [rule]
        return f"Uebernommen: {sug['title']}"
    if sug["kind"] == "undo_rule":
        lr["rules"] = [r for r in lr["rules"] if r["id"] != sug["rule_id"]]
        return f"Uebernommen: {sug['title']}"
    if sug["kind"] == "coin":
        lr["coin_cooldown"][sug["symbol"]] = iso(now + timedelta(hours=sug["hours"]))
        return f"Uebernommen: {sug['title']}"
    if sug["kind"] == "params":
        lr.setdefault("params_before", sug["previous"])
        p = sug["params"]
        cfg["strategy_params"] = {k: v for k, v in p.items() if k in DEFAULT_PARAMS}
        cfg["breakeven_at_r"], cfg["trail_at_r"] = p["breakeven_at_r"], p["trail_at_r"]
        return f"Uebernommen: Strategie-Einstellungen ({sug['why']})"
    raise ValueError("unbekannter Vorschlag")


def remove_rule(cfg: dict, rule_id: str) -> str | None:
    lr = learned(cfg)
    for r in lr["rules"]:
        if r["id"] == rule_id:
            lr["rules"].remove(r)
            return f"Zurueckgenommen: {r.get('title', r['value'])}"
    if rule_id.startswith("coin:"):
        sym = rule_id[5:]
        if lr["coin_cooldown"].pop(sym, None):
            return f"Zurueckgenommen: {sym} wird wieder gehandelt"
    if rule_id == "params":
        prev = lr.pop("params_before", None)
        cfg["strategy_params"] = {}
        if prev:
            cfg["breakeven_at_r"], cfg["trail_at_r"] = prev["breakeven_at_r"], prev["trail_at_r"]
        return "Zurueckgenommen: Strategie-Einstellungen auf vorherige Werte"
    return None


def reset_all(cfg: dict) -> str:
    lr = learned(cfg)
    prev = lr.get("params_before")
    if prev:
        cfg["breakeven_at_r"], cfg["trail_at_r"] = prev["breakeven_at_r"], prev["trail_at_r"]
    cfg["strategy_params"] = {}
    cfg["learned"] = {"rules": [], "coin_cooldown": {}}
    return "Alles Gelernte zurueckgesetzt (Regeln, Coin-Sperren, Strategie-Einstellungen)."


def active_items(cfg: dict, now: datetime | None = None) -> list[dict]:
    now = now or now_utc()
    lr = learned(cfg)
    items = [{"id": r["id"], "text": r.get("title", f"{r['dim']}={r['value']}"), "since": r.get("since")} for r in lr["rules"]]
    for sym, until in lr["coin_cooldown"].items():
        if datetime.fromisoformat(until) > now:
            items.append({"id": "coin:" + sym, "text": f"{sym} gemieden bis {until[:16].replace('T', ' ')} UTC", "since": None})
    if cfg.get("strategy_params") or lr.get("params_before"):
        p = current_params(cfg)
        items.append({"id": "params", "text": "Strategie-Einstellungen: " + ", ".join(
            f"{PARAM_LABELS[k]} {p[k]:g}" for k in PARAM_LABELS), "since": None})
    return items
