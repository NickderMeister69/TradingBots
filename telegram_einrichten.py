"""Richtet Telegram-Meldungen ein. Aufruf:  .venv\\Scripts\\python telegram_einrichten.py

1. In Telegram @BotFather oeffnen, /newbot senden, Namen vergeben -> du bekommst einen Token.
2. Dieses Skript starten, Token einfuegen.
3. Deinem neuen Bot in Telegram irgendeine Nachricht schicken (z. B. "hallo").
Das Skript findet dann deine Chat-ID und traegt beides in .env ein.
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bot.notify import _post  # noqa: E402

ENV = Path(__file__).parent / ".env"


def set_env(key, value):
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [ln for ln in lines if not re.match(rf"\s*{key}\s*=", ln)]
    lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    token = input("Bot-Token von @BotFather einfuegen: ").strip()
    try:
        me = _post(token, "getMe", {})
    except Exception as e:
        sys.exit(f"Token funktioniert nicht: {e}")
    name = me["result"]["username"]
    print(f"\nOK, Bot @{name} gefunden. Schick ihm jetzt in Telegram eine Nachricht (z. B. 'hallo').")
    print("Ich warte bis zu 2 Minuten ...")
    chat_id = None
    for _ in range(40):
        upd = _post(token, "getUpdates", {"timeout": 0})
        for u in upd.get("result", []):
            msg = u.get("message") or {}
            if msg.get("chat", {}).get("id"):
                chat_id = msg["chat"]["id"]
        if chat_id:
            break
        time.sleep(3)
    if not chat_id:
        sys.exit("Keine Nachricht angekommen. Bitte nochmal starten und dem Bot schreiben.")
    set_env("TELEGRAM_BOT_TOKEN", token)
    set_env("TELEGRAM_CHAT_ID", str(chat_id))
    _post(token, "sendMessage", {"chat_id": chat_id, "text": "✅ Trading-Bot verbunden. Ab jetzt bekommst du hier Meldungen."})
    print("\nFertig. In .env eingetragen. Bot neu starten, dann kommen die Meldungen.")


if __name__ == "__main__":
    main()
