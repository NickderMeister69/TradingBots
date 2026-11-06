from tests.test_reset import make


def test_heartbeat_every_two_hours(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch, mode="paper")
    t = [10_000.0]
    monkeypatch.setattr("bot.engine.time.time", lambda: t[0])
    e._heartbeat(["BTC/USDT", "ETH/USDT"], 5123.4)
    msgs = [x["msg"] for x in e.log if x["msg"].startswith("Lebenszeichen")]
    assert msgs == ["Lebenszeichen: Bot läuft. 2 Coins geprüft, 0 Trade(s) offen, Konto 5.123,40 USDT. Alles in Ordnung."]
    t[0] += 3600
    e._heartbeat(["BTC/USDT"], 5000)
    assert sum(x["msg"].startswith("Lebenszeichen") for x in e.log) == 1      # erst nach 2 h wieder
    e._hb_errs = 3
    t[0] += 3601
    e._heartbeat(["BTC/USDT"], 5000)
    last = e.log[-1]
    assert "3 Fehler" in last["msg"] and last["kind"] == "warn"
