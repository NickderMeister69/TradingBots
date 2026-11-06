import numpy as np
import pandas as pd
import pytest
import yaml

import bot.engine as E
from bot.backtest import run_breakout_backtest
from bot.strategy import Signal, add_breakout_levels, breakout_signal, breakout_exit, BREAKOUT_MIN_CANDLES
from tests.test_engine_learning import FakeEx, ROW


def _df(closes):
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=len(c), freq="4h", tz="UTC"),
                         "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1.0})


def test_breakout_long_signal_and_exit():
    closes = list(np.linspace(100, 120, 300)) + [130]       # langer Aufwaertstrend, dann neues Hoch
    df = add_breakout_levels(_df(closes))
    sig = breakout_signal(df, allow_short=True)
    assert sig and sig.side == "long" and sig.take_profit is None and sig.stop < sig.entry
    df2 = add_breakout_levels(_df(closes + [100]))          # faellt unter das 20-Kerzen-Tief
    assert breakout_exit(df2.iloc[-1], "long")
    assert not breakout_exit(df.iloc[-1], "long")


def test_no_long_against_long_trend():
    closes = list(np.linspace(200, 100, 300)) + [99, 98, 120]   # kurzer Sprung, aber langer Trend faellt
    df = add_breakout_levels(_df(closes))
    sig = breakout_signal(df, allow_short=True)
    assert sig is None or sig.side != "long"


def test_too_few_candles_no_signal():
    df = add_breakout_levels(_df(np.linspace(100, 130, 100)))
    assert breakout_signal(df, allow_short=True) is None


def test_backtest_runs_and_counts_costs():
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 1500)))
    ohlcv = [[int(t.timestamp() * 1000), x, x * 1.01, x * 0.99, x, 1] for t, x in
             zip(pd.date_range("2025-01-01", periods=len(c), freq="4h", tz="UTC"), c)]
    cfg = dict(fee_rate=0.0005, risk_per_trade=0.01, max_leverage=10, spot_max_allocation=0.2, timeframe="4h")
    r = run_breakout_backtest(ohlcv, cfg, False)
    assert r["trades"] > 5 and "error" not in r
    free = run_breakout_backtest(ohlcv, {**cfg, "fee_rate": 0.0, "funding_8h": 0.0}, False)
    assert free["total_r"] > r["total_r"]
    assert "error" in run_breakout_backtest(ohlcv[:BREAKOUT_MIN_CANDLES], cfg, False)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(E, "Exchange", FakeEx)
    cfg = {"mode": "testnet", "market": "futures", "symbols": ["BTC/USDT"], "timeframe": "4h", "loop_seconds": 30,
           "risk_per_trade": 0.0025, "max_leverage": 10, "max_positions": 10, "daily_loss_limit": 0.03,
           "breakeven_at_r": 1.0, "trail_at_r": 2.0, "spot_max_allocation": 0.3, "paper_start_balance": 1000,
           "fee_rate": 0.0005, "dashboard_port": 8000, "strategy_mode": "ausbruch", "max_open_risk": 0.006,
           "preset_version": E.PRESET_VERSION}
    e = E.Engine(cfg)
    e.learn = E.L.LearningStore(tmp_path / "data/learning.json")
    return e


def test_breakout_position_no_target_no_trailing(eng):
    eng.ex.px["A/USDT"] = 100.0
    eng._open("A/USDT", Signal("long", 100.0, 90.0, None, "Ausbruch", "test"), ROW, "trend_up", 5000)
    p = eng.positions["A/USDT"]
    assert p["take_profit"] is None and p["risk_usdt"] == pytest.approx(12.5, rel=0.01)
    eng._manage("A/USDT", 140.0)                 # +4 R: Standard wuerde absichern und nachziehen
    assert p["stop"] == pytest.approx(90.0) and not p["breakeven"]
    assert "A/USDT" in eng.positions
    eng._manage("A/USDT", 89.0)                  # fester Stop greift
    assert "A/USDT" not in eng.positions


def test_breakout_ignores_learned_block_and_pros(eng):
    E.L.learned(eng.cfg)["rules"].append({"id": "b", "dim": "setup", "value": "Scalping Long", "action": "block"})
    eng.cfg["follow_pros"] = "nur_mit"
    eng.ex.top_trader_ratio = lambda s: {"top_long": 0.2, "crowd_long": 0.2}
    eng.ex.px["A/USDT"] = 100.0
    eng._open("A/USDT", Signal("long", 100.0, 90.0, None, "Ausbruch", "t"), ROW, "trend_up", 5000)
    assert "A/USDT" in eng.positions


