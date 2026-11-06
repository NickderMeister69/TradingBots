"""Wochen-Tagebuch: jeden Sonntag um 19 Uhr eine Telegram-Nachricht mit der Woche aller Bots.

Laeuft nur im Hauptbot (Port 8000). Liest die Kurz-Uebersicht jedes Bots ueber localhost (/api/kurz),
merkt sich den Stand in data/journal.json und vergleicht mit der Vorwoche.
War der PC am Sonntagabend aus, kommt die Nachricht beim naechsten Start nach.
"""
import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
SEND_WEEKDAY, SEND_HOUR = 6, 19          # Sonntag, 19 Uhr


def _fetch(port, timeout=6):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/kurz", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001 – ein Bot aus = einfach nicht dabei
        return None


def snapshot(ports):
    out = {}
    for p in ports:
        b = _fetch(p)
        if not b:
            continue
        out[str(p)] = {"name": b.get("name") or str(p), "konto": float(b.get("konto") or 0),
                       "start": float(b.get("start") or 0), "trades": int(b.get("trades") or 0),
                       "wins": int(b.get("wins") or 0), "status": b.get("status") or "", "quote": b.get("quote") or ""}
    return out


def eur(x):
    return f"{x:+,.0f}".replace(",", ".")


def build_message(now_snap, prev_snap):
    rows = []
    for port, b in now_snap.items():
        p = (prev_snap or {}).get(port)
        neu = p is None or b["trades"] < p["trades"] or abs(b["konto"] - b["start"]) < 1e-9 and p["konto"] != b["konto"]
        base_k = b["start"] if neu else p["konto"]
        base_t = 0 if neu else p["trades"]
        base_w = 0 if neu else p["wins"]
        wk, wt, ww = b["konto"] - base_k, b["trades"] - base_t, b["wins"] - base_w
        rows.append((wk, b, wt, ww))
    rows.sort(key=lambda r: r[0], reverse=True)
    total = sum(r[0] for r in rows)
    lines = ["📒 Wochen-Tagebuch", f"Alle Bots zusammen diese Woche: {eur(total)}", ""]
    for i, (wk, b, wt, ww) in enumerate(rows, 1):
        hit = f"{ww}/{wt} im Plus" if wt else "keine Trades"
        seit = (b["konto"] / b["start"] - 1) * 100 if b["start"] else 0
        stop = " ⛔" if b["status"] in ("Notbremse",) else ""
        konto = f"{b['konto']:,.0f}".replace(",", ".")
        pct = f"{seit:+.1f}".replace(".", ",")
        lines.append(f"{i}. {b['name']}: {eur(wk)} | {hit} | Konto {konto} ({pct} % seit Start){stop}")
    traded = [r for r in rows if r[2] > 0]
    if traded:
        lines += ["", f"Beste Woche: {rows[0][1]['name']} · Schwächste: {rows[-1][1]['name']}"]
    lines += ["", "Einzelne Wochen sagen wenig – entscheidend ist das Ergebnis über Monate."]
    return "\n".join(lines)


def last_due(now):
    """Letzter Sonntag 19:00 (Berlin) vor oder gleich jetzt."""
    d = now - timedelta(days=(now.weekday() - SEND_WEEKDAY) % 7)
    due = d.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
    if due > now:
        due -= timedelta(days=7)
    return due


class Journal:
    def __init__(self, notify, ports, path=Path("data/journal.json"), log=print):
        self.notify, self.ports, self.path, self.log = notify, ports, Path(path), log

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, d):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def tick(self, now=None):
        now = now or datetime.now(TZ)
        d = self._load()
        due = last_due(now)
        if not d.get("snap"):                       # erster Start: nur Ausgangsstand merken
            snap = snapshot(self.ports)
            if snap:
                self._save({"snap": snap, "sent_for": due.isoformat()})
            return False
        if d.get("sent_for", "") >= due.isoformat():
            return False
        snap = snapshot(self.ports)
        if not snap:
            return False
        msg = build_message(snap, d["snap"])
        if self.notify and self.notify.enabled:
            self.notify.send(msg)
        self.log(msg)
        self._save({"snap": snap, "sent_for": due.isoformat()})
        return True

    def start(self, first_delay=600, every=300):
        def loop():
            time.sleep(first_delay)               # erst wenn alle Bots laufen
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 – Tagebuch darf den Bot nie stoppen
                    self.log(f"Wochen-Tagebuch: {e}")
                time.sleep(every)
        threading.Thread(target=loop, daemon=True).start()
