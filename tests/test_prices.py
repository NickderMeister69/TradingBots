from bot.exchange import Exchange


class FakePub:
    def __init__(self, id_, has_lp=True, fail=False):
        self.id = id_
        self.has = {"fetchLastPrices": has_lp}
        self.fail = fail
        self.calls = []

    def fetch_last_prices(self, syms):
        self.calls.append("lp")
        if self.fail:
            raise RuntimeError("x")
        return {s: {"symbol": s, "price": 2.0} for s in syms}

    def fetch_tickers(self, syms):
        self.calls.append("tickers")
        return {s: {"last": 1.0} for s in syms}


def make(pub):
    ex = Exchange.__new__(Exchange)
    ex.public = pub
    ex._trade_sym = lambda s: s + ":USDT"
    return ex


def test_binance_uses_last_prices():
    pub = FakePub("binanceusdm")
    assert make(pub).prices(["BTC/USDT"]) == {"BTC/USDT": 2.0}
    assert pub.calls == ["lp"]


def test_fallback_on_error_and_other_exchanges():
    pub = FakePub("binance", fail=True)
    assert make(pub).prices(["BTC/USDT"]) == {"BTC/USDT": 1.0}
    pub2 = FakePub("bybit")
    assert make(pub2).prices(["BTC/USDT"]) == {"BTC/USDT": 1.0}
    assert pub2.calls == ["tickers"]
