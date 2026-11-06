"""Der eigentliche Bot: Scanner, Analyse, Positionen, Schutzregeln."""
import json
import math
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .analysis import to_df, add_indicators, detect_regime, strategy_for
from .strategy import (get_signal, choose_leverage, position_size, add_breakout_levels, breakout_signal,
                       breakout_exit, breakout_ready, BREAKOUT, BREAKOUT_MIN_CANDLES)
from .exchange import Exchange
from .backtest import run_backtest, run_breakout_backtest
from . import learning as L
from .notify import Notifier


TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")


PLAIN_REASON = {
    "Pullback an EMA20 im Aufwaertstrend": "Kurs steigt schon länger, ist kurz zurückgekommen und dreht wieder nach oben",
    "Pullback an EMA20 im Abwaertstrend": "Kurs fällt schon länger, ist kurz gestiegen und dreht wieder nach unten",
    "Unter Bollinger-Band, RSI ueberverkauft": "Kurs ist ungewöhnlich weit gefallen, meist pendelt er zurück",
    "Ueber Bollinger-Band, RSI ueberkauft": "Kurs ist ungewöhnlich weit gestiegen, meist pendelt er zurück",
}
PLAIN_CLOSE = {
    "Stop erreicht": "Verlust-Grenze erreicht", "Ziel erreicht": "Gewinn-Ziel erreicht",
    "Break-even/Trailing-Stop erreicht": "Gewinn gesichert", "Stop an der Boerse ausgeloest": "Verlust-Grenze bei der Börse ausgelöst",
    "Manuell geschlossen": "von dir beendet", "Gesamt-Notbremse": "Notbremse",
    "Trend vorbei": "Trend ist vorbei, Gewinn/Verlust mitgenommen",
    "Neustart": "beendet für den Neustart",
}
STRATEGY_MODES = ("standard", "ausbruch")

# Empfohlene Einstellungen nach dem Jahrestest (Okt. 2026). Werden beim ersten Start mit der
# neuen Version einmal uebernommen (pro Config-Datei), danach gelten wieder deine Einstellungen.
PRESET_VERSION = 23
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
PYRAMID = {"step_atr": 1.0, "max_units": 3}
MAJORS10 = ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC")
NEUSTART = {"account_reset": {"start": 1000.0, "at": None}, "max_capital_usdt": 0, "paper_start_balance": 1000}
PRESETS_V2 = {
    "config.yaml": {
        "strategy_mode": "ausbruch", "timeframe": "4h", "risk_per_trade": 0.0025, "max_positions": 10,
        "max_open_risk": 0.025, "max_total_drawdown": 0.25, "fee_rate": 0.0005, "symbol_mode": "manual",
        "symbols": [f"{b}/USDT" for b in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC")],
    },
    "config_5min.yaml": {"timeframe": "1h", "fee_rate": 0.0005},
    "config_bybit.yaml": {"timeframe": "4h", "risk_per_trade": 0.01},
}
# Version -> {Config-Datei -> Werte}. Beim Start werden alle noch nicht angewendeten Versionen einmal uebernommen.
PRESETS = {
    2: PRESETS_V2,
    # 18.10.2026: Neustart, jeder Bot mit 1000 und sauberem Ergebnis (alte Trades zaehlen nicht mehr mit)
    # Risiko auf Wunsch: 1 % pro Trade (10 bei 1000), zusammen hoechstens 5 % gleichzeitig (= max. 5 Trades)
    3: {"config.yaml": {**NEUSTART, "risk_per_trade": 0.01, "max_open_risk": 0.05},
        "config_5min.yaml": dict(NEUSTART), "config_bybit.yaml": dict(NEUSTART)},
    # 18.10.2026: alle Bots mit der Ausbruch-Strategie, jeder in einer anderen, getesteten Variante.
    # Neustart nochmal, damit nur Trades der neuen Strategie zaehlen.
    4: {
        "config.yaml": {"risk_per_trade": 0.01, "max_open_risk": 0.05},
        # Tageskerzen, Ausbruch 20 / Ausstieg 10 (Test 2020-2026: +341 R, 10/10 Coins, letzte 12 Monate +14 R)
        "config_5min.yaml": {**NEUSTART, "strategy_mode": "ausbruch", "timeframe": "1d", "bot_name": "Binance Tag",
                             "breakout_params": {"entry_n": 20, "exit_m": 10}, "risk_per_trade": 0.01,
                             "max_open_risk": 0.05, "max_positions": 10, "max_total_drawdown": 0.25,
                             "fee_rate": 0.0005, "symbol_mode": "manual", "symbols": [f"{b}/USDT" for b in MAJORS10]},
        # Spot nur Long, 4h, 55/20 (Test mit 0,1 % Spot-Gebuehr: +863 R, 10/10 Coins, letzte 12 Monate +5 R)
        "config_bybit.yaml": {**NEUSTART, "strategy_mode": "ausbruch", "timeframe": "4h", "risk_per_trade": 0.01,
                              "max_open_risk": 0.05, "max_positions": 10, "max_total_drawdown": 0.25,
                              "symbol_mode": "manual", "symbols": [f"{b}/USDC" for b in MAJORS10]},
    },
    # 18.10.2026: Nachkaufen bei den 4h-Bots (Test 2020-2026, je Teil 0,5 %, Gesamtverlust pro Trade max. 1 Teil:
    # Hauptbot letzte 12 Monate +50 % statt +19 %, Bybit +15 % statt 0 %). Tageskerzen-Bot bleibt ohne.
    5: {
        "config.yaml": {**NEUSTART, "risk_per_trade": 0.005, "pyramid": dict(PYRAMID)},
        "config_bybit.yaml": {**NEUSTART, "risk_per_trade": 0.005, "pyramid": dict(PYRAMID)},
    },
    # 18.10.2026 nachts: Einstellungen im Dashboard versehentlich verstellt -> zurueck auf den getesteten Stand,
    # nochmal Neustart, damit Trades aus der Zwischenzeit (Coin-Suche) nicht im Ergebnis landen.
    6: {
        "config.yaml": {**NEUSTART, "symbol_mode": "manual", "risk_per_trade": 0.005},
        "config_5min.yaml": {**NEUSTART, "symbol_mode": "manual", "risk_per_trade": 0.01},
        "config_bybit.yaml": {**NEUSTART, "symbol_mode": "manual", "risk_per_trade": 0.005},
    },
    # 18.10.2026: "nur wenn sicher" -> 4h-Bots steigen nur ein, wenn der lange Trend klar ist (>= 2 ATR von der EMA200)
    7: {
        "config.yaml": {"breakout_params": {"min_trend_atr": 2.0}},
        "config_bybit.yaml": {"breakout_params": {"min_trend_atr": 2.0}},
    },
    # 18.10.2026: Zwei-Konten-Plan (Bybit EU Spot + Kraken Futures). Tageskerzen-Bot rechnet ab jetzt mit Kraken-Kursen.
    # Der Hauptbot zieht mit kraken_einrichten.py um, sobald es Kraken-Demo-Keys gibt.
    8: {
        "config_5min.yaml": {**NEUSTART, "exchange": "krakenfutures", "quote": "USD", "bot_name": "Kraken Tag",
                             "fee_rate": 0.0005, "symbols": [f"{b}/USD" for b in MAJORS10],
                             "scan_always": ["BTC/USD"]},
    },
    # 18.10.2026: Nachkaufen bis 5 Teile (Test: 5 Teile x 0,33 % -> +69 % / Notbremse 0 % statt 3 x 0,5 % -> +58 % / 13 %).
    # Scanner-Test (5000 im Topf) auf Nicks Wunsch: 5 Teile x 1 % (Test: Notbremse oft, bewusst so gewaehlt).
    9: {
        "config.yaml": {"risk_per_trade": 0.0033, "pyramid": {"step_atr": 1.0, "max_units": 5}},
        "config_bybit.yaml": {"risk_per_trade": 0.0033, "pyramid": {"step_atr": 1.0, "max_units": 5}},
        "config_scanner.yaml": {"risk_per_trade": 0.01, "pyramid": {"step_atr": 1.0, "max_units": 5}},   # Nick: 1 %
    },
    # 18.10.2026 abends: Vergleichstest. 8004 ist die riskante Daytrade-Variante (1 % + 2 Teile),
    # 8005 "Daytrade sicher" hat schon 0,5 % ohne Nachkaufen.
    10: {
        "config_daytrade.yaml": {"bot_name": "Daytrade riskant", "risk_per_trade": 0.01,
                                 "pyramid": {"step_atr": 1.0, "max_units": 2}},
    },
    # 19.10.2026: Nick: "frueher merken, ob BTC steigt/faellt". Test 2020-2026:
    # BTC-Filter (EMA200) fuer alle; 4h zusaetzlich frueherer Ausbruch 30/15 statt 55/20.
    # 1h/15m bleiben 55/20 (30/15 war dort schlechter). Alle Bots neu mit 5000 (Regeln geaendert).
    11: {
        **{f: {"btc_filter": True, "breakout_params": {"min_trend_atr": 2.0, "entry_n": 30, "exit_m": 15},
               "account_reset": {"start": 5000.0, "at": None}}
           for f in ("config.yaml", "config_bybit.yaml", "config_scanner.yaml", "config_4h_einfach.yaml",
                     "config_riskant.yaml")},
        **{f: {"btc_filter": True, "account_reset": {"start": 5000.0, "at": None}}
           for f in ("config_5min.yaml", "config_daytrade.yaml", "config_daytrade_sicher.yaml",
                     "config_daytrade_mittel.yaml", "config_15min.yaml")},
    },
    # 20.10.2026: Nick: "sollen sich den Chart/Ichimoku anschauen". Test 2020-2026: auf 4h als Zusatz-Filter
    # Gewinn etwa gleich, schlechte Phasen etwas kleiner (auf neuen Coins neutral); auf 1h schlechter.
    # Kein Neustart noetig: die 4h-Bots hatten seit dem 19.10. noch keinen Trade.
    12: {
        f: {"breakout_params": {"min_trend_atr": 2.0, "entry_n": 30, "exit_m": 15, "ichimoku": True}}
        for f in ("config.yaml", "config_bybit.yaml", "config_scanner.yaml", "config_4h_einfach.yaml",
                  "config_riskant.yaml")
    },
    # 20.10.2026: Nick: "ueberall 50 % weniger Hebel". Bei diesen Bots bestimmt das Risiko pro Trade den Verlust
    # (der Hebel nur die gebundene Sicherheit) -> Risiko pro Trade UND Hebel-Obergrenze halbiert.
    13: {
        "config.yaml": {"risk_per_trade": 0.00165, "max_leverage": 5},
        "config_bybit.yaml": {"risk_per_trade": 0.00165, "max_leverage": 3},
        "config_5min.yaml": {"risk_per_trade": 0.005, "max_leverage": 3},
        "config_scanner.yaml": {"risk_per_trade": 0.005, "max_leverage": 5},
        "config_daytrade.yaml": {"risk_per_trade": 0.005, "max_leverage": 10},
        "config_daytrade_sicher.yaml": {"risk_per_trade": 0.0025, "max_leverage": 5},
        "config_daytrade_mittel.yaml": {"risk_per_trade": 0.005, "max_leverage": 5},
        "config_4h_einfach.yaml": {"risk_per_trade": 0.00165, "max_leverage": 5},
        "config_riskant.yaml": {"risk_per_trade": 0.01, "max_leverage": 5},
        "config_15min.yaml": {"risk_per_trade": 0.00125, "max_leverage": 5},
    },
    # 20.10.2026: Nick: "mehr/bessere Gewinne". Test 2021-2026 1h (55/20, BTC-Filter): kein Einstieg am Wochenende
    # + nur bei hohem Umsatz (1,5x) -> +18 % R, kleinere Rueckgaenge. Auf 4h kein klarer Vorteil -> nur 1h-Bots.
    14: {
        f: {"breakout_params": {"min_trend_atr": 2.0, "skip_weekend": True, "volume_mult": 1.5}}
        for f in ("config_daytrade.yaml", "config_daytrade_sicher.yaml", "config_daytrade_mittel.yaml")
    },
    # 20.10.2026: Nick: "resette die Bots, aber das Gelernte nicht vergessen" -> alle neu mit 5000.
    # Alte Trades, learning.json und gelernte Regeln bleiben gespeichert (zaehlen nur nicht mehr zum Ergebnis).
    15: {f: {"account_reset": {"start": 5000.0, "at": None}}
         for f in ("config.yaml", "config_bybit.yaml", "config_5min.yaml", "config_scanner.yaml", "config_daytrade.yaml",
                   "config_daytrade_sicher.yaml", "config_daytrade_mittel.yaml", "config_4h_einfach.yaml",
                   "config_riskant.yaml", "config_15min.yaml")},
    # 20.10.2026: Nick: "Limit-Orders statt Markt-Orders". Einstieg + ruhiger Ausstieg (Trend vorbei) als
    # post-only Limit (Maker 0,02 %), nach 15 s Rest per Markt. Stops bleiben Markt (muessen sicher greifen).
    # Bybit EU Spot nicht: dort kosten Limit und Markt gleich viel.
    16: {f: {"order_type": "limit", "limit_wait_s": 15, "fee_maker": 0.0002}
         for f in ("config.yaml", "config_5min.yaml", "config_scanner.yaml", "config_daytrade.yaml",
                   "config_daytrade_sicher.yaml", "config_daytrade_mittel.yaml", "config_4h_einfach.yaml",
                   "config_riskant.yaml", "config_15min.yaml")},
    # 20.10.2026 spaet: Nick: "4h-Bots auf 0,25 %" (Test: 0,165 % = Median 2M +3 %, DD 27 %; 0,25 % = +4,5 %, DD 38 %).
    # Scanner (0,5 %) und Riskant (1 %) bleiben als bewusst riskantere Vergleichs-Bots. Kein Neustart.
    17: {f: {"risk_per_trade": 0.0025} for f in ("config.yaml", "config_bybit.yaml", "config_4h_einfach.yaml")},
    # 21.10.2026: Nick: 8007 auf 1 % (Test 4h, 1 Teil: Median 2M +13 %, Notbremse 4 %, groesster Rueckgang 37 %).
    # Vergleich gleiche Strategie: 8007 = 1 %, 8018 = 2 %. Kein Neustart.
    18: {"config_4h_einfach.yaml": {"risk_per_trade": 0.01}},
    # 21.10.2026: Nick: "stell alles so, wie es laut Test passt". Einzige Aenderung: Scanner mit 5 Teilen x 0,5 %
    # lag ueber dem sinnvollen Bereich (Test 4h 5 Teile: 0,5 % -> groesster Rueckgang 62 %, 0,25 % -> 38 %,
    # auf fremden Coins noch schlechter). Jetzt wie der Hauptbot 0,25 % x 5. Kein Neustart.
    19: {"config_scanner.yaml": {"risk_per_trade": 0.0025}},
    # Nick 21.10.: Kurse alle 2 statt 3 Sekunden (Dashboard soll schneller mitlaufen).
    # 1 Sekunde waere bei ~12 Binance-Bots auf einer IP zu nah am Abruf-Limit.
    20: {f: {"price_seconds": 2}
         for f in ("config.yaml", "config_bybit.yaml", "config_5min.yaml", "config_scanner.yaml", "config_daytrade.yaml",
                   "config_daytrade_sicher.yaml", "config_daytrade_mittel.yaml", "config_4h_einfach.yaml",
                   "config_riskant.yaml", "config_15min.yaml", "config_2prozent.yaml", "config_gold.yaml")},
    # 22.10.2026 "Lern aus 10 Mio. Kerzen": 15min-Bot bekommt die Filter der 1h-Bots (kein Wochenende, Volumen > 1,5x).
    # Test 15m, 10 Coins, 2021-2026, Regeln wie der Bot (55/20, Limit rein): +255 R -> +852 R, Jahre im Plus 3/6 -> 5/6.
    21: {"config_15min.yaml": {"breakout_params": {"min_trend_atr": 2.0, "skip_weekend": True, "volume_mult": 1.5}}},
    # 22.10.2026 "Lern aus 100 Mio. Kerzen" (40 Coins, 1-Minuten-Daten 2021-2026): auf 1h und 15m laufen die Trends
    # laenger, als der Ausstieg nach 20 Kerzen erlaubt. Ausstieg erst unter dem Tief der letzten 40 Kerzen:
    # 1h (10 Coins, Bot-Regeln): +1204 R -> +1577 R, typische 2 Monate +0,7 % -> +4,6 %, alle Jahre im Plus.
    # 15m: +852 R -> +1583 R, Jahre im Plus 5/6 -> 6/6. 40 Coins, Lernen bis 2023 / Test 2024-26: gleiches Bild.
    22: {**{f: {"breakout_params": {"min_trend_atr": 2.0, "skip_weekend": True, "volume_mult": 1.5, "exit_m": 40}}
            for f in ("config_daytrade.yaml", "config_daytrade_sicher.yaml", "config_daytrade_mittel.yaml", "config_15min.yaml")}},
    # 22.10.2026 Nick: 15min-Bot auf 0,25 % Risiko pro Trade (vorher 0,125 %).
    # Test 15m mit neuen Regeln: etwa doppelter Gewinn, groesster Ruecksetzer ca. -41 % statt -23 % (Backtest).
    23: {"config_15min.yaml": {"risk_per_trade": 0.0025}},
}


# Die bekanntesten, am meisten gehandelten Coins. Werden (wenn eingeschaltet) immer beobachtet.
MAJORS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "SUI", "TON"]


