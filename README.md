# Trading Bots (Krypto, Binance / Bybit EU / Kraken)

> **Wichtig:** Das ist ein privates Bastelprojekt, keine Anlageberatung. Krypto-Trading ist riskant.
> Starte immer im Simulationsmodus und setze nur Geld ein, dessen Verlust du verkraftest.

## Schnellstart fuer neue Nutzer

1. **Python 3.11+** installieren (python.org), beim Installieren den Haken bei *Add Python to PATH* setzen.
2. Repo herunterladen (gruener Knopf *Code -> Download ZIP*) und entpacken.
3. Die Datei **`.env.example` kopieren** und die Kopie **`.env`** nennen.
   *(Unter Windows ggf. "Dateinamenerweiterungen" im Explorer einblenden, sonst heisst sie `.env.txt`.)*
4. `start.bat` doppelklicken. Beim ersten Start wird alles Noetige installiert.
5. Im Browser `http://localhost:8000` oeffnen.

Alle Bots starten im Modus **`mode: paper`** (Simulation mit echten Kursen und Spielgeld).
**Dafuer brauchst du keine Schluessel und kein Boersenkonto.** Die `.env` darf leer bleiben.
Weitere Bots: `alle_starten.bat` startet alle, jeder hat seine eigene `config_….yaml` und seinen eigenen Port.

## Schluessel besorgen (nur wenn du mehr willst)

Alle Schluessel kommen **nur** in die Datei `.env`, nirgendwo sonst.
Die `.env` steht in `.gitignore` und wird **nie** hochgeladen. Gib sie niemandem.

| Wofuer | Eintraege in `.env` | Woher |
|---|---|---|
| Binance Demo-Konto (Spielgeld an der echten Boerse) | `BINANCE_API_KEY`, `BINANCE_API_SECRET` | siehe unten |
| Telegram-Meldungen aufs Handy | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | siehe unten |
| Dashboard vom Handy aus | `DASHBOARD_PASSWORD` | selbst ausdenken |
| Echtgeld Bybit EU (Spot) | `BYBIT_API_KEY`, `BYBIT_API_SECRET` | bybit.eu -> API |
| Echtgeld Kraken Futures | `KRAKEN_FUTURES_API_KEY`, `KRAKEN_FUTURES_API_SECRET` | futures.kraken.com -> API |
| Echtgeld-Freigabe | `LIVE_CONFIRM=JA` | nur setzen, wenn du es wirklich willst |

### Binance Demo-Konto (empfohlen als naechster Schritt)
1. Normales Binance-Konto anlegen, dann https://demo.binance.com oeffnen und anmelden.
2. Profil -> **API-Verwaltung** -> **API erstellen (systemgeneriert)**.
3. Key und Secret in die `.env` kopieren (`BINANCE_API_KEY=…`, `BINANCE_API_SECRET=…`).
4. In `config.yaml` die erste Zeile auf `mode: testnet` aendern und den Bot neu starten.
   Oben im Dashboard muss dann "Verbunden" stehen.

### Telegram (optional)
1. In Telegram **@BotFather** oeffnen, `/newbot` senden, Namen vergeben -> du bekommst einen **Token**.
2. Im Bot-Ordner ausfuehren: `.venv\Scripts\python telegram_einrichten.py`
3. Token einfuegen und deinem neuen Telegram-Bot irgendeine Nachricht schicken.
   Das Skript findet deine Chat-ID selbst und traegt beides in die `.env` ein.

### Dashboard vom Handy (optional)
1. **Tailscale** auf PC und Handy installieren und mit demselben Konto anmelden (tailscale.com).
2. In der `.env` ein eigenes, langes Passwort setzen: `DASHBOARD_PASSWORD=…`
3. `fernzugriff_einrichten.bat` einmal **als Administrator** ausfuehren (Firewall nur fuer Tailscale).
4. Am Handy: `http://<Tailscale-Adresse des PCs>:8000` (Adresse steht in der Tailscale-App).
   Die Seite ist **nie** ohne Tailscale und Passwort erreichbar. Ports nicht im Router freigeben.

### Echtgeld (erst nach Wochen im Demo-Modus)
- API-Key bei der Boerse anlegen mit Rechten **Lesen + Handel**. **Auszahlungen NIEMALS erlauben.**
- **IP-Beschraenkung** auf die IP deines Anschlusses setzen.
- In der passenden `config_….yaml` `mode: live` setzen, in der `.env` `LIVE_CONFIRM=JA`.
- Klein anfangen. Details weiter unten unter "Mit Binance verbinden".

