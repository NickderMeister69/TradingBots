"""Telegram-Meldungen aufs Handy.

Einrichtung: `python telegram_einrichten.py` ausfuehren (fragt den Bot-Token ab und
traegt TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID in .env ein).
Ohne diese Eintraege ist die Funktion einfach aus.
"""
import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def _post(token, method, data, timeout=15):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(API.format(token=token, method=method), data=body)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


class Notifier:
    def __init__(self, log=print, prefix=""):
        self.prefix = f"[{prefix}] " if prefix else ""
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.log = log
        self.q = queue.Queue(maxsize=200)
        self.last_error = None
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True).start()

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def send(self, text, important=False):
        """Nicht blockierend. Der Bot wartet nie auf Telegram."""
        if not self.enabled:
            return
        alarm = "🚨 " if important else ""
        try:
            self.q.put_nowait(alarm + self.prefix + text)
        except queue.Full:
            pass

    def _worker(self):
        while True:
            text = self.q.get()
            for attempt in range(3):
                try:
                    _post(self.token, "sendMessage", {"chat_id": self.chat_id, "text": text[:4000]})
                    self.last_error = None
                    break
                except Exception as e:  # noqa: BLE001 – Telegram darf den Bot nie stoppen
                    self.last_error = str(e)
                    time.sleep(5 * (attempt + 1))
            else:
                self.log(f"Telegram-Meldung konnte nicht gesendet werden: {self.last_error}")
            time.sleep(1)   # Telegram-Limit schonen
