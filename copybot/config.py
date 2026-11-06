"""Feste Grundeinstellungen. Alles, was du im Browser änderst, landet in der Datenbank.

Im Trading-Projekt laufen mehrere Copy-Bots mit derselben Software (Varianten mit anderem Risiko/Hebel).
run_copybot.py setzt dafuer vor dem Start: COPYBOT_PORT, COPYBOT_DATA, COPYBOT_PARAMS (JSON), COPYBOT_FEED."""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / os.getenv("COPYBOT_DATA", "data")
DB_PATH = DATA_DIR / "copybot.sqlite3"

HOST = "0.0.0.0"          # 0.0.0.0 = auch vom Handy im selben Netz / per Tailscale erreichbar
PORT = int(os.getenv("COPYBOT_PORT", "8010"))
# Varianten holen Profi-Daten und Kurse vom Haupt-Copy-Bot statt selbst von Hyperliquid (schont das Abfrage-Limit)
FEED_URL = os.getenv("COPYBOT_FEED", "")
TIMEZONE = "Europe/Berlin"

START_CAPITAL = 5000.0    # Euro Spielgeld
CURRENCY = "€"

# Datenquelle: Hyperliquid (Perpetual-Börse, alle Konten und Positionen sind öffentlich)
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

TOP_N = 100                         # wie viele Profis kopiert werden
LEADERBOARD_REFRESH_HOURS = 6       # wie oft die Bestenliste neu geprüft wird
EVALUATION_HOURS = 6                # wie oft der Bot sich selbst auswertet
EQUITY_SNAPSHOT_SECONDS = 300       # wie oft der Kontostand für die Kurve gespeichert wird
HTTP_CONCURRENCY = 5                # gleichzeitige Anfragen an Hyperliquid (Rate-Limit schonen)

# Simulierte Kosten (realistisch für eine Perp-Börse)
TAKER_FEE = 0.00045       # 0,045 % Gebühr pro Kauf/Verkauf
SLIPPAGE = 0.0003         # 0,03 % schlechterer Preis als der Mittelkurs

# Auswahl der Profis aus der Bestenliste
SELECTION = {
    "min_account_usd": 25_000,      # Mindest-Kontowert (Perp-Konto, live geprüft)
    "min_month_roi": 0.03,          # mind. +3 % im letzten Monat
    "max_month_roi": 5.0,           # über +500 % ist meist ein Einzahlungs-Effekt
    "min_week_roi": -0.15,          # diese Woche nicht mehr als 15 % verloren
    "min_alltime_pnl": 10_000,      # insgesamt mind. 10.000 $ Gewinn
    "min_turnover": 0.5,            # Monats-Volumen mind. 0,5x Kontowert (handelt wirklich)
    "max_turnover": 200.0,          # über 200x = Market-Maker, nicht kopierbar
    "max_open_positions": 30,       # wer 30+ Coins gleichzeitig hält, ist meist ein Algo-Korb
    "candidate_pool": 320,          # so viele Kandidaten werden live geprüft
}

# Startwerte der Hauptstrategie (können im Browser geändert werden; der Bot lernt sie auch selbst)
DEFAULT_PARAMS = {
    "leverage": 3.0,          # Hebel des Bots (20.10. halbiert auf 1,5, 21.10. auf Nicks Wunsch wieder 3)
    "max_coin_pct": 0.20,     # max. Einsatz (Margin) je Coin: 20 % vom Topf -> mit Hebel 3 = 60 % Positionsgröße
    "max_margin_pct": 0.60,   # alle Positionen zusammen binden max. 60 % vom Topf als Margin
    "max_positions": 12,      # höchstens so viele Coins gleichzeitig (die stärksten Signale)
    "min_traders": 3,         # mind. so viele Profis müssen im Coin sein
    "min_agreement": 0.50,    # 0,5 = gewichtet 75 % der Profis auf einer Seite
    "exit_agreement": 0.15,   # unter diesem Wert wird ausgestiegen
    "full_traders": 10,       # ab so vielen Profis volle Positionsgröße
    "stop_loss_pct": 0.06,    # Stop-Loss: 6 % Kursbewegung gegen den Bot
    "cooldown_min": 240,      # nach einem Stop-Loss 4 h Pause für diesen Coin
    "rebalance_band": 0.30,   # erst nachkaufen/abbauen, wenn Ziel 30 % abweicht
    "min_order_eur": 25.0,    # kleinere Änderungen lohnen die Gebühren nicht
    "use_trust": True,        # gelerntes Vertrauen in die Profis benutzen
    "swap_bonus": 1.25,       # neuer Coin muss 1,25x stärker sein, um einen gehaltenen zu verdrängen
    "min_hold_min": 0,        # Mindest-Haltezeit (Minuten) vor einem Tausch, 0 = aus
    "max_net_share": 1.0,     # Wende-Schutz: 1,0 = aus; 0,5 = Mehrheits-Seite max. 50 % des Deckels über der Gegenseite
}
DEFAULT_PARAMS.update(json.loads(os.getenv("COPYBOT_PARAMS") or "{}"))

RESET_TOKEN = "2026-10-20"      # ändern = alle Copy-Konten einmalig auf Startkapital (Gelerntes bleibt)

DEFAULT_SETTINGS = {
    "auto_learn": True,           # Bot darf seine Strategie selbst wechseln
    "max_drawdown_pct": 0.30,     # fällt der Topf 30 % unter den Höchststand: Not-Aus
    "cycle_seconds": 45,          # alle 45 s Positionen der Profis abfragen
}

# Lernen
TRUST_LR = 8.0            # Lernrate für das Vertrauen (Multiplicative Weights): +5 % Copy-Rendite -> x1,5
TRUST_MIN = 0.15
TRUST_MAX = 4.0
TRUST_DROP_BELOW = 0.30   # darunter wird der Profi nach 24 h aussortiert
SWITCH_MIN_TRADES = 20    # erst ab 20 Trades zieht der Bot Schlüsse
SWITCH_MIN_HOURS = 48     # Schattenkonto muss mind. 48 h gelaufen sein
SWITCH_MARGIN_PCT = 1.0   # muss mind. 1 Prozentpunkt besser sein
MUTATE_EVERY_HOURS = 72   # alle 3 Tage wird die schwächste Variante ersetzt
COIN_BLOCK_MIN_TRADES = 5
COIN_BLOCK_DAYS = 3
