import numpy as np, pandas as pd, yaml
from bot.strategy import add_breakout_levels, add_trendlines, breakout_signal, trendline_break

def _df(n=400, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"open": c, "high": c * 1.004, "low": c * 0.996, "close": c, "volume": 1.0,
                         "ts": pd.date_range("2026-01-05", periods=n, freq="4h")})

def test_no_lookahead():
    d = _df()
    full = add_trendlines(d.copy(), 5)
    for cut in (150, 220, 300, 399):
        part = add_trendlines(d.iloc[:cut + 1].copy(), 5)
        for col in ("tl_dn", "tl_up", "tl_dn_slope"):
            a, b = full[col].iloc[cut], part[col].iloc[cut]
            assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9

def test_break_long_detected():
    n = 300
    c = np.r_[np.linspace(100, 140, 230), np.linspace(140, 120, 30) + np.tile([0, 4, 0, -4, 0], 6)[:30] * 0, np.linspace(120, 130, 40)]
    d = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0})
    # zwei fallende Hochs einbauen
    d.loc[240, "high"] = 141; d.loc[252, "high"] = 136
    d = add_trendlines(d, 5)
    hits = [i for i in range(255, n) if trendline_break(d.iloc[:i + 1], "long")]
    assert hits, "Bruch der fallenden Linie nicht erkannt"

def test_config_and_signal_runs():
    c = yaml.safe_load(open("config_trend.yaml"))
    assert c["dashboard_port"] == 8021 and c["breakout_params"]["trendline"] is True and c["mode"] == "paper"
    d = add_breakout_levels(_df(), c["breakout_params"])
    for i in range(270, 400):
        breakout_signal(d.iloc[:i + 1], True, c["breakout_params"])