def test_open_risk_cap(eng):
    for s in ("A/USDT", "B/USDT", "C/USDT"):
        eng.ex.px[s] = 100.0
        eng._open(s, Signal("long", 100.0, 90.0, None, "Ausbruch", "t"), ROW, "trend_up", 5000)
    assert len(eng.positions) == 2                 # 2 x 0,25 % = 0,5 %, Grenze 0,6 %
    assert "genug Trades" in eng.log[-1]["msg"]


def test_learning_cycle_off_in_breakout(eng):
    eng._learn_dirty = True
    eng._learning_cycle()
    assert eng._learn_dirty is True


def test_preset_applied_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E, "Exchange", FakeEx)
    cfg = {"mode": "testnet", "market": "futures", "symbols": ["X/USDT"], "timeframe": "15m", "risk_per_trade": 0.01,
           "max_positions": 5, "fee_rate": 0.0004, "symbol_mode": "scanner", "max_leverage": 10}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    e = E.Engine(dict(cfg), "config.yaml")
    assert e.cfg["strategy_mode"] == "ausbruch" and e.cfg["timeframe"] == "4h" and e.cfg["risk_per_trade"] == 0.0025 and e.cfg["max_open_risk"] == 0.05 and e.cfg["pyramid"]["max_units"] == 5
    assert e._preset_changes
    saved = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert saved["preset_version"] == E.PRESET_VERSION and saved["fee_rate"] == 0.0005
    saved["risk_per_trade"] = 0.005                # du aenderst danach etwas -> bleibt so
    e2 = E.Engine(saved, "config.yaml")
    assert e2.cfg["risk_per_trade"] == 0.005 and not e2._preset_changes


def test_dashboard_settings_strategy(eng):
    r = eng.update_settings({"strategy_mode": "standard", "max_open_risk": 0.025})
    assert eng.cfg["strategy_mode"] == "standard" and eng.cfg["max_open_risk"] == 0.025 and r["changed"]


def test_pyramid_adds_and_caps_total_risk(eng):
    eng.cfg["pyramid"] = {"step_atr": 1.0, "max_units": 3}
    eng.cfg["max_open_risk"] = 0
    eng.ex.px["A/USDT"] = 100.0
    eng._open("A/USDT", Signal("long", 100.0, 90.0, None, "Ausbruch", "t"), ROW, "trend_up", 5000)
    p = eng.positions["A/USDT"]
    risk0 = p["risk_usdt"]
    assert p["atr"] == pytest.approx(5.0) and p["next_add"] == pytest.approx(105.0)
    eng.ex.px["A/USDT"] = 105.0; eng._manage("A/USDT", 105.0)
    assert len(p["units"]) == 2 and p["entry"] == pytest.approx(102.5)
    # Gesamtverlust am Stop = geplantes Anfangsrisiko
    assert (p["entry"] - p["stop"]) * p["amount"] == pytest.approx(risk0, rel=1e-6)
    eng.ex.px["A/USDT"] = 110.0; eng._manage("A/USDT", 110.0)
    eng.ex.px["A/USDT"] = 120.0; eng._manage("A/USDT", 120.0)
    assert len(p["units"]) == 3                    # nie mehr als 3 Teile
    assert (p["entry"] - p["stop"]) * p["amount"] <= risk0 + 1e-6
    stop = p["stop"]
    eng.ex.px["A/USDT"] = stop - 0.01; eng._manage("A/USDT", stop - 0.01)
    t = eng.trades[-1]
    assert t["pnl"] > -risk0 * 1.05 and t["r_mult"] > -1.05


def test_pyramid_backtest_more_r_than_without():
    rng = np.random.default_rng(7)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0015, 0.02, 2500)))
    ohlcv = [[int(t.timestamp() * 1000), x, x * 1.01, x * 0.99, x, 1] for t, x in
             zip(pd.date_range("2024-01-01", periods=len(c), freq="4h", tz="UTC"), c)]
    cfg = dict(fee_rate=0.0005, risk_per_trade=0.005, max_leverage=10, spot_max_allocation=1, timeframe="4h")
    plain = run_breakout_backtest(ohlcv, cfg, False)
    pyr = run_breakout_backtest(ohlcv, {**cfg, "pyramid": {"step_atr": 1.0, "max_units": 3}}, False)
    assert pyr["trades"] >= plain["trades"] and pyr["total_r"] > plain["total_r"]   # Stop rueckt nach -> eher raus


