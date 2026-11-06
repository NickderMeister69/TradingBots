import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bot.engine as E
from bot.dashboard import create_app
from bot.strategy import Signal

SRC_STATE = Path("/mnt/user-data/uploads/tradebot/data/state.json")


class FakeEx:
    is_spot = False
    paper_balance = 5000.0
    name, label, quote = "binance", "Binance", "USDT"

    def __init__(self, cfg):
        self.px, self.blocked = {}, set()
        self.reject = None

    def equity(self): return 5000.0
    def supports_exchange_stop(self): return False
    def open(self, sym, side, amount, lev):
        if self.reject:
            raise RuntimeError(self.reject)
        return self.px.get(sym, 1.0), amount
    def close(self, sym, side, amount): return self.px[sym]
    def price(self, sym): return self.px[sym]
    def book_paper_pnl(self, pnl): pass
    def open_position_sizes(self): return None
    def history(self, *a): raise RuntimeError("offline")
    def top_trader_ratio(self, sym): return {"top_long": 0.6, "crowd_long": 0.42}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    if SRC_STATE.exists():
        shutil.copy(SRC_STATE, tmp_path / "data/state.json")
    monkeypatch.setattr(E, "Exchange", FakeEx)
    cfg = {"mode": "testnet", "market": "futures", "symbols": ["BTC/USDT"], "timeframe": "15m", "loop_seconds": 30,
           "risk_per_trade": 0.01, "max_leverage": 5, "max_positions": 3, "daily_loss_limit": 0.03,
           "breakeven_at_r": 1.0, "trail_at_r": 2.0, "spot_max_allocation": 0.3, "paper_start_balance": 1000,
           "fee_rate": 0.0004, "dashboard_port": 8000, "preset_version": E.PRESET_VERSION}
    (tmp_path / "config.yaml").write_text("x: 1")
    e = E.Engine(cfg)
    e.learn = E.L.LearningStore(tmp_path / "data/learning.json")
    return e


ROW = {"adx": 31.0, "rsi": 50.0, "vol_pct": 0.006, "atr": 0.01}


def test_trade_records_context_and_r(engine):
    engine.ex.px["Z/USDT"] = 1.0
    engine._open("Z/USDT", Signal("long", 1.0, 0.98, 1.06, "Trendfolge", "test"), ROW, "trend_up", 5000)
    p = engine.positions["Z/USDT"]
    assert p["ctx"]["top_long"] == 0.6
    assert p["ctx"]["adx"] == 31.0 and p["risk_usdt"] == pytest.approx(50, rel=0.01)
    engine.ex.px["Z/USDT"] = 1.04
    engine._close("Z/USDT", 1.04, "Ziel erreicht")
    t = engine.trades[-1]
    assert t["r_mult"] == pytest.approx(2.0, rel=0.05) and t["ctx"]["regime"] == "trend_up"


def test_block_rule_prevents_open(engine):
    E.L.learned(engine.cfg)["rules"].append({"id": "b", "dim": "setup", "value": "Trendfolge Short", "action": "block"})
    engine.ex.px["Z/USDT"] = 1.0
    engine._open("Z/USDT", Signal("short", 1.0, 1.02, 0.94, "Trendfolge", "t"), ROW, "trend_down", 5000)
    assert "Z/USDT" not in engine.positions
    assert "gesperrt" in engine.log[-1]["msg"]


def test_dashboard_learning_flow(engine):
    engine.cfg["learning"] = {"auto_apply": False}
    engine.learn_analyze()
    client = TestClient(create_app(engine))
    st = client.get("/api/state").json()
    L = st["learning"]
    assert L["trades"] == len(engine._my_trades())
    print("\nTrades:", L["trades"], "Vorschlaege:", [s["title"] for s in L["suggestions"]])
    for b in L["stats"]:
        print(f"  {b['dim_label']:<22} {b['value']:<18} n={b['n']:<3} avgR={b['avg_r']:+.2f} pnl={b['pnl']:+.2f}")
    assert "Lernen" in client.get("/klassisch").text
    # Coin-Vorschlag erzwingen und uebernehmen
    engine.trades += [dict(engine.trades[0], closed_at=E.now_iso(), pnl=-10.0, symbol="ZZ/USDT")] * 2
    engine.learn_analyze()
    sid = next(s["id"] for s in engine.learn.suggestions if s["kind"] == "coin" and s["symbol"] == "ZZ/USDT")
    assert client.post(f"/api/learn/apply/{sid}").json()["ok"]
    assert E.L.coin_blocked_until(engine.cfg, "ZZ/USDT")
    assert "learned" in Path("config.yaml").read_text()
    act = client.get("/api/state").json()["learning"]["active"]
    assert any(a["id"] == "coin:ZZ/USDT" for a in act)
    assert client.post("/api/learn/undo/coin:ZZ/USDT").json()["ok"]
    assert not E.L.coin_blocked_until(engine.cfg, "ZZ/USDT")
    assert client.post("/api/learn/reset").json()["ok"]


