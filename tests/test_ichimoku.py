import numpy as np
import pandas as pd

from bot.strategy import add_breakout_levels, breakout_signal

PRM = {"entry_n": 30, "exit_m": 15, "min_trend_atr": 0.0}
ICH = {**PRM, "ichimoku": True}


def frame(prices):
    p = np.array(prices, float)
    return pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=len(p), freq="4h", tz="UTC"),
                         "open": p, "high": p * 1.002, "low": p * 0.998, "close": p, "volume": 1.0})


def sig(path, prm):
    return breakout_signal(add_breakout_levels(frame(path), prm), True, prm)


def test_clean_uptrend_passes_ichimoku():
    path = list(np.linspace(100, 200, 400)) + [210]
    assert sig(path, PRM).side == "long" and sig(path, ICH).side == "long"


def test_jump_below_cloud_is_blocked():
    # langer Aufwaertstrend (EMA200 tief darunter), dann Absturz und kurzer Sprung ueber das 30er-Hoch:
    # Ausbruch-Signal ja, aber der Kurs steckt noch unter der Ichimoku-Wolke -> kein Einstieg
    path = list(np.linspace(100, 300, 300)) + list(np.linspace(300, 255, 40)) + [255] * 30 + [262.65]
    assert sig(path, PRM) is not None and sig(path, PRM).side == "long"
    assert sig(path, ICH) is None


def test_weekend_and_volume_filter():
    path = list(np.linspace(100, 200, 400)) + [210]
    df = frame(path)
    df.loc[df.index[-1], "volume"] = 3.0                       # hoher Umsatz in der Ausbruchs-Kerze
    prm = {**PRM, "volume_mult": 1.5}
    wd = df["ts"].iloc[-1].weekday()
    s = breakout_signal(add_breakout_levels(df.copy(), prm), True, prm)
    assert s is not None and s.side == "long"
    df2 = df.copy(); df2.loc[df2.index[-1], "volume"] = 1.2    # normaler Umsatz -> kein Einstieg
    assert breakout_signal(add_breakout_levels(df2, prm), True, prm) is None
    prm_w = {**prm, "skip_weekend": True}
    s_w = breakout_signal(add_breakout_levels(df.copy(), prm_w), True, prm_w)
    assert (s_w is None) == (wd >= 5)
