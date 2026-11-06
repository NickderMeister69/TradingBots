"""Legt das Passwort fuer den Fernzugriff aufs Dashboard fest (DASHBOARD_PASSWORD in .env).
Wird von fernzugriff_einrichten.bat aufgerufen. Das Passwort wird nicht angezeigt."""
import getpass
import re
import sys
from pathlib import Path

ENV = Path(__file__).parent / ".env"


def set_env(key, value):
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [ln for ln in lines if not re.match(rf"\s*{key}\s*=", ln)]
    if value:
        lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    print("Passwort fuer den Fernzugriff aufs Dashboard festlegen.")
    print("Mindestens 10 Zeichen. Beim Tippen siehst du nichts, das ist normal.")
    print("Leer lassen und Enter = Fernzugriff wieder AUSschalten.\n")
    pw = getpass.getpass("Neues Passwort: ").strip()
    if not pw:
        set_env("DASHBOARD_PASSWORD", "")
        print("\nFernzugriff ist AUS. Bots neu starten.")
        return
    if len(pw) < 10 or any(c in pw for c in " #\"'"):
        sys.exit("\nZu kurz oder mit Leerzeichen/#/Anfuehrungszeichen. Bitte nochmal starten.")
    if getpass.getpass("Nochmal zur Kontrolle: ").strip() != pw:
        sys.exit("\nDie beiden Passwoerter sind verschieden. Bitte nochmal starten.")
    set_env("DASHBOARD_PASSWORD", pw)
    print("\nGespeichert.")


if __name__ == "__main__":
    main()
