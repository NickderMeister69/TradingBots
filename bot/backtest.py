"""Backtest: dieselben Regeln wie der echte Bot, Kerze fuer Kerze auf alten Daten."""
from datetime import datetime, timezone

from .analysis import to_df, add_indicators, detect_regime
from .strategy import get_signal, choose_leverage, position_size, params_from


def _plain(x):
    """numpy-Zahlen in normale Python-Zahlen umwandeln (fuer JSON)."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_plain(v) for v in x]
    if hasattr(x, "item"):
        return x.item()
    return x


def run_backtest(ohlcv, cfg, is_spot, start_equity=1000.0, df=None, start_index=60, end_index=None):
    """df/start_index/end_index erlauben dem Lern-Modul, Indikatoren nur einmal zu berechnen
    und Trainings- und Testzeitraum getrennt auszuwerten."""
    return _plain(_run(ohlcv, cfg, is_spot, start_equity, df, start_index, end_index))


def _run(ohlcv, cfg, is_spot, start_equity, df=None, start_index=60, end_index=None):
    if df is None:
        if len(ohlcv) < 120:
            return {"error": "Zu wenig Kursdaten fuer einen Backtest."}
        df = add_indicators(to_df(ohlcv))   # Indikatoren schauen nur zurueck, kein Blick in die Zukunft
    prm = params_from(cfg)
    s0 = max(60, start_index)
    s1 = len(df) if end_index is None else min(len(df), end_index)
    fee = cfg["fee_rate"]
    equity = start_equity
    peak, max_dd = equity, 0.0
    pos = None
    trades, curve = [], [{"t": int(df["ts"].iloc[s0].timestamp() * 1000), "e": equity}]
    day, day_start, halted = None, equity, False

    for i in range(s0, s1):
        row = df.iloc[i]
        ts = row["ts"]
        d = ts.strftime("%Y-%m-%d")
        if d != day:
            day, day_start, halted = d, equity, False

        if pos:
            sign = 1 if pos["side"] == "long" else -1
            hit_stop = row["low"] <= pos["stop"] if sign == 1 else row["high"] >= pos["stop"]
            hit_tp = row["high"] >= pos["tp"] if sign == 1 else row["low"] <= pos["tp"]
            exit_px, reason = None, None
            if hit_stop:     # vorsichtig: wenn beides in einer Kerze, zaehlt der Stop
                gap = (row["open"] < pos["stop"]) if sign == 1 else (row["open"] > pos["stop"])
                exit_px = row["open"] if gap else pos["stop"]
                reason = "Break-even/Trailing" if pos["be"] else "Stop"
            elif hit_tp:
                exit_px, reason = pos["tp"], "Ziel"
            if exit_px is not None:
                pnl = (exit_px - pos["entry"]) * sign * pos["amount"] - (pos["entry"] + exit_px) * pos["amount"] * fee
                equity += pnl
                trades.append({"pnl": pnl, "strategy": pos["strategy"], "reason": reason, "side": pos["side"],
                               "r_mult": pnl / pos["risk"] if pos["risk"] else 0.0})
                curve.append({"t": int(ts.timestamp() * 1000), "e": round(equity, 2)})
                pos = None
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
                if (equity - day_start) / day_start <= -cfg["daily_loss_limit"]:
                    halted = True
            else:
                r_now = (row["close"] - pos["entry"]) * sign / pos["r"]
                if not pos["be"] and r_now >= cfg["breakeven_at_r"]:
                    pos["stop"] = pos["entry"] + sign * pos["entry"] * fee * 2
                    pos["be"] = True
                if r_now >= cfg["trail_at_r"]:
                    ns = row["close"] - sign * pos["r"]
                    if (sign == 1 and ns > pos["stop"]) or (sign == -1 and ns < pos["stop"]):
                        pos["stop"] = ns
            continue

        if halted:
            continue
        regime = detect_regime(row)
        sig = get_signal(regime, df.iloc[: i + 1], allow_short=not is_spot, params=prm)
        if not sig:
            continue
        lev = 1 if is_spot else choose_leverage(float(row["vol_pct"]), cfg["max_leverage"])
        amount = position_size(equity, cfg["risk_per_trade"], sig.entry, sig.stop, lev, is_spot, cfg["spot_max_allocation"])
        if amount <= 0:
            continue
        pos = {"side": sig.side, "entry": sig.entry, "stop": sig.stop, "tp": sig.take_profit,
               "r": abs(sig.entry - sig.stop), "amount": amount, "be": False, "strategy": sig.strategy,
               "risk": abs(sig.entry - sig.stop) * amount}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    first, last = float(df["close"].iloc[s0]), float(df["close"].iloc[s1 - 1])
    by_strat = {}
    for t in trades:
        s = by_strat.setdefault(t["strategy"], {"trades": 0, "pnl": 0.0, "wins": 0})
        s["trades"] += 1; s["pnl"] += t["pnl"]; s["wins"] += t["pnl"] > 0
    step = max(1, len(curve) // 200)
    return {
        "trades": len(trades),
        "total_r": sum(t["r_mult"] for t in trades),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "return_pct": (equity - start_equity) / start_equity * 100,
        "max_drawdown_pct": max_dd * 100,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_win": gross_win / len(wins) if wins else 0,
        "avg_loss": -gross_loss / len(losses) if losses else 0,
        "breakeven_exits": sum(1 for t in trades if t["reason"] == "Break-even/Trailing"),
        "hold_return_pct": (last - first) / first * 100,
        "by_strategy": [{"strategy": k, **v, "pnl": round(v["pnl"], 2)} for k, v in by_strat.items()],
        "curve": curve[::step] + ([curve[-1]] if curve and curve[-1] not in curve[::step] else []),
        "start_equity": start_equity, "end_equity": equity,
        "from": df["ts"].iloc[s0].strftime("%d.%m.%Y"), "to": df["ts"].iloc[s1 - 1].strftime("%d.%m.%Y"),
    }


TF_HOURS = {"1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "1h": 1, "4h": 4, "1d": 24}


def run_breakout_backtest(ohlcv, cfg, is_spot, start_equity=1000.0, timeframe=None):
    """Backtest der Ausbruch-Trendfolge. Einstieg zur Eroeffnung der naechsten Kerze, Stop innerhalb
    der Kerze (bei Kurs-Luecke zum Eroeffnungskurs), Ausstieg zum Schlusskurs, wenn der Gegen-Kanal bricht.
    Kosten: Gebuehr je Seite (fee_rate) + Finanzierung bei Futures (funding_8h, Standard 0,01 % / 8 h)."""
    from .strategy import add_breakout_levels, breakout_signal, breakout_exit, BREAKOUT_MIN_CANDLES, BREAKOUT
    if len(ohlcv) < BREAKOUT_MIN_CANDLES + 20:
        return {"error": "Zu wenig Kursdaten fuer einen Backtest (die Ausbruch-Strategie braucht mehr Vorlauf, mehr Tage waehlen)."}
    prm = cfg.get("breakout_params") or None
    df = add_breakout_levels(to_df(ohlcv), prm)
    fee = cfg["fee_rate"]
    fund = 0.0 if is_spot else cfg.get("funding_8h", 0.0001)
    hours = TF_HOURS.get(timeframe or cfg.get("timeframe"), 4)
    equity, peak, max_dd = start_equity, start_equity, 0.0
    s0 = BREAKOUT_MIN_CANDLES
    trades, curve = [], [{"t": int(df["ts"].iloc[s0].timestamp() * 1000), "e": equity}]
    pos, pend = None, None
    O, H, Lo = df["open"].values, df["high"].values, df["low"].values
    pyr = cfg.get("pyramid") or {}
    max_units, step_atr = int(pyr.get("max_units") or 1), float(pyr.get("step_atr", 1.0))
    stop_atr = float({**BREAKOUT, **(prm or {})}["stop_atr"])
    for i in range(s0, len(df)):
        row = df.iloc[i]
        if pend is not None:
            sig = pend; pend = None
            e = float(O[i]); sign = 1 if sig.side == "long" else -1
            dist = abs(sig.entry - sig.stop)
            amount = position_size(equity, cfg["risk_per_trade"], e, e - sign * dist, cfg["max_leverage"],
                                   is_spot, cfg["spot_max_allocation"])
            if amount > 0:
                n = dist / stop_atr
                pos = {"side": sig.side, "sign": sign, "entry": e, "stop": e - sign * dist, "amount": amount,
                       "risk": dist * amount, "i": i, "units": [(e, amount)], "unit": amount, "n": n,
                       "next": e + sign * step_atr * n}
        if pos:
            sign, ex, reason = pos["sign"], None, None
            if (sign == 1 and Lo[i] <= pos["stop"]) or (sign == -1 and H[i] >= pos["stop"]):
                gap = O[i] < pos["stop"] if sign == 1 else O[i] > pos["stop"]
                ex, reason = (float(O[i]) if gap else pos["stop"]), "Stop"
            elif breakout_exit(row, pos["side"]):
                ex, reason = float(row["close"]), "Trend vorbei"
            if ex is None:
                # Nachkaufen, wenn der Kurs die naechste Stufe erreicht (wie eine Stop-Kauf-Order)
                while len(pos["units"]) < max_units and ((sign == 1 and H[i] >= pos["next"]) or (sign == -1 and Lo[i] <= pos["next"])):
                    px = max(pos["next"], float(O[i])) if sign == 1 else min(pos["next"], float(O[i]))
                    pos["units"].append((px, pos["unit"]))
                    pos["next"] = px + sign * step_atr * pos["n"]
                    tot = sum(a for _, a in pos["units"])
                    avg = sum(p_ * a for p_, a in pos["units"]) / tot
                    ns = avg - sign * pos["risk"] / tot
                    if (sign == 1 and ns > pos["stop"]) or (sign == -1 and ns < pos["stop"]):
                        pos["stop"] = ns
                    pos["entry"], pos["amount"] = avg, tot
            if ex is not None:
                n = i - pos["i"] + 1
                cost = (pos["entry"] + ex) * pos["amount"] * fee + pos["entry"] * pos["amount"] * fund * n * hours / 8
                pnl = (ex - pos["entry"]) * sign * pos["amount"] - cost
                equity += pnl
                trades.append({"pnl": pnl, "strategy": "Ausbruch", "reason": reason, "side": pos["side"],
                               "r_mult": pnl / pos["risk"] if pos["risk"] else 0.0})
                curve.append({"t": int(row["ts"].timestamp() * 1000), "e": round(equity, 2)})
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
                pos = None
            continue
        sig = breakout_signal(df.iloc[: i + 1], allow_short=not is_spot, prm=prm)
        if sig:
            pend = sig
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    first, last = float(df["close"].iloc[s0]), float(df["close"].iloc[-1])
    step = max(1, len(curve) // 200)
    return _plain({
        "trades": len(trades), "total_r": sum(t["r_mult"] for t in trades),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "return_pct": (equity - start_equity) / start_equity * 100,
        "max_drawdown_pct": max_dd * 100,
        "profit_factor": (gw / gl) if gl > 0 else None,
        "avg_win": gw / len(wins) if wins else 0, "avg_loss": -gl / len(losses) if losses else 0,
        "breakeven_exits": sum(1 for t in trades if t["reason"] == "Trend vorbei"),
        "hold_return_pct": (last - first) / first * 100,
        "by_strategy": [{"strategy": "Ausbruch", "trades": len(trades), "wins": len(wins),
                         "pnl": round(sum(t["pnl"] for t in trades), 2)}] if trades else [],
        "curve": curve[::step] + ([curve[-1]] if curve and curve[-1] not in curve[::step] else []),
        "start_equity": start_equity, "end_equity": equity,
        "from": df["ts"].iloc[s0].strftime("%d.%m.%Y"), "to": df["ts"].iloc[-1].strftime("%d.%m.%Y"),
    })
