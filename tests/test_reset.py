import pytest
import yaml

import bot.engine as E
from bot.strategy import Signal
from tests.test_engine_learning import FakeEx, ROW


def make(tmp_path, monkeypatch, mode="testnet", real=4900.0):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)

    class Ex(FakeEx):
        def equity(self):
            return self.paper_balance if mode == "paper" else real
    monkeypatch.setattr(E, "Exchange", Ex)
    cfg = {"mode": mode, "market": "futures", "symbols": ["BTC/USDT"], "timeframe": "4h", "loop_seconds": 30,
           "risk_per_trade": 0.01, "max_leverage": 10, "max_positions": 10, "daily_loss_limit": 0.03,
           "breakeven_at_r": 1.0, "trail_at_r": 2.0, "spot_max_allocation": 0.3, "paper_start_balance": 5000,
           "fee_rate": 0.0005, "dashboard_port": 8000, "preset_version": E.PRESET_VERSION, "strategy_mode": "standard",
           "account_reset": {"start": 1000.0, "at": None}}
    cfg["risk_per_trade"] = 0.0033
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    e = E.Engine(dict(cfg), "config.yaml")
    e.learn = E.L.LearningStore(tmp_path / "data/learning.json")
    return e


def old_trade(e, pnl, closed="2026-10-10T10:00:00+00:00"):
    e.trades.append({"symbol": "OLD/USDT", "side": "long", "entry": 1, "exit": 1, "amount": 1, "leverage": 1,
                     "strategy": "Trendfolge", "pnl": pnl, "reason": "Stop erreicht", "opened_at": closed,
                     "closed_at": closed, "mode": e.cfg["mode"], "exchange": "binance"})


def test_reset_closes_trades_and_starts_clean(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch)
    assert e.reset_pending() and not e._may_open()
    old_trade(e, -100.0)
    e.ex.px["A/USDT"] = 1.0
    e.positions["A/USDT"] = {"symbol": "A/USDT", "side": "long", "entry": 1.0, "amount": 10, "stop": 0.9,
                             "take_profit": 1.2, "r": 0.1, "leverage": 1, "strategy": "Trendfolge", "breakeven": False,
                             "opened_at": "2026-10-17T10:00:00+00:00", "risk_usdt": 1.0}
    e._maybe_reset()
    assert not e.positions and not e.reset_pending()
    assert e.trades[-1]["reason"] == "Neustart"
    assert e._period_trades() == []                      # alte Trades zaehlen nicht mehr
    assert len(e._my_trades()) >= 2                       # ...bleiben aber fuers Lernen gespeichert
    assert e._equity() == pytest.approx(1000.0)           # Topf statt 4900 auf dem Testkonto
    snap = e.snapshot()
    assert snap["trades_count"] == 0 and snap["total_pnl"] == 0 and snap["budget"]["start"] == 1000
    saved = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert saved["account_reset"]["at"] and saved["preset_version"] == E.PRESET_VERSION


def test_budget_follows_results_and_sizes_trades(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch)
    e._maybe_reset()
    e.ex.px["A/USDT"] = 100.0
    e._open("A/USDT", Signal("long", 100.0, 90.0, 120.0, "Trendfolge", "t"), ROW, "trend_up", e._equity())
    assert e.positions["A/USDT"]["risk_usdt"] == pytest.approx(3.3, rel=0.01)   # 0,33 % von 1000 (Hauptbot mit Nachkaufen)
    e.ex.px["A/USDT"] = 120.0
    e._close("A/USDT", 120.0, "Ziel erreicht")
    assert e._equity() == pytest.approx(1000 + e.trades[-1]["pnl"])
    assert e.snapshot()["trades_count"] == 1
    e.exchange_pnl, e._xpnl_iso = {"total": 15.0}, E.now_iso()   # Boersen-Zahl hat Vorrang
    assert e._equity() == pytest.approx(1015.0)


def test_reset_paper_balance(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch, mode="paper")
    e.ex.paper_balance = 5043.0
    e._maybe_reset()
    assert e._equity() == pytest.approx(1000.0) and e.snapshot()["equity"] == pytest.approx(1000.0)


def test_reset_retries_when_close_fails(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch)
    e.positions["A/USDT"] = {"symbol": "A/USDT", "side": "long", "entry": 1.0, "amount": 10, "stop": 0.9,
                             "take_profit": 1.2, "r": 0.1, "leverage": 1, "strategy": "Trendfolge", "breakeven": False,
                             "opened_at": "2026-10-17T10:00:00+00:00"}
    e.ex.px["A/USDT"] = 1.0
    e.ex.close = lambda *a: (_ for _ in ()).throw(RuntimeError("Netz weg"))
    e._maybe_reset()
    assert e.reset_pending() and "A/USDT" in e.positions


def test_all_bots_get_breakout_presets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E, "Exchange", FakeEx)
    base = {"mode": "paper", "market": "futures", "symbols": ["X/USDT"], "timeframe": "5m", "risk_per_trade": 0.03,
            "max_positions": 5, "fee_rate": 0.0004, "symbol_mode": "scanner", "max_leverage": 5, "preset_version": 2,
            "paper_start_balance": 5000}
    for name, tf, spot in (("config_5min.yaml", "1d", False), ("config_bybit.yaml", "4h", True)):
        (tmp_path / name).write_text(yaml.safe_dump(base))
        e = E.Engine(dict(base), name)
        assert e.breakout() and e.cfg["timeframe"] == tf
        assert e.cfg["risk_per_trade"] == (0.0025 if spot else 0.005) and bool(e.cfg.get("pyramid")) == spot
        assert e.reset_pending() and e.cfg["symbol_mode"] == "manual" and len(e.cfg["symbols"]) == 10
    assert e.cfg["symbols"][0] == "BTC/USDC"


