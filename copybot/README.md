# Copy-Trading Bot (Spielgeld)

Kopiert die offenen Positionen der 100 besten Trader auf Hyperliquid – mit 5.000 € Spielgeld.
Es wird **nichts** an eine Börse geschickt und kein Konto oder API-Schlüssel gebraucht.

## Starten
- **Windows:** Doppelklick auf `start.bat` (beim ersten Mal wird alles installiert, dauert ca. 1 Minute).
- **Mac/Linux:** `./start.sh`
- Dann im Browser: **http://localhost:8010**
- Vom Handy (gleiches WLAN oder Tailscale): `http://<IP-des-PCs>:8010`

Beim ersten Start sucht der Bot 1–2 Minuten nach den Top-Profis, danach handelt er.
Alles wird in `data/copybot.sqlite3` gespeichert – nach einem Neustart macht er genau dort weiter.

## Wie er arbeitet
1. **Auswahl:** ca. 47.000 Konten der öffentlichen Bestenliste werden gefiltert (Gewinn im Monat und insgesamt,
   keine Market-Maker, keine Einzahlungs-Effekte, mind. 25.000 $ im Konto), live geprüft, die besten 100 kopiert.
   Alle 6 Stunden neu.
2. **Kopieren:** Alle 45 Sekunden werden alle Positionen der 100 Profis gelesen. Pro Coin entsteht eine gewichtete
   Mehrheit. Ab 75 % Mehrheit und mind. 3 Profis steigt der Bot ein, höchstens 12 Coins gleichzeitig, Hebel 3,
   max. 60 % des Topfs als Margin, Stop-Loss 6 %.
3. **Lernen:**
   - *Vertrauen:* Der Bot rechnet jede Runde nach, was das Kopieren jedes einzelnen Profis gebracht hätte
     (inkl. Gebühren). Gute zählen mehr, schlechte weniger, sehr schlechte werden aussortiert.
   - *Schattenkonten:* 6 Testkonten mit anderen Regeln laufen mit. Ist eines nach mind. 20 Trades und 48 h
     klar besser, übernimmt der Bot dessen Regeln. Alle 3 Tage wird das schwächste durch eine Abwandlung
     des besten ersetzt. „Ohne Lernen" dient als Vergleich.
   - *Coin-Sperre:* Coins mit wiederholten Verlusten werden 3 Tage ausgelassen.
4. **Sicherheit:** Fällt der Topf 30 % unter seinen Höchststand, schließt der Bot alles (automatischer Not-Aus).

## Dateien
| Datei | Inhalt |
|---|---|
| `config.py` | alle Grundwerte (Startkapital, Filter, Lernraten) |
| `engine.py` | Hauptschleife, Auswahl, Auswertung |
| `strategy.py` | Mehrheit der Profis → Ziel-Positionen |
| `learning.py` | Vertrauen, Schattenkonten, Coin-Sperre |
| `portfolio.py` | Spielgeld-Buchhaltung (Gebühren, Funding, Stop-Loss) |
| `hyperliquid.py` | Lesezugriff auf Hyperliquid |
| `app.py` + `static/` | Webseite und API |

## Hinweis
Die Website hat kein Passwort. Sie ist für dein Heimnetz/Tailscale gedacht – Port 8010 nicht ins Internet freigeben.
Wenn du nur lokal willst: in `config.py` `HOST = "127.0.0.1"` setzen.

Vergangene Gewinne der Profis sind keine Garantie. Lass den Test mehrere Wochen laufen, bevor du über echtes Geld nachdenkst.

## Im Trading-Projekt (ab 19.10.2026)
Der Copy-Bot ist Teil des tradebot-Ordners und läuft als Bot Nr. 11 auf Port 8010:
- Start: `start_copybot.bat` (oder `alle_starten.bat` für alle Bots), Autostart über `autostart_einrichten.bat`.
- Zugriff wie bei den anderen Bots: am PC frei, vom Handy nur über Tailscale mit dem Dashboard-Passwort.
- Telegram-Meldungen bei Einstieg, Ausstieg und Not-Aus (gleiche Einstellungen wie die anderen Bots).
- Erscheint in der Gesamt-Übersicht (`:8000/alle`) und reagiert auf „Alle neu laden / pausieren / weiter / beenden“.
- Daten: `copybot/data/` (eigene Datenbank). Protokoll zusätzlich in `copybot/data/bot.log`.
