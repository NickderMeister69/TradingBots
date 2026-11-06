"""Wählt aus der öffentlichen Bestenliste die Profis aus, die sich wirklich kopieren lassen."""
from config import SELECTION as S


def _win(row):
    w = {k: v for k, v in row["windowPerformances"]}
    get = lambda k: {kk: float(vv) for kk, vv in w.get(k, {}).items()}
    return get("day"), get("week"), get("month"), get("allTime")


def _rank(values):
    """Prozent-Rang 0..1 (1 = bester)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    r = [0.0] * len(values)
    for pos, i in enumerate(order):
        r[i] = pos / max(1, len(values) - 1)
    return r


def candidates(rows):
    pool = []
    for row in rows:
        try:
            av = float(row["accountValue"])
            day, week, month, allt = _win(row)
        except (KeyError, ValueError, TypeError):
            continue
        if av < S["min_account_usd"]:
            continue
        mroi, mpnl, mvlm = month.get("roi", 0), month.get("pnl", 0), month.get("vlm", 0)
        if not (S["min_month_roi"] <= mroi <= S["max_month_roi"]):
            continue
        if mpnl <= 0 or allt.get("pnl", 0) < S["min_alltime_pnl"]:
            continue
        if week.get("roi", 0) < S["min_week_roi"]:
            continue
        turnover = mvlm / av if av else 0
        if not (S["min_turnover"] <= turnover <= S["max_turnover"]):
            continue
        if week.get("vlm", 0) <= 0:          # diese Woche gar nicht gehandelt
            continue
        pool.append({
            "address": row["ethAddress"].lower(),
            "name": row.get("displayName"),
            "lb_account": av,
            "month_roi": mroi, "month_pnl": mpnl,
            "week_roi": week.get("roi", 0), "alltime_pnl": allt.get("pnl", 0),
            "alltime_roi": allt.get("roi", 0), "turnover": turnover,
        })
    if not pool:
        return []
    # Punkte: Monatsgewinn absolut, Monatsrendite, Gesamtgewinn, Woche – jeweils als Rang, damit Ausreißer nicht dominieren
    r_mpnl = _rank([p["month_pnl"] for p in pool])
    r_mroi = _rank([min(p["month_roi"], 1.5) for p in pool])
    r_apnl = _rank([p["alltime_pnl"] for p in pool])
    r_wroi = _rank([p["week_roi"] for p in pool])
    for i, p in enumerate(pool):
        p["score"] = round(0.35 * r_mpnl[i] + 0.30 * r_mroi[i] + 0.20 * r_apnl[i] + 0.15 * r_wroi[i], 4)
    pool.sort(key=lambda p: p["score"], reverse=True)
    return pool[: S["candidate_pool"]]
