"""Wie der Bot lernt – drei ehrliche, nachprüfbare Mechanismen:

1. Vertrauen pro Profi (Multiplicative Weights): Der Bot rechnet jede Runde nach, was es gebracht hätte,
   genau diesen Profi zu kopieren (inkl. Gebühren). Wer Geld bringt, bekommt mehr Gewicht, wer verliert, weniger.
   Das Vertrauen kehrt langsam zu 1 zurück, damit alte Glückssträhnen nicht ewig zählen.
2. Strategie-Wettbewerb: Mehrere Schattenkonten handeln parallel mit anderen Regeln. Ist eines nachweislich
   besser (mind. 20 Trades, 48 h, +1 Prozentpunkt), übernimmt der Bot dessen Regeln. Das schwächste
   Schattenkonto wird regelmäßig durch eine Abwandlung des besten ersetzt.
3. Coin-Sperre: Coins, die dem Bot wiederholt Verluste bringen, werden für ein paar Tage ausgelassen.
"""
import copy
import math
import random
import time

import config as C
from config import TAKER_FEE

HALF_LIFE_SECONDS = 7 * 24 * 3600     # Vertrauen halbiert seinen Abstand zu 1 in ca. 7 Tagen

LEARNABLE = {
    "min_traders": ("int", 2, 10),
    "min_agreement": ("float", 0.20, 0.90),
    "exit_agreement": ("float", 0.0, 0.50),
    "stop_loss_pct": ("float", 0.02, 0.15),
    "full_traders": ("int", 4, 25),
    "cooldown_min": ("int", 30, 720),
    "max_net_share": ("float", 0.30, 1.0),
    "swap_bonus": ("float", 1.0, 3.0),
    "min_hold_min": ("int", 0, 240),
}

PARAM_NAMES = {
    "min_traders": "Mindestzahl Profis", "min_agreement": "Einstiegs-Mehrheit", "exit_agreement": "Ausstiegs-Grenze",
    "stop_loss_pct": "Stop-Loss", "full_traders": "Profis für volle Größe", "cooldown_min": "Pause nach Stop",
    "use_trust": "Vertrauen benutzen", "max_net_share": "Wende-Schutz",
    "swap_bonus": "Tausch-Hürde", "min_hold_min": "Mindest-Haltezeit",
}


def initial_variants(base):
    def v(**kw):
        p = copy.deepcopy(base)
        p.update(kw)
        return p
    return [
        ("v_careful", "Vorsichtig", v(min_agreement=0.60, min_traders=4, stop_loss_pct=0.05)),
        ("v_standard", "Standard", v()),
        ("v_bold", "Mutig", v(min_agreement=0.35, min_traders=2, stop_loss_pct=0.08, exit_agreement=0.05)),
        ("v_control", "Ohne Lernen (Vergleich)", v(use_trust=False)),
        ("v_crowd", "Viele Profis", v(min_traders=6, min_agreement=0.40, full_traders=15)),
        ("v_strong", "Nur klare Mehrheit", v(min_agreement=0.75, exit_agreement=0.30)),
        ("v_turn", "Wende-Schutz", v(max_net_share=0.50)),
        ("v_calm", "Weniger tauschen", v(swap_bonus=2.0, min_hold_min=60)),
    ]


# ---------------------------------------------------------------- 1. Vertrauen
def update_trust(trust, traders, states, prev_states, market, prev_market, dt):
    """Eine Lernrunde. Gibt die Copy-Rendite pro Profi zurück (für Anzeige)."""
    results = {}
    decay = math.log(2) * dt / HALF_LIFE_SECONDS
    for t in traders:
        a = t["address"]
        tr = trust.setdefault(a, {"trust": 1.0, "obs": 0, "copy_ret": 0.0, "since": time.time(), "hist": []})
        prev, now = prev_states.get(a), states.get(a)
        if not prev or not now or prev["account"] <= 0:
            continue
        r = 0.0
        prev_exp, now_exp = {}, {}
        for coin, p in prev["positions"].items():
            m0, m1 = prev_market.get(coin), market.get(coin)
            if not m0 or not m1:
                continue
            e = p["value"] / prev["account"] * (1 if p["szi"] > 0 else -1)
            prev_exp[coin] = e
            r += e * (m1["mark"] / m0["mark"] - 1)
        for coin, p in now["positions"].items():
            now_exp[coin] = p["value"] / now["account"] * (1 if p["szi"] > 0 else -1)
        turnover = sum(abs(now_exp.get(c, 0) - prev_exp.get(c, 0)) for c in set(prev_exp) | set(now_exp))
        r -= turnover * (TAKER_FEE + C.SLIPPAGE)
        r = max(-0.1, min(0.1, r))
        log_t = math.log(tr["trust"])
        log_t = (1 - decay) * log_t + C.TRUST_LR * r
        tr["trust"] = min(C.TRUST_MAX, max(C.TRUST_MIN, math.exp(log_t)))
        tr["obs"] += 1
        tr["copy_ret"] += r
        results[a] = r
    return results


