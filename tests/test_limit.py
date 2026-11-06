import bot.exchange as X


class Pub:
    def __init__(self, seq, bid=99.0, ask=101.0):
        self.seq, self.bid, self.ask = list(seq), bid, ask

    def fetch_ticker(self, sym):
        last = self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]
        return {"last": last, "bid": self.bid, "ask": self.ask}


def make(seq, wait=0.3, spot=False):
    e = object.__new__(X.Exchange)
    e.cfg = {"order_type": "limit", "limit_wait_s": wait}
    e.mode, e.is_spot, e.name, e.public = "paper", spot, "binance", Pub(seq)
    e._trade_sym = lambda s: s
    return e


def test_paper_limit_fills_when_price_comes_back(monkeypatch):
    monkeypatch.setattr(X.time, "sleep", lambda s: None)
    e = make([100, 100, 98.5])                  # Kurs faellt auf unseren Kaufpreis (Geldkurs 99)
    px, amt = e.open("BTC/USDT", "long", 1.0, 3)
    assert px == 99.0 and e.last_maker == 1.0


def test_paper_limit_falls_back_to_market(monkeypatch):
    e = make([100, 102, 103], wait=0.05)        # Kurs laeuft weg -> nach Wartezeit Markt
    px, amt = e.open("BTC/USDT", "long", 1.0, 3)
    assert e.last_maker == 0.0 and px >= 100


def test_spot_never_uses_limit():
    e = make([100], spot=True)
    assert not e.use_limit()


def test_stop_close_is_market_trend_end_is_limit(monkeypatch):
    monkeypatch.setattr(X.time, "sleep", lambda s: None)
    e = make([100, 101.5])                      # Verkauf zum Briefkurs 101
    assert e.close("BTC/USDT", "long", 1.0) == 100 and e.last_maker == 0.0
    e = make([100, 101.5])
    assert e.close("BTC/USDT", "long", 1.0, limit=True) == 101.0 and e.last_maker == 1.0


def test_engine_charges_maker_fee(tmp_path, monkeypatch):
    from tests.test_reset import make as mk
    eng = mk(tmp_path, monkeypatch, mode="paper")
    eng.cfg["fee_maker"] = 0.0002
    assert abs(eng._fee_cost(100, 1, 1.0) - 0.02) < 1e-12
    assert abs(eng._fee_cost(100, 1, 0.0) - 0.05) < 1e-12
    p = {"symbol": "A/USDT", "side": "long", "entry": 100.0, "amount": 1.0, "leverage": 1, "strategy": "Ausbruch",
         "opened_at": "2026-10-20T10:00:00+00:00", "risk_usdt": 2.0, "fee_in": 0.02}
    eng.positions["A/USDT"] = p
    eng._record_close("A/USDT", p, 110.0, "Trend vorbei", maker_out=1.0)
    assert abs(eng.trades[-1]["pnl"] - (10 - 0.02 - 0.022)) < 1e-9


class LiveEx:
    def __init__(self, fill_now):
        self.fill_now, self.orders = fill_now, []

    def price_to_precision(self, s, p): return p
    def amount_to_precision(self, s, a): return round(a, 6)
    def market(self, s): return {"limits": {}}
    def set_leverage(self, *a): pass

    def create_order(self, sym, typ, side, amt, px=None, params=None):
        self.orders.append((typ, side, amt, px, dict(params or {})))
        if typ == "market":
            return {"id": "m", "average": 101.0, "filled": amt}
        return {"id": "L", "status": "open", "filled": 0}

    def fetch_order(self, oid, sym):
        return {"status": "open", "filled": self.fill_now, "average": 99.0}

    def cancel_order(self, oid, sym): self.orders.append(("cancel", oid))


def test_live_partial_limit_then_market(monkeypatch):
    monkeypatch.setattr(X.time, "sleep", lambda s: None)
    e = make([100], wait=0.05)
    e.mode, e.ex = "testnet", LiveEx(fill_now=0.4)
    px, amt = e.open("BTC/USDT", "long", 1.0, 3)
    kinds = [o[0] for o in e.ex.orders]
    assert kinds[0] == "limit" and e.ex.orders[0][4].get("postOnly") and "cancel" in kinds and kinds[-1] == "market"
    assert abs(amt - 1.0) < 1e-9 and abs(e.last_maker - 0.4) < 1e-9
    assert abs(px - (0.4 * 99 + 0.6 * 101)) < 1e-9
