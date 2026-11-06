"""Zieht den Hauptbot (config.yaml) auf Kraken Futures um.

ACHTUNG: Kraken hat sein Futures-Demo (demo-futures.kraken.com) abgeschaltet. Es gibt dort nur noch ECHTGELD.
Deshalb ist dieses Skript erst fuer den Echtgeld-Start gedacht (nach der Testphase).

Vorher:
  1. Bei Kraken das echte Konto verifizieren und Futures freischalten (Eignungstest).
  2. Auf https://futures.kraken.com unter Settings -> API Keys einen Futures-Key anlegen:
     Rechte zum Handeln ja, KEINE Auszahlungs-Rechte (Withdrawal).
  3. Alle Bots beenden (taskkill /F /IM python.exe).
Dann: kraken_einrichten.bat starten. Key und Secret werden nicht angezeigt und nur in .env gespeichert.
"""
import getpass
import json
import re
import socket
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
ENV, CFG, STATE = ROOT / ".env", ROOT / "config.yaml", ROOT / "data" / "state.json"
MAJORS10 = ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC")


def set_env(key, value):
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [ln for ln in lines if not re.match(rf"\s*{key}\s*=", ln)]
    lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 8000)) == 0:
            sys.exit("Der Hauptbot laeuft noch. Erst alle Bots beenden (taskkill /F /IM python.exe), dann nochmal starten.")
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding="utf-8"))
        if st.get("positions"):
            print("ACHTUNG: Der Hauptbot hat noch laufende Trades bei", cfg.get("exchange", "binance"), "->",
                  ", ".join(st["positions"]))
            print("Die bleiben dort offen (mit ihrer Verlust-Grenze an der Boerse), der Bot kuemmert sich dann nicht mehr darum.")
            if input("Trotzdem umziehen? (j/n): ").strip().lower() != "j":
                sys.exit("Abgebrochen. Tipp: im Dashboard erst 'Alle Trades beenden' klicken.")
    print("Kraken bietet kein Futures-Demo mehr an. Der Hauptbot handelt danach mit ECHTGELD.")
    if input("Wirklich auf Kraken mit Echtgeld umstellen? Zum Bestaetigen JA tippen: ").strip() != "JA":
        sys.exit("Abgebrochen. Der Hauptbot bleibt wie er ist.")
    real = True
    key = getpass.getpass("Kraken-Futures API-Key (wird nicht angezeigt): ").strip()
    sec = getpass.getpass("Kraken-Futures API-Secret (wird nicht angezeigt): ").strip()
    if not key or not sec:
        sys.exit("Key oder Secret fehlt.")
    import ccxt
    ex = ccxt.krakenfutures({"apiKey": key, "secret": sec, "enableRateLimit": True})
    if not real:
        ex.set_sandbox_mode(True)
    sys.path.insert(0, str(ROOT))
    from bot.exchange import kraken_accounts
    try:
        raw = ex.privateGetAccounts()
        if isinstance(raw, str) and raw.lstrip().startswith("<"):
            sys.exit("\nKraken hat eine Webseite statt Daten geschickt. Meist heisst das: falscher Key-Typ. "
                     "Es muss ein FUTURES-Key von futures.kraken.com sein (nicht der normale Kraken-Pro-Key).")
        value, name, overview = kraken_accounts(ex)
        print("\nVerbindung klappt. Konten bei Kraken: " + (", ".join(f"{n} ({t})" for n, t in overview) or "keine"))
        if name and name != "cash":
            print(f"Guthaben im Futures-Konto (Multi-Collateral): {value:.2f} USD")
        else:
            print("HINWEIS: Es gibt noch kein Multi-Collateral-Konto (Flex) mit Guthaben. Der Bot braucht das fuer die "
                  "Perpetuals (PF_...).\n  -> Auf " + ("futures.kraken.com" if real else "demo-futures.kraken.com")
                  + " einloggen, oben bei 'Wallets' Guthaben ins Multi-Collateral-Konto uebertragen "
                  "(im Demo gibt es dort Spielgeld).\n  Die Keys werden trotzdem gespeichert.")
    except Exception as e:
        try:   # Fehlersuche: Aufbau der Antwort zeigen (nur Namen und Typen, keine Schluessel, keine Betraege)
            raw = ex.privateGetAccounts()
            accs = raw.get("accounts") if isinstance(raw, dict) else raw
            print("\n[Diagnose] Antwort:", type(raw).__name__, "| accounts:", type(accs).__name__,
                  "|", [(k, type(v).__name__) for k, v in accs.items()] if isinstance(accs, dict) else str(accs)[:200])
        except Exception as e2:
            print("\n[Diagnose] Konten-Abfrage:", e2)
        sys.exit(f"\nVerbindung zu Kraken hat nicht geklappt: {e}\nKey/Secret pruefen und ob es ein "
                 f"{'Echtgeld' if real else 'Demo'}-Key ist.")
    set_env("KRAKEN_FUTURES_API_KEY", key)
    set_env("KRAKEN_FUTURES_API_SECRET", sec)
    cfg.update({"exchange": "krakenfutures", "quote": "USD", "market": "futures",
                "mode": "live" if real else "testnet", "fee_rate": 0.0005,
                "symbols": [f"{b}/USD" for b in MAJORS10], "scan_always": ["BTC/USD"], "symbol_mode": "manual",
                "account_reset": {"start": 1000.0, "at": None}, "bot_name": "Kraken 4h"})
    CFG.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print("\nFertig. Der Hauptbot handelt ab dem naechsten Start bei Kraken Futures"
          + (" mit ECHTGELD (dafuer muss in .env auch LIVE_CONFIRM=JA stehen)." if real else " (Demo), wieder mit 1000-Topf."))
    print("Jetzt die Bots wieder starten.")


if __name__ == "__main__":
    main()