def test_total_drawdown_kill_persists(engine, tmp_path):
    engine.ex.px.update({"Z/USDT": 1.0})
    engine.ex.px.update({s: p["entry"] for s, p in engine.positions.items()})
    engine._open("Z/USDT", Signal("long", 1.0, 0.98, 1.06, "Trendfolge", "t"), ROW, "trend_up", 5000)
    engine._check_total_drawdown(5000)
    engine._check_total_drawdown(4100)          # -18 %: noch ok
    assert not engine.kill_reason
    engine.ex.px["Z/USDT"] = 0.99
    engine._check_total_drawdown(3990)          # -20.2 %
    assert engine.kill_reason and engine.paused and not engine.positions
    e2 = E.Engine(dict(engine.cfg))
    assert e2.kill_reason and e2.paused        # bleibt nach Neustart aktiv
    e2.resume()
    assert not e2.kill_reason and not e2.paused


def test_capital_cap(engine):
    engine.cfg["max_capital_usdt"] = 1000
    assert engine._sizing_equity(5000) == 1000
    engine.cfg["max_capital_usdt"] = 0
    assert engine._sizing_equity(5000) == 5000


def test_auto_apply_learning(engine):
    engine.trades += [dict(engine.trades[0], closed_at=E.now_iso(), pnl=-10.0, symbol="ZZ/USDT")] * 2
    engine.learn_analyze()
    assert E.L.coin_blocked_until(engine.cfg, "ZZ/USDT")
    assert any("Automatisch" in h["text"] for h in engine.learn.history)
    engine.update_settings({"learning_auto_apply": False})
    assert E.L.settings(engine.cfg)["auto_apply"] is False


def test_preflight_blocks_live(engine):
    engine.ex.preflight = lambda: {"ok": False, "problems": ["Futures gesperrt"], "notes": []}
    engine._connect()
    assert engine.paused and engine.preflight["problems"]
    engine.resume()
    assert engine.paused


def test_notifier_sends(monkeypatch):
    import bot.notify as N
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(N, "_post", lambda tok, m, d, timeout=15: sent.append(d["text"]) or {"ok": True})
    n = N.Notifier()
    n.send("hallo", important=True)
    import time; time.sleep(0.5)
    assert sent == ["🚨 hallo"]
    n2 = N.Notifier(prefix="Bybit EU"); n2.send("x"); time.sleep(0.5)
    assert sent[-1] == "[Bybit EU] x"


def test_spot_equity_counts_held_coins(engine):
    engine.ex.is_spot = True
    engine.positions.clear()   # Spot: alte Futures-Positionen aus der Datei passen nicht ins Spot-Guthaben
    engine.ex.px["Z/USDT"] = 2.0
    engine._open("Z/USDT", Signal("long", 2.0, 1.9, 2.3, "Trendfolge", "t"), ROW, "trend_up", 5000)
    amt = engine.positions["Z/USDT"]["amount"]
    engine.market_view["Z/USDT"] = {"price": 2.1}
    open_before = sum(p["amount"] * p["entry"] for s, p in engine.positions.items() if s != "Z/USDT")
    assert engine._equity() == pytest.approx(5000 + 2.1 * amt + sum(
        p["amount"] * p["entry"] for s, p in engine.positions.items() if s != "Z/USDT"))


def test_other_exchange_does_not_load_binance_positions(engine, monkeypatch):
    class BybitFake(FakeEx):
        name, label, quote, is_spot = "bybiteu", "Bybit EU", "USDC", True
    monkeypatch.setattr(E, "Exchange", BybitFake)
    e2 = E.Engine(dict(engine.cfg))
    assert e2.positions == {} and e2._my_trades() == []


def test_tradfi_perp_gets_blocked_and_persisted(engine):
    engine.ex.reject = 'binanceusdm {"code":-4411,"msg":"Please sign TradFi-Perps agreement contract fapi."}'
    engine.auto_symbols = ["KORU/USDT", "BTC/USDT"]
    engine._open("KORU/USDT", Signal("long", 1.0, 0.98, 1.06, "Trendfolge", "t"), ROW, "trend_up", 5000)
    assert "KORU/USDT" in engine.ex.blocked and engine.auto_symbols == ["BTC/USDT"]
    e2 = E.Engine(dict(engine.cfg))
    assert "KORU/USDT" in e2.ex.blocked


def test_is_crypto_market():
    from bot.exchange import Exchange
    assert Exchange.is_crypto_market({"info": {"underlyingType": "COIN", "contractType": "PERPETUAL"}})
    assert not Exchange.is_crypto_market({"info": {"underlyingType": "EQUITY", "contractType": "PERPETUAL"}})
    assert not Exchange.is_crypto_market({"info": {"underlyingType": "COIN", "contractType": "TRADIFI_PERPETUAL"}})
    assert Exchange.is_crypto_market({"info": {}})


def test_scan_always(engine):
    engine.cfg["symbol_mode"] = "scanner"
    engine.cfg["scan_always"] = ["BTC/USDT"]
    engine.auto_symbols = ["ETH/USDT"]
    assert engine.active_symbols()[:2] == ["ETH/USDT", "BTC/USDT"]
    engine.cfg["symbol_mode"] = "manual"
    assert "ETH/USDT" not in engine.active_symbols()
    engine.update_settings({"scan_min_volume_m": 0.3})
    assert engine.cfg["scan_min_volume_m"] == 0.3


