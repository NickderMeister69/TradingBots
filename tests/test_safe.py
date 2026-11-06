from bot.engine import Engine
from bot.board import safe_text, safe_label

def _e():
    e = Engine.__new__(Engine); e.cfg = {"fee_rate": 0.0005}; return e

def test_safe_long_stop_above_entry_is_profit():
    p = {"side": "long", "entry": 100.0, "stop": 110.0, "amount": 2.0}
    assert abs(_e()._safe_pnl(p) - (20 - 210 * 2 * 0.0005)) < 0.01

def test_safe_short_stop_above_entry_is_loss():
    p = {"side": "short", "entry": 100.0, "stop": 104.0, "amount": 1.0}
    assert _e()._safe_pnl(p) < -4

def test_safe_missing_stop():
    assert _e()._safe_pnl({"side": "long", "entry": 1, "amount": 1}) is None

def test_text():
    assert safe_text(1234.5, "USDT") == "+1.234,50 USDT" and safe_text(-3.2) == "−3,20 €"
    assert safe_label(1).startswith("Save") and safe_label(-1).startswith("Stoploss")

def test_safe_uses_better_exit_line():
    p = {"side": "long", "entry": 100.0, "stop": 98.0, "exit_level": 103.0, "amount": 1.0}
    assert _e()._safe_pnl(p) > 2.8
    p = {"side": "short", "entry": 100.0, "stop": 102.0, "exit_level": 99.0, "amount": 1.0}
    assert _e()._safe_pnl(p) > 0.8
