"""Indikatoren und Markt-Regime-Erkennung."""
import numpy as np
import pandas as pd


def to_df(ohlcv):
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c, h, l = df["close"], df["high"], df["low"]
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()

    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    # ADX (Trendstaerke)
    up = h.diff()
    down = -l.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr14 = df["atr"].replace(0, np.nan)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean()

    # RSI
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    # Bollinger
    df["bb_mid"] = c.rolling(20).mean()
    std = c.rolling(20).std()
    df["bb_up"] = df["bb_mid"] + 2 * std
    df["bb_low"] = df["bb_mid"] - 2 * std

    df["vol_pct"] = df["atr"] / c  # Volatilitaet relativ zum Preis
    return df


def detect_regime(row) -> str:
    """Gibt zurueck: trend_up | trend_down | range | chaos | neutral"""
    if row["vol_pct"] > 0.03:          # ATR > 3% pro Kerze: zu wild
        return "chaos"
    if row["adx"] >= 25:
        return "trend_up" if row["ema20"] > row["ema50"] else "trend_down"
    if row["adx"] < 20:
        return "range"
    return "neutral"


def strategy_for(regime: str) -> str:
    return {
        "trend_up": "Trendfolge (Daytrading)",
        "trend_down": "Trendfolge (Daytrading)",
        "range": "Scalping (Mean-Reversion)",
        "chaos": "Pause - zu volatil",
        "neutral": "Pause - kein klares Bild",
    }[regime]
