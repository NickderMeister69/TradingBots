import base64
import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

PW = "geheim-12345"


def load():
    os.environ["DASHBOARD_PASSWORD"] = PW
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import run_copybot as R
    return R


def auth(pw):
    return {"Authorization": "Basic " + base64.b64encode(f"user:{pw}".encode()).decode()}


def test_access_rules():
    R = load()
    local = TestClient(R.app, client=("127.0.0.1", 5000))       # ohne "with": Bot-Schleife startet nicht
    assert local.get("/api/kurz").json()["name"] == "Copy-Bot"
    phone = TestClient(R.app, client=("100.101.5.7", 5000))
    assert phone.get("/api/kurz").status_code == 401
    assert phone.get("/api/kurz", headers=auth(PW)).status_code == 200
    lan = TestClient(R.app, client=("192.168.178.20", 5000))
    assert lan.get("/api/kurz", headers=auth(PW)).status_code == 403
    # Formular-POST (z. B. von einer fremden Webseite) wird abgelehnt
    assert local.post("/api/control", data={"action": "kill"}).status_code == 403
    assert local.post("/api/pause", json={}).status_code == 403          # ohne eigenen Kopf
    r = local.get("/alle", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith(":8000/alle")


def test_variants_defined_and_main_has_no_extra_accounts(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import engine as CE
    import storage as CS
    import run_copybot as R
    ports = sorted(p for p, _, _ in R.VARIANTS.values())
    assert ports == [8011, 8012, 8013, 8014, 8015, 8016]
    assert R.VARIANTS["riskant"][2]["leverage"] == 5.0 and R.VARIANTS["sicher"][2]["leverage"] == 0.5
    e = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert not any(pid.startswith("cmp_") for pid in e.pfs)
    # Ein altes Vergleichs-Konto aus der Datenbank wird beim Start entfernt
    from portfolio import Portfolio
    e.pfs["cmp_alt"] = Portfolio("cmp_alt", "alt", e.main.params, 5000)
    e.save()
    e2 = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert "cmp_alt" not in e2.pfs


def test_wende_schutz_caps_one_direction(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import strategy as CS
    from portfolio import Portfolio
    import config as CC
    prm = {**CC.DEFAULT_PARAMS, "max_net_share": 0.5, "max_positions": 20}
    pf = Portfolio("x", "x", prm, 5000)
    cons = {f"C{i}": {"agreement": -0.9, "n": 20, "supporters": []} for i in range(6)}
    cons["L1"] = {"agreement": 0.9, "n": 20, "supporters": []}
    tg, *_ = CS.targets(pf, cons, 5000, 0)
    cap = 5000 * prm["max_margin_pct"] * prm["leverage"]
    lng = sum(v for v in tg.values() if v > 0); sht = -sum(v for v in tg.values() if v < 0)
    assert sht - lng <= 0.5 * cap + 1e-6 and lng > 0
    pf.params["max_net_share"] = 1.0
    tg2, *_ = CS.targets(pf, cons, 5000, 0)
    assert -sum(v for v in tg2.values() if v < 0) > sht       # ohne Schutz mehr Shorts


def test_wende_schutz_variant_added_once(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import engine as CE
    import storage as CS
    e = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert "v_turn" in e.pfs and e.pfs["v_turn"].params["max_net_share"] == 0.5
    assert e.main.params["max_net_share"] == 1.0
    # altes Konto ohne v_turn -> wird nachgerüstet; danach rausgeflogen -> bleibt weg
    del e.pfs["v_turn"]; e.db.put("added_variants", []); e.save()
    e2 = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert "v_turn" in e2.pfs
    del e2.pfs["v_turn"]; e2.save()
    e3 = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert "v_turn" not in e3.pfs


def test_leverage_halved_once_for_old_accounts(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import engine as CE
    import storage as CS
    e = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    lev = e.main.params["leverage"]
    assert lev == CE.C.DEFAULT_PARAMS["leverage"]          # neues Konto: nicht nochmal halbiert
    # altes Konto (vor dem Update) mit Hebel 3
    for pf in e.pfs.values():
        pf.params["leverage"] = 3.0
    e.db.put("lev_halved", False); e.save()
    e2 = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert all(pf.params["leverage"] == 1.5 for pf in e2.pfs.values())
    e3 = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert e3.main.params["leverage"] == 1.5                # nur einmal


def test_reset_keeps_learned_rules(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import engine as CE
    import storage as CS
    from portfolio import Portfolio
    e = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    e.main.params["min_agreement"] = 0.66                      # "gelernt"
    e.main.blocked["DOGE"] = 9e12
    e.trust["0xabc"] = {"trust": 2.5, "obs": 10, "copy_ret": 0.1, "since": 0, "hist": []}
    e.pfs["v_m7"] = Portfolio("v_m7", "Abwandlung #7", {**e.main.params, "stop_loss_pct": 0.09}, 5000)
    e.main.cash = 3500.0                                        # Verlust
    e.db.put("reset_token", "alt"); e.save()
    e2 = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert e2.main.equity({}) == 5000
    assert e2.main.params["min_agreement"] == 0.66 and "DOGE" in e2.main.blocked
    assert e2.trust["0xabc"]["trust"] == 2.5
    assert e2.pfs["v_m7"].params["stop_loss_pct"] == 0.09 and "v_turn" in e2.pfs
    e3 = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert e3.main.params["min_agreement"] == 0.66               # kein zweiter Reset


def test_weniger_tauschen_keeps_young_positions(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import strategy as CS
    from portfolio import Portfolio
    import config as CC
    prm = {**CC.DEFAULT_PARAMS, "max_positions": 2}
    cons = {"A": {"agreement": -0.6, "n": 10, "supporters": []},
            "B": {"agreement": -0.9, "n": 20, "supporters": []},
            "C": {"agreement": -0.95, "n": 25, "supporters": []}}
    now = 1_000_000.0
    pf = Portfolio("x", "x", prm, 5000)
    pf.positions["A"] = {"qty": -1.0, "entry": 1.0, "opened_at": now - 600}
    tg, reasons, *_ = CS.targets(pf, cons, 5000, now)
    assert "A" not in tg and reasons["A"] == "Andere Signale sind stärker"      # Standard: A fliegt raus
    pf.params = {**prm, "swap_bonus": 2.0, "min_hold_min": 60}
    tg2, *_ = CS.targets(pf, cons, 5000, now)
    assert "A" in tg2                                                          # erst 10 min alt -> bleibt


def test_weniger_tauschen_variant_added():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import learning as CL
    import config as CC
    ids = [v for v, _, _ in CL.initial_variants(CC.DEFAULT_PARAMS)]
    assert "v_calm" in ids and "v_turn" in ids


def test_random_candidates_ignore_performance():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import run_copybot as R
    rows = []
    for i in range(300):
        roi = -0.5 if i % 2 else 0.5                      # Haelfte Verlierer
        rows.append({"ethAddress": f"0x{i:040x}", "accountValue": "50000",
                     "windowPerformances": [["week", {"roi": str(roi), "vlm": "1000", "pnl": "1"}],
                                            ["month", {"roi": str(roi), "vlm": "100000", "pnl": str(roi * 1000)}],
                                            ["allTime", {"roi": "0", "pnl": "0", "vlm": "1"}]]})
    pool = R.random_candidates(rows)
    assert len(pool) > 50
    assert any(p["month_roi"] < 0 for p in pool[:100]) and any(p["month_roi"] > 0 for p in pool[:100])
    assert [p["address"] for p in R.random_candidates(rows)] == [p["address"] for p in pool]   # feste Auslosung
    assert R.VARIANTS["zufall"][0] == 8016 and R.VARIANTS["zufall"][2]["use_trust"] is False


def test_leverage_back_to_default_once(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
    import engine as CE
    import storage as CS
    e = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    e.main.params["leverage"] = 1.5
    e.db.put("lev_set_v2", False); e.save()
    e2 = CE.Engine(CS.Storage(tmp_path / "t.sqlite3"))
    assert e2.main.params["leverage"] == CE.C.DEFAULT_PARAMS["leverage"]
    e2.main.params["leverage"] = 2.0; e2.save()
    assert CE.Engine(CS.Storage(tmp_path / "t.sqlite3")).main.params["leverage"] == 2.0   # nur einmal