def plain_reason(r):
    return PLAIN_REASON.get(r, r)


def plain_close(r):
    return PLAIN_CLOSE.get(r, r)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Engine:
    EDITABLE = {
        "risk_per_trade": (float, 0.001, 0.05), "max_leverage": (int, 1, 20),
        "max_positions": (int, 1, 20), "daily_loss_limit": (float, 0.005, 0.5),
        "breakeven_at_r": (float, 0.2, 5), "trail_at_r": (float, 0.5, 10),
        "spot_max_allocation": (float, 0.05, 1), "loop_seconds": (int, 10, 600),
        "price_seconds": (int, 1, 60), "scan_top": (int, 1, 15),
        "scan_min_volume_m": (float, 0.1, 5000), "scan_minutes": (int, 5, 240),
        "max_total_drawdown": (float, 0.05, 0.9), "max_capital_usdt": (float, 0, 1e9),
        "scan_top_volume": (int, 0, 15), "max_open_risk": (float, 0, 0.5),
    }

    def __init__(self, cfg, config_path="config.yaml"):
        # Mehrere Bots im selben Ordner: jeder hat eigene Config und eigenen Datenordner
        self.config_path = config_path
        self.state_file = Path(cfg.get("data_dir", "data")) / "state.json"
        cfg.setdefault("symbol_mode", "manual")
        cfg.setdefault("scan_top", 5)
        cfg.setdefault("scan_min_volume_m", 50)
        cfg.setdefault("scan_minutes", 15)
        cfg.setdefault("price_seconds", 3)
        cfg.setdefault("max_total_drawdown", 0.20)   # Gesamt-Notbremse: -20 % vom Hoechststand
        cfg.setdefault("max_capital_usdt", 0)        # 0 = ganzes Konto, sonst Obergrenze fuer den Bot
        self.cfg = cfg
        cfg.setdefault("strategy_mode", "standard")
        cfg.setdefault("max_open_risk", 0)            # 0 = aus, sonst max. Summe aller Trade-Risiken (Anteil vom Konto)
        # Grundwerte, falls eine (neue) Config sie nicht enthaelt
        for k, v in {"spot_max_allocation": 0.2, "breakeven_at_r": 1.0, "trail_at_r": 2.0, "fee_rate": 0.0005,
                     "daily_loss_limit": 0.03, "max_positions": 10, "max_leverage": 10, "loop_seconds": 15,
                     "paper_start_balance": 1000, "follow_pros": "nicht_gegen", "strategy_params": {}}.items():
            cfg.setdefault(k, v)
        cfg.setdefault("learned", {"rules": [], "coin_cooldown": {}})
        self._preset_changes = self._apply_preset()  # VOR der Boersen-Verbindung (Preset kann die Boerse wechseln)
        self.lock = threading.Lock()
        self.ex = Exchange(cfg)
        self.positions, self.trades, self.log = {}, [], []
        self.market_view, self.scanner_rows, self.auto_symbols = {}, [], []
        self.last_scan, self.last_signal_ts = 0.0, {}
        self.paused, self.halted_until = False, None
        self.day, self.day_start_equity = None, None
        self.status, self.last_price_update, self.connection = "startet", None, None
        self.notify = Notifier(self._log, prefix=cfg.get("bot_name") or getattr(self.ex, "label", ""))
        self.kill_reason, self.peak_equity, self.preflight = None, None, None
        self._err_streak, self._err_alerted = 0, False
        self.learn = L.LearningStore(self.state_file.parent / "learning.json")
        # Bitcoin hat jeder Bot immer im Blick (zusaetzlich zur Coin-Suche), solange nichts anderes eingestellt ist
        cfg.setdefault("scan_always", [f"BTC/{getattr(self.ex, 'quote', 'USDT')}"])
        cfg.setdefault("scan_majors", True)        # bekannte Coins (BTC, ETH, SOL, XRP, ...) immer beobachten
        cfg.setdefault("scan_top_volume", 5)       # + die 5 meistgehandelten Coins des Tages
        # Profis folgen: "aus" | "nicht_gegen" (nie gegen die Mehrheit der Top-Trader) | "nur_mit" (nur wenn sie klar mitwetten)
        cfg.setdefault("follow_pros", "nicht_gegen")
        self.top_volume_syms, self._valid_cache, self._sym_err = [], {}, {}
        self._learn_dirty = True          # nach jedem abgeschlossenen Trade neu auswerten
        if cfg.get("bot_name"):
            self.notify.prefix = f"[{cfg['bot_name']}] "
        self._load()

    def breakout(self):
        return self.cfg.get("strategy_mode") == "ausbruch"

    def _apply_preset(self):
        have = int(self.cfg.get("preset_version") or 0)
        if have >= PRESET_VERSION:
            return []
        changed = []
        for ver in sorted(v for v in PRESETS if v > have):
            preset = json.loads(json.dumps(PRESETS[ver].get(Path(self.config_path).name, {})))
            changed += [f"{k}: {self.cfg.get(k)} -> {v}" for k, v in preset.items()
                        if self.cfg.get(k) != v and k != "account_reset"]
            if "account_reset" in preset:
                changed.append(f"Neustart mit {preset['account_reset']['start']:.0f} (alte Trades zählen nicht mehr mit)")
            self.cfg.update(preset)
        self.cfg["preset_version"] = PRESET_VERSION
        try:
            yaml.safe_dump(self.cfg, open(self.config_path, "w", encoding="utf-8"), sort_keys=False, allow_unicode=True)
        except OSError:
            pass
        return changed

    # ---- Persistenz --------------------------------------------------------
    def _load(self):
        if self.state_file.exists():
            d = json.loads(self.state_file.read_text())
            self.trades = d.get("trades", [])
            # Positionen nur uebernehmen, wenn sie aus demselben Modus stammen
            if self._same_account(d):
                self.positions = d.get("positions", {})
            if self.cfg["mode"] == "paper":
                self.ex.paper_balance = d.get("paper_balance", self.ex.paper_balance)
            if self._same_account(d):
                self.peak_equity = d.get("peak_equity")
                self.kill_reason = d.get("kill_reason")
                if hasattr(self.ex, "blocked"):
                    self.ex.blocked.update(d.get("blocked_symbols", []))
                if self.kill_reason:
                    self.paused = True

    def _save(self):
        self.state_file.parent.mkdir(exist_ok=True)
        self.state_file.write_text(json.dumps({
            "mode": self.cfg["mode"], "exchange": self.ex.name, "positions": self.positions,
            "trades": self.trades[-1000:], "paper_balance": self.ex.paper_balance,
            "peak_equity": self.peak_equity, "kill_reason": self.kill_reason,
            "blocked_symbols": sorted(getattr(self.ex, "blocked", set())),
        }, indent=1))

    def _log(self, msg, kind="info"):
        entry = {"t": datetime.now().strftime("%H:%M:%S"), "kind": kind, "msg": msg}   # Uhrzeit deines PCs
        self.log = (self.log + [entry])[-300:]
        try:
            print(entry["t"], msg, flush=True)
        except (OSError, ValueError):
            pass   # Konsolenfenster nicht erreichbar (z. B. WinError 233) -> Bot laeuft trotzdem weiter

    # ---- Welche Coins handelt der Bot gerade? ------------------------------
    def watch_extra(self):
        """Zusaetzlich zur Coin-Suche immer beobachtet: eigene Liste + bekannte Coins + meistgehandelte Coins.
        Nur Coins, die es an dieser Boerse gibt und die nicht gesperrt sind."""
        if self.cfg["symbol_mode"] != "scanner":
            return []
        q = getattr(self.ex, "quote", "USDT")
        syms = list(self.cfg.get("scan_always") or [])
        if self.cfg.get("scan_majors", True):
            syms += [f"{b}/{q}" for b in MAJORS]
        syms += self.top_volume_syms
        blocked = getattr(self.ex, "blocked", set())
        ok = []
        for s in dict.fromkeys(syms):
            if s in blocked:
                continue
            if s not in self._valid_cache:
                fn = getattr(self.ex, "is_valid", None)
                self._valid_cache[s] = bool(fn(s)) if fn else True
            if self._valid_cache[s]:
                ok.append(s)
        return ok

    def active_symbols(self):
        base = self.auto_symbols if self.cfg["symbol_mode"] == "scanner" else self.cfg["symbols"]
        return list(dict.fromkeys(list(base) + self.watch_extra() + list(self.positions)))

    # ---- Steuerung aus dem Dashboard ---------------------------------------
    def pause(self):
        self.paused = True
        self._log("Pause: keine neuen Trades. Auf laufende Trades passt der Bot weiter auf.", "warn")

    def resume(self):
        if self.preflight and not self.preflight["ok"]:
            self._log("Weiter geht nicht: Beim Start ist ein Problem aufgefallen (siehe roter Kasten oben). Erst beheben, dann Bot neu starten.", "error")
            return
        with self.lock:
            if self.kill_reason:
                self._log(f"Notbremse von dir aufgehoben (Grund war: {self.kill_reason}). Der Bot zählt ab jetzt vom aktuellen Kontostand.", "warn")
                self.kill_reason, self.peak_equity = None, None
            self.paused, self.halted_until = False, None
            self._save()
        self._log("Bot läuft wieder.")
        self.notify.send("▶️ Bot laeuft wieder.")

    def close_all(self):
        with self.lock:
            for sym in list(self.positions):
                self._close(sym, self.ex.price(sym), "Manuell geschlossen")
            self._save()
        self.paused = True
        self._log("NOT-AUS: alle Trades beendet, Bot angehalten.", "warn")
        self.notify.send("NOT-AUS im Dashboard: alle Positionen geschlossen, Bot pausiert.", important=True)

    def deposit(self, amount):
        """Einzahlung buchen. Testkonto/Simulation: der Topf des Bots wird groesser (zaehlt NICHT als Gewinn).
        Echtes Konto ohne Topf: einfach bei der Boerse einzahlen, der Bot rechnet automatisch mit dem neuen Kontostand."""
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            return {"ok": False, "msg": "Bitte einen Betrag eingeben."}
        if not 0 < amount <= 1_000_000:
            return {"ok": False, "msg": "Bitte einen Betrag über 0 eingeben."}
        r = self._reset_info()
        if not r.get("at"):
            return {"ok": False, "msg": "Echtes Konto: Zahl das Geld einfach bei der Börse ein. Der Bot rechnet automatisch mit dem neuen Kontostand."}
        with self.lock:
            r["start"] = round(float(r["start"]) + amount, 2)
            r.setdefault("deposits", []).append({"at": now_iso(), "amount": amount})
            if self.cfg["mode"] == "paper":
                self.ex.paper_balance += amount
            if self.peak_equity:
                self.peak_equity += amount
            if self.day_start_equity:
                self.day_start_equity += amount
            self._persist_reset()
            self._save()
        msg = f"Einzahlung {amount:.2f} {self.ex.quote} gebucht. Der Topf ist jetzt {r['start']:.2f} plus Ergebnis; zählt nicht als Gewinn."
        self._log(msg)
        self.notify.send("💶 " + msg)
        return {"ok": True, "msg": msg}

    def close_one(self, sym):
        with self.lock:
            if sym in self.positions:
                self._close(sym, self.ex.price(sym), "Manuell geschlossen")
                self._save()

    def scan_now(self):
        self.last_scan = 0

    def backtest(self, symbol, days, timeframe=None):
        symbol = self._normalize(symbol)
        if not symbol or not self.ex.is_valid(symbol):
            return {"error": f"Diesen Coin gibt es auf {self.ex.label} nicht."}
        tf = timeframe if timeframe in TIMEFRAMES else self.cfg["timeframe"]
        days = max(3, min(730 if self.breakout() else 180, int(days)))
        candles = self.ex.history(symbol, tf, days)
        if self.breakout():
            res = run_breakout_backtest(candles, dict(self.cfg), self.ex.is_spot, timeframe=tf)
        else:
            res = run_backtest(candles, dict(self.cfg), self.ex.is_spot)
        res.update({"symbol": symbol, "timeframe": tf, "days": days})
        if "error" not in res:
            self._log(f"Test mit alten Kursen, {symbol}, {days} Tage, Kerzen {tf}: {res['trades']} Trades, Ergebnis {res['return_pct']:+.1f} %, "
                      f"max. Rueckgang {res['max_drawdown_pct']:.1f}%")
        return res

    def overview(self):
        """Kurze Zusammenfassung fuer die Gesamt-Uebersicht aller Bots (Handy)."""
        with self.lock:
            r = self._reset_info()
            period = self._period_trades()
            realized = self._realized_since_reset() if r.get("at") else sum(t["pnl"] for t in period)
            unreal = sum(p.get("unrealized", 0) or 0 for p in self.positions.values())
            start = float(r.get("start") or self.cfg.get("paper_start_balance") or 0)
            deposits = sum(float(d.get("amount") or 0) for d in r.get("deposits", []))
            today_s = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            pos = []
            for sym, p in self.positions.items():
                px = (self.market_view.get(sym) or {}).get("price") or p["entry"]
                risk = p.get("risk_usdt") or 0
                u = p.get("unrealized", 0) or 0
                pos.append({"symbol": sym, "side": p["side"], "entry": p["entry"], "price": px, "stop": p.get("stop"),
                            "pnl": u, "r": (u / risk) if risk else None, "units": len(p.get("units") or [1]),
                            "max_units": p.get("max_units") or 1, "leverage": p.get("leverage"),
                            "opened_at": p.get("opened_at"), "exit": p.get("exit_level"), "safe": self._safe_pnl(p)})
            pos.sort(key=lambda x: x["opened_at"] or "")
            last = [{"symbol": t["symbol"], "side": t["side"], "pnl": t["pnl"], "reason": plain_close(t.get("reason")),
                     "closed_at": t["closed_at"]} for t in period[-3:]][::-1]
            gewinn = realized + unreal
            if self.kill_reason:
                status = "Notbremse"
            elif self.paused:
                status = "Pause"
            elif self.halted_until:
                status = "Tageslimit"
            elif self.reset_pending():
                status = "startet"
            else:
                status = "läuft"
            return {
                "name": self.cfg.get("bot_name") or ("Hauptbot" if self.config_path == "config.yaml" else self.ex.label),
                "port": self.cfg.get("dashboard_port"), "mode": self.cfg["mode"], "exchange": self.ex.label,
                "quote": self.ex.quote, "timeframe": self.cfg["timeframe"], "risk": self.cfg["risk_per_trade"],
                "units": int((self.cfg.get("pyramid") or {}).get("max_units") or 1),
                "start": start, "deposits": deposits, "realized": realized, "unrealized": unreal,
                "konto": start + gewinn, "gewinn": gewinn, "pct": (gewinn / start * 100) if start else 0,
                "trades": len(period), "wins": sum(1 for t in period if t["pnl"] > 0),
                "today": sum(t["pnl"] for t in period if t["closed_at"][:10] == today_s),
                "status": status, "since": r.get("at"), "positions": pos, "last": last,
                "safe": (round(sum(x["safe"] for x in pos if x["safe"] is not None), 2) if pos else None),
                "updated": self.last_price_update,
            }

    def snapshot(self):
        with self.lock:
            equity = self.ex.equity() if self.cfg["mode"] == "paper" else (self.connection or {}).get("equity", 0)
            unreal = sum(p.get("unrealized", 0) for p in self.positions.values())
            today_s = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            mine = self._period_trades()   # nur seit dem Neustart
            today = [t for t in mine if t["closed_at"][:10] == today_s]
            wins = [t for t in mine if t["pnl"] > 0]
            active = self.active_symbols()
            return {
                "learning": self._learning_snapshot(self._my_trades(), equity),
                "budget": ({**self._reset_info(), "real_equity": getattr(self, "real_equity", None)}
                           if self._reset_info().get("at") else None),
                "mode": self.cfg["mode"], "market": self.cfg["market"], "status": self.status,
                "exchange": self.ex.label, "quote": self.ex.quote,
                "paused": self.paused, "halted_until": self.halted_until, "connection": self.connection,
                "kill_reason": self.kill_reason, "preflight": self.preflight,
                "telegram": self.notify.enabled, "auto_learn": L.settings(self.cfg)["auto_apply"],
                "peak_equity": self.peak_equity, "exchange_pnl": getattr(self, "exchange_pnl", None),
                "exchange_pnl_today": getattr(self, "exchange_pnl_today", None),
                "exchange_stops": self.ex.supports_exchange_stop(),
                "equity": equity, "unrealized": unreal,
                "today_pnl": sum(t["pnl"] for t in today), "total_pnl": sum(t["pnl"] for t in mine),
                "trades_count": len(mine),
                "win_rate": (len(wins) / len(mine) * 100) if mine else 0,
                "positions": [{**p, "safe": self._safe_pnl(p)} for p in self.positions.values()],
                "markets": [self.market_view[s] for s in active if s in self.market_view],
                "scanner": self.scanner_rows, "last_scan": self.last_scan,
                "trades": list(reversed(mine[-30:])), "log": list(reversed(self.log[-80:])),
                "last_price_update": self.last_price_update,
                "config": {**{k: self.cfg.get(k) for k in list(self.EDITABLE) + ["timeframe", "symbols", "symbol_mode", "scan_always", "scan_majors", "follow_pros", "strategy_mode"]},
                           "pyramid_max_units": int((self.cfg.get("pyramid") or {}).get("max_units") or 1),
                           "watch_extra": self.watch_extra(),
                           "learning_auto_apply": L.settings(self.cfg)["auto_apply"]},
            }

    # ---- Hauptschleife -----------------------------------------------------
    def run(self):
        if self._preset_changes:
            self._log("Neue empfohlene Einstellungen übernommen (einmalig): " + "; ".join(self._preset_changes), "warn")
            self.notify.send("⚙️ Neue Einstellungen übernommen:\n" + "\n".join(self._preset_changes))
        self._connect()
        self._drop_unknown_symbols()
        try:
            self._reconcile_history()
        except Exception as e:
            self._log(f"Abgleich mit der Börse hat nicht geklappt: {e}", "warn")
        if self.learn.tune_result and not L.learned(self.cfg).get("ampel"):
            with self.lock:
                self._update_ampel(self.learn.tune_result, False)
        while True:
            try:
                if self.cfg["symbol_mode"] == "scanner" and time.time() - self.last_scan > self.cfg["scan_minutes"] * 60:
                    self._scan()
                self._maybe_reset()
                self._tick()
                self._learning_cycle()
                self._refresh_exchange_pnl()
                self.status = "pausiert" if self.paused else "laeuft"
                self._err_streak, self._err_alerted = 0, False
            except Exception as e:
                if "-1021" in str(e) and getattr(self.ex, "resync_time", None):
                    # PC-Uhr weicht von der Boersen-Uhr ab -> sofort neu abgleichen, naechster Durchlauf klappt dann
                    if self.ex.resync_time(force=True):
                        self._log("PC-Uhr weicht von der Börsen-Uhr ab. Neu abgeglichen, es geht gleich normal weiter.", "warn")
                        time.sleep(2)
                        continue
                self.status = f"Fehler: {e}"
                self._log(f"Fehler: {e}", "error")
                self._hb_errs = getattr(self, "_hb_errs", 0) + 1
                try:
                    traceback.print_exc()
                except (OSError, ValueError):
                    pass
                self._err_streak += 1
                if self._err_streak >= 5 and not self._err_alerted:
                    self._err_alerted = True
                    self.notify.send(f"Bot hat seit {self._err_streak} Durchlaeufen Fehler: {e}\n"
                                     "Offene Futures-Positionen sind durch Stops an der Boerse geschuetzt.", important=True)
            time.sleep(self.cfg["loop_seconds"])

    def _drop_unknown_symbols(self):
        """Feste Liste: Coins, die es an dieser Boerse nicht gibt, einmal beim Start aussortieren."""
        if self.cfg.get("symbol_mode") != "manual" or not getattr(self.ex, "is_valid", None):
            return
        try:
            ok = [s for s in self.cfg["symbols"] if self.ex.is_valid(s)]
        except Exception:
            return
        gone = [s for s in self.cfg["symbols"] if s not in ok]
        if gone and ok:
            self.cfg["symbols"] = ok
            self._persist_reset()
            self._log(f"Gibt es bei {self.ex.label} nicht, aus der Liste genommen: {', '.join(gone)}", "warn")

    def _connect(self):
        mode = {"paper": f"Simulation ({self.ex.label}-Kurse)", "testnet": f"{self.ex.label} Testnet",
                "live": f"{self.ex.label} ECHTGELD"}[self.cfg["mode"]]
        try:
            eq = self._equity()
            self.connection = {"ok": True, "label": mode, "equity": eq}
            self._log(f"Verbunden: {mode} ({self.cfg['market']}). Geld auf dem Konto: {eq:.2f} {self.ex.quote}.")
            if self.cfg["mode"] != "paper" and eq <= 0:
                self._log(f"Kein {self.ex.quote}-Guthaben gefunden. Bei Futures: Geld in der App vom Spot- ins Futures-Konto übertragen.", "warn")
            if self.cfg["mode"] != "paper" and self.ex.is_spot:
                self._log("Spot: Auf die Verlust-Grenzen passt der Bot selbst auf. Der PC muss dafür laufen.", "warn")
            pf = getattr(self.ex, "preflight", None)
            self.preflight = pf() if pf else None
            if self.preflight:
                for n in self.preflight["notes"]:
                    self._log("Beim Start aufgefallen: " + n, "warn")
                if not self.preflight["ok"]:
                    self.paused = True
                    for p in self.preflight["problems"]:
                        self._log("Beim Start aufgefallen: " + p, "error")
                    self.notify.send("Echtgeld-Start blockiert:\n- " + "\n- ".join(self.preflight["problems"]), important=True)
            cap = self.cfg.get("max_capital_usdt") or 0
            self.notify.send(f"🤖 Bot gestartet: {mode} ({self.cfg['market']}). Kontostand {eq:.2f} {self.ex.quote}"
                             + (f", Bot nutzt max. {cap:.0f} {self.ex.quote}" if cap else "")
                             + (". PAUSIERT (Notbremse aktiv)." if self.kill_reason else "."))
        except Exception as e:
            self.connection = {"ok": False, "label": mode, "error": str(e)}
            self._log(f"Keine Verbindung zu {self.ex.label}: {e}. API-Key, seine Rechte und die IP-Freigabe prüfen.", "error")
            self.notify.send(f"Bot kann sich nicht mit {self.ex.label} verbinden: {e}", important=True)

    def _check_day(self, equity):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.day != today:
            if self.day:
                self._daily_report(self.day, equity)
            self.day, self.day_start_equity = today, equity
            if self.halted_until:
                self.halted_until = None
                self._log("Neuer Tag: Tages-Verlustgrenze zurückgesetzt, der Bot darf wieder neue Trades machen.")
        dd = (equity - self.day_start_equity) / self.day_start_equity if self.day_start_equity else 0
        if dd <= -self.cfg["daily_loss_limit"] and not self.halted_until:
            self.halted_until = today + " 24:00 UTC"
            self._log(f"Heute schon {dd*100:.1f} % im Minus. Keine neuen Trades bis 2 Uhr nachts.", "warn")
            self.notify.send(f"Tagesverlust-Limit erreicht ({dd*100:.1f} %). Keine neuen Trades bis Mitternacht (UTC).", important=True)
        self._check_total_drawdown(equity)

    def _check_total_drawdown(self, equity):
        """Gesamt-Notbremse: faellt das Konto zu weit unter den Hoechststand, alles schliessen und stoppen.
        Bleibt auch nach einem Neustart aktiv, bis du im Dashboard auf 'Weiter' klickst."""
        if equity <= 0 or self.kill_reason:
            return
        self.peak_equity = max(self.peak_equity or equity, equity)
        limit = self.cfg.get("max_total_drawdown") or 0
        if limit and equity <= self.peak_equity * (1 - limit):
            self.kill_reason = (f"Gesamt-Notbremse: Konto {equity:.2f} {self.ex.quote} liegt {limit*100:.0f} % unter dem "
                                f"Hoechststand {self.peak_equity:.2f} {self.ex.quote}")
            self._log(self.kill_reason + ". Notbremse: Alle Trades werden beendet.", "error")
            for sym in list(self.positions):
                try:
                    self._close(sym, self.ex.price(sym), "Gesamt-Notbremse")
                except Exception as e:
                    self._log(f"{sym}: Beenden bei der Notbremse hat nicht geklappt ({e})", "error")
            self.paused = True
            self._save()
            self.notify.send(self.kill_reason + ".\nAlle Positionen geschlossen, Bot gestoppt. "
                             "Weiter nur per Klick im Dashboard.", important=True)

    def _daily_report(self, day, equity):
        mine = [t for t in self._period_trades() if t["closed_at"][:10] == day]
        pnl = sum(t["pnl"] for t in mine)
        wins = sum(1 for t in mine if t["pnl"] > 0)
        self.notify.send(f"📊 Tagesbilanz {day}: {len(mine)} Trades, {wins} Gewinner, {pnl:+.2f} {self.ex.quote}. "
                         f"Kontostand {equity:.2f} {self.ex.quote}, offene Positionen: {len(self.positions)}.")

    # ---- Scanner (wie "Stock Races" / "Money Machine", nur fuer Krypto) -----
    def _scan(self):
        self.last_scan = time.time()
        rows = self.ex.universe(self.cfg["scan_min_volume_m"] * 1_000_000)
        if not rows:
            self._log("Coin-Suche: Kein Coin wird genug gehandelt (Mindest-Handelsvolumen in den Einstellungen).", "warn")
            return
        # Die meistgehandelten Coins des Tages (Umsatz) immer mit beobachten
        n_top = int(self.cfg.get("scan_top_volume") or 0)
        self.top_volume_syms = [r["symbol"] for r in sorted(rows, key=lambda r: r["volume"], reverse=True)[:n_top]]
        # Vorauswahl: groesste Bewegung + Umsatz
        for r in rows:
            r["momentum"] = abs(r["change24"]) * math.log10(max(r["volume"], 10))
        rows.sort(key=lambda r: r["momentum"], reverse=True)
        candidates = rows[:20]
        for r in candidates:
            try:
                df = add_indicators(to_df(self.ex.ohlcv(r["symbol"], self.cfg["timeframe"], **self._ohlcv_kw())).iloc[:-1])
                last = df.iloc[-1]
                if not all(math.isfinite(float(last[k])) for k in ("adx", "vol_pct", "ema50")):
                    raise ValueError("zu wenig Kursdaten")
                r["regime"] = detect_regime(last)
                r["adx"] = round(float(last["adx"]), 1)
                r["vol_pct"] = round(float(last["vol_pct"]) * 100, 2)
            except Exception:
                r["regime"], r["adx"], r["vol_pct"] = "neutral", 0, 0
            # Score: sauberer Trend zaehlt am meisten, Seitwaerts ok, Chaos/unklar raus
            bonus = {"trend_up": 2.0, "trend_down": 2.0 if not self.ex.is_spot else 0.0, "range": 1.0}.get(r["regime"], 0.0)
            r["score"] = round(bonus * (1 + r["adx"] / 50) * r["momentum"], 1)
        candidates.sort(key=lambda r: r["score"], reverse=True)
        for r in candidates:
            r["cooldown"] = bool(L.coin_blocked_until(self.cfg, r["symbol"]))
            r["always"] = r["symbol"] in (self.cfg.get("scan_always") or [])
        picked = [r["symbol"] for r in candidates if r["score"] > 0 and not r["cooldown"]
                  and r["symbol"] not in self.ex.blocked][: self.cfg["scan_top"]]
        for r in candidates:
            r["picked"] = r["symbol"] in picked
        with self.lock:
            self.scanner_rows = candidates
            if picked != self.auto_symbols:
                self.auto_symbols = picked
                self._log("Coin-Suche: beobachte jetzt " + (", ".join(picked) if picked else "keinen Coin, der Markt ist gerade zu unklar"))

    def _tick(self):
        with self.lock:
            equity = self._equity()
            if self.connection:
                self.connection["equity"] = equity
            self._check_day(equity)
            self._last_equity = equity
            self._sync_exchange()
            active = self.active_symbols()
            for sym in active:
                try:
                    self._tick_symbol(sym, equity)
                except Exception as e:   # ein kaputter Coin darf die anderen nicht aufhalten
                    last = self._sym_err.get(sym, 0)
                    if time.time() - last > 3600:
                        self._sym_err[sym] = time.time()
                        self._log(f"{sym}: Kursdaten holen/auswerten hat nicht geklappt ({e}). Die anderen Coins laufen normal weiter.", "warn")
                    self._hb_errs = getattr(self, "_hb_errs", 0) + 1
            for sym in [s for s in self.market_view if s not in active]:
                del self.market_view[sym]
            self._save()
            self._heartbeat(active, equity)

    def _heartbeat(self, active, equity):
        """Alle paar Stunden eine Zeile ins Protokoll: Bot lebt, was er gerade prueft (nur Anzeige, aendert nichts am Handeln)."""
        every = float(self.cfg.get("heartbeat_hours", 2)) * 3600
        now = time.time()
        if now - getattr(self, "_hb_at", 0) < every:
            return
        self._hb_at = now
        errs = getattr(self, "_hb_errs", 0)
        self._hb_errs = 0
        konto = f"{equity:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        txt = (f"Lebenszeichen: Bot {'pausiert' if self.paused else 'läuft'}. {len(active)} Coins geprüft, "
               f"{len(self.positions)} Trade(s) offen, Konto {konto} {self.ex.quote}")
        txt += "." if errs == 0 else f". Achtung: {errs} Fehler seit der letzten Meldung (siehe oben)."
        if errs == 0:
            txt += " Alles in Ordnung."
        self._log(txt, "info" if errs == 0 else "warn")

    def _tick_symbol(self, sym, equity):
        # nur abgeschlossene Kerzen: genau wie im Backtest, kein "Flackern".
        # Nach der Uhrzeit filtern (nicht einfach die letzte Kerze weglassen): direkt nach Kerzenende
        # liefert die Boerse die neue Kerze manchmal noch nicht.
        tf_sec = TF_SECONDS.get(self.cfg["timeframe"], 3600)
        now_ts = datetime.now(timezone.utc).timestamp()
        df = add_indicators(self._closed_candles(sym))
        bo = self.breakout()
        if bo:
            df = add_breakout_levels(df, self.cfg.get("breakout_params"))
        row = df.iloc[-1]
        if any(not math.isfinite(float(row[k])) for k in ("adx", "rsi", "atr", "vol_pct", "ema50")):
            return   # zu wenig Kursdaten (z. B. ganz neuer Coin) -> nicht bewerten, nicht handeln
        p_open = self.positions.get(sym)
        if p_open and p_open.get("strategy") == "Ausbruch" and bo:
            self._update_exit_level(sym, p_open, row)
        if p_open and p_open.get("strategy") == "Ausbruch" and bo and breakout_exit(row, p_open["side"]):
            price = (self.market_view.get(sym) or {}).get("price") or self.ex.price(sym)
            self._close(sym, float(price), "Trend vorbei")
            self.last_signal_ts[sym] = str(row["ts"])   # wie im Test: in derselben Kerze kein neuer Einstieg
        regime = detect_regime(row)
        old = self.market_view.get(sym, {})
        self.market_view[sym] = {
            "symbol": sym, "price": old.get("price", float(row["close"])), "regime": regime,
            "strategy": strategy_for(regime), "adx": round(float(row["adx"]), 1),
            "rsi": round(float(row["rsi"]), 1), "vol_pct": round(float(row["vol_pct"]) * 100, 2),
            "leverage": self._leverage(float(row["vol_pct"])),
        }
        if bo:
            ready = breakout_ready(row)
            self.market_view[sym]["strategy"] = "Ausbruch" if ready else "Ausbruch: noch zu wenig Kursdaten"
            if ready:
                self.market_view[sym]["bo_up"] = float(row["bo_hi"])
                self.market_view[sym]["bo_down"] = float(row["bo_lo"]) if not self.ex.is_spot else None
                self.market_view[sym]["trend_long"] = "steigt" if row["close"] > row["ema_long"] else "faellt"
        ratio = self._ratio(sym)
        if ratio:
            self.market_view[sym]["top_long"] = round(ratio["top_long"] * 100)
            self.market_view[sym]["crowd_long"] = round(ratio["crowd_long"] * 100)
        candle = str(row["ts"])
        cooldown = None if bo else L.coin_blocked_until(self.cfg, sym)
        if cooldown:
            self.market_view[sym]["strategy"] = "Pause - gelernt: Coin gemieden"
        # Wie im Test: ein Signal gilt nur direkt nach dem Kerzenende (Einstieg zur naechsten Kerze).
        # Sonst wuerde der Bot nach einem Stop oder nach einem Neustart auf ein altes Signal nochmal einsteigen.
        age = now_ts - (row["ts"].timestamp() + tf_sec)
        fresh = (not bo) or age <= max(120, min(tf_sec * 0.25, 1800))
        if (sym not in self.positions and not cooldown and self._may_open() and fresh
                and self.last_signal_ts.get(sym) != candle):
            if bo:
                sig = breakout_signal(df, allow_short=not self.ex.is_spot, prm=self.cfg.get("breakout_params"))
            else:
                sig = get_signal(regime, df, allow_short=not self.ex.is_spot, params=L.params_from(self.cfg))
            if sig and bo and self.cfg.get("btc_filter"):
                bt = self._btc_trend()
                if bt == 0 or (sig.side == "long") != (bt > 0):
                    self.last_signal_ts[sym] = candle
                    why = "Bitcoin-Trend unbekannt" if bt == 0 else ("Bitcoin steigt" if bt > 0 else "Bitcoin fällt")
                    self._log(f"{sym}: {'Long' if sig.side == 'long' else 'Short'}-Signal nicht genommen ({why}, BTC-Filter).")
                    sig = None
            if sig:
                self.last_signal_ts[sym] = candle
                self._open(sym, sig, row, regime, self._sizing_equity(equity))

    def _update_exit_level(self, sym, p, row):
        """Merkt sich die aktuelle 'Trend vorbei'-Linie (Tief/Hoch der letzten M Kerzen) als 'Raus spaetestens bei'.
        Nur Anzeige + Telegram, wenn sie deutlich nachgezogen wurde (>= 0,5 ATR). Am Handeln aendert das nichts."""
        try:
            lvl = float(row["ex_lo"] if p["side"] == "long" else row["ex_hi"])
            atr = float(row.get("atr_bo") or row.get("atr") or 0)
        except (KeyError, TypeError, ValueError):
            return
        if not math.isfinite(lvl):
            return
        p["exit_level"] = lvl
        sign = 1 if p["side"] == "long" else -1
        last = p.get("exit_notified")
        if last is None:
            p["exit_notified"] = lvl
            return
        if atr > 0 and (lvl - last) * sign >= 0.5 * atr:
            p["exit_notified"] = lvl
            locked = (lvl - p["entry"]) * sign * p["amount"]
            msg = (f"{sym}: Ausstieg nachgezogen auf {lvl:.6g} (Kurs fällt unter das Tief der letzten Kerzen → raus). "
                   if sign == 1 else
                   f"{sym}: Ausstieg nachgezogen auf {lvl:.6g} (Kurs steigt über das Hoch der letzten Kerzen → raus). ")
            msg += (f"Damit wären {locked:+.2f} {self.ex.quote} gesichert." if locked >= 0
                    else f"Der Verlust wäre damit höchstens {locked:+.2f} {self.ex.quote}.")
            self._log(msg)
            self.notify.send(f"🔼 {msg}" if sign == 1 else f"🔽 {msg}")

    def _closed_candles(self, sym):
        """Nur abgeschlossene Kerzen (nach Uhrzeit, nicht einfach die letzte weglassen)."""
        tf_sec = TF_SECONDS.get(self.cfg["timeframe"], 3600)
        raw = to_df(self.ex.ohlcv(sym, self.cfg["timeframe"], **self._ohlcv_kw()))
        now_ts = datetime.now(timezone.utc).timestamp()
        closed = raw["ts"].map(lambda t: t.timestamp() + tf_sec <= now_ts)
        return raw[closed].reset_index(drop=True)

    def _btc_trend(self):
        """Bitcoin ueber (+1) oder unter (-1) seinem langen Trend (EMA200, gleiche Kerzen-Laenge).
        Test 2020-2026: nur in Bitcoins Richtung handeln -> etwas mehr R, kleinere schlechte Phasen.
        0 = unbekannt (Kurse nicht abrufbar)."""
        tf_sec = TF_SECONDS.get(self.cfg["timeframe"], 3600)
        now_ts = datetime.now(timezone.utc).timestamp()
        cache = getattr(self, "_btc_cache", None)
        if cache and now_ts < cache["valid_until"]:
            return cache["trend"]
        trend = 0
        try:
            df = self._closed_candles(f"BTC/{self.ex.quote}")
            if len(df) >= 210:
                c = df["close"]
                ema = c.ewm(span=200, adjust=False).mean()
                trend = 1 if float(c.iloc[-1]) > float(ema.iloc[-1]) else -1
                last_close = df["ts"].iloc[-1].timestamp() + tf_sec
                self._btc_cache = {"trend": trend, "valid_until": last_close + tf_sec}
                return trend
        except Exception:
            pass
        self._btc_cache = {"trend": trend, "valid_until": now_ts + 60}
        return trend

    # ---- Neustart mit festem Startkapital ("Topf") -----------------------------
    def _reset_info(self):
        return self.cfg.get("account_reset") or {}

    def reset_pending(self):
        r = self._reset_info()
        return bool(r) and not r.get("at")

    def _maybe_reset(self):
        """Einmaliger Neustart: alle laufenden Trades beenden, dann zaehlt alles ab jetzt mit Startkapital.
        Klappt das Beenden nicht, wird es beim naechsten Durchlauf nochmal versucht (bis dahin keine neuen Trades)."""
        if not self.reset_pending():
            return
        r = self._reset_info()
        with self.lock:
            for sym in list(self.positions):
                try:
                    self._close(sym, self.ex.price(sym), "Neustart")
                except Exception as e:
                    self._log(f"Neustart: {sym} beenden hat nicht geklappt ({e}), versuche es gleich nochmal", "error")
            if self.positions:
                return
            time.sleep(1)   # Gebuehren der Schliess-Orders zaehlen noch zum alten Ergebnis
            r["at"] = now_iso()
            start = float(r.get("start") or 1000)
            if self.cfg["mode"] == "paper":
                self.ex.paper_balance = start
            self.peak_equity, self.kill_reason, self.halted_until = None, None, None
            self.day, self.day_start_equity = None, None
            self.exchange_pnl, self.exchange_pnl_today, self._xpnl_at = None, None, 0
            self.paused = bool(self.preflight and not self.preflight["ok"])
            self._persist_reset()
            self._save()
        msg = (f"Neustart: Ab jetzt handelt der Bot mit {start:.0f} {self.ex.quote}. Alte Trades zählen nicht mehr zum Ergebnis "
               f"(fürs Lernen bleiben sie gespeichert).")
        self._log(msg, "warn")
        self.notify.send("🔄 " + msg)

    def _persist_reset(self):
        try:
            yaml.safe_dump(self.cfg, open(self.config_path, "w", encoding="utf-8"), sort_keys=False, allow_unicode=True)
        except OSError as e:
            self._log(f"Einstellungen speichern hat nicht geklappt ({e})", "warn")

    def _period_trades(self):
        """Trades, die zum aktuellen Ergebnis zaehlen (seit dem Neustart)."""
        mine = self._my_trades()
        at = self._reset_info().get("at")
        return [t for t in mine if t.get("closed_at", "") >= at] if at else mine

    def _realized_since_reset(self):
        """Echtes Ergebnis seit dem Neustart: Zahl der Boerse (inkl. Gebuehren/Funding) und dazu
        Trades, die erst nach der letzten Abfrage beendet wurden. Ohne Boersen-Zahl: Rechnung des Bots."""
        period = self._period_trades()
        xp = getattr(self, "exchange_pnl", None)
        if xp and getattr(self, "_xpnl_iso", None):
            return float(xp["total"]) + sum(t["pnl"] for t in period if t["closed_at"] > self._xpnl_iso)
        return sum(t["pnl"] for t in period)

    def _ohlcv_kw(self):
        # Ausbruch-Strategie braucht den langen Trend (EMA200) -> mehr Kerzen laden
        return {"limit": BREAKOUT_MIN_CANDLES + 60} if self.breakout() else {}

    def _sync_exchange(self):
        """Wurde ein Trade an der Boerse per Stop geschlossen, waehrend der Bot nicht hingeschaut hat?"""
        live = self.ex.open_position_sizes()
        if live is None:
            return
        for sym in list(self.positions):
            if sym not in live:
                p = self.positions[sym]
                # Ausfuehrungspreis laut Boerse; nur wenn der nicht abrufbar ist: der Stop, der dort lag
                real = self._close_fill(sym, p["side"], p["opened_at"])
                fill = real["price"] if real else (p.get("exchange_stop") or p["stop"])
                self._record_close(sym, p, fill, "Stop an der Boerse ausgeloest", verified=bool(real))

    # ---- Live-Kurse alle paar Sekunden ------------------------------------
    def run_prices(self):
        while True:
            try:
                prices = self.ex.prices(self.active_symbols())
                with self.lock:
                    for sym, price in prices.items():
                        if sym in self.market_view:
                            self.market_view[sym]["price"] = price
                        if sym in self.positions:
                            self._manage(sym, price)
                    self.last_price_update = datetime.now(timezone.utc).strftime("%H:%M:%S")
            except Exception as e:
                self._log(f"Kurse holen hat nicht geklappt: {e}", "error")
            time.sleep(self.cfg.get("price_seconds", 3))

    # ---- Einstellungen -----------------------------------------------------
    def _normalize(self, raw):
        s = str(raw).strip().upper().replace(" ", "")
        if not s:
            return None
        if "/" not in s:
            q = self.ex.quote
            s = (s[:-len(q)] if s.endswith(q) and len(s) > len(q) else s) + "/" + q
        return s.split(":")[0]

    def _parse_symbols(self, raw_text):
        syms = []
        for raw in str(raw_text or "").replace(";", ",").replace("\n", ",").split(","):
            s = self._normalize(raw)
            if not s:
                continue
            if self.ex.is_valid(s):
                syms.append(s)
            else:
                self._log(f"{s} gibt es auf {self.ex.label} nicht, übersprungen.", "warn")
        return list(dict.fromkeys(syms))

    def update_settings(self, data: dict):
        with self.lock:
            changed = []
            for key, (typ, lo, hi) in self.EDITABLE.items():
                if key in data and data[key] not in (None, ""):
                    val = max(lo, min(hi, typ(data[key])))
                    if val != self.cfg.get(key):
                        self.cfg[key] = val; changed.append(f"{key}={val}")
            if data.get("timeframe") in TIMEFRAMES and data["timeframe"] != self.cfg["timeframe"]:
                self.cfg["timeframe"] = data["timeframe"]; changed.append(f"timeframe={data['timeframe']}")
            if data.get("pyramid_max_units") not in (None, ""):
                mu = max(1, min(6, int(float(data["pyramid_max_units"]))))
                pyr = dict(self.cfg.get("pyramid") or {"step_atr": 1.0})
                if int(pyr.get("max_units") or 1) != mu:
                    pyr["max_units"] = mu
                    self.cfg["pyramid"] = pyr
                    changed.append(f"Nachkaufen bis {mu} Teile")
            if data.get("strategy_mode") in STRATEGY_MODES and data["strategy_mode"] != self.cfg.get("strategy_mode"):
                self.cfg["strategy_mode"] = data["strategy_mode"]
                changed.append(f"Strategie={data['strategy_mode']}")
            if data.get("follow_pros") in ("aus", "nicht_gegen", "nur_mit") and data["follow_pros"] != self.cfg.get("follow_pros"):
                self.cfg["follow_pros"] = data["follow_pros"]
                changed.append(f"Profis folgen={data['follow_pros']}")
            if isinstance(data.get("scan_majors"), bool) and data["scan_majors"] != self.cfg.get("scan_majors", True):
                self.cfg["scan_majors"] = data["scan_majors"]
                changed.append(f"bekannte Coins beobachten={'ja' if data['scan_majors'] else 'nein'}")
            if isinstance(data.get("learning_auto_apply"), bool) and data["learning_auto_apply"] != L.settings(self.cfg)["auto_apply"]:
                self.cfg.setdefault("learning", {})["auto_apply"] = data["learning_auto_apply"]
                changed.append(f"Lernen automatisch={'an' if data['learning_auto_apply'] else 'aus'}")
            if data.get("symbol_mode") in ("manual", "scanner") and data["symbol_mode"] != self.cfg["symbol_mode"]:
                self.cfg["symbol_mode"] = data["symbol_mode"]; changed.append(f"symbol_mode={data['symbol_mode']}")
                self.last_scan = 0
            if "symbols" in data:
                syms = self._parse_symbols(data["symbols"])
                if syms and syms != self.cfg["symbols"]:
                    self.cfg["symbols"] = syms; changed.append("symbols=" + ", ".join(syms))
            if "scan_always" in data:
                syms = self._parse_symbols(data["scan_always"])
                if syms != (self.cfg.get("scan_always") or []):
                    self.cfg["scan_always"] = syms
                    changed.append("immer beobachten=" + (", ".join(syms) or "nichts"))
            if changed:
                yaml.safe_dump(self.cfg, open(self.config_path, "w", encoding="utf-8"), sort_keys=False, allow_unicode=True)
                self._log("Einstellungen geändert: " + "; ".join(changed))
            return {"ok": True, "changed": changed}

    def _equity(self):
        """Kontowert. Bei Spot (echt/Testnet) sind gekaufte Coins nicht mehr im Quote-Guthaben,
        darum wird ihr aktueller Wert dazugerechnet. Sonst wuerden Tageslimit und Notbremse
        faelschlich ausloesen, sobald der Bot etwas gekauft hat."""
        eq = self.ex.equity()
        if self.ex.is_spot and self.cfg["mode"] != "paper":
            for sym, p in self.positions.items():
                px = (self.market_view.get(sym) or {}).get("price") or p["entry"]
                eq += float(px) * p["amount"]
        r = self._reset_info()
        if self.cfg["mode"] != "paper" and r.get("at"):
            # Echtes Konto hat mehr Geld: der Bot rechnet nur mit seinem Topf (Start + Ergebnis seit Neustart)
            self.real_equity = eq
            pot = float(r["start"]) + self._realized_since_reset()
            if self.cfg["mode"] == "testnet":
                # Demo-Konto: mit dem Topf rechnen, auch wenn das Demo-Guthaben etwas kleiner ist
                # (sonst sieht ein kleineres Demo-Guthaben wie ein Verlust aus und das Tageslimit greift).
                return pot + sum(p.get("unrealized", 0) for p in self.positions.values())
            return min(eq, pot)
        return eq

    def _close_fill(self, sym, side, opened_iso, closed_iso=None):
        fn = getattr(self.ex, "close_fill", None)
        return fn(sym, side, opened_iso, closed_iso) if fn else None

    def _fee_cost(self, price, amount, maker=0.0):
        """Gebuehr in Quote: Limit-Anteil zahlt fee_maker, der Rest fee_rate (Markt/Taker)."""
        fm = float(self.cfg.get("fee_maker", 0.0002))
        ft = float(self.cfg["fee_rate"])
        maker = max(0.0, min(1.0, float(maker or 0.0)))
        return price * amount * (fm * maker + ft * (1 - maker))

    def _safe_pnl(self, p):
        """Was der Trade mindestens bringt ("Save") oder hoechstens kostet ("Stoploss"): Ergebnis an der
        besseren der beiden Ausstiegs-Linien (fester Stop oder mitwandernde Linie).
        Mit Gebuehren. Bei einem Kurssprung ueber die Grenze hinweg kann es etwas schlechter werden."""
        st, amt = p.get("stop"), p.get("amount")
        if not st or not amt:
            return None
        ex = p.get("exit_level")
        if ex:   # die Ausstiegs-Linie (wandert mit) kann schon besser liegen als der feste Stop
            st = max(st, ex) if p["side"] == "long" else min(st, ex)
        return round(self._pnl(p["side"], p["entry"], st, amt), 2)

    def _pnl(self, side, entry, exit_px, amount):
        sign = 1 if side == "long" else -1
        return (exit_px - entry) * sign * amount - (entry + exit_px) * amount * self.cfg["fee_rate"]

    def _reconcile_history(self):
        """Alte Trades, die per Boersen-Stop geschlossen wurden, mit dem ECHTEN Ausstiegspreis
        nachrechnen. Frueher hat der Bot dort den eigenen (evtl. weiter nachgezogenen) Stop angenommen."""
        fixed = []
        with self.lock:
            for t in self._my_trades():
                if t.get("reason") != "Stop an der Boerse ausgeloest" or t.get("verified") is not None:
                    continue
                real = self._close_fill(t["symbol"], t["side"], t["opened_at"], t["closed_at"])
                if not real:
                    t["verified"] = False
                    continue
                old = t["pnl"]
                t["pnl_bot"], t["exit"] = old, real["price"]
                t["pnl"] = round(self._pnl(t["side"], t["entry"], real["price"], t["amount"]), 4)
                t["verified"] = True
                fixed.append(f"{t['symbol']}: {old:+.2f} -> {t['pnl']:+.2f}")
            if fixed:
                self._save()
        if fixed:
            msg = "Abgleich mit der Boerse, korrigiert: " + "; ".join(fixed)
            self._log(msg, "warn")
            self.notify.send(msg)

    def _refresh_exchange_pnl(self):
        fn = getattr(self.ex, "exchange_pnl", None)
        mine = self._period_trades()
        at = self._reset_info().get("at")
        if self.reset_pending() or not fn or not (mine or at) or time.time() - getattr(self, "_xpnl_at", 0) < 300:
            return
        self._xpnl_at = time.time()
        first = at or min(t["opened_at"] for t in mine)
        stamp = now_iso()
        self.exchange_pnl = fn(first)
        self._xpnl_iso = stamp
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
        self.exchange_pnl_today = fn(max(today, first))

    def _pros_check(self, side, ctx):
        """Top-Trader-Filter (offizielle Binance-Statistik, wie die Profis gerade positioniert sind).
        Gibt einen Grund zurueck, wenn der Trade NICHT gemacht werden soll, sonst None."""
        mode = self.cfg.get("follow_pros", "nicht_gegen")
        tl = (ctx or {}).get("top_long")
        if mode == "aus" or tl is None:
            return None
        ours = tl if side == "long" else 1 - tl
        word = "steigt" if side == "long" else "fällt"
        if mode == "nicht_gegen" and ours < 0.45:
            return f"die Profi-Trader wetten gerade dagegen (nur {ours*100:.0f} % von ihnen auf „{word}“)"
        if mode == "nur_mit" and ours < 0.55:
            return f"die Profi-Trader wetten nicht klar mit (nur {ours*100:.0f} % auf „{word}“, gebraucht werden 55 %)"
        return None

    def _sizing_equity(self, equity):
        """Positionsgroesse richtet sich nach dem Kontostand, optional gedeckelt (max_capital_usdt)."""
        cap = self.cfg.get("max_capital_usdt") or 0
        return min(equity, cap) if cap > 0 else equity

    def _may_open(self):
        return (not self.paused and not self.halted_until and not self.reset_pending()
                and len(self.positions) < self.cfg["max_positions"])

    # ---- Oeffnen / Verwalten / Schliessen ---------------------------------
    def _leverage(self, vol_pct, strategy=None):
        """Spot: kein Hebel. Ausbruch (Trades ueber Tage): fester Hebel (Standard 5), damit das Pfand fuer mehrere
        Teile in den Topf passt. Das Risiko pro Trade aendert sich dadurch NICHT (das regelt die Verlust-Grenze),
        und bis zur Zwangsschliessung ist bei 5x rund 20 % Abstand, die Verlust-Grenze liegt viel naeher."""
        if self.ex.is_spot:
            return 1
        if (strategy or ("Ausbruch" if self.breakout() else "")) == "Ausbruch":
            return max(1, min(int(self.cfg["max_leverage"]), int(self.cfg.get("breakout_leverage", 5))))
        return choose_leverage(vol_pct, self.cfg["max_leverage"])

    def _open(self, sym, sig, row, regime, equity):
        vol_pct = float(row["vol_pct"])
        lev = self._leverage(vol_pct, sig.strategy)
        scan_row = next((r for r in self.scanner_rows if r["symbol"] == sym), None)
        ctx = L.entry_context(row, regime, lev, scan_row, self._ratio(sym))
        planned = {"strategy": sig.strategy, "side": sig.side, "leverage": lev, "opened_at": now_iso(), "ctx": ctx}
        if sig.strategy == "Ausbruch":
            # Getestete Regeln 1:1: keine gelernten Filter, kein Profi-Filter (waren nicht im Test)
            allowed, risk_mult, notes = True, 1.0, []
        else:
            allowed, risk_mult, notes = L.entry_check(self.cfg, planned)
        if not allowed:
            self._log(f"{sym}: Gelegenheit ausgelassen – {notes[0]}", "warn")
            return
        pros = None if sig.strategy == "Ausbruch" else self._pros_check(sig.side, ctx)
        if pros:
            self._log(f"{sym}: Gelegenheit ausgelassen – {pros}", "warn")
            return
        risk = self.cfg["risk_per_trade"] * risk_mult
        cap = self.cfg.get("max_open_risk") or 0
        if cap > 0:
            open_risk = sum(float(p.get("risk_usdt") or 0) for p in self.positions.values())
            if open_risk + risk * equity > cap * equity + 1e-9:
                self._log(f"{sym}: Gelegenheit ausgelassen – es laufen schon genug Trades "
                          f"(zusammen {open_risk / equity * 100:.1f} % vom Konto im Risiko, Grenze {cap * 100:.1f} %)", "warn")
                return
        if notes:
            self._log(f"{sym}: " + "; ".join(notes))
        amount = position_size(equity, risk, sig.entry, sig.stop, lev,
                               self.ex.is_spot, self.cfg["spot_max_allocation"])
        if amount <= 0:
            return
        if not self._spot_cash_ok(amount * sig.entry, equity):
            self._log(f"{sym}: Gelegenheit ausgelassen – nicht genug freies Geld im Topf (Spot: alles steckt schon in Coins)", "warn")
            return
        try:
            fill, amount = self.ex.open(sym, sig.side, amount, lev)
        except Exception as e:
            msg = str(e)
            if "-4411" in msg or "agreement" in msg.lower() or "TradFi" in msg:
                # Aktien-/TradFi-Perp: nicht handelbar ohne Zusatzvertrag -> Coin dauerhaft ausschliessen
                self.ex.blocked.add(sym)
                self.auto_symbols = [s for s in self.auto_symbols if s != sym]
                self._log(f"{sym} ist eine Aktie, kein Krypto-Coin (braucht bei Binance einen Extra-Vertrag). Wird ab jetzt ausgelassen.", "warn")
                self._save()
            else:
                self._log(f"{self.ex.label} hat den Trade {sym} abgelehnt: {e}", "error")
            return
        # Stop relativ zum echten Einstieg neu setzen (gleicher Abstand wie geplant)
        sign = 1 if sig.side == "long" else -1
        dist = abs(sig.entry - sig.stop)
        stop = fill - sign * dist
        tp = None if sig.take_profit is None else fill + sign * abs(sig.take_profit - sig.entry)
        p = {"symbol": sym, "side": sig.side, "entry": fill, "amount": amount, "stop": stop,
             "take_profit": tp, "initial_stop": stop, "r": dist, "leverage": lev,
             "strategy": sig.strategy, "reason": sig.reason, "breakeven": False,
             "opened_at": now_iso(), "unrealized": 0.0, "exchange_stop": None,
             "ctx": ctx, "risk_usdt": round(dist * amount, 4), "risk_mult": risk_mult,
             "fee_in": self._fee_cost(fill, amount, getattr(self.ex, "last_maker", 0.0))}
        pyr = self.cfg.get("pyramid") or {}
        if sig.strategy == "Ausbruch" and int(pyr.get("max_units") or 1) > 1:
            n = dist / float({**BREAKOUT, **(self.cfg.get("breakout_params") or {})}["stop_atr"])   # Schwankungsbreite (ATR)
            p.update({"units": [{"price": fill, "amount": amount}], "unit_amount": amount, "atr": n,
                      "max_units": int(pyr["max_units"]), "next_add": fill + sign * float(pyr.get("step_atr", 1.0)) * n})
        self.positions[sym] = p
        self._log(f"Neuer Trade {sym}: Wette, dass der Kurs {'steigt' if sig.side == 'long' else 'fällt'}. Eingestiegen bei {fill:.4g}, Verlust-Grenze {stop:.4g}, {f"Gewinn-Ziel {tp:.4g}" if tp else "kein festes Gewinn-Ziel (läuft, solange der Trend hält)"}, Hebel {lev}x. Warum: {plain_reason(sig.reason)}", "trade")
        self.notify.send(f"{'🟢' if sig.side == 'long' else '🔴'} {sig.side.upper()} {sym} @ {fill:.6g}, Hebel {lev}x\n"
                         f"Stop {stop:.6g} | Ziel {f'{tp:.6g}' if tp else 'offen (Trend)'} | Risiko {dist * amount:.2f} {self.ex.quote}\n{sig.reason}")
        self._set_exchange_stop(sym, p)

    def _set_exchange_stop(self, sym, p):
        if not self.ex.supports_exchange_stop():
            return
        try:
            self.ex.cancel_stop(sym)
            self.ex.place_stop(sym, p["side"], p["amount"], p["stop"])
            p["exchange_stop"] = p["stop"]
        except Exception as e:
            p["exchange_stop"] = None
            self._log(f"{sym}: Verlust-Grenze konnte nicht bei der Börse hinterlegt werden ({e}). Der Bot passt selbst darauf auf.", "warn")

    def _manage(self, sym, price):
        p = self.positions[sym]
        sign = 1 if p["side"] == "long" else -1
        move = (price - p["entry"]) * sign
        p["unrealized"] = move * p["amount"]
        r_now = move / p["r"] if p["r"] else 0
        stop_moved = False
        trend_mode = p.get("strategy") == "Ausbruch"   # wie im Test: fester Stop, Ausstieg ueber den Kanal (in _tick)
        if trend_mode and p.get("units") and len(p["units"]) < p.get("max_units", 1):
            hit_add = price >= p["next_add"] if sign == 1 else price <= p["next_add"]
            if hit_add and self._add_unit(sym, p, price):
                stop_moved = True
                move = (price - p["entry"]) * sign
                p["unrealized"] = move * p["amount"]

        if not trend_mode and not p["breakeven"] and r_now >= self.cfg["breakeven_at_r"]:
            p["stop"] = p["entry"] + sign * p["entry"] * self.cfg["fee_rate"] * 2
            p["breakeven"] = stop_moved = True
            self._log(f"{sym}: Trade abgesichert. Die Verlust-Grenze liegt jetzt beim Einstiegspreis, ins Minus kann er nicht mehr.")

        if not trend_mode and r_now >= self.cfg["trail_at_r"]:
            ns = price - sign * p["r"]
            if (sign == 1 and ns > p["stop"]) or (sign == -1 and ns < p["stop"]):
                p["stop"] = ns
                # Boersen-Stop nur nachziehen, wenn er sich spuerbar bewegt hat (schont das Rate-Limit)
                if p.get("exchange_stop") is None or abs(ns - p["exchange_stop"]) >= 0.25 * p["r"]:
                    stop_moved = True

        if stop_moved:
            self._set_exchange_stop(sym, p)

        hit_stop = price <= p["stop"] if sign == 1 else price >= p["stop"]
        tp = p.get("take_profit")
        hit_tp = tp is not None and (price >= tp if sign == 1 else price <= tp)
        if hit_stop:
            self._close(sym, price, "Break-even/Trailing-Stop erreicht" if p["breakeven"] else "Stop erreicht")
        elif hit_tp:
            self._close(sym, price, "Ziel erreicht")

    def _spot_cash_ok(self, extra_notional, equity):
        """Spot: Man kann nur mit Geld kaufen, das noch nicht in Coins steckt."""
        if not self.ex.is_spot:
            return True
        used = sum(float(q["entry"]) * float(q["amount"]) for q in self.positions.values())
        return used + extra_notional <= equity * 0.98

    def _add_unit(self, sym, p, price):
        """Nachkaufen: Trend laeuft weiter -> noch ein Teil. Danach wird die Verlust-Grenze so nachgezogen,
        dass der ganze Trade hoechstens so viel verliert wie anfangs geplant (risk_usdt)."""
        sign = 1 if p["side"] == "long" else -1
        step = float((self.cfg.get("pyramid") or {}).get("step_atr", 1.0)) * p["atr"]
        p["next_add"] = price + sign * step          # naechste Stufe (auch wenn dieser Kauf scheitert)
        if self.paused or self.halted_until or self.reset_pending():
            return False
        equity = getattr(self, "_last_equity", None) or self._equity()
        if not self._spot_cash_ok(p["unit_amount"] * price, equity):
            self._log(f"{sym}: Nachkaufen ausgelassen – kein freies Geld mehr im Topf", "warn")
            return False
        try:
            fill, amt = self.ex.open(sym, p["side"], p["unit_amount"], p["leverage"])
        except Exception as e:
            self._log(f"{sym}: Nachkaufen hat nicht geklappt ({e})", "warn")
            return False
        p["fee_in"] = float(p.get("fee_in", p["entry"] * p["amount"] * self.cfg["fee_rate"])) + \
            self._fee_cost(fill, amt, getattr(self.ex, "last_maker", 0.0))
        p["units"].append({"price": fill, "amount": amt})
        total = sum(u["amount"] for u in p["units"])
        avg = sum(u["price"] * u["amount"] for u in p["units"]) / total
        p["entry"], p["amount"] = avg, total
        p["next_add"] = fill + sign * step
        ns = avg - sign * float(p["risk_usdt"]) / total      # Gesamtverlust bleibt beim geplanten Risiko
        if (sign == 1 and ns > p["stop"]) or (sign == -1 and ns < p["stop"]):
            p["stop"] = ns
        k = len(p["units"])
        self._log(f"{sym}: Trend läuft weiter, nachgekauft (Teil {k} von {p['max_units']}) bei {fill:.4g}. "
                  f"Neue Verlust-Grenze {p['stop']:.4g}, der ganze Trade verliert höchstens {p['risk_usdt']:.2f} {self.ex.quote}.", "trade")
        self.notify.send(f"➕ {sym} nachgekauft (Teil {k}/{p['max_units']}) @ {fill:.6g}\n"
                         f"Stop jetzt {p['stop']:.6g} | max. Verlust {p['risk_usdt']:.2f} {self.ex.quote}")
        return True

    def _close(self, sym, price, reason):
        p = self.positions[sym]
        self.ex.last_maker = 0.0
        try:
            # Ruhiger Ausstieg (Trend vorbei, zur Kerzen-Schlusszeit) -> Limit; Stops & Notbremse immer Markt
            kw = {"limit": True} if reason == "Trend vorbei" and getattr(self.ex, "use_limit", lambda: False)() else {}
            fill = self.ex.close(sym, p["side"], p["amount"], **kw)
        except Exception as e:
            live = self.ex.open_position_sizes()
            if live is not None and sym not in live:   # Boersen-Stop war schneller
                fill, reason = p["stop"], "Stop an der Boerse ausgeloest"
            else:
                self._log(f"{sym}: Beenden hat nicht geklappt ({e}). Der Bot versucht es gleich nochmal.", "error")
                return
        self._record_close(sym, p, fill, reason, maker_out=getattr(self.ex, "last_maker", 0.0))

    def _record_close(self, sym, p, fill, reason, verified=None, maker_out=0.0):
        self.positions.pop(sym, None)
        sign = 1 if p["side"] == "long" else -1
        fee_in = float(p.get("fee_in", p["entry"] * p["amount"] * self.cfg["fee_rate"]))
        pnl = (fill - p["entry"]) * sign * p["amount"] - fee_in - self._fee_cost(fill, p["amount"], maker_out)
        self.ex.book_paper_pnl(pnl)
        self.trades.append({
            "symbol": sym, "side": p["side"], "entry": p["entry"], "exit": fill, "amount": p["amount"],
            "leverage": p["leverage"], "strategy": p["strategy"], "pnl": round(pnl, 4), "reason": reason,
            "opened_at": p["opened_at"], "closed_at": now_iso(), "mode": self.cfg["mode"], "exchange": self.ex.name,
            "ctx": p.get("ctx"), "risk_usdt": p.get("risk_usdt"),
            **({"verified": verified} if verified is not None else {}),
            "r_mult": round(pnl / p["risk_usdt"], 3) if p.get("risk_usdt") else None,
        })
        self._learn_dirty = True
        self._log(f"Trade beendet {sym}: {pnl:+.2f} {self.ex.quote} ({plain_close(reason)}), ausgestiegen bei {fill:.4g}", "trade")
        self.notify.send(f"{'✅' if pnl > 0 else '❌'} {sym} geschlossen: {pnl:+.2f} {self.ex.quote} ({reason})")

    def _ratio(self, sym):
        fn = getattr(self.ex, "top_trader_ratio", None)
        return fn(sym) if fn else None

    # ---- Lernen (nur Vorschlaege, Uebernahme per Klick im Dashboard) --------
    def _my_trades(self):
        return [t for t in self.trades if self._same_account(t)]

    def _same_account(self, d):
        """Gleicher Modus UND gleiche Boerse (alte Eintraege ohne Boerse = Binance)."""
        return (d.get("mode", "paper") == self.cfg["mode"]
                and d.get("exchange", "binance") == getattr(self.ex, "name", "binance"))

    def _equity_hint(self):
        return (self.connection or {}).get("equity") or self.ex.paper_balance

    def learn_analyze(self):
        with self.lock:
            sugg = L.analyze(self._my_trades(), self.cfg, self._equity_hint())
            before = {x["id"] for x in self.learn.suggestions}
            self.learn.set_suggestions(sugg)
            self.learn.last_analyze = L.iso(L.now_utc())
            new = [x for x in self.learn.suggestions if x["id"] not in before]
            for x in new:
                self._log(f"Gelernt, Vorschlag: {x['title']}")
            self._auto_apply()
            self.learn.save()
            self._learn_dirty = False

    def _learning_cycle(self):
        if self.breakout():
            return   # feste, ueber Jahre getestete Regeln: kein Umbauen nach wenigen Trades
        if self._learn_dirty:
            self.learn_analyze()
        if self.learn.tune_due(self.cfg):
            self.learn_tune_start()

    def learn_tune_start(self):
        if self.learn.tuning:
            return False
        self.learn.tuning = True
        threading.Thread(target=self._tune_worker, daemon=True).start()
        return True

    def _tune_worker(self):
        try:
            with self.lock:
                recent = [t["symbol"] for t in reversed(self._my_trades())]
                syms = list(dict.fromkeys(recent + list(self.active_symbols()) + list(self.cfg.get("symbols", []))))
                cfg = json.loads(json.dumps(self.cfg))   # Kopie, damit der Bot weiterlaufen kann
            self._log("Selbsttest gestartet: Der Bot probiert im Hintergrund andere Einstellungen auf alten Kursen aus (dauert ein paar Minuten).")
            res = L.tune(self.ex.history, cfg, self.ex.is_spot, syms, progress=self._log)
            with self.lock:
                self.learn.tune_result = res
                self.learn.last_tune = L.iso(L.now_utc())
                self.learn.suggestions = [x for x in self.learn.suggestions if x["kind"] != "params"]
                applied = False
                if res.get("suggestion") and res["suggestion"]["id"] not in self.learn.dismissed:
                    res["suggestion"]["created_at"] = L.iso(L.now_utc())
                    self.learn.suggestions.append(res["suggestion"])
                    self._auto_apply()
                    applied = not any(x["id"] == res["suggestion"]["id"] for x in self.learn.suggestions)
                self.learn.note("Parameter-Test: " + res["summary"])
                self._update_ampel(res, applied)
                self.learn.save()
            self._log("Lernen: " + res["summary"])
        except Exception as e:
            self._log(f"Selbsttest hat nicht geklappt ({e})", "error")
            with self.lock:
                self.learn.last_tune = L.iso(L.now_utc())   # nicht in Endlosschleife neu starten
                self.learn.save()
        finally:
            self.learn.tuning = False

    def _update_ampel(self, res, params_applied):
        """Risiko automatisch an die Marktphase anpassen (nur wenn 'Lernen automatisch' an ist)."""
        new = L.ampel_from(res, params_applied)
        lr = L.learned(self.cfg)
        old = (lr.get("ampel") or {}).get("status")
        if not L.settings(self.cfg)["auto_apply"]:
            lr["ampel"] = {**new, "mult": 1.0, "why": new["why"] + " (nur Anzeige, Automatik aus)"}
        else:
            lr["ampel"] = new
        self._persist_cfg()
        if new["status"] != old:
            icon = {"gruen": "🟢", "gelb": "🟡", "rot": "🔴", "grau": "⚪"}[new["status"]]
            text = f"Strategie-Ampel {icon} {new['status']}: {new['why']}. Risiko pro Trade x{lr['ampel']['mult']}."
            self.learn.note(text)
            self._log("Lernen: " + text, "warn" if new["status"] == "rot" else "info")
            self.notify.send(f"{icon} {text}", important=new["status"] == "rot")

    def _persist_cfg(self):
        yaml.safe_dump(self.cfg, open(self.config_path, "w", encoding="utf-8"), sort_keys=False, allow_unicode=True)

    def _apply_locked(self, sid, auto=False):
        sug = self.learn.pop(sid)
        if not sug:
            return None
        text = L.apply_suggestion(self.cfg, sug)
        if auto:
            text = text.replace("Uebernommen:", "Automatisch uebernommen:")
        self._persist_cfg()
        self.learn.note(text)
        self.learn.save()
        self._learn_dirty = auto is False
        self._log("Lernen: " + text, "trade")
        self.notify.send(f"🧠 {text}\nBegruendung: {sug.get('why', '')} {sug.get('evidence', '')}".strip())
        return text

    def _auto_apply(self):
        """Nur wenn im Dashboard 'Lernen automatisch' an ist. Aufruf mit gehaltenem Lock."""
        if not L.settings(self.cfg)["auto_apply"]:
            return
        for sug in list(self.learn.suggestions):
            self._apply_locked(sug["id"], auto=True)

    def learn_apply(self, sid):
        with self.lock:
            text = self._apply_locked(sid)
        return {"ok": bool(text)} if text else {"ok": False, "error": "Vorschlag nicht mehr vorhanden."}

    def telegram_test(self):
        if not self.notify.enabled:
            return {"ok": False, "error": "Telegram ist nicht eingerichtet (telegram_einrichten.py ausfuehren)."}
        self.notify.send("✅ Test vom Trading-Bot. Meldungen kommen an.")
        return {"ok": True}

    def learn_dismiss(self, sid):
        with self.lock:
            ok = self.learn.dismiss(sid)
            self.learn.save()
        return {"ok": ok}

    def learn_undo(self, rule_id):
        with self.lock:
            text = L.remove_rule(self.cfg, rule_id)
            if text:
                self._persist_cfg()
                self.learn.note(text)
                self.learn.save()
                self._learn_dirty = True
        if text:
            self._log("Lernen: " + text)
        return {"ok": bool(text)}

    def learn_reset(self):
        with self.lock:
            text = L.reset_all(self.cfg)
            self._persist_cfg()
            self.learn.note(text)
            self.learn.save()
            self._learn_dirty = True
        self._log("Lernen: " + text, "warn")
        return {"ok": True}

    def _learning_snapshot(self, mine, equity):
        s = L.settings(self.cfg)
        stats = L.feature_stats(mine, self.cfg, equity)
        nxt = None
        if self.learn.last_tune:
            nxt = L.iso(L.datetime.fromisoformat(self.learn.last_tune) + L.timedelta(hours=s["tune_every_hours"]))
        return {
            "suggestions": self.learn.suggestions, "active": L.active_items(self.cfg), "auto_apply": s["auto_apply"],
            "ampel": L.learned(self.cfg).get("ampel"),
            "stats": stats, "min_trades": s["min_trades"], "trades": len(mine),
            "with_context": sum(1 for t in mine if t.get("ctx")),
            "last_analyze": self.learn.last_analyze, "last_tune": self.learn.last_tune, "next_tune": nxt,
            "tuning": self.learn.tuning, "tune_result": self.learn.tune_result,
            "history": list(reversed(self.learn.history[-15:])),
        }