def test_breakout_uses_fixed_leverage(eng):
    eng.ex.px["A/USDT"] = 100.0
    eng._open("A/USDT", Signal("long", 100.0, 95.0, None, "Ausbruch", "t"), {**ROW, "vol_pct": 0.05}, "trend_up", 1000)
    assert eng.positions["A/USDT"]["leverage"] == 5
    eng.cfg["max_leverage"] = 3
    assert eng._leverage(0.05, "Ausbruch") == 3
    eng.ex.is_spot = True
    assert eng._leverage(0.05, "Ausbruch") == 1


def test_min_trend_filter():
    rng = np.random.default_rng(5)
    closes = list(np.linspace(100, 101, 300) * (1 + rng.normal(0, 0.002, 300))) + [103]
    df = add_breakout_levels(_df(closes))
    assert breakout_signal(df, allow_short=True) is not None                      # ohne Filter: Einstieg
    assert breakout_signal(df, allow_short=True, prm={"min_trend_atr": 50}) is None  # Trend nicht klar genug


def test_kraken_stop_and_equity_params():
    from bot.exchange import Exchange
    calls = []

    class FakeKraken:
        markets = {"BTC/USD:USD": {}}
        def amount_to_precision(self, s, a): return a
        def price_to_precision(self, s, p): return p
        def create_order(self, *a): calls.append(a); return {"id": "x"}
        def privateGetAccounts(self):
            return {"accounts": {"cash": {"type": "cashAccount", "balances": {"usd": "5"}},
                                 "flex": {"type": "multiCollateralMarginAccount", "balanceValue": "1234.5"}}}

    ex = Exchange.__new__(Exchange)
    ex.name, ex.mode, ex.is_spot, ex.quote = "krakenfutures", "testnet", False, "USD"
    ex.ex = FakeKraken()
    ex.public = type("P", (), {"markets": {"BTC/USD:USD": {}}})()
    assert ex.place_stop("BTC/USD", "long", 0.01, 80000) == "x"
    params = calls[0][-1]
    assert params["stopLossPrice"] == 80000 and params["reduceOnly"] and params["triggerSignal"] == "mark"
    assert "workingType" not in params
    assert ex.equity() == 1234.5


def test_kraken_without_flex_uses_cash():
    from bot.exchange import kraken_accounts
    class K:
        def privateGetAccounts(self): return {"accounts": {"cash": {"type": "cashAccount", "balances": {"usd": "50000"}}}}
    v, name, ov = kraken_accounts(K())
    assert v == 50000 and name == "cash" and ov == [("cash", "cashAccount")]


def test_kraken_accounts_tolerates_odd_shapes():
    from bot.exchange import kraken_accounts
    class K:
        def privateGetAccounts(self):
            return {"result": "success", "serverTime": "2026-10-18",
                    "accounts": {"cash": {"type": "cashAccount", "balances": {"usd": "10"}}, "note": "x",
                                 "flex": {"type": "multiCollateralMarginAccount", "balanceValue": "50000"}}}
    assert kraken_accounts(K())[0:2] == (50000.0, "flex")


def test_dashboard_pyramid_units(eng):
    eng.cfg["pyramid"] = {"step_atr": 1.0, "max_units": 3}
    r = eng.update_settings({"pyramid_max_units": "5"})
    assert eng.cfg["pyramid"]["max_units"] == 5 and r["changed"]
    assert eng.snapshot()["config"]["pyramid_max_units"] == 5
    eng.update_settings({"pyramid_max_units": 99})
    assert eng.cfg["pyramid"]["max_units"] == 6


def test_no_reentry_on_old_signal(monkeypatch, tmp_path):
    """Nach einem Stop (oder Neustart) mitten in der Kerze: kein neuer Einstieg auf das alte Signal."""
    import bot.engine as E2
    from datetime import datetime, timezone
    from tests.test_engine_learning import FakeEx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E2, "Exchange", FakeEx)
    cfg = {"mode": "paper", "market": "futures", "symbols": ["A/USDT"], "timeframe": "4h", "risk_per_trade": 0.01,
           "strategy_mode": "ausbruch", "data_dir": "d", "preset_version": E2.PRESET_VERSION}
    e = E2.Engine(dict(cfg), "config_x.yaml")
    hour = 3600 * 1000
    now = datetime.now(timezone.utc).timestamp() * 1000

    def candles(end_ms):
        # 330 Kerzen, steigend, die letzte abgeschlossene schliesst klar ueber dem 55er-Hoch
        rows, start = [], end_ms - 330 * 4 * hour
        for i in range(330):
            c = 100 + i * 0.1 + (0.3 if i % 2 else -0.3) + (8 if i == 329 else 0)
            rows.append([start + i * 4 * hour, c - 0.2, c + 0.3, c - 0.5, c, 1])
        return rows
    opened = []
    monkeypatch.setattr(e, "_open", lambda *a, **k: opened.append(a[0]))
    monkeypatch.setattr(e, "_may_open", lambda: True)
    # Kerze ist vor 3 Stunden zu Ende gegangen -> altes Signal -> kein Einstieg
    e.ex.ohlcv = lambda *a, **k: candles(now - 3 * hour)
    e._tick_symbol("A/USDT", 5000)
    assert opened == []
    # Kerze ist gerade eben zu Ende gegangen -> frisches Signal -> Einstieg
    e.last_signal_ts.clear()
    e.ex.ohlcv = lambda *a, **k: candles(now - 60 * 1000)
    e._tick_symbol("A/USDT", 5000)
    assert opened == ["A/USDT"]