def test_deposit_grows_pot_not_profit(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch)
    e._maybe_reset()
    e._check_day(e._equity())
    r = e.deposit(300)
    assert r["ok"] and e._equity() == pytest.approx(1300.0)
    snap = e.snapshot()
    assert snap["total_pnl"] == 0 and snap["budget"]["start"] == 1300
    assert e.day_start_equity == pytest.approx(1300.0)
    assert not e.deposit(-5)["ok"]
    saved = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert saved["account_reset"]["deposits"][0]["amount"] == 300


def test_deposit_paper(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch, mode="paper")
    e._maybe_reset()
    e.deposit(300)
    assert e._equity() == pytest.approx(1300.0)


def test_tag_bot_moves_to_kraken_prices(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E, "Exchange", FakeEx)
    base = {"mode": "paper", "market": "futures", "symbols": ["BTC/USDT"], "timeframe": "1d", "risk_per_trade": 0.01,
            "max_positions": 10, "fee_rate": 0.0005, "symbol_mode": "manual", "max_leverage": 5, "preset_version": 7,
            "paper_start_balance": 1000, "account_reset": {"start": 1000.0, "at": "2026-10-18T00:04:02+00:00"}}
    (tmp_path / "config_5min.yaml").write_text(yaml.safe_dump(base))
    e = E.Engine(dict(base), "config_5min.yaml")
    assert e.cfg["exchange"] == "krakenfutures" and e.cfg["quote"] == "USD" and e.cfg["symbols"][0] == "BTC/USD"
    assert e.reset_pending() and e.cfg["risk_per_trade"] == 0.005


def test_testnet_pot_not_capped_by_smaller_demo_balance(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch, real=4775.0)
    e._maybe_reset()
    e.deposit(4000)                                       # Topf 5000, Demo-Konto nur 4775
    assert e._equity() == pytest.approx(5000.0)           # kein Schein-Verlust von -4,5 %
    e._check_day(e._equity())
    assert not e.halted_until


def test_live_pot_still_capped(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch, mode="live", real=800.0)
    e._maybe_reset()
    assert e._equity() == pytest.approx(800.0)            # echtes Geld: nie mehr als wirklich da ist


def test_minimal_config_gets_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E, "Exchange", FakeEx)
    cfg = {"mode": "paper", "market": "futures", "symbols": ["BTC/USDT"], "timeframe": "1h", "risk_per_trade": 0.005,
           "strategy_mode": "ausbruch", "data_dir": "d", "preset_version": E.PRESET_VERSION}
    e = E.Engine(dict(cfg), "config_x.yaml")
    assert e.cfg["spot_max_allocation"] == 0.2 and e.cfg["breakeven_at_r"] == 1.0 and e.cfg["learned"]["rules"] == []


def test_preset10_makes_daytrade_risky(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E, "Exchange", FakeEx)
    cfg = {"mode": "paper", "market": "futures", "symbols": ["BTC/USDT"], "timeframe": "1h", "risk_per_trade": 0.005,
           "strategy_mode": "ausbruch", "data_dir": "d", "preset_version": 9, "bot_name": "Daytrade 1h",
           "pyramid": {"step_atr": 1.0, "max_units": 1}}
    (tmp_path / "config_daytrade.yaml").write_text(yaml.safe_dump(cfg))
    e = E.Engine(dict(cfg), "config_daytrade.yaml")
    assert e.cfg["risk_per_trade"] == 0.005 and e.cfg["pyramid"]["max_units"] == 2 and e.cfg["bot_name"] == "Daytrade riskant"
    saved = yaml.safe_load((tmp_path / "config_daytrade.yaml").read_text())
    assert saved["preset_version"] == E.PRESET_VERSION and saved["risk_per_trade"] == 0.005


def test_preset13_halves_risk():
    import bot.engine as E
    for f, ch in E.PRESETS[13].items():
        assert ch["risk_per_trade"] <= 0.01 and ch["max_leverage"] <= 10
    assert E.PRESETS[13]["config_riskant.yaml"]["risk_per_trade"] == 0.01
    assert E.PRESETS[13]["config.yaml"]["risk_per_trade"] == 0.00165


def test_exit_level_moves_and_notifies(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch, mode="paper")
    sent = []
    e.notify.send = lambda m, important=False: sent.append(m)
    p = {"symbol": "A/USDT", "side": "long", "entry": 100.0, "amount": 2.0, "stop": 96.0}
    e._update_exit_level("A/USDT", p, {"ex_lo": 97.0, "ex_hi": 110.0, "atr_bo": 2.0})
    assert p["exit_level"] == 97.0 and not sent                 # erster Wert: nur merken
    e._update_exit_level("A/USDT", p, {"ex_lo": 97.5, "ex_hi": 110.0, "atr_bo": 2.0})
    assert p["exit_level"] == 97.5 and not sent                 # < 0,5 ATR: keine Nachricht
    e._update_exit_level("A/USDT", p, {"ex_lo": 101.0, "ex_hi": 110.0, "atr_bo": 2.0})
    assert len(sent) == 1 and "101" in sent[0] and "+2.00" in sent[0]
