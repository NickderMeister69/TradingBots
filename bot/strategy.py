"""Signale pro Regime plus Risiko- und Hebel-Management."""
from dataclasses import dataclass
from typing import Optional

# Standardwerte der Regeln. Der Lern-Modul kann Alternativen VORSCHLAGEN;
# uebernommene Werte stehen in config.yaml unter strategy_params.
DEFAULT_PARAMS = {
    "trend_adx_min": 25.0,    # ab welcher Trendstaerke Trendfolge-Einstiege erlaubt sind
    "trend_stop_atr": 2.0,    # Stop-Abstand Trendfolge in ATR
    "trend_tp_atr": 3.0,      # Ziel-Abstand Trendfolge in ATR
    "range_stop_atr": 1.5,    # Stop-Abstand Scalping in ATR
}


def params_from(cfg: Optional[dict]) -> dict:
    p = dict(DEFAULT_PARAMS)
    p.update((cfg or {}).get("strategy_params") or {})
    return p


@dataclass
class Signal:
    side: str          # "long" | "short"
    entry: float
    stop: float
    take_profit: float
    strategy: str
    reason: str


def trend_signal(df, allow_short: bool, prm: dict) -> Optional[Signal]:
    r, p = df.iloc[-1], df.iloc[-2]
    atr = r["atr"]
    sl, tp, adx_min = prm["trend_stop_atr"], prm["trend_tp_atr"], prm["trend_adx_min"]
    # Long: Aufwaertstrend, Rueckläufer an die EMA20, Kerze dreht wieder nach oben
    if r["ema20"] > r["ema50"] and r["adx"] >= adx_min:
        near_ema = r["low"] <= r["ema20"] * 1.003 and r["close"] > r["ema20"]
        turning = r["close"] > p["close"]
        if near_ema and turning and 40 <= r["rsi"] <= 65:
            return Signal("long", r["close"], r["close"] - sl * atr, r["close"] + tp * atr,
                          "Trendfolge", "Pullback an EMA20 im Aufwaertstrend")
    if allow_short and r["ema20"] < r["ema50"] and r["adx"] >= adx_min:
        near_ema = r["high"] >= r["ema20"] * 0.997 and r["close"] < r["ema20"]
        turning = r["close"] < p["close"]
        if near_ema and turning and 35 <= r["rsi"] <= 60:
            return Signal("short", r["close"], r["close"] + sl * atr, r["close"] - tp * atr,
                          "Trendfolge", "Pullback an EMA20 im Abwaertstrend")
    return None


def range_signal(df, allow_short: bool, prm: dict) -> Optional[Signal]:
    r = df.iloc[-1]
    atr = r["atr"]
    sl = prm["range_stop_atr"]
    if r["close"] < r["bb_low"] and r["rsi"] < 30:
        return Signal("long", r["close"], r["close"] - sl * atr, r["bb_mid"],
                      "Scalping", "Unter Bollinger-Band, RSI ueberverkauft")
    if allow_short and r["close"] > r["bb_up"] and r["rsi"] > 70:
        return Signal("short", r["close"], r["close"] + sl * atr, r["bb_mid"],
                      "Scalping", "Ueber Bollinger-Band, RSI ueberkauft")
    return None


def get_signal(regime: str, df, allow_short: bool, params: Optional[dict] = None) -> Optional[Signal]:
    prm = params or DEFAULT_PARAMS
    if regime in ("trend_up", "trend_down"):
        return trend_signal(df, allow_short, prm)
    if regime == "range":
        return range_signal(df, allow_short, prm)
    return None


def choose_leverage(vol_pct: float, max_leverage: int) -> int:
    """Ruhiger Coin -> mehr Hebel, wilder Coin -> weniger. Nie ueber dem Maximum."""
    if vol_pct <= 0:
        return 1
    lev = int(0.02 / vol_pct)
    return max(1, min(max_leverage, lev))


def position_size(equity: float, risk_per_trade: float, entry: float, stop: float,
                  leverage: int, is_spot: bool, spot_max_alloc: float) -> float:
    """Menge so, dass bei Stop-Loss genau risk_per_trade * equity verloren geht."""
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0
    amount = (equity * risk_per_trade) / stop_dist
    max_notional = equity * (spot_max_alloc if is_spot else leverage) * 0.95
    amount = min(amount, max_notional / entry)
    if amount * entry < 6:      # Binance-Mindestgroesse
        return 0.0
    return amount