### Falls ein Schluessel doch mal irgendwo gelandet ist
Sofort bei der Boerse **loeschen** und neu erstellen (Telegram: bei @BotFather `/revoke`).
Aus einem Git-Verlauf bekommt man ihn nicht zuverlaessig wieder heraus.

---

## Die Bots im Detail

## Was er kann

- **Markt-Scanner:** Alle 15 Minuten durchsucht er alle USDT-Coins auf Binance,
  filtert nach Umsatz und sucht die mit der staerksten, saubersten Bewegung.
  Die besten 5 handelt er. (Oder feste Liste, umschaltbar im Dashboard.)
- **Strategie pro Coin:** Trend -> Trendfolge. Seitwaerts -> Scalping.
  Chaos oder unklar -> Finger weg.
- **Hebel-Management:** ruhige Coins mehr Hebel, wilde weniger, nie ueber dem Maximum.
  Jeder Trade riskiert maximal 1 % des Kontos.
- **Break-even und Trailing:** ab 1R Gewinn wandert der Stop auf den Einstieg, ab 2R laeuft er nach.
- **Stops direkt an der Boerse** (Futures, Testnet/Live): greifen auch, wenn dein PC aus ist.
- **Tageslimit:** bei -3 % am Tag keine neuen Trades bis Mitternacht.
- **Backtest im Dashboard:** testet die Regeln auf bis zu 180 Tagen echter Kursdaten.
- **Live-Dashboard:** Kurse alle 3 Sekunden, alle Entscheidungen, Not-Aus.

## Lernen (Vorschlaege aus Erfahrung)

Im Dashboard gibt es den Bereich **Lernen**. Der Bot wertet dort aus, was bei ihm funktioniert,
und macht daraus Vorschlaege. **Er aendert nichts selbst**: Erst ein Klick auf *Uebernehmen*
macht eine Regel aktiv. Jede Regel laesst sich einzeln zuruecknehmen, oder alles mit
*Alles zuruecksetzen*.

- **Aus eigenen Trades:** Zu jedem neuen Trade merkt er sich die Marktlage beim Einstieg
  (Trend/Seitwaerts, ADX, Volatilitaet, Long/Short, Tageszeit, Hebel). Verliert ein Merkmal
  dauerhaft, schlaegt er vor, es zu sperren oder das Risiko zu halbieren. Laeuft es klar besser,
  schlaegt er Risiko x1.25 vor. Erst ab **20 Trades pro Merkmal**, vorher ist es Zufall.
- **Top-Trader & Masse:** Binance veroeffentlicht offiziell, wie viel Prozent der Top-Trader
  (Top 20 % nach Kontogroesse) und aller Trader gerade Long sind. Der Bot zeigt das pro Coin an
  und merkt sich bei jedem Einstieg, ob er *mit* oder *gegen* die Top-Trader bzw. die Masse handelt.
  Ab 20 Trades sieht man, ob "den Top-Tradern folgen" bei ihm wirklich etwas bringt, und bekommt
  ggf. einen Vorschlag (z. B. "gegen Top-Trader sperren"). Kein blindes Kopieren.
- **Coin-Gedaechtnis:** 2 Verluste in Folge auf einem Coin innerhalb von 7 Tagen
  -> Vorschlag, den Coin 3 Tage zu meiden.
- **Parameter-Test:** Einmal pro Woche (oder per Knopf) testet er rund 40 Varianten von
  Stop, Ziel, ADX-Schwelle, Break-even und Trailing auf den letzten 30 Tagen der zuletzt gehandelten
  Coins. Vorgeschlagen wird nur, was im Trainingszeitraum **und** im ungesehenen Testzeitraum
  besser ist. Dauert ein paar Minuten, der Bot laeuft nebenher weiter.

Uebernommene Regeln stehen in `config.yaml` unter `learned` und `strategy_params`,
der Verlauf in `data/learning.json`. Schwellen anpassen (optional) in `config.yaml`:

```yaml
learning:
  min_trades: 20          # Trades pro Merkmal, bevor eine Regel vorgeschlagen wird
  coin_cooldown_hours: 72
  tune_every_hours: 168
```

