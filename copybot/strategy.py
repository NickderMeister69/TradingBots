"""Aus den Positionen der Profis wird pro Coin eine gewichtete Mehrheitsmeinung – und daraus die Ziel-Position des Bots."""
import math

MIN_COIN_DAY_VOLUME = 2_000_000   # nur Coins mit mind. 2 Mio $ Tagesvolumen (sonst zu illiquide)


def consensus(traders, states, trust, market, use_trust=True):
    """traders: Liste ausgewählter Profis; states: address -> {account, positions}; trust: address -> {trust}."""
    coins = {}
    for t in traders:
        st = states.get(t["address"])
        if not st or st["account"] <= 0:
            continue
        w = t.get("base_weight", 1.0) * (trust.get(t["address"], {}).get("trust", 1.0) if use_trust else 1.0)
        for coin, p in st["positions"].items():
            if coin not in market or market[coin]["day_vlm"] < MIN_COIN_DAY_VOLUME:
                continue
            exposure = abs(p["value"]) / st["account"]          # wie viel vom eigenen Konto steckt drin (inkl. Hebel)
            contrib = w * math.sqrt(min(max(exposure, 0.02), 4.0))
            c = coins.setdefault(coin, {"long_w": 0.0, "short_w": 0.0, "n_long": 0, "n_short": 0,
                                        "supporters": [], "exposure_sum": 0.0})
            side = "long" if p["szi"] > 0 else "short"
            c[side + "_w"] += contrib
            c["n_" + side] += 1
            c["exposure_sum"] += exposure
            c["supporters"].append((contrib, t["address"], side))
    for coin, c in coins.items():
        tot = c["long_w"] + c["short_w"]
        c["n"] = c["n_long"] + c["n_short"]
        c["agreement"] = (c["long_w"] - c["short_w"]) / tot if tot else 0.0   # +1 alle long, -1 alle short
        c["long_share"] = c["long_w"] / tot if tot else 0.5
        c["avg_exposure"] = c["exposure_sum"] / c["n"] if c["n"] else 0.0
        c["supporters"].sort(reverse=True)
    return coins


def targets(pf, cons, equity, now):
    """Ziel-Nominalwerte für ein Konto. Gibt (targets, reasons, supporters, verdicts) zurück."""
    prm = pf.params
    lev = prm["leverage"]
    tg, reasons, sup, verdict, conviction = {}, {}, {}, {}, {}
    for coin, c in cons.items():
        held = pf.positions.get(coin)
        held_side = (1 if held["qty"] > 0 else -1) if held else 0
        direction = 1 if c["agreement"] > 0 else -1
        strength = abs(c["agreement"])
        enough = c["n"] >= prm["min_traders"]
        if held and held_side == direction:
            keep = strength >= prm["exit_agreement"] and c["n"] >= max(2, prm["min_traders"] - 1)
            if not keep:
                reasons[coin] = "Mehrheit der Profis ist weg"
                verdict[coin] = "exit"
                continue
        elif not (enough and strength >= prm["min_agreement"]):
            if held:
                reasons[coin] = "Profis haben gedreht"
                verdict[coin] = "exit"
            else:
                verdict[coin] = "few" if not enough else "split"
            continue
        size = min(1.0, c["n"] / max(1, prm["full_traders"])) * (0.5 + 0.5 * strength)
        tg[coin] = direction * equity * prm["max_coin_pct"] * lev * size
        conviction[coin] = strength * math.sqrt(c["n"])      # klare Mehrheit UND viele Profis
        reasons[coin] = "Profis drehen" if held and held_side != direction else "Mehrheit der Profis"
        sup[coin] = {"n": c["n"], "agreement": round(c["agreement"], 3),
                     "top": [a for _, a, s in c["supporters"][:5] if s == ("long" if direction > 0 else "short")]}
        verdict[coin] = "long" if direction > 0 else "short"
    # Nur die stärksten Signale (weniger Gebühren, klarere Positionen). Gehaltene Coins haben leichten Vorrang.
    max_pos = int(prm.get("max_positions", 12))
    # swap_bonus: wie viel stärker ein neuer Coin sein muss, um einen gehaltenen zu verdrängen.
    # min_hold_min: so lange (Minuten) wird ein frisch gekaufter Coin nicht gegen einen anderen getauscht.
    bonus = float(prm.get("swap_bonus", 1.25))
    hold_s = float(prm.get("min_hold_min", 0)) * 60

    def young(k):
        p = pf.positions.get(k)
        return bool(p and hold_s > 0 and now - float(p.get("opened_at") or now) < hold_s)
    if len(tg) > max_pos:
        ranked = sorted(tg, key=lambda k: (young(k), conviction[k] * (bonus if k in pf.positions else 1.0)),
                        reverse=True)
        for k in ranked[max_pos:]:
            del tg[k]
            if k in pf.positions:
                reasons[k] = "Andere Signale sind stärker"
            verdict[k] = "weak"
    # Gesamt-Deckel: alle Positionen zusammen dürfen max. max_margin_pct vom Topf als Margin binden
    gross = sum(abs(v) for v in tg.values())
    cap = equity * prm["max_margin_pct"] * lev
    if gross > cap > 0:
        f = cap / gross
        tg = {k: v * f for k, v in tg.items()}
    # Wende-Schutz: nicht alles auf eine Richtung. Die Mehrheits-Seite darf die andere Seite höchstens um
    # max_net_share x Gesamt-Deckel übersteigen (1,0 = aus). Fast alle Coins laufen mit Bitcoin –
    # 12 Shorts sind sonst eine einzige große Wette, die bei einer Wende auf einmal verliert.
    share = float(prm.get("max_net_share", 1.0))
    if share < 1.0 and cap > 0:
        lng = sum(v for v in tg.values() if v > 0)
        sht = -sum(v for v in tg.values() if v < 0)
        big, small = (lng, sht) if lng >= sht else (sht, lng)
        allowed = small + share * cap
        if big > allowed > 0:
            f = allowed / big
            side = 1 if lng >= sht else -1
            tg = {k: (v * f if v * side > 0 else v) for k, v in tg.items()}
    return tg, reasons, sup, verdict
