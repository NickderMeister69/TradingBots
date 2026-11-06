import json

from fastapi.testclient import TestClient

import bot.dashboard as D
from tests.test_engine_learning import engine  # noqa: F401  (Fixture)
from tests.test_remote_access import PW, auth


def test_kurz_and_alle(engine, monkeypatch):
    engine.cfg["dashboard_port"] = 8000
    engine.cfg["account_reset"] = {"start": 5000.0, "at": "2026-10-18T00:00:00+00:00", "deposits": [{"amount": 4000.0}]}
    engine.positions.clear()
    engine.positions["BTC/USDT"] = {"symbol": "BTC/USDT", "side": "short", "entry": 100.0, "amount": 1, "stop": 105,
                                    "unrealized": 7.5, "risk_usdt": 5.0, "units": [{}], "max_units": 2,
                                    "opened_at": "2026-10-18T10:00:00+00:00", "leverage": 5}
    other = {"name": "Bybit EU", "port": 8001, "konto": 5100.0, "gewinn": 100.0, "pct": 2.0, "positions": [],
             "start": 5000.0, "status": "läuft"}

    class Resp:
        def __init__(self, d): self.d = d
        def read(self): return json.dumps(self.d).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(url, timeout):
        if ":8001/" in url:
            return Resp(other)
        raise OSError("aus")
    monkeypatch.setattr(D.urllib.request, "urlopen", fake_open)
    c = TestClient(D.create_app(engine, PW), client=("100.101.5.7", 5000))
    k = c.get("/api/kurz", headers=auth(PW)).json()
    assert abs(k["konto"] - (5000 + k["gewinn"])) < 1e-9 and k["unrealized"] == 7.5 and abs(k["gewinn"] - (k["realized"] + 7.5)) < 1e-9 and k["positions"][0]["r"] == 1.5 and k["deposits"] == 4000
    bots = c.get("/api/alle", headers=auth(PW)).json()["bots"]
    assert len(bots) == 21 and bots[0]["ok"] and bots[1]["name"] == "Bybit EU" and bots[1]["ok"]
    assert not bots[5]["ok"] and bots[5]["name"] == "Daytrade sicher"
    page = c.get("/alle", headers=auth(PW))
    assert page.status_code == 200 and "Alle Bots" in page.text
    assert c.get("/alle").status_code == 401          # Handy ohne Passwort: nein


def test_reload_needs_password_and_header(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(D.os, "_exit", lambda code: calls.append(("exit", code)))
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    monkeypatch.setattr(D.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(OSError("aus")))
    phone = TestClient(D.create_app(engine, PW), client=("100.101.5.7", 5000))
    assert phone.post("/api/alle_neu_laden").status_code == 401                       # ohne Passwort
    assert phone.post("/api/alle_neu_laden", headers=auth(PW)).status_code == 403     # ohne eigenen Kopf
    lan = TestClient(D.create_app(engine, PW), client=("192.168.178.20", 5000))
    assert lan.post("/api/neu_laden", headers={**auth(PW), "x-bot-action": "1"}).status_code == 403
    r = phone.post("/api/alle_neu_laden", headers={**auth(PW), "x-bot-action": "1"}).json()
    assert r["ok"] and r["done"] == [8000] and 8001 in r["missing"]
    import time as _t
    for _ in range(50):
        if calls:
            break
        _t.sleep(0.02)
    assert calls == [("exit", 1)]


def test_stop_and_pause_all(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(D.os, "_exit", lambda code: calls.append(code))
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    monkeypatch.setattr(D.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(OSError("aus")))
    phone = TestClient(D.create_app(engine, PW), client=("100.101.5.7", 5000))
    h = {**auth(PW), "x-bot-action": "1"}
    assert phone.post("/api/alle_pause", headers=h).json()["ok"] and engine.paused
    assert phone.post("/api/alle_weiter", headers=h).json()["ok"] and not engine.paused
    assert phone.post("/api/alle_beenden", headers=auth(PW)).status_code == 403
    assert phone.post("/api/alle_beenden", headers=h).json()["ok"]
    import time as _t
    for _ in range(50):
        if calls:
            break
        _t.sleep(0.02)
    assert calls == [3]          # 3 = bleibt aus (start.bat startet nicht neu)


def test_extra_accounts_flattened(engine, monkeypatch):
    import json as _j

    class Resp:
        def __init__(self, d): self.d = d
        def read(self): return _j.dumps(self.d).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    cb = {"name": "Copy-Bot", "port": 8010, "konto": 5000, "gewinn": 0, "pct": 0, "start": 5000, "positions": [],
          "extra": [{"name": "Copy sicher", "port": 8010, "konto": 5010, "gewinn": 10, "pct": 0.2, "start": 5000,
                     "positions": []}]}

    def fake_open(url, timeout):
        if ":8010/" in url:
            return Resp(cb)
        raise OSError("aus")
    monkeypatch.setattr(D.urllib.request, "urlopen", fake_open)
    engine.cfg["dashboard_port"] = 8000
    c = TestClient(D.create_app(engine, PW), client=("127.0.0.1", 5000))
    bots = c.get("/api/alle").json()["bots"]
    names = [b.get("name") for b in bots]
    assert len(bots) == 22 and names.index("Copy sicher") == names.index("Copy-Bot") + 1
    assert bots[names.index("Copy sicher")]["ok"] and "extra" not in bots[names.index("Copy-Bot")]


def test_board_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.test_reset import make
    from bot.dashboard import create_app
    import bot.board as BD
    e = make(tmp_path, monkeypatch, mode="paper")
    monkeypatch.setattr(BD, "hold_curve", lambda *a, **k: ([], None))
    c = TestClient(create_app(e, ""), client=("127.0.0.1", 5000))
    assert "Bot-Konto vs. Bitcoin halten" in c.get("/").text
    assert "Lernen" in c.get("/klassisch").text
    assert c.get("/board/lwc.js").text.startswith("/*!")
    b = c.get("/api/board").json()
    assert b["cards"]["equity"]["start"] and b["signal"]["rules"] and b["details_url"] == "/klassisch"