## Boerse wechseln: Bybit EU

Bybit EU (Bybit EU GmbH, Wien) hat eine EU-Lizenz nach MiCA und ist fuer Deutschland zugelassen.
Unterschiede zu Binance, die der Bot automatisch beruecksichtigt:

- **Nur Spot:** kein Futures, kein Hebel, keine Short-Trades. Der Bot kauft nur und verkauft spaeter.
- **USDC statt USDT:** gehandelt wird z. B. `XRP/USDC` (oder EUR, siehe unten).
- **Weniger Coins und weniger Umsatz** als bei Binance: Mindestumsatz im Scanner niedriger setzen.
- **Stops verwaltet der Bot selbst:** Der PC muss laufen (Autostart einrichten).

**Umstellen** in `config.yaml` (Bot vorher schliessen):

```yaml
exchange: bybiteu
market: spot
quote: USDC              # oder EUR
mode: paper              # erst Simulation mit echten Bybit-Kursen, kein Key noetig
scan_min_volume_m: 1     # Bybit EU hat weniger Umsatz pro Coin
fee_rate: 0.001          # Spot-Gebuehr 0,1 % (bei Bybit EU pruefen)
```

Fuer Echtgeld: bei bybit.eu einen API-Key anlegen (Lesen + Spot-Handel, **keine Auszahlung**,
IP-Beschraenkung), in `.env` als `BYBIT_API_KEY` und `BYBIT_API_SECRET` eintragen, dann
`mode: live` und `LIVE_CONFIRM=JA`. Die Startpruefung kontrolliert die Key-Rechte.
`mode: testnet` versucht das Bybit-EU-Testnet; ob es das fuer EU-Konten gibt, zeigt der Start.

Trades, Positionen und Notbremse werden pro Boerse und Modus getrennt gespeichert.

**Beide Bots gleichzeitig:** `config_bybit.yaml` ist ein fertiger zweiter Bot (Bybit EU,
Simulation, Port 8001, Daten in `data_bybit`). Starten mit `start_bybit.bat`,
Dashboard unter http://localhost:8001. Der Binance-Bot laeuft unveraendert ueber
`start.bat` auf http://localhost:8000. Telegram-Meldungen tragen den Namen des Bots
(`[Binance]` bzw. `[Bybit EU]`). `autostart_einrichten.bat` richtet alle ein.

**Dritter Bot (Daytrading, 5-Minuten-Kerzen):** `config_5min.yaml`, Start mit `start_5min.bat`,
Dashboard http://localhost:8002. Binance Futures als Simulation mit 5000 USDT Spielgeld,
sonst gleiche Einstellungen wie der Haupt-Bot, damit man 5m und 15m fair vergleichen kann.
Zurueck zu Binance: `exchange: binance`, `market: futures`, `quote: USDT`.

## Vollautomatisch mit Echtgeld (PC bleibt an)

Der Bot richtet die Positionsgroesse nach deinem Kontostand: jeder Trade riskiert
`risk_per_trade` (Standard 1 %) des aktuellen Guthabens. Mehr Geld = groessere Trades, nach
Verlusten automatisch kleinere.

**Einmal einrichten:**

1. **Telegram:** `.venv\Scripts\python telegram_einrichten.py` ausfuehren und den Anweisungen folgen.
   Du bekommst dann jeden Trade, eine Tagesbilanz, jede automatisch gelernte Regel und sofort
   eine 🚨-Meldung bei Notbremse, Startsperre oder Dauerfehlern. Test: im Dashboard oben auf "Telegram an" klicken.
2. **Autostart:** `autostart_einrichten.bat` doppelklicken. Der Bot startet dann nach jedem
   Windows-Login und der PC schlaeft am Netzteil nicht mehr ein. Stuerzt der Bot ab, startet
   `start.bat` ihn nach 30 Sekunden neu. Hinweis: Nach einem Windows-Update-Neustart muss sich
   jemand anmelden, sonst startet er nicht.
3. **Echtgeld freischalten** (erst wenn das Testnet ueberzeugt):
   - Binance-API-Key fuer das echte Konto: Lesen + Handel, **Auszahlungen aus**, IP-Beschraenkung an.
   - In `.env`: `BINANCE_API_KEY`, `BINANCE_API_SECRET` (echte Keys) und `LIVE_CONFIRM=JA`.
   - In `config.yaml`: `mode: live`.
   - Bot neu starten.

