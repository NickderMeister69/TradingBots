from bot.exchange import Exchange

class FakeCcxt:
    def __init__(self): self.n = 0
    def load_time_difference(self): self.n += 1

def test_resync_throttled_and_forced():
    x = Exchange.__new__(Exchange); x.ex = FakeCcxt()
    assert x.resync_time() is True and x.ex.n == 1
    assert x.resync_time() is False and x.ex.n == 1          # nicht oefter als alle 30 min
    assert x.resync_time(force=True) is True and x.ex.n == 2  # bei Fehler -1021 sofort

def test_resync_paper_noop():
    x = Exchange.__new__(Exchange); x.ex = None
    assert x.resync_time(force=True) is False