# =====================================================================
#  Ausbruch-Trendfolge (klassisch, "Donchian"/"Turtle")
#  Getestet 2020-2026 auf 10 bekannten Coins, 4h-Kerzen: siehe Projekt-Notiz.
#  Einstieg: Schlusskurs ueber dem Hoch der letzten 55 Kerzen (Long) bzw. unter dem Tief (Short),
#            nur in Richtung des langen Trends (EMA200).
#  Stop:     2 x durchschnittliche Schwankung (ATR 20) vom Einstieg.
#  Ausstieg: Schlusskurs unter dem Tief der letzten 20 Kerzen (Long) bzw. ueber dem Hoch (Short).
#            Kein festes Gewinn-Ziel: Gewinner duerfen laufen.
# =====================================================================
BREAKOUT = {"entry_n": 55, "exit_m": 20, "stop_atr": 2.0, "atr_n": 20, "ema_n": 200,
            # nur einsteigen, wenn der lange Trend klar ist: Kurs mind. so viele ATR von der EMA200 entfernt
            # (Test 2020-2026, 4h, mit Nachkaufen: 2 ATR -> +6 % R, Notbremse 13 % statt 19 %; 1 und 3 ATR aehnlich)
            "min_trend_atr": 0.0,
            # Ichimoku als Zusatz-Filter (Nick, 20.10.2026; Test 2020-2026 4h: Gewinn etwa gleich,
            # schlechte Phasen etwas kleiner; auf 1h schlechter -> nur 4h-Bots)
            "ichimoku": False,
            # 20.10.2026 (1h-Bots): kein neuer Einstieg, wenn die Ausbruchs-Kerze am Wochenende (Sa/So, UTC) beginnt,
            # und nur bei Umsatz ueber volume_mult x Schnitt der letzten 20 Kerzen (0 = aus).
            # Test 2021-2026, 1h: +18 % R, Notbremse-Faelle 34 % -> 19 % (bei 0,5 %), schlechtester Rueckgang 70 -> 57 %.
            "skip_weekend": False, "volume_mult": 0.0,
            # 21.10.2026 Vergleichs-Bot "4h + Trendlinien": zusaetzlich einsteigen, wenn der Schlusskurs eine
            # schraege Trendlinie durch die letzten zwei Hochs (Long) bzw. Tiefs (Short) bricht.
            # Test 2020-2026, 4h, 10 Coins: +13 % R, aber groessere Ruecksetzer -> nur als Vergleich.
            "trendline": False, "trend_pivot": 5}
BREAKOUT_MIN_CANDLES = 260


def add_breakout_levels(df, prm=None):
    import pandas as pd
    p = {**BREAKOUT, **(prm or {})}
    h, l, c = df["high"], df["low"], df["close"]
    df["bo_hi"] = h.rolling(p["entry_n"]).max().shift(1)    # Hoch der letzten N Kerzen (ohne die aktuelle)
    df["bo_lo"] = l.rolling(p["entry_n"]).min().shift(1)
    df["ex_hi"] = h.rolling(p["exit_m"]).max().shift(1)
    df["ex_lo"] = l.rolling(p["exit_m"]).min().shift(1)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr_bo"] = tr.rolling(p["atr_n"]).mean()
    df["ema_long"] = c.ewm(span=p["ema_n"], adjust=False).mean()
    if p.get("trendline"):
        add_trendlines(df, int(p.get("trend_pivot") or 5))
    if p.get("ichimoku"):
        tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2           # Umkehrlinie
        kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2          # Basislinie
        span_a = ((tenkan + kijun) / 2).shift(26)                        # Wolke (26 Kerzen vorgeschoben)
        span_b = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
        df["ich_tenkan"], df["ich_kijun"] = tenkan, kijun
        df["ich_top"] = pd.concat([span_a, span_b], axis=1).max(axis=1)
        df["ich_bot"] = pd.concat([span_a, span_b], axis=1).min(axis=1)
    return df


def add_trendlines(df, k=5):
    """Schraege Linien: durch die letzten zwei bestaetigten Hochs (tl_dn) bzw. Tiefs (tl_up).
    Ein Hoch gilt erst k Kerzen spaeter als bestaetigt (kein Blick in die Zukunft).
    Spalten: Linienwert auf der Kerze und auf der Kerze davor, plus Steigung."""
    import numpy as np
    H, L = df["high"].to_numpy(float), df["low"].to_numpy(float)
    n = len(df)
    out = {name: np.full(n, np.nan) for name in ("tl_dn", "tl_dn_prev", "tl_dn_slope", "tl_up", "tl_up_prev", "tl_up_slope")}
    ph, pl = [], []
    for i in range(n):
        j = i - k
        if j - k >= 0:
            if H[j] >= H[j - k:i + 1].max():
                ph.append((j, H[j]))
            if L[j] <= L[j - k:i + 1].min():
                pl.append((j, L[j]))
        for piv, key in ((ph, "tl_dn"), (pl, "tl_up")):
            if len(piv) >= 2:
                (i1, p1), (i2, p2) = piv[-2], piv[-1]
                if i2 > i1:
                    sl = (p2 - p1) / (i2 - i1)
                    out[key][i] = p2 + sl * (i - i2)
                    out[key + "_prev"][i] = p2 + sl * (i - 1 - i2)
                    out[key + "_slope"][i] = sl
    for name, v in out.items():
        df[name] = v
    return df