**Startpruefung:** Im Echtgeld-Modus prueft der Bot beim Start, ob Binance deinem Konto den
Handel erlaubt (Spot bzw. Futures) und ob der API-Key sicher eingestellt ist. Wenn nicht,
handelt er nicht, zeigt den Grund oben im Dashboard und meldet sich per Telegram.
Hintergrund: Binance hat seit Juli 2026 keine MiCA-Lizenz in der EU und viele Dienste fuer
EU-Nutzer eingeschraenkt. Ob dein Konto (vor allem Futures) handeln darf, zeigt erst diese Pruefung.
Geht nur Spot: in `config.yaml` `market: spot` setzen (dann nur Long, ohne Hebel).

**Schutzschalter (Einstellungen im Dashboard):**

- *Tageslimit:* bei -3 % am Tag keine neuen Trades bis Mitternacht (UTC).
- *Gesamt-Notbremse:* faellt das Konto 20 % unter seinen Hoechststand, schliesst der Bot alle
  Positionen und stoppt. Das bleibt auch nach einem Neustart so, bis du im Dashboard auf
  *Weiter* klickst.
- *Max. Kapital fuer den Bot:* z. B. 500 = der Bot rechnet so, als waeren nur 500 USDT da,
  auch wenn mehr auf dem Konto liegt. 0 = ganzes Konto.
- *Gelerntes automatisch uebernehmen:* Standard an. Jede automatische Aenderung steht im
  Verlauf, kommt per Telegram und laesst sich zuruecknehmen.

**Steuern:** Gewinne aus Krypto-Trades, die kuerzer als ein Jahr gehalten werden, sind in
Deutschland steuerpflichtig. Die Trades stehen in `data/state.json` und in der Binance-Historie.
Kein Steuer- oder Finanzrat, im Zweifel Steuerberater fragen.

## Start (Windows)

1. Python 3.11+ installieren (python.org), Haken bei "Add Python to PATH".
2. `start.bat` doppelklicken.
3. Browser: `http://localhost:8000`

Er startet im Simulationsmodus. Kein Binance-Konto noetig.

## Mit Binance verbinden

### Schritt 1: Testnet (Spielgeld, unbedingt zuerst)

1. https://demo.binance.com oeffnen und mit deinem normalen Binance-Konto anmelden.
2. Profil -> API-Verwaltung -> API erstellen (systemgeneriert), Key und Secret kopieren.
3. `.env.example` kopieren, die Kopie `.env` nennen, Key und Secret eintragen.
4. In `config.yaml`: `mode: testnet`
5. Bot neu starten. Im Dashboard oben muss "Verbunden" stehen.

Mindestens 2-4 Wochen so laufen lassen.

### Schritt 2: Echtgeld (erst wenn Testnet ueberzeugt)

1. Binance -> Profil -> API-Verwaltung -> API erstellen (systemgeneriert).
2. Rechte: **Lesen** und **Futures** (bzw. Spot & Margin bei Spot) aktivieren.
   **Auszahlungen NIEMALS aktivieren.**
3. **IP-Beschraenkung** auf die IP deines PCs setzen (zeigt z.B. wieistmeineip.de).
4. Bei Futures: USDT vom Spot-Wallet ins USDS-M-Futures-Wallet uebertragen.
5. Key und Secret in `.env` eintragen (die Testnet-Keys ersetzen).
6. In `config.yaml`: `mode: live`
7. Bot starten, im Fenster mit `JA` bestaetigen.

Klein anfangen. Die Schluessel bleiben nur auf deinem PC, gib `.env` niemandem.


- **Futures in Deutschland:** Binance bietet Derivate fuer Privatkunden in Deutschland
  nicht bzw. nur eingeschraenkt an. Wenn du beim API-Key kein Futures-Recht setzen kannst
  oder Orders abgelehnt werden: `market: spot` setzen. Dann ohne Hebel und nur Long.
  Im Spot-Modus verwaltet der Bot die Stops selbst, der PC muss also laufen.
- **Kein Bot gewinnt immer.** Verlusttrades gibt es, sie sind aber klein und geplant.
  Ein guter Backtest heisst nicht, dass die Zukunft gleich laeuft.
