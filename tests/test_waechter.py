import importlib, os, sys
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

def load(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT))
    import waechter as W
    importlib.reload(W)
    monkeypatch.setattr(W, "ROOT", str(tmp_path))
    monkeypatch.setattr(W, "LOG", str(tmp_path / "w.log"))
    (tmp_path / "start.bat").write_text("x")
    calls = []
    monkeypatch.setattr(W.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(W, "is_up", lambda p: p == 8001)
    return W, calls

def test_start_all_only_missing_and_never_twice(monkeypatch, tmp_path):
    W, calls = load(monkeypatch, tmp_path)
    c = TestClient(W.app)
    assert c.post("/api/start_all").status_code == 403          # ohne Knopf-Header nicht erlaubt
    j = c.post("/api/start_all", headers={"x-bot-action": "1"}).json()
    assert j["started"] == ["Hauptbot"]                           # nur vorhandene Datei, 8001 laeuft schon
    assert j["result"]["Bybit EU"] == "läuft schon" and j["result"]["Gold Tag"] == "Start-Datei fehlt"
    j = c.post("/api/start_all", headers={"x-bot-action": "1"}).json()
    assert j["started"] == [] and j["result"]["Hauptbot"] == "startet schon" and len(calls) == 1
    st = {x["port"]: x for x in c.get("/api/status").json()}
    assert st[8001]["up"] and st[8000]["status"].startswith("startet") and st[8019]["status"] == "aus"
    assert "Bot-Wächter" in c.get("/").text
