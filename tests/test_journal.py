from datetime import datetime

import bot.journal as J


def test_weekly_message_and_due(tmp_path, monkeypatch):
    now_snap = {"8000": {"name": "Hauptbot", "konto": 5100.0, "start": 5000.0, "trades": 6, "wins": 2, "status": "läuft", "quote": "USDT"},
                "8010": {"name": "Copy-Bot", "konto": 4900.0, "start": 5000.0, "trades": 40, "wins": 12, "status": "läuft", "quote": "€"}}
    prev = {"8000": {"name": "Hauptbot", "konto": 5000.0, "start": 5000.0, "trades": 1, "wins": 0},
            "8010": {"name": "Copy-Bot", "konto": 4950.0, "start": 5000.0, "trades": 10, "wins": 3}}
    msg = J.build_message(now_snap, prev)
    assert "1. Hauptbot: +100" in msg and "2/5 im Plus" in msg and "2. Copy-Bot: -50" in msg
    assert "zusammen diese Woche: +50" in msg
    snaps = iter([now_snap, now_snap])
    monkeypatch.setattr(J, "snapshot", lambda ports: next(snaps))
    sent = []

    class N:
        enabled = True
        def send(self, m): sent.append(m)
    j = J.Journal(N(), [8000, 8010], tmp_path / "j.json", log=lambda m: None)
    mon = datetime(2026, 10, 19, 12, 0, tzinfo=J.TZ)            # Montag: nur Ausgangsstand
    assert j.tick(mon) is False and not sent
    sun = datetime(2026, 10, 25, 19, 5, tzinfo=J.TZ)            # Sonntag 19:05 -> senden
    assert j.tick(sun) is True and len(sent) == 1
    assert j.tick(sun) is False                                 # nur einmal pro Woche


def test_reset_bot_counts_from_start():
    now_snap = {"8003": {"name": "Scanner", "konto": 5020.0, "start": 5000.0, "trades": 2, "wins": 1, "status": "", "quote": ""}}
    prev = {"8003": {"name": "Scanner", "konto": 4700.0, "start": 5000.0, "trades": 30, "wins": 9}}
    assert "Scanner: +20" in J.build_message(now_snap, prev)