def test_btc_filter_blocks_against_bitcoin(monkeypatch, tmp_path):
    import bot.engine as E2
    from datetime import datetime, timezone
    from tests.test_engine_learning import FakeEx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E2, "Exchange", FakeEx)
    cfg = {"mode": "paper", "market": "futures", "symbols": ["A/USDT"], "timeframe": "4h", "risk_per_trade": 0.01,
           "strategy_mode": "ausbruch", "data_dir": "d", "preset_version": E2.PRESET_VERSION, "btc_filter": True}
    e = E2.Engine(dict(cfg), "config_x.yaml")
    hour = 3600 * 1000
    end = datetime.now(timezone.utc).timestamp() * 1000 - 60 * 1000
    rows, start = [], end - 330 * 4 * hour
    for i in range(330):
        c = 100 + i * 0.1 + (0.3 if i % 2 else -0.3) + (8 if i == 329 else 0)
        rows.append([start + i * 4 * hour, c - 0.2, c + 0.3, c - 0.5, c, 1])
    e.ex.ohlcv = lambda *a, **k: rows
    opened = []
    monkeypatch.setattr(e, "_open", lambda *a, **k: opened.append(a[0]))
    monkeypatch.setattr(e, "_may_open", lambda: True)
    monkeypatch.setattr(e, "_btc_trend", lambda: -1)      # Bitcoin faellt -> kein Long
    e._tick_symbol("A/USDT", 5000)
    assert opened == []
    e.last_signal_ts.clear()
    monkeypatch.setattr(e, "_btc_trend", lambda: 1)       # Bitcoin steigt -> Long erlaubt
    e._tick_symbol("A/USDT", 5000)
    assert opened == ["A/USDT"]


def test_btc_trend_from_candles(monkeypatch, tmp_path):
    import bot.engine as E2
    from datetime import datetime, timezone
    from tests.test_engine_learning import FakeEx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E2, "Exchange", FakeEx)
    cfg = {"mode": "paper", "market": "futures", "symbols": ["A/USDT"], "timeframe": "1h", "risk_per_trade": 0.01,
           "strategy_mode": "ausbruch", "data_dir": "d", "preset_version": E2.PRESET_VERSION, "btc_filter": True}
    e = E2.Engine(dict(cfg), "config_x.yaml")
    hour = 3600 * 1000
    end = datetime.now(timezone.utc).timestamp() * 1000
    down = [[end - (300 - i) * hour, 0, 0, 0, 200 - i * 0.5, 0] for i in range(300)]
    e.ex.ohlcv = lambda *a, **k: down
    assert e._btc_trend() == -1


def test_preset11_all_bots(monkeypatch, tmp_path):
    import yaml as Y
    import bot.engine as E2
    from tests.test_engine_learning import FakeEx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E2, "Exchange", FakeEx)
    for f, n in [("config.yaml", 30), ("config_daytrade_sicher.yaml", 55)]:
        cfg = {"mode": "paper", "market": "futures", "symbols": ["BTC/USDT"], "timeframe": "4h", "risk_per_trade": 0.01,
               "strategy_mode": "ausbruch", "data_dir": "d_" + f, "preset_version": 10,
               "breakout_params": {"min_trend_atr": 2.0},
               "account_reset": {"start": 5000.0, "at": "2026-10-18T19:00:00+00:00", "deposits": [{"amount": 4000}]}}
        (tmp_path / f).write_text(Y.safe_dump(cfg))
        e = E2.Engine(dict(cfg), f)
        assert e.cfg["btc_filter"] is True and e.reset_pending()
        assert e.cfg["account_reset"]["start"] == 5000.0
        assert {**E2.BREAKOUT, **(e.cfg.get("breakout_params") or {})}["entry_n"] == n