def test_reconcile_corrects_exchange_stop_trades(engine):
    for t in engine.trades:
        if t["reason"] == "Stop an der Boerse ausgeloest":
            t.pop("verified", None)
            if "pnl_bot" in t:
                t["pnl"] = t.pop("pnl_bot")
    q = next(t for t in engine.trades if t["symbol"] == "Q/USDT")
    engine.ex.close_fill = lambda sym, side, o, c=None: {"price": 0.0521, "amount": q["amount"], "fee": 0} if sym == "Q/USDT" else None
    engine._reconcile_history()
    q = next(t for t in engine.trades if t["symbol"] == "Q/USDT")
    assert q["verified"] and q["pnl"] < 0 and q["pnl_bot"] > 300
    tia = next(t for t in engine.trades if t["symbol"] == "TIA/USDT")
    assert tia["verified"] is False
    before = q["pnl"]; engine._reconcile_history()
    assert q["pnl"] == before


def test_engine_ampel_updates(engine):
    engine._update_ampel({"current_test": {"total_r": -30, "trades": 30}, "best_test": {"total_r": -18, "trades": 30}}, False)
    assert E.L.learned(engine.cfg)["ampel"]["mult"] == 0.5
    engine.cfg["learning"] = {"auto_apply": False}
    engine._update_ampel({"current_test": {"total_r": -30, "trades": 30}, "best_test": {"total_r": -18, "trades": 30}}, False)
    assert E.L.learned(engine.cfg)["ampel"]["mult"] == 1.0


def test_btc_always_by_default_and_editable(engine):
    assert engine.cfg["scan_always"] == ["BTC/USDT"]
    engine.ex.is_valid = lambda s: True
    engine.update_settings({"scan_always": "BTC, ETH"})
    assert engine.cfg["scan_always"] == ["BTC/USDT", "ETH/USDT"]
    engine.update_settings({"scan_always": ""})
    assert engine.cfg["scan_always"] == []


def test_majors_and_top_volume(engine):
    engine.cfg["symbol_mode"] = "scanner"
    engine.ex.is_valid = lambda s: s != "TON/USDT"
    engine._valid_cache = {}
    engine.top_volume_syms = ["PEPE/USDT", "BTC/USDT"]
    w = engine.watch_extra()
    assert "ETH/USDT" in w and "DOGE/USDT" in w and "PEPE/USDT" in w and "TON/USDT" not in w
    assert w.count("BTC/USDT") == 1
    engine.update_settings({"scan_majors": False})
    assert "ETH/USDT" not in engine.watch_extra()


def test_follow_pros_filter(engine):
    engine.ex.px["Z/USDT"] = 1.0
    engine.ex.top_trader_ratio = lambda s: {"top_long": 0.30, "crowd_long": 0.6}
    engine._open("Z/USDT", Signal("long", 1.0, 0.98, 1.06, "Trendfolge", "t"), ROW, "trend_up", 5000)
    assert "Z/USDT" not in engine.positions and "Profi" in engine.log[-1]["msg"]
    engine._open("Z/USDT", Signal("short", 1.0, 1.02, 0.94, "Trendfolge", "t"), ROW, "trend_down", 5000)
    assert "Z/USDT" in engine.positions
    engine.positions.clear()
    engine.cfg["follow_pros"] = "nur_mit"
    engine.ex.top_trader_ratio = lambda s: {"top_long": 0.52, "crowd_long": 0.6}
    engine._open("Z/USDT", Signal("long", 1.0, 0.98, 1.06, "Trendfolge", "t"), ROW, "trend_up", 5000)
    assert "Z/USDT" not in engine.positions
    engine.cfg["follow_pros"] = "aus"
    engine._open("Z/USDT", Signal("long", 1.0, 0.98, 1.06, "Trendfolge", "t"), ROW, "trend_up", 5000)
    assert "Z/USDT" in engine.positions


def test_dashboard_survives_nan(engine):
    engine.market_view["X/USDT"] = {"symbol": "X/USDT", "price": 1.0, "regime": "neutral", "strategy": "-",
                                    "adx": float("nan"), "rsi": float("inf"), "vol_pct": 0.1, "leverage": 1}
    engine.auto_symbols = ["X/USDT"]
    engine.cfg["symbol_mode"] = "scanner"
    client = TestClient(create_app(engine))
    r = client.get("/api/state")
    assert r.status_code == 200
    m = [x for x in r.json()["markets"] if x["symbol"] == "X/USDT"][0]
    assert m["adx"] is None and m["rsi"] is None


def test_exchange_pnl_today(engine):
    calls = []
    engine.ex.exchange_pnl = lambda since: calls.append(since) or {"realized": 1, "fees": -1, "funding": 0, "total": 0}
    engine._xpnl_at = 0
    engine._refresh_exchange_pnl()
    assert len(calls) == 2 and calls[1].endswith("T00:00:00+00:00") or calls[1] == calls[0]
    assert engine.snapshot()["exchange_pnl_today"]["total"] == 0