def trendline_break(df, side: str) -> bool:
    """Long: Schlusskurs bricht eine FALLENDE Linie durch die letzten Hochs nach oben (Kerze davor noch darunter).
    Short: Schlusskurs bricht eine STEIGENDE Linie durch die letzten Tiefs nach unten."""
    import math
    if len(df) < 2 or "tl_dn" not in df:
        return False
    r, p = df.iloc[-1], df.iloc[-2]
    if side == "long":
        v, vp, sl = float(r["tl_dn"]), float(r["tl_dn_prev"]), float(r["tl_dn_slope"])
        return all(math.isfinite(x) for x in (v, vp, sl)) and sl < 0 and float(r["close"]) > v and float(p["close"]) <= vp
    v, vp, sl = float(r["tl_up"]), float(r["tl_up_prev"]), float(r["tl_up_slope"])
    return all(math.isfinite(x) for x in (v, vp, sl)) and sl > 0 and float(r["close"]) < v and float(p["close"]) >= vp


def ichimoku_ok(r, side: str) -> bool:
    """Long nur ueber der Wolke mit Umkehrlinie ueber Basislinie, Short umgekehrt."""
    import math
    vals = [float(r.get(k, float("nan"))) for k in ("ich_tenkan", "ich_kijun", "ich_top", "ich_bot")]
    if not all(math.isfinite(v) for v in vals):
        return False
    ten, kij, top, bot = vals
    c = float(r["close"])
    return (c > top and ten > kij) if side == "long" else (c < bot and ten < kij)


def breakout_ready(row) -> bool:
    import math
    return all(math.isfinite(float(row[k])) for k in ("bo_hi", "bo_lo", "ex_hi", "ex_lo", "atr_bo", "ema_long"))


def breakout_signal(df, allow_short: bool, prm=None) -> Optional[Signal]:
    p = {**BREAKOUT, **(prm or {})}
    if len(df) < BREAKOUT_MIN_CANDLES:   # langer Trend (EMA200) braucht genug Vorlauf, sonst ungenau
        return None
    r = df.iloc[-1]
    if not breakout_ready(r):
        return None
    if p.get("skip_weekend") and "ts" in df and df["ts"].iloc[-1].weekday() >= 5:
        return None
    vm = float(p.get("volume_mult") or 0.0)
    if vm > 0 and "volume" in df:
        avg = float(df["volume"].iloc[-21:-1].mean())
        if not avg > 0 or float(df["volume"].iloc[-1]) <= vm * avg:
            return None
    c, a = float(r["close"]), float(r["atr_bo"])
    dist = (c - float(r["ema_long"])) / a if a > 0 else 0.0     # Abstand zum langen Trend in ATR
    need = float(p.get("min_trend_atr") or 0.0)
    ich = bool(p.get("ichimoku"))
    extra = ", ueber der Ichimoku-Wolke" if ich else ""
    tl = bool(p.get("trendline"))
    if c > r["ema_long"] and dist >= need and (not ich or ichimoku_ok(r, "long")):
        if c > r["bo_hi"]:
            return Signal("long", c, c - p["stop_atr"] * a, None, "Ausbruch",
                          f"Ausbruch nach oben: hoechster Kurs seit {p['entry_n']} Kerzen, langer Trend steigt{extra}")
        if tl and trendline_break(df, "long"):
            return Signal("long", c, c - p["stop_atr"] * a, None, "Trendlinie",
                          f"Fallende Trendlinie nach oben gebrochen, langer Trend steigt{extra}")
    if allow_short and c < r["ema_long"] and -dist >= need and (not ich or ichimoku_ok(r, "short")):
        if c < r["bo_lo"]:
            return Signal("short", c, c + p["stop_atr"] * a, None, "Ausbruch",
                          f"Ausbruch nach unten: tiefster Kurs seit {p['entry_n']} Kerzen, langer Trend faellt"
                          f"{extra.replace('ueber', 'unter')}")
        if tl and trendline_break(df, "short"):
            return Signal("short", c, c + p["stop_atr"] * a, None, "Trendlinie",
                          f"Steigende Trendlinie nach unten gebrochen, langer Trend faellt{extra.replace('ueber', 'unter')}")
    return None


def breakout_exit(row, side: str) -> bool:
    """True, wenn der Trend vorbei ist (Schlusskurs bricht den Gegen-Kanal)."""
    if not breakout_ready(row):
        return False
    return float(row["close"]) < row["ex_lo"] if side == "long" else float(row["close"]) > row["ex_hi"]