def snapshot_trust_history(trust):
    now = time.time()
    for tr in trust.values():
        h = tr.setdefault("hist", [])
        if not h or now - h[-1][0] >= 3600:
            h.append([now, round(tr["trust"], 4)])
            del h[:-48]


def trust_change_24h(tr):
    h = tr.get("hist") or []
    if not h:
        return 0.0
    ref = next((v for ts, v in h if ts >= time.time() - 86400), h[0][1])
    return tr["trust"] - ref


# ---------------------------------------------------------------- 2. Strategie-Wettbewerb
def window_stats(storage, pf_id, since, current_equity):
    start_eq = storage.equity_at(pf_id, since + 1) or storage.equity_at(pf_id, time.time()) or current_equity
    curve = storage.equity_curve(pf_id, since, max_points=2000)
    peak, mdd = start_eq, 0.0
    for _, eq in curve + [(0, current_equity)]:
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak if peak else 0)
    ret = (current_equity / start_eq - 1) if start_eq else 0.0
    ts = storage.trade_stats(pf_id, since)
    return {"ret_pct": ret * 100, "mdd_pct": mdd * 100, "trades": ts["n"], "wins": ts["wins"],
            "score": ret * 100 - 0.5 * mdd * 100}


def mutate(params, rnd=random):
    p = copy.deepcopy(params)
    keys = rnd.sample(list(LEARNABLE), 2)
    for k in keys:
        kind, lo, hi = LEARNABLE[k]
        span = hi - lo
        val = p[k] + rnd.gauss(0, span * 0.18)
        val = max(lo, min(hi, val))
        p[k] = int(round(val)) if kind == "int" else round(val, 3)
    if p["exit_agreement"] > p["min_agreement"] - 0.1:
        p["exit_agreement"] = round(max(0.0, p["min_agreement"] - 0.15), 3)
    return p, keys


def describe_params(p, ref=None):
    parts = []
    for k in ("min_traders", "min_agreement", "exit_agreement", "stop_loss_pct", "full_traders", "cooldown_min", "max_net_share", "swap_bonus", "min_hold_min", "use_trust"):
        if ref is not None and ref.get(k) == p.get(k):
            continue
        v = p[k]
        if k in ("min_agreement", "exit_agreement"):
            s = f"{round(50 + 50 * v)} %"
        elif k == "stop_loss_pct":
            s = f"{v * 100:.1f} %".replace(".", ",")
        elif k == "cooldown_min":
            s = f"{v} min"
        elif k == "use_trust":
            s = "ja" if v else "nein"
        elif k == "swap_bonus":
            s = f"neuer Coin muss {v:.2f}x stärker sein".replace(".", ",")
        elif k == "min_hold_min":
            s = f"{v} min"
        elif k == "max_net_share":
            s = "aus" if v >= 1.0 else f"max. {v * 100:.0f} % mehr in eine Richtung"
        else:
            s = str(v)
        parts.append(f"{PARAM_NAMES[k]}: {s}")
    return ", ".join(parts) if parts else "gleiche Regeln"


# ---------------------------------------------------------------- 3. Coin-Sperre
def coins_to_block(storage, pf_id, now):
    out = []
    for c in storage.coin_stats(pf_id, since=now - 14 * 86400):
        if c["n"] >= C.COIN_BLOCK_MIN_TRADES and c["net"] < 0 and c["wins"] / c["n"] < 0.35:
            out.append(c)
    return out
