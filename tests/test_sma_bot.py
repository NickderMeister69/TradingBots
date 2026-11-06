import bot.sma_bot as S


def make(tmp_path, closes, monkeypatch, tf=300):
    b = S.SmaBot(tmp_path / "d", None)
    b.cfg.update({"fast": 2, "slow": 3, "fee_pct": 0.1, "slippage_pct": 0.0})
    t0 = 1_700_000_000_000

    def fetch():
        k = [[t0 + i * tf * 1000, c, c, c, c, 1] for i, c in enumerate(closes[: state["n"]])]
        b.candles, b.price = k, k[-1][4]
        return k
    state = {"n": 4}
    monkeypatch.setattr(b, "_fetch", fetch)
    return b, state


def test_golden_buy_death_sell(tmp_path, monkeypatch):
    closes = [10, 10, 10, 9, 8, 12, 14, 9, 7]
    b, st = make(tmp_path, closes, monkeypatch)
    b.tick()                                   # erste Kerze: nur merken
    for n in range(5, len(closes) + 1):
        st["n"] = n
        b.tick()
    assert len(b.trades) == 1 and b.qty == 0
    t = b.trades[0]
    assert t["entry"] == 12 and t["exit"] == 9 and t["pnl"] < 0
    assert abs(b.fees - (5000 * 0.001 + t["exit"] * t["amount"] * 0.001)) < 1e-6
    assert any("Golden Cross" in x["msg"] for x in b.log) and any("Death Cross" in x["msg"] for x in b.log)


def test_paused_does_not_trade_and_reset(tmp_path, monkeypatch):
    closes = [10, 10, 10, 9, 8, 12, 14]
    b, st = make(tmp_path, closes, monkeypatch)
    b.tick(); b.pause()
    for n in range(5, len(closes) + 1):
        st["n"] = n
        b.tick()
    assert b.qty == 0 and b.cash == 5000
    b.reset(1000)
    assert b.equity() == 1000 and b.trades == []
    brd = b.board()
    assert brd["can_reset"] and brd["settings"][0]["key"] == "timeframe"
    assert b.update_settings({"fast": 30, "slow": 20}) and b.cfg["fast"] < b.cfg["slow"]
