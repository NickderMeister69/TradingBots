"""Weboberflaeche: http://localhost:8000

Fernzugriff (z. B. vom iPhone): nur ueber Tailscale (private Adressen 100.64.0.0/10) und nur mit
Passwort (DASHBOARD_PASSWORD in .env, einrichten mit fernzugriff_einrichten.bat).
Alle anderen Geraete (auch im eigenen WLAN) werden abgewiesen.
"""
import asyncio
import base64
import ipaddress
import math
import secrets
import json
import os
import threading
import time
import urllib.request

from .board import EngineBoard, board_html, lwc_js
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

TAILSCALE_NETS = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48")]
MAX_FAILS, FAIL_WINDOW, LOCK_SECONDS = 10, 600, 900
OVERVIEW_PORTS = list(range(8000, 8020)) + [8021]   # 8020 = Bot-Waechter (kein Bot)   # alle Bots auf diesem PC (Gesamt-Uebersicht /alle)
BOT_NAMES = {8000: "Hauptbot", 8001: "Bybit EU", 8002: "Kraken Tag", 8003: "Scanner-Test", 8004: "Daytrade riskant",
             8005: "Daytrade sicher", 8006: "Daytrade mittel", 8007: "4h ohne Nachkaufen", 8008: "Riskant 4h", 8009: "15 Minuten", 8010: "Copy-Bot",
             8011: "Copy sicher", 8012: "Copy vorsichtig", 8013: "Copy mutig", 8014: "Copy riskant",
             8015: "Copy konzentriert", 8016: "Copy Zufall", 8017: "SMA-Kreuz (Video)", 8018: "2 % Risiko", 8019: "Gold Tag", 8021: "4h + Trendlinien"}


def client_kind(host: str) -> str:
    """'local' = dieser PC, 'tailscale' = eigenes Geraet ueber Tailscale, sonst 'other'."""
    try:
        ip = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return "local" if host in ("testclient", "localhost") else "other"
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "local"
    return "tailscale" if any(ip in n for n in TAILSCALE_NETS) else "other"


def password_ok(header: str, password: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        _, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    return secrets.compare_digest(pw.encode(), password.encode())

HTML = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Bot</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--bg:#F3F4F2;--panel:#FFFFFF;--ink:#1B2730;--muted:#6B7780;--line:#DCE0DD;--up:#1F9E89;--down:#D65A3C;--warn:#E3A33D;--pause:#8A94A0;color-scheme:light}
@media(prefers-color-scheme:dark){:root{--bg:#101A22;--panel:#182530;--ink:#EAF0F2;--muted:#8FA0AB;--line:#263643;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif}
.num{font-family:"IBM Plex Mono",ui-monospace,monospace}
header{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:16px 20px;border-bottom:1px solid var(--line)}
header h1{font-size:17px;font-weight:600;margin:0 auto 0 0}
.pill{padding:4px 10px;border-radius:999px;font-size:13px;border:1px solid var(--line)}
.pill.live{background:var(--down);color:#fff;border-color:transparent}
.pill.paper{background:var(--up);color:#fff;border-color:transparent}
.pill.testnet{background:var(--warn);color:#1B2730;border-color:transparent}
.pill.bad{border-color:var(--down);color:var(--down)}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--up);outline-offset:2px}
button.danger{background:var(--down);color:#fff;border-color:transparent}
button.primary{background:var(--up);color:#fff;border-color:transparent}
button:disabled{opacity:.5;cursor:wait}
main{padding:20px;max-width:1240px;margin:0 auto}
.hero{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:16px;margin-bottom:20px}
.hero>div{padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.hero .big{font-size:34px;font-weight:500;line-height:1.1}
.hero small{display:block;color:var(--muted);margin-bottom:6px}
.pos{color:var(--up)}.neg{color:var(--down)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;overflow-x:auto}
section.full{grid-column:1/-1}
section h2{font-size:14px;font-weight:600;margin:0 0 10px;color:var(--muted);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
section h2 .sub{font-weight:400}
section h2 button{margin-left:auto;padding:4px 10px;font-size:13px}
section h2 button+button{margin-left:0}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:500;padding:6px 8px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:0}
tr.picked td:first-child{box-shadow:inset 3px 0 0 var(--up)}
.bar{height:6px;border-radius:3px;background:var(--up);min-width:2px}
.regime{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
.r-trend_up{background:var(--up)}.r-trend_down{background:var(--down)}.r-range{background:var(--warn)}.r-chaos,.r-neutral{background:var(--pause)}
.log{font-family:"IBM Plex Mono",monospace;font-size:13px;max-height:360px;overflow:auto}
.log div{padding:3px 0;border-bottom:1px dashed var(--line)}
.log .trade{color:var(--up)}.log .warn{color:var(--warn)}.log .error{color:var(--down)}
.log span{color:var(--muted);margin-right:8px}
.empty{color:var(--muted);padding:14px 0;white-space:normal}
.banner{padding:12px 16px;border-radius:10px;margin-bottom:16px;display:none}
.banner.warn{background:var(--warn);color:#1B2730}.banner.bad{background:var(--down);color:#fff}
.form{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px 16px;align-items:end}
.form label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px}
.form input,.form select,.form textarea{width:100%;font:inherit;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
.form .wide{grid-column:1/-1}
.form textarea{font-family:"IBM Plex Mono",monospace;min-height:48px;resize:vertical}
.note{color:var(--muted);font-size:13px}
.saved{color:var(--up);font-size:13px;margin-left:10px}
.live{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--up);animation:blink 3s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
@media(prefers-reduced-motion:reduce){.live{animation:none}}
.bt-stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin:14px 0}
.bt-stats div{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.bt-stats small{display:block;color:var(--muted);font-size:12px}
.bt-stats b{font-family:"IBM Plex Mono",monospace;font-size:20px;font-weight:500}
.verdict{padding:12px 14px;border-radius:10px;border:1px solid var(--line);margin-top:10px}
svg.curve{width:100%;height:180px;display:block}
.lr-head{display:flex;flex-wrap:wrap;gap:8px 20px;color:var(--muted);font-size:13px;margin-bottom:12px}
.sugg{border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:10px;padding:12px 14px;margin-bottom:10px;display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
.sugg .t{font-weight:600}.sugg .w{margin:2px 0}.sugg .e{color:var(--muted);font-size:13px}
.sugg .b{margin-left:auto;display:flex;gap:8px}
.sugg>div:first-child{flex:1 1 320px;min-width:0;white-space:normal}
.prog{height:6px;border-radius:3px;background:var(--line);overflow:hidden;min-width:80px}
.prog i{display:block;height:100%;background:var(--muted)}
.prog.ok i{background:var(--up)}
h3{font-size:13px;font-weight:600;color:var(--muted);margin:18px 0 8px}
.help-box{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-bottom:16px}
.help-box summary{cursor:pointer;font-weight:600}
.help-box dl{display:grid;grid-template-columns:minmax(160px,auto) 1fr;gap:6px 16px;margin:12px 0 4px}
.help-box dt{font-weight:600}.help-box dd{margin:0;color:var(--muted)}
@media(max-width:700px){.help-box dl{grid-template-columns:1fr}.help-box dd{margin-bottom:6px}}
.sub2{color:var(--muted);font-size:13px;margin-top:4px;line-height:1.4}
.muted{color:var(--muted)}
small.help{display:block;color:var(--muted);font-size:12px;margin-top:4px;line-height:1.35}
.ampel{color:var(--ink)}


@media(max-width:800px){.hero{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}.hero .big{font-size:30px}}
@media(max-width:520px){.hero{grid-template-columns:1fr}section{overflow-x:auto}.hero .big{font-size:26px}}
</style></head><body>
<header>
 <h1>Trading Bot</h1>

 <span id="mode" class="pill"></span>
 <span id="conn" class="pill"></span>
 <span id="status" class="pill"></span>
 <span id="tg" class="pill" title="Klicken = Test-Nachricht aufs Handy"></span>
 <button id="pauseBtn">Pause</button>
 <button class="danger" onclick="if(confirm('Alle laufenden Trades sofort beenden und den Bot anhalten?'))act('close_all')">Not-Aus</button>
</header>
<main>
 <div id="banner" class="banner"></div>
 <details class="help-box">
  <summary>❓ So liest du diese Seite (Begriffe einfach erklärt)</summary>
  <dl>
   <dt>Trade</dt><dd>Eine Wette auf einen Coin: Der Bot steigt ein und später wieder aus. Dazwischen ist der Trade „laufend“.</dd>
   <dt>Wette auf steigt ↑ / fällt ↓</dt><dd>„Steigt“ (Fachwort: Long) heißt: Gewinn, wenn der Kurs hochgeht. „Fällt“ (Short) heißt: Gewinn, wenn der Kurs runtergeht.</dd>
   <dt>Verlust-Grenze (Stop)</dt><dd>Der Preis, bei dem der Bot einen schlechten Trade automatisch beendet. So bleibt der Verlust klein und geplant.</dd>
   <dt>Gewinn-Ziel</dt><dd>Der Preis, bei dem der Bot den Gewinn mitnimmt.</dd>
   <dt>Einsatz-Risiko</dt><dd>So viel verliert ein Trade höchstens, wenn er die Verlust-Grenze erreicht. Standard: 1 % vom Konto.</dd>
   <dt>Abgesichert</dt><dd>Der Trade ist so weit im Plus, dass der Bot die Verlust-Grenze auf den Einstiegspreis geschoben hat. Er kann dann nicht mehr ins Minus.</dd>
   <dt>Hebel (z. B. 3x)</dt><dd>Mit Hebel bewegt sich dein Ergebnis stärker als der Kurs. Der Bot rechnet das ein, damit pro Trade trotzdem nur das Einsatz-Risiko auf dem Spiel steht.</dd>
   <dt>Gebühren</dt><dd>Geld, das die Börse bei jedem Kauf und Verkauf behält.</dd>
   <dt>Finanzierungskosten</dt><dd>Bei Binance-Futures zahlt man alle 8 Stunden kleine Beträge, solange ein Trade läuft (Fachwort: Funding). Manchmal bekommt man auch etwas.</dd>
   <dt>„Von Binance abgerechnet“</dt><dd>Die echte Zahl direkt von der Börse, mit allen Gebühren. Die zählt. Der Bot rechnet zusätzlich selbst mit, das kann leicht abweichen.</dd>
   <dt>Spielgeld / Simulation</dt><dd>Testkonto bzw. nur gerechnet. Kein echtes Geld im Spiel.</dd>
  </dl>
 </details>
 <div class="hero">
  <div><small id="eq_label">Geld auf dem Konto</small><div class="big num" id="equity">–</div><div id="unreal" class="num"></div></div>
  <div><small>Heute</small><div class="big num" id="today">–</div><div class="sub2" id="todayx">seit 2 Uhr nachts</div></div>
  <div><small>Gewinn / Verlust insgesamt</small><div class="big num" id="total">–</div><div id="totalx" class="sub2"></div></div>
  <div><small>Trades mit Gewinn</small><div class="big num" id="winrate">–</div><div id="count" class="sub2"></div></div>
 </div>
 <div class="grid">
  <section class="full"><h2>Coin-Suche <span class="sub" id="scaninfo"></span><button onclick="act('scan')">Jetzt suchen</button></h2>
   <p class="note">Der Bot sucht alle paar Minuten die Coins, die sich gerade am stärksten und saubersten bewegen. Die markierten beobachtet er und handelt sie, wenn sich eine gute Gelegenheit ergibt.</p>
   <table id="scanner"></table></section>
  <section><h2><span class="live"></span>Was der Bot gerade pro Coin denkt <span class="sub" id="ptime"></span></h2><table id="markets"></table></section>
  <section><h2>Laufende Trades</h2><table id="positions"></table></section>
  <section><h2>Abgeschlossene Trades</h2><table id="trades"></table></section>
  <section><h2>Was der Bot gerade tut</h2><div class="log" id="log"></div></section>
  <section><h2>Alle Bots auf diesem PC <a href="/alle" class="sub" style="margin-left:8px">Übersicht aller Bots →</a></h2>
   <p class="note">Gilt für alle Bots gleichzeitig.</p>
   <div style="display:flex;flex-wrap:wrap;gap:8px">
    <button class="allbtn" onclick="allAct('reload')">Alle neu laden</button><button class="allbtn" onclick="allAct('pause')">Alle pausieren</button>
    <button class="allbtn" onclick="allAct('weiter')">Alle weiter</button><button class="allbtn danger" onclick="allAct('stop')">Alle beenden</button>
   </div><p class="note" id="allmsg"></p>
   <p class="note"><a id="wlink" href="#">Bot-Wächter öffnen</a> – startet beendete Bots wieder.</p></section>

  <section class="full"><h2>Lernen: was der Bot aus seinen Trades lernt
    <button onclick="learn('analyze')">Jetzt auswerten</button><button id="tuneBtn" onclick="learn('tune')">Selbsttest starten</button></h2>
   <div class="lr-head" id="lr_head"></div>
   <p class="note" id="lr_mode"></p>
   <div id="lr_sugg"></div>
   <h3>Was der Bot gelernt hat und gerade anwendet</h3><div id="lr_active"></div>
   <h3>Was bisher gut und was schlecht lief</h3>
   <p class="note">Erst ab 20 Trades pro Zeile zieht der Bot daraus Schlüsse. Vorher ist es zu viel Zufall.</p>
   <table id="lr_stats"></table>
   <h3>Täglicher Selbsttest</h3><div id="lr_tune" class="note"></div>
   <h3>Verlauf</h3><div class="log" id="lr_hist" style="max-height:180px"></div>
  </section>

  <section class="full"><h2>Test mit alten Kursen: hätte sich das gelohnt?</h2>
   <div class="form">
    <div><label>Coin</label><input id="b_sym" value="BTC"></div>
    <div><label>Wie viele Tage zurück</label><input id="b_days" type="number" min="3" max="730" value="30"><small class="help">Bei der Ausbruch-Strategie bis 730 Tage, sonst bis 180. Ausbruch braucht mindestens etwa 60 Tage (4h-Kerzen).</small></div>
    <div><label>Kerzen-Länge</label><select id="b_tf"><option value="5m">5 Minuten</option><option value="15m" selected>15 Minuten</option><option value="1h">1 Stunde</option><option value="4h">4 Stunden</option><option value="1d">1 Tag</option></select></div>
    <div><button class="primary" id="b_btn" onclick="runBacktest()">Test starten</button></div>
   </div>
   <p class="note">Der Bot spielt seine Regeln auf den Kursen der Vergangenheit durch, mit Gebühren. Er startet dabei mit 1000. So siehst du, ob es sich früher gelohnt hätte. Eine Garantie für die Zukunft ist das nicht.</p>
   <div id="bt"></div>
  </section>

  <section class="full"><h2>Einstellungen</h2>
   <div class="form">
    <div class="wide"><label>Strategie</label><select id="s_strat"><option value="ausbruch">Ausbruch-Trendfolge (über Jahre getestet, empfohlen mit 4-Stunden-Kerzen)</option><option value="standard">Standard (alte Regeln: Rücksetzer im Trend + Zurückpendeln)</option></select><small class="help">Ausbruch: Der Bot steigt ein, wenn ein Coin den höchsten (oder tiefsten) Kurs seit 55 Kerzen erreicht, und bleibt drin, bis der Trend dreht. Kein festes Gewinn-Ziel, viele kleine Verluste, ab und zu große Gewinne. Bei dieser Strategie sind Lernen und Profi-Filter aus, damit sie genau wie im Test handelt.</small></div>
    <div><label>Coins auswählen</label><select id="s_mode"><option value="scanner">Automatisch (Coin-Suche)</option><option value="manual">Feste Liste</option></select><small class="help">Automatisch: der Bot sucht selbst. Feste Liste: nur deine Coins von unten.</small></div>
    <div><label>Wie viele Coins beobachten</label><input type="number" min="1" max="15" id="s_top"><small class="help">So viele Coins nimmt die Coin-Suche höchstens.</small></div>
    <div><label>Mindest-Handelsvolumen pro Tag (Mio.)</label><input type="number" min="0.1" step="0.1" id="s_minvol"><small class="help">Coins, die weniger gehandelt werden, ignoriert er. Wenig Handel = schlechte Preise.</small></div>
    <div><label>Coin-Suche alle … Minuten</label><input type="number" min="5" max="240" id="s_scanmin"></div>
    <div class="wide"><label>Den Profi-Tradern folgen</label><select id="s_pros"><option value="aus">Aus: Profis egal</option><option value="nicht_gegen">Nie gegen die Profis (empfohlen)</option><option value="nur_mit">Nur wenn die Profis klar mitwetten</option></select><small class="help">Binance zeigt offiziell, worauf die erfolgreichsten Trader (Top 20 %) gerade setzen. „Nie gegen die Profis“: Der Bot lässt Trades aus, wenn die Mehrheit der Profis in die andere Richtung wettet. „Nur mit den Profis“: strenger, nur wenn mindestens 55 % von ihnen dieselbe Wette haben. Weniger Trades, dafür im Einklang mit den Profis. Ob das hilft, zeigt dir unten „Lernen“ nach ein paar Wochen.</small></div>
    <div><label>Bekannte Coins immer beobachten</label><select id="s_majors"><option value="1">Ja</option><option value="0">Nein</option></select><small class="help">BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX, LINK, LTC, DOT, TRX, SUI, TON (soweit es sie an der Börse gibt).</small></div>
    <div><label>Dazu die meistgehandelten Coins des Tages</label><input type="number" min="0" max="15" id="s_topvol"><small class="help">Anzahl. Die Coins mit dem meisten Handel heute. 0 = aus.</small></div>
    <div class="wide"><label>Immer beobachten (zusätzlich zur Coin-Suche, mit Komma trennen, z. B. BTC, ETH)</label><textarea id="s_always"></textarea><small class="help">Diese Coins hat der Bot immer im Blick, auch wenn die Coin-Suche sie nicht auswählt. Leer lassen = nur die Coin-Suche.</small></div>
    <div class="wide"><label>Feste Liste (nur bei „Feste Liste“, mit Komma trennen, z. B. BTC, ETH)</label><textarea id="s_symbols"></textarea><small class="help">Wird nur benutzt, wenn oben „Coins auswählen“ auf „Feste Liste“ steht.</small></div>
    <div><label>Einsatz-Risiko pro Trade (% vom Konto)</label><input type="number" step="0.05" min="0.1" max="5" id="s_risk"><small class="help">So viel verliert ein Trade höchstens. 1 % bei 5000 = 50. Empfehlung: Standard 0,5 bis 1, Ausbruch 0,25 (dort laufen oft viele Trades gleichzeitig).</small></div>
    <div><label>Nachkaufen: höchstens … Teile (1 = aus)</label><input type="number" min="1" max="6" step="1" id="s_units"><small class="help">Läuft der Trend weiter, kauft der Bot nach (nur bei Ausbruch). Der ganze Trade verliert trotzdem höchstens das Einsatz-Risiko. Test: 5 Teile mit 0,33 % war am ausgewogensten.</small></div>
    <div><label>Höchstens so viel Risiko gleichzeitig (% vom Konto, 0 = aus)</label><input type="number" step="0.5" min="0" max="50" id="s_openrisk"><small class="help">Summe aller laufenden Trades: so viel ginge verloren, wenn alle gleichzeitig ihre Verlust-Grenze treffen. Empfehlung bei Ausbruch: 2,5.</small></div>
    <div><label>Hebel höchstens</label><input type="number" min="1" max="20" id="s_max_leverage"><small class="help">Der Bot wählt den Hebel pro Coin selbst, aber nie mehr als das. Nur bei Futures.</small></div>
    <div><label>Höchstens gleichzeitig laufende Trades</label><input type="number" min="1" max="20" id="s_max_positions"><small class="help">Mal Einsatz-Risiko = was im schlimmsten Fall gleichzeitig verloren gehen kann.</small></div>
    <div><label>Tages-Verlustgrenze (%)</label><input type="number" step="0.5" min="0.5" max="50" id="s_daily"><small class="help">Ist das Konto heute so viel im Minus, macht er bis 2 Uhr nachts keine neuen Trades.</small></div>
    <div><label>Absichern ab (× Einsatz)</label><input type="number" step="0.1" min="0.2" max="5" id="s_be"><small class="help">1 = sobald ein Trade so viel im Plus ist, wie er riskiert, kann er nicht mehr ins Minus.</small></div>
    <div><label>Gewinn mitlaufen lassen ab (× Einsatz)</label><input type="number" step="0.1" min="0.5" max="10" id="s_trail"><small class="help">Ab hier zieht er die Verlust-Grenze hinter dem Kurs her und sichert so immer mehr Gewinn.</small></div>
    <div><label>Kerzen-Länge</label><select id="s_tf"><option value="1m">1 Minute</option><option value="5m">5 Minuten</option><option value="15m">15 Minuten</option><option value="1h">1 Stunde</option><option value="4h">4 Stunden</option><option value="1d">1 Tag</option></select><small class="help">Kürzer = schnellere Trades, aber mehr Gebühren und mehr Zufall.</small></div>
    <div><label>Nachdenken alle … Sekunden</label><input type="number" min="10" max="600" id="s_loop"></div>
    <div><label>Kurse holen alle … Sekunden</label><input type="number" min="1" max="60" id="s_price"></div>
    <div><label>Nur Spot: höchstens % vom Konto pro Coin</label><input type="number" min="5" max="100" id="s_spot"><small class="help">Mal „laufende Trades“ sollte höchstens 100 ergeben.</small></div>
    <div><label>Notbremse ab (% unter dem besten Kontostand)</label><input type="number" step="1" min="5" max="90" id="s_killdd"><small class="help">Dann beendet er alle Trades und stoppt, bis du auf „Weiter“ klickst.</small></div>
    <div><label>Höchstens so viel Geld nutzen (0 = alles)</label><input type="number" step="50" min="0" id="s_cap"><small class="help">Z. B. 500: Der Bot tut so, als wären nur 500 auf dem Konto.</small></div>
    <div><label>Gelerntes automatisch übernehmen</label><select id="s_autolearn"><option value="1">Ja</option><option value="0">Nein, nur vorschlagen</option></select></div>
    <div class="wide"><button class="primary" onclick="saveSettings()">Einstellungen speichern</button><span class="saved" id="saved"></span></div>
    <div class="wide"><label>Geld einzahlen (Test: Einzahlung nachspielen)</label><input type="number" min="1" step="50" id="dep_amt" placeholder="z. B. 300" style="max-width:160px"> <button onclick="deposit()">Einzahlung buchen</button><span class="saved" id="dep_msg"></span><small class="help">Im Test: Der Topf des Bots wird um den Betrag größer, ab dem nächsten Trade rechnet er mit mehr Geld. Zählt nicht als Gewinn. Mit echtem Geld später einfach bei der Börse einzahlen, der Bot nimmt dann automatisch den neuen Kontostand.</small></div>
   </div>
  </section>
 </div>
</main>
<script>
const $=id=>document.getElementById(id);
const f=(n,d=2)=>(n>=0?'+':'')+Number(n).toFixed(d).replace('.',',');
const n2=(n,d=2)=>Number(n).toFixed(d).replace('.',',');
const c=n=>n>0?'pos':n<0?'neg':'';
const RG={trend_up:'Steigt ↑',trend_down:'Fällt ↓',range:'Seitwärts ↔',chaos:'Zu wild',neutral:'Unklar'};
const SIDE=s=>s==='long'?'steigt ↑':'fällt ↓';
const fmtVol=v=>v>=1e9?n2(v/1e9,1)+' Mrd':(v>=1e6?n2(v/1e6,1)+' Mio':n2(v/1e3,0)+' Tsd');
const adxWord=a=>a>=25?'stark':a>=20?'mittel':'schwach';
const REASON={'Stop erreicht':'Verlust-Grenze erreicht','Ziel erreicht':'Gewinn-Ziel erreicht',
 'Break-even/Trailing-Stop erreicht':'Gewinn gesichert und beendet','Stop an der Boerse ausgeloest':'Verlust-Grenze bei der Börse ausgelöst',
 'Manuell geschlossen':'Von dir beendet','Gesamt-Notbremse':'Notbremse'};
function doing(s,x){s=String(s||'');
 if(s==='Ausbruch'&&x&&x.bo_up!=null){
  const px=+x.price, pct=v=>Math.abs((v-px)/px*100).toFixed(1).replace('.',',');
  if(x.trend_long==='steigt')return`Langer Trend steigt. Kauft, wenn der Kurs über ${(+x.bo_up).toPrecision(5)} geht (noch ${pct(x.bo_up)} % höher).`;
  if(x.bo_down!=null)return`Langer Trend fällt. Wettet auf fallend, wenn der Kurs unter ${(+x.bo_down).toPrecision(5)} geht (noch ${pct(x.bo_down)} % tiefer).`;
  return'Langer Trend fällt. Spot kann nicht auf fallende Kurse setzen, darum wartet der Bot, bis der Trend wieder steigt.';}
 if(s.startsWith('Trendfolge'))return'Wartet auf einen Einstieg mit dem Trend';
 if(s.startsWith('Scalping'))return'Wartet auf einen Ausschlag, um aufs Zurückpendeln zu setzen';
 if(s.includes('gemieden'))return'Hält sich raus: hier zuletzt oft verloren';
 if(s.includes('volatil'))return'Hält sich raus: Kurs zu wild';
 if(s.includes('klares'))return'Hält sich raus: keine klare Richtung';
 return s;}
const DIM={setup:'Art der Wette',regime:'Marktlage beim Einstieg',adx:'Trendstärke beim Einstieg',vol:'Wie stark der Coin schwankte',
 session:'Uhrzeit (UTC, deutsche Zeit minus 2 Std.)',hebel:'Hebel',toptrader:'Profi-Trader bei Binance',crowd:'Alle anderen Trader'};
function plain(t){return String(t??'')
 .replace(/Trendfolge Long/g,'mit dem Trend, Wette auf steigt').replace(/Trendfolge Short/g,'mit dem Trend, Wette auf fällt')
 .replace(/Scalping Long/g,'Zurückpendeln, Wette auf steigt').replace(/Scalping Short/g,'Zurückpendeln, Wette auf fällt')
 .replace(/Strategie & Richtung/g,'Art der Wette').replace(/Trendstaerke \(ADX\)/g,'Trendstärke').replace(/Volatilitaet pro Kerze/g,'Schwankung')
 .replace(/mit Top-Tradern/g,'gleiche Richtung wie die Profis').replace(/gegen Top-Trader/g,'gegen die Profis').replace(/Top-Trader gemischt/g,'Profis uneinig')
 .replace(/Top-Trader \(Binance\)/g,'Profi-Trader').replace(/Alle Trader \(Masse\)/g,'Alle anderen Trader')
 .replace(/([+-]?\d+(?:\.\d+)?) R\b/g,(m,x)=>x.replace('.',',')+' × Einsatz').replace(/Ø /g,'im Schnitt ')
 .replace(/\b(Uebernommen|uebernommen|Zurueckgenommen|zurueckgesetzt|fuer|Hoechststand|laeuft|waere|Boerse|ueber|Spaet|Gebuehr\w*|naechst\w*|moeglich|koennen|aendert|geaendert|gruen|ausgeloest|Schliessen|schliessen|Rueckgang|Trendstaerke|Auspraegung|uebersprungen|Pruefung|pruefen|Startpruefung|Verluste in Folge|halbieren)\b/g,w=>({Uebernommen:'Übernommen',uebernommen:'übernommen',Zurueckgenommen:'Zurückgenommen',zurueckgesetzt:'zurückgesetzt',fuer:'für',Hoechststand:'besten Kontostand',laeuft:'läuft',waere:'wäre',Boerse:'Börse',ueber:'über',Spaet:'Spät',naechsten:'nächsten',naechster:'nächster',moeglich:'möglich',koennen:'können',aendert:'ändert',geaendert:'geändert',gruen:'grün',ausgeloest:'ausgelöst',Schliessen:'Beenden',schliessen:'beenden',Rueckgang:'Absturz',Trendstaerke:'Trendstärke',Auspraegung:'Fall',uebersprungen:'übersprungen',Pruefung:'Prüfung',pruefen:'prüfen',Startpruefung:'Prüfung beim Start','Verluste in Folge':'Verluste hintereinander',halbieren:'halbieren'})[w]||w.replace('ue','ü').replace('ae','ä').replace('oe','ö'));}
async function act(a,sym){await fetch('/api/'+a+(sym?'/'+encodeURIComponent(sym):''),{method:'POST'});load();}
let formFilled=false, formDirty=false, lastCfg='';
document.addEventListener('input',e=>{if(e.target.closest&&e.target.closest('.form'))formDirty=true;});

const esc=t=>String(t??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const dt=t=>t?new Date(t).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'–';
async function learn(a,id){
 if(a==='reset'&&!confirm('Alles Gelernte vergessen und mit den Grundeinstellungen weitermachen?'))return;
 await fetch('/api/learn/'+a+(id?'/'+encodeURIComponent(id):''),{method:'POST'});load();
}
const AMPEL={gruen:'🟢 Strategie passt gerade gut zum Markt. Der Bot setzt das volle Einsatz-Risiko.',
 gelb:'🟡 Läuft gerade nur mittel. Der Bot riskiert etwas weniger (×0,75).',
 rot:'🔴 Passt gerade nicht zum Markt. Der Bot riskiert nur die Hälfte.',
 grau:'⚪ Noch zu wenig Daten für eine Aussage.'};
function renderLearning(L,cfg){
 if(!L)return;
 if(cfg&&cfg.strategy_mode==='ausbruch'){$('lr_head').innerHTML='<span>Bei der Ausbruch-Strategie ist das Lernen aus: Sie handelt mit festen Regeln, die über fast 7 Jahre getestet wurden. Nach ein paar Trades umzubauen würde nur dem Zufall hinterherlaufen.</span>';$('lr_mode').innerHTML='';$('tuneBtn').disabled=true;return;}
 $('lr_head').innerHTML=`${L.ampel?`<span class="ampel"><b>${AMPEL[L.ampel.status]}</b></span>`:''}
  <span>${L.trades} Trades ausgewertet</span>
  <span>Selbsttest: ${L.tuning?'<b>läuft gerade …</b>':'zuletzt '+dt(L.last_tune)+', nächster '+dt(L.next_tune)}</span>`;
 $('tuneBtn').disabled=L.tuning;
 $('lr_mode').innerHTML=L.auto_apply?'Der Bot übernimmt Gelerntes <b>automatisch</b> und schreibt dir per Telegram, was er geändert hat. Alles lässt sich unten rückgängig machen. Abschalten unter Einstellungen.'
  :'Der Bot ändert nichts selbst. Vorschläge werden erst aktiv, wenn du auf <b>Übernehmen</b> klickst.';
 $('lr_sugg').innerHTML=L.suggestions.length?L.suggestions.map(x=>`<div class="sugg"><div><div class="t">${esc(plain(x.title))}</div><div class="w">${esc(plain(x.why))}</div><div class="e">${esc(plain(x.evidence))}</div></div>
   <div class="b"><button class="primary" onclick="learn('apply','${x.id}')">Übernehmen</button><button onclick="learn('dismiss','${x.id}')">Verwerfen</button></div></div>`).join('')
  :'';
 $('lr_active').innerHTML=L.active.length?'<table>'+L.active.map(a=>`<tr><td style="white-space:normal">${esc(plain(a.text))}</td><td style="width:1%"><button onclick="learn('undo','${esc(a.id)}')">Rückgängig</button></td></tr>`).join('')+
   '</table><p><button onclick="learn(\'reset\')">Alles vergessen</button></p>':'<p class="empty">Noch nichts. Der Bot handelt mit den Grundeinstellungen.</p>';
 let last='';
 $('lr_stats').innerHTML=L.stats.length?'<tr><th>Bereich</th><th>Fall</th><th>Trades</th><th>davon Gewinner</th><th>im Schnitt pro Trade</th><th>zusammen</th><th style="width:14%">genug Daten?</th></tr>'+
  L.stats.map(b=>{const lab0=DIM[b.dim]||b.dim_label;const lab=lab0!==last?lab0:'';last=lab0;const pct=Math.min(100,b.n/L.min_trades*100);
   return `<tr><td style="white-space:normal">${esc(lab)}</td><td>${esc(plain(b.value))}</td><td class=num>${b.n}</td><td class=num>${b.wins} (${b.win_rate.toFixed(0)} %)</td><td class="num ${c(b.pnl)}">${f(b.pnl/b.n)}</td><td class="num ${c(b.pnl)}">${f(b.pnl)}</td>
   <td title="${b.n} von ${L.min_trades} Trades"><div class="prog ${b.ready?'ok':''}"><i style="width:${pct}%"></i></div></td></tr>`}).join('')
  :'<tr><td class=empty>Noch keine abgeschlossenen Trades.</td></tr>';
 const T=L.tune_result;
 let tune='Noch nicht gelaufen. Startet automatisch einmal am Tag oder über den Knopf oben.';
 if(T&&T.current_test){const r=T.current_test.total_r;
  tune=`Der Bot hat seine Regeln mit verschiedenen Einstellungen auf den letzten 30 Tagen durchgespielt. Ergebnis mit den aktuellen Einstellungen in den letzten Tagen: <b class="${c(r)}">${r>0?'im Plus':'im Minus'}</b> (${f(r,1)} × Einsatz bei ${T.current_test.trades} Trades). `+
   (T.suggestion?'Er hat bessere Einstellungen gefunden.':'Keine andere Einstellung war klar besser, deshalb bleibt alles, wie es ist.')+` Getestete Coins: ${esc((T.coins||[]).join(', '))}.`;}
 else if(T) tune=esc(plain(T.summary));
 $('lr_tune').innerHTML=tune;
 $('lr_hist').innerHTML=L.history.length?L.history.map(h=>`<div><span>${dt(h.t)}</span>${esc(plain(h.text))}</div>`).join(''):'<div class="empty">Noch nichts.</div>';
}
async function load(){
 let s;try{s=await (await fetch('/api/state')).json()}catch(e){$('status').textContent='Bot nicht erreichbar';return}
 $('mode').textContent=(s.mode==='paper'?'Simulation, kein echtes Geld':s.mode==='testnet'?'Spielgeld (Testkonto)':'ECHTES GELD')+' · '+(s.exchange||'Binance')+' '+(s.market==='futures'?'Futures':'Spot');
 $('mode').className='pill '+s.mode;
 const cn=s.connection;$('conn').textContent=!cn?'verbindet …':cn.ok?(s.exchange_stops?'Verbunden · Verlust-Grenzen liegen bei der Börse':'Verbunden'):'Keine Verbindung';
 $('conn').className='pill'+(cn&&!cn.ok?' bad':'');
 $('status').textContent=String(s.status).replace('laeuft','läuft');
 $('tg').textContent=s.telegram?'Handy-Meldungen an':'Handy-Meldungen aus';$('tg').style.cursor='pointer';
 $('tg').onclick=async()=>{const r=await (await fetch('/api/telegram_test',{method:'POST'})).json();alert(r.ok?'Test-Nachricht gesendet. Schau aufs Handy.':r.error)};
 $('pauseBtn').textContent=s.paused?'Weiter':'Pause';$('pauseBtn').onclick=()=>act(s.paused?'resume':'pause');
 const b=$('banner');
 const pf=s.preflight;
 if(pf&&!pf.ok){b.style.display='block';b.className='banner bad';b.innerHTML='<b>Start mit echtem Geld blockiert.</b> '+pf.problems.map(x=>esc(plain(x))).join(' ')}
 else if(s.kill_reason){b.style.display='block';b.className='banner bad';b.innerHTML='<b>Notbremse ausgelöst:</b> '+esc(plain(s.kill_reason))+'. Alle Trades wurden beendet. Schau dir an, was passiert ist, und klick dann oben auf <b>Weiter</b>.'}
 else if(cn&&!cn.ok){b.style.display='block';b.className='banner bad';b.textContent='Keine Verbindung zu '+(s.exchange||'der Börse')+': '+cn.error}
 else if(s.halted_until){b.style.display='block';b.className='banner warn';b.textContent='Heute schon zu viel Verlust. Der Bot macht bis 2 Uhr nachts keine neuen Trades. Laufende Trades passt er weiter auf.'}
 else if(s.paused){b.style.display='block';b.className='banner warn';b.textContent='Pausiert: Der Bot macht keine neuen Trades. Laufende Trades passt er weiter auf.'}
 else b.style.display='none';
 const Q=' '+(s.quote||'USDT');
 $('equity').textContent=n2(s.equity)+Q;
 if(s.budget){const d=new Date(s.budget.at);
  $('eq_label').innerHTML=`Topf des Bots <span class="muted">· eingezahlt ${n2(s.budget.start,0)}${Q} seit ${d.toLocaleDateString('de-DE')}${s.budget.real_equity!=null?' · ganzes Testkonto '+n2(s.budget.real_equity,0)+Q:''}</span>`;}
 const np=s.positions.length;
 $('unreal').innerHTML=np?`<b>${np} ${np===1?'Trade läuft':'Trades laufen'}</b>, zusammen gerade <span class="${c(s.unrealized)}">${f(s.unrealized)}${Q}</span> <span class="muted">(noch nicht sicher)</span><br>`+
  s.positions.map(x=>`<span class="muted">${esc(x.symbol)} ${x.side==='long'?'↑':'↓'}</span> <span class="${c(x.unrealized)}">${f(x.unrealized)}</span>`).join(' · ')
  :'gerade läuft kein Trade';
 const XT=s.exchange_pnl_today;
 if(XT){$('today').textContent=f(XT.total)+Q;$('today').className='big num '+c(XT.total);
  $('todayx').innerHTML=`seit 2 Uhr nachts, von Binance abgerechnet<br>Trades ${f(XT.realized)} · Gebühren ${f(XT.fees)} · Finanzierungskosten ${f(XT.funding)}`;}
 else{$('today').textContent=f(s.today_pnl)+Q;$('today').className='big num '+c(s.today_pnl);
  $('todayx').textContent=s.mode==='paper'?'seit 2 Uhr nachts, in der Simulation':'seit 2 Uhr nachts (die Zahl von Binance kommt gleich)';}
 const X=s.exchange_pnl;
 if(X){$('total').textContent=f(X.total)+Q;$('total').className='big num '+c(X.total);
  $('totalx').innerHTML=`Von Binance abgerechnet, das ist die echte Zahl.<br>Trades ${f(X.realized)} · Gebühren ${f(X.fees)} · Finanzierungskosten ${f(X.funding)}`;}
 else{$('total').textContent=f(s.total_pnl)+Q;$('total').className='big num '+c(s.total_pnl);
  $('totalx').textContent=s.mode==='paper'?'in der Simulation, mit geschätzten Gebühren':'vom Bot gerechnet (die Zahl von der Börse kommt gleich)';}
 const wins=Math.round(s.win_rate*s.trades_count/100);
 $('winrate').textContent=s.trades_count?`${wins} von ${s.trades_count}`:'–';
 $('count').textContent=s.trades_count?`${s.win_rate.toFixed(0)} % der Trades im Plus. Unter 50 % ist normal: Verluste klein, Gewinne größer.`:'noch kein Trade beendet';

 const auto=s.config.symbol_mode==='scanner';
 $('scaninfo').textContent=auto?(s.last_scan?'zuletzt '+new Date(s.last_scan*1000).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'})+' Uhr':'erste Suche läuft …'):'aus (feste Liste)';
 const maxScore=Math.max(1,...s.scanner.map(r=>r.score||0));
 $('scanner').innerHTML=s.scanner.length?'<tr><th>Coin</th><th>24 Std.</th><th>gehandelt in 24 Std.</th><th>Wie läuft der Kurs?</th><th title="0 bis 100: wie klar der Kurs in eine Richtung läuft. Ab 25 = klarer Trend">Trendstärke</th><th style="width:22%">Eignung</th><th></th></tr>'+
  s.scanner.map(r=>`<tr class="${r.picked||(s.config.watch_extra||[]).includes(r.symbol)?'picked':''}"><td>${r.symbol}</td><td class="num ${c(r.change24)}">${f(r.change24,1)} %</td><td class=num>${fmtVol(r.volume)}</td><td><span class="regime r-${r.regime}"></span>${RG[r.regime]}</td><td class=num>${adxWord(r.adx)} (${n2(r.adx,0)})</td><td><div class="bar" style="width:${(r.score/maxScore*100).toFixed(0)}%;${r.score?'':'background:var(--pause)'}"></div></td><td>${r.picked?'wird beobachtet':(s.config.watch_extra||[]).includes(r.symbol)?'immer im Blick':r.cooldown?'gemieden (verlor hier oft)':''}</td></tr>`).join('')
  :`<tr><td class=empty>${auto?'Die Coin-Suche läuft gerade.':(s.config.strategy_mode==='ausbruch'?'Aus: Die Ausbruch-Strategie handelt die feste Liste der 10 bekannten Coins (so wurde sie getestet).':'Stell unten „Coins auswählen“ auf „Automatisch“, dann sucht der Bot selbst.')}</td></tr>`;

 $('ptime').textContent=s.last_price_update?'Kurse von '+s.last_price_update+' (UTC)':'';
 $('markets').innerHTML=s.markets.length?'<tr><th>Coin</th><th>Kurs</th><th>Wie läuft der Kurs?</th><th>Was der Bot macht</th><th>Hebel</th><th title="Wie viel Prozent wetten gerade auf steigend: die Profi-Trader bei Binance / alle Trader">Wetten auf steigt: Profis / alle</th></tr>'+
  s.markets.map(x=>`<tr><td>${x.symbol}${(s.config.watch_extra||[]).includes(x.symbol)?' <span class="muted" title="immer im Blick">★</span>':''}</td><td class=num>${x.price}</td><td><span class="regime r-${x.regime}"></span>${RG[x.regime]}</td><td style="white-space:normal">${doing(x.strategy,x)}</td><td class=num>${x.leverage}x</td><td class=num>${x.top_long==null?'–':x.top_long+' % / '+x.crowd_long+' %'}</td></tr>`).join('')
  :'<tr><td class=empty>Die Coin-Suche hat gerade keinen passenden Coin gefunden. Der Bot wartet.</td></tr>';
 $('positions').innerHTML=s.positions.length?'<tr><th>Coin</th><th>Wette</th><th>Eingestiegen bei</th><th>Verlust-Grenze</th><th title="Wandert mit dem Kurs: Tief (bei Short: Hoch) der letzten Kerzen. Schließt eine Kerze dahinter, steigt der Bot aus.">Raus spätestens bei</th><th>Gewinn-Ziel</th><th>Hebel</th><th title="Save = so viel bleibt mindestens, auch wenn der Kurs jetzt dreht. Stoploss = so viel kann der Trade höchstens noch kosten. Mit Gebühren.">Save / Stoploss</th><th>Stand jetzt</th><th></th></tr>'+
  s.positions.map(x=>`<tr><td>${x.symbol}</td><td>${SIDE(x.side)}${x.units?` <span class="muted" title="Nachgekauft, weil der Trend weiterläuft. Der ganze Trade verliert trotzdem höchstens das geplante Risiko.">· ${x.units.length}/${x.max_units} Teile</span>`:''}</td><td class=num>${x.entry.toPrecision(5)}</td><td class=num>${x.stop.toPrecision(5)}${x.breakeven?' <span class="pos">abgesichert</span>':''}${x.exchange_stop?' <span title="liegt direkt bei der Börse und greift auch, wenn dein PC aus ist">✓</span>':''}</td><td class=num>${x.exit_level?x.exit_level.toPrecision(5):'<span class="muted">–</span>'}</td><td class=num>${x.take_profit==null?'<span class="muted" title="Kein festes Ziel: Der Bot steigt aus, wenn der Trend dreht">offen, läuft mit dem Trend</span>':x.take_profit.toPrecision(5)}</td><td class=num>${x.leverage}x</td><td class="num ${x.safe==null?'':c(x.safe)}">${x.safe==null?'<span class="muted">–</span>':(x.safe>=0?'Save '+f(x.safe):'Stoploss '+f(x.safe))}</td><td class="num ${c(x.unrealized)}">${f(x.unrealized)}</td><td><button onclick="if(confirm('Diesen Trade jetzt beenden?'))act('close','${x.symbol}')">Beenden</button></td></tr>`).join('')
  :'<tr><td class=empty>Gerade läuft kein Trade. Der Bot wartet auf eine gute Gelegenheit.</td></tr>';
 $('trades').innerHTML=s.trades.length?'<tr><th>Wann</th><th>Coin</th><th>Wette</th><th>Ergebnis</th><th>Warum beendet</th></tr>'+
  s.trades.map(x=>`<tr><td class=num>${dt(x.closed_at)}</td><td>${x.symbol}</td><td>${SIDE(x.side)}</td><td class="num ${c(x.pnl)}">${f(x.pnl)}</td><td style="white-space:normal">${REASON[x.reason]||x.reason}${x.verified?' <span class="muted" title="Preis mit der Börse abgeglichen">✓</span>':''}</td></tr>`).join('')
  :'<tr><td class=empty>Noch kein Trade beendet.</td></tr>';
 $('log').innerHTML=s.log.map(x=>`<div class="${x.kind}"><span>${x.t}</span>${esc(plain(x.msg))}</div>`).join('');
 renderLearning(s.learning,s.config);
 // Einstellungen neu einlesen, wenn sie sich im Bot geaendert haben (z. B. nach einem Neustart),
 // aber nicht, solange du gerade selbst etwas eintippst.
 const cfgNow=JSON.stringify(s.config);
 if(!formFilled||(cfgNow!==lastCfg&&!formDirty)){fillForm(s.config);formFilled=true;}
 lastCfg=cfgNow;
}
function fillForm(k){
 $('s_mode').value=k.symbol_mode;$('s_top').value=k.scan_top;$('s_minvol').value=k.scan_min_volume_m;$('s_scanmin').value=k.scan_minutes;
 $('s_symbols').value=k.symbols.join(', ');$('s_always').value=(k.scan_always||[]).join(', ');$('s_majors').value=k.scan_majors===false?'0':'1';$('s_pros').value=k.follow_pros||'nicht_gegen';$('s_topvol').value=k.scan_top_volume??5;$('s_risk').value=+(k.risk_per_trade*100).toFixed(2);$('s_strat').value=k.strategy_mode||'standard';$('s_openrisk').value=+((k.max_open_risk||0)*100).toFixed(2);$('s_units').value=k.pyramid_max_units||1;$('s_max_leverage').value=k.max_leverage;
 $('s_max_positions').value=k.max_positions;$('s_daily').value=(k.daily_loss_limit*100).toFixed(1);$('s_be').value=k.breakeven_at_r;
 $('s_trail').value=k.trail_at_r;$('s_tf').value=k.timeframe;$('s_loop').value=k.loop_seconds;$('s_price').value=k.price_seconds;
 $('s_spot').value=Math.round(k.spot_max_allocation*100);
 $('s_killdd').value=Math.round((k.max_total_drawdown||0.2)*100);$('s_cap').value=k.max_capital_usdt||0;$('s_autolearn').value=k.learning_auto_apply?'1':'0';$('b_tf').value=k.timeframe;
}
async function deposit(){
 const a=$('dep_amt').value; if(!a||+a<=0){$('dep_msg').textContent='Bitte Betrag eingeben.';return;}
 if(!confirm(`${a} als Einzahlung buchen?`))return;
 const r=await (await fetch('/api/deposit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({amount:a})})).json();
 $('dep_msg').textContent=r.msg; if(r.ok)$('dep_amt').value='';}
async function saveSettings(){
 const body={symbol_mode:$('s_mode').value,scan_top:$('s_top').value,scan_min_volume_m:$('s_minvol').value,scan_minutes:$('s_scanmin').value,
  symbols:$('s_symbols').value,scan_always:$('s_always').value,scan_majors:$('s_majors').value==='1',follow_pros:$('s_pros').value,scan_top_volume:$('s_topvol').value,risk_per_trade:$('s_risk').value/100,strategy_mode:$('s_strat').value,max_open_risk:$('s_openrisk').value/100,pyramid_max_units:$('s_units').value,max_leverage:$('s_max_leverage').value,
  max_positions:$('s_max_positions').value,daily_loss_limit:$('s_daily').value/100,breakeven_at_r:$('s_be').value,
  trail_at_r:$('s_trail').value,timeframe:$('s_tf').value,loop_seconds:$('s_loop').value,price_seconds:$('s_price').value,
  spot_max_allocation:$('s_spot').value/100,max_total_drawdown:$('s_killdd').value/100,max_capital_usdt:$('s_cap').value,
  learning_auto_apply:$('s_autolearn').value==='1'};
 const r=await (await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
 $('saved').textContent=r.changed.length?'Gespeichert, gilt ab sofort.':'Nichts geändert.';
 setTimeout(()=>$('saved').textContent='',4000);formFilled=false;formDirty=false;load();
}
function curveSvg(pts,start){
 if(pts.length<2)return'';
 const W=1000,H=180,P=6,es=pts.map(p=>p.e),mn=Math.min(start,...es),mx=Math.max(start,...es),rg=(mx-mn)||1;
 const x=i=>P+i/(pts.length-1)*(W-2*P),y=v=>H-P-(v-mn)/rg*(H-2*P);
 const d=pts.map((p,i)=>(i?'L':'M')+x(i).toFixed(1)+','+y(p.e).toFixed(1)).join('');
 const col=es[es.length-1]>=start?'var(--up)':'var(--down)';
 return `<svg class="curve" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Kontoverlauf im Test">
  <line x1="0" x2="${W}" y1="${y(start)}" y2="${y(start)}" stroke="var(--line)" stroke-dasharray="6 6"/>
  <path d="${d}" fill="none" stroke="${col}" stroke-width="2.5" vector-effect="non-scaling-stroke"/></svg>`;
}
async function runBacktest(){
 const btn=$('b_btn');btn.disabled=true;btn.textContent='Lädt alte Kurse …';$('bt').innerHTML='';
 try{
  const q=new URLSearchParams({symbol:$('b_sym').value,days:$('b_days').value,timeframe:$('b_tf').value});
  const r=await (await fetch('/api/backtest?'+q,{method:'POST'})).json();
  if(r.error){$('bt').innerHTML=`<p class="neg">${esc(r.error)}</p>`;return}
  const pf=r.profit_factor==null?'–':n2(r.profit_factor);
  let verdict;
  if(r.trades<15) verdict='Zu wenige Trades für eine Aussage. Probier mehr Tage oder eine kürzere Kerzen-Länge.';
  else if(r.return_pct>0&&r.profit_factor>1.2&&r.max_drawdown_pct<15) verdict='👍 Hätte sich gelohnt, ohne große Abstürze zwischendurch. Am besten noch mit anderen Coins und Zeiträumen gegenprüfen.';
  else if(r.return_pct>0) verdict='🤏 Knapp im Plus, aber wackelig. Darauf würde ich noch kein echtes Geld setzen.';
  else verdict='👎 Hätte auf diesem Coin Geld verloren.';
  $('bt').innerHTML=`
   <div class="bt-stats">
    <div><small>Ergebnis</small><b class="${c(r.return_pct)}">${f(r.return_pct,1)} %</b></div>
    <div><small>Zum Vergleich: einfach kaufen und liegen lassen</small><b class="${c(r.hold_return_pct)}">${f(r.hold_return_pct,1)} %</b></div>
    <div><small>Anzahl Trades</small><b>${r.trades}</b></div>
    <div><small>davon mit Gewinn</small><b>${r.win_rate.toFixed(0)} %</b></div>
    <div><small>Tiefster Absturz zwischendurch</small><b class="neg">−${n2(r.max_drawdown_pct,1)} %</b></div>
    <div><small>Gewinne geteilt durch Verluste (über 1 = gut)</small><b>${pf}</b></div>
    <div><small>im Schnitt Gewinn / Verlust pro Trade</small><b>${n2(r.avg_win,1)} / ${n2(r.avg_loss,1)}</b></div>
    <div><small>abgesichert beendet</small><b>${r.breakeven_exits}</b></div>
   </div>
   ${curveSvg(r.curve,r.start_equity)}
   <p class="note">Linie = Kontostand im Test. ${esc(r.symbol)}, ${r.from} bis ${r.to}, Kerzen ${r.timeframe}, Start mit 1000.</p>
   <div class="verdict">${verdict} Die Vergangenheit ist keine Garantie für die Zukunft.</div>`;
 }catch(e){$('bt').innerHTML='<p class="neg">Test fehlgeschlagen: '+esc(e)+'</p>'}
 finally{btn.disabled=false;btn.textContent='Test starten'}
}
const ALLA={reload:["/api/alle_neu_laden","Alle Bots neu laden?\nOffene Trades bleiben gespeichert. Die Bots sind ca. 1 Minute nicht erreichbar.","Alle Bots starten neu – in ca. 1 Minute wieder da."],
 pause:["/api/alle_pause","Alle Bots pausieren?\nKeine neuen Trades, offene Trades werden weiter bewacht.","Alle Bots pausiert."],
 weiter:["/api/alle_weiter","Alle Bots wieder handeln lassen?","Alle Bots handeln wieder."],
 stop:["/api/alle_beenden","Alle Bots wirklich BEENDEN?\n\nWieder starten: über den Bot-Wächter (Link unten). Solange sie aus sind, bewacht niemand die offenen Trades.\n\nTipp: Pausieren ist meistens besser.","Alle Bots beendet. Wieder starten: Bot-Wächter (Link unten)."]};
try{$('wlink').href=location.protocol+'//'+location.hostname+':8020/'}catch(e){}
async function allAct(k){const a=ALLA[k];if(!confirm(a[1]))return;if(k==='stop'&&!confirm('Sicher? Die Bots bleiben aus, bis du am PC bist.'))return;
 const bs=[...document.querySelectorAll('.allbtn')];bs.forEach(x=>x.disabled=true);$('allmsg').textContent='Läuft …';
 try{const j=await (await fetch(a[0],{method:'POST',headers:{'x-bot-action':'1'}})).json();$('allmsg').textContent=a[2]+(j.missing&&j.missing.length?` (Nicht erreicht: ${j.missing.join(', ')})`:'')}
 catch(e){$('allmsg').textContent='Keine Antwort vom PC: '+e.message}
 setTimeout(()=>bs.forEach(x=>x.disabled=false),k==='reload'?60000:3000)}
// Alle 1,5 Sekunden neu laden (nicht, wenn der Tab im Hintergrund ist; nie zwei Abfragen gleichzeitig)
let busy=false;async function tick(){if(busy||document.hidden)return;busy=true;try{await load()}finally{busy=false}}
load();setInterval(tick,1500);
</script></body></html>"""


OVERVIEW_HTML = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Alle Bots</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--line:#e6e8ec;--text:#14171c;--muted:#6b7280;--accent:#2563eb;--up:#16a34a;--down:#dc2626;--warn:#d97706;--chip:#eef2ff;--chiptext:#3730a3;color-scheme:light}
:root[data-theme="dark"]{--bg:#0d0f12;--card:#16191e;--line:#262a31;--text:#e8eaee;--muted:#8b93a1;--accent:#3b82f6;--up:#22c55e;--down:#ef4444;--warn:#f59e0b;--chip:#1e2433;--chiptext:#a5b4fc;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:16px;padding-top:max(16px,env(safe-area-inset-top))}
.num{font-variant-numeric:tabular-nums}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:14px}
.brand{display:flex;gap:12px;align-items:center}
.logo{width:36px;height:36px;border-radius:9px;background:var(--accent);display:grid;place-items:center;color:#fff;font-weight:700}
h1{font-size:18px;margin:0;display:flex;gap:8px;align-items:center}
.badge{font-size:10px;font-weight:700;letter-spacing:.04em;padding:2px 7px;border-radius:999px;background:var(--chip);color:var(--chiptext)}
.sub{color:var(--muted);font-size:12px}
.actions{display:flex;gap:8px;align-items:center}
.pill{display:flex;gap:6px;align-items:center;font-size:12px;padding:6px 10px;border-radius:999px;border:1px solid var(--line);background:var(--card)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--up)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--down)}
button{font:inherit;font-size:13px;padding:7px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
button:hover{border-color:var(--accent)}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.stat .k{font-size:12px;color:var(--muted)}.stat .v{font-size:22px;font-weight:700;margin-top:4px}.stat .s{font-size:12px;margin-top:2px;color:var(--muted)}
.up{color:var(--up)}.down{color:var(--down)}
.bar{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:var(--card)}
.seg button{border:0;border-radius:0;padding:7px 12px}
.seg button.on{background:var(--accent);color:#fff}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.bot{display:flex;flex-direction:column;text-decoration:none;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}
.bot:hover{border-color:var(--accent)}
.bot.off{opacity:.55}
.top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.top>div:first-child{min-width:0}
.name{font-weight:700;font-size:15px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.meta{font-size:12px;color:var(--muted);margin-top:2px}
.konto{text-align:right;flex-shrink:0}.konto .v{font-size:18px;font-weight:700}.konto .s{font-size:12px;white-space:nowrap}
.tag{font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;background:var(--chip);color:var(--chiptext)}
.tag.bad{background:#fee2e2;color:#991b1b}.tag.warn{background:#fef3c7;color:#92400e}
:root[data-theme="dark"] .tag.bad{background:#3b1010;color:#fca5a5}:root[data-theme="dark"] .tag.warn{background:#3a2a0a;color:#fcd34d}
.mini{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0 8px;padding:10px;border-radius:10px;background:var(--bg)}
.mini div{font-size:11px;color:var(--muted)}.mini b{display:block;font-size:13px;color:var(--text)}
.pos{flex:1}
.row{display:flex;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px}
.row:last-child{border-bottom:0}
.side{font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;margin-left:6px}
.side.long{background:color-mix(in srgb,var(--up) 18%,transparent);color:var(--up)}
.side.short{background:color-mix(in srgb,var(--down) 18%,transparent);color:var(--down)}
.empty{font-size:13px;color:var(--muted);padding:6px 0}
.open{margin-top:10px;font-size:12px;color:var(--accent);font-weight:600}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:14px}
.acts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:8px}
.acts button{font-weight:600;padding:11px 8px;border-radius:10px}
.acts button.danger{color:var(--down);border-color:var(--down)}
.acts button:disabled{opacity:.5}
.err{background:var(--card);border:1px solid var(--down);color:var(--down);border-radius:12px;padding:10px;margin-bottom:12px;display:none}
.foot{color:var(--muted);font-size:12px;margin-top:12px}
@media (max-width:1000px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:700px){.cards{grid-template-columns:1fr 1fr;gap:8px}.grid{grid-template-columns:1fr}.acts{grid-template-columns:1fr 1fr}.stat .v{font-size:18px}.wrap{padding:12px}}
</style></head><body><div class="wrap">
<header>
 <div class="brand"><div class="logo">₿</div><div><h1>Alle Bots <span class="badge">SPIELGELD</span></h1><div class="sub" id="upd">lädt …</div></div></div>
 <div class="actions"><div class="pill"><span class="dot" id="dot"></span><span id="s_on2">…</span></div><button id="theme" title="Hell/Dunkel">☾</button></div>
</header>
<div class="err" id="err"></div>
<section class="cards">
 <div class="stat"><div class="k">Auf allen Konten (≈ USD)</div><div class="v num" id="s_konto">–</div><div class="s" id="s_start">&nbsp;</div></div>
 <div class="stat"><div class="k">Gewinn / Verlust seit Start</div><div class="v num" id="s_gew">–</div><div class="s" id="s_pct">&nbsp;</div></div>
 <div class="stat"><div class="k">Offene Trades</div><div class="v num" id="s_pos">–</div><div class="s" id="s_open">&nbsp;</div></div>
 <div class="stat"><div class="k">Bester / schwächster Bot</div><div class="v" id="s_best" style="font-size:15px">–</div><div class="s" id="s_worst">&nbsp;</div></div>
</section>
<div class="bar"><div class="seg"><button id="b_port" onclick="setSort('port')">Nach Bot</button><button id="b_gew" onclick="setSort('gew')">Nach Gewinn</button><button id="b_open" onclick="setSort('open')">Mit Trades zuerst</button></div></div>
<div class="grid" id="list"></div>
<div class="panel"><b>Alle Bots steuern</b><div class="sub">Gilt für alle Bots auf deinem PC.</div>
 <div class="acts">
  <button id="a_reload" onclick="act('reload')">Alle neu laden</button>
  <button id="a_pause" onclick="act('pause')">Alle pausieren</button>
  <button id="a_weiter" onclick="act('weiter')">Alle weiter</button>
  <button id="a_stop" class="danger" onclick="act('stop')">Alle beenden</button>
 </div><div class="sub" id="actmsg" style="margin-top:8px"></div></div>
<p class="foot"><a id="wl" href="#" style="color:inherit">Bot-Wächter öffnen</a> (startet beendete Bots wieder) · Antippen öffnet das Dashboard des Bots. Gewinn = beendete + offene Trades seit dem Neustart (Einzahlungen zählen nicht). Aktualisiert sich alle 5 Sekunden. Keine Finanzberatung.</p>
</div>
<script>
const LS={get(k){try{return localStorage.getItem(k)}catch(e){return null}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
let sort=LS.get("sort")||"port", last=null;
(function(){const t=LS.get("board_theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=t})();
document.getElementById("theme").onclick=()=>{const t=document.documentElement.dataset.theme==="dark"?"light":"dark";document.documentElement.dataset.theme=t;LS.set("board_theme",t)};
const $=id=>document.getElementById(id);
const eur=(v,d=2)=>v==null?"–":Number(v).toLocaleString("de-DE",{minimumFractionDigits:d,maximumFractionDigits:d});
const sg=(v,d=2)=>(v>0?"+":"")+eur(v,d);
const cls=v=>v>0.004?"up":(v<-0.004?"down":"");
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const price=v=>v==null?"–":(v>=1000?eur(v,0):v>=1?eur(v,2):Number(v).toPrecision(4).replace(".",","));
function setSort(s){sort=s;LS.set("sort",s);render()}
function card(b){
  const url=`${location.protocol}//${location.hostname}:${b.port}/`;
  if(!b.ok) return `<a class="bot off" href="${url}"><div class="top"><div><div class="name">${esc(b.name||("Port "+b.port))}</div><div class="meta">Port ${b.port}</div></div><span class="tag bad">NICHT ERREICHBAR</span></div></a>`;
  const q=b.quote||"USDT";
  const st=b.status!=="läuft"?`<span class="tag ${b.status==="Notbremse"?"bad":"warn"}">${esc(b.status).toUpperCase()}</span>`:"";
  const sim=b.mode==="paper"?"Simulation":(b.mode==="testnet"?"Demo":"ECHT");
  const risk=(b.risk*100).toLocaleString("de-DE",{maximumFractionDigits:3})+" %"+(b.units>1?` × ${b.units}`:"");
  const meta=b.meta||`${b.port} · ${sim} · ${esc(b.timeframe)} · ${risk}`;
  const pos=b.positions.length?b.positions.slice(0,6).map(p=>`<div class="row"><span><b>${esc(p.symbol.split("/")[0])}</b><span class="side ${p.side}">${p.side==="long"?"LONG":"SHORT"}</span>${p.units>1?`<span class="sub"> ${p.units}/${p.max_units}</span>`:""}<br><span class="sub num">${price(p.entry)} → ${price(p.price)}${p.exit?` · raus ab ${price(p.exit)}`:""}${p.safe!=null?` · <span class="${cls(p.safe)}">${p.safe>=0?"Save":"Stoploss"} ${sg(p.safe)}</span>`:""}</span></span><span class="num ${cls(p.pnl)}" style="text-align:right">${sg(p.pnl)}<br><span class="sub">${p.r==null?"":(p.r>0?"+":"")+eur(p.r,1)+" R"}</span></span></div>`).join("")+(b.positions.length>6?`<div class="empty">+ ${b.positions.length-6} weitere</div>`:""):`<div class="empty">Kein offener Trade – wartet auf ein Signal</div>`;
  return `<a class="bot" href="${url}">
   <div class="top"><div><div class="name">${esc(b.name)}${st}</div><div class="meta">${esc(meta)}</div></div>
   <div class="konto"><div class="v num">${eur(b.konto)}</div><div class="s num ${cls(b.gewinn)}">${sg(b.gewinn)} ${q} (${b.pct>0?"+":""}${eur(b.pct,1)} %)</div></div></div>
   <div class="mini"><div>Trades<b class="num">${b.trades}${b.trades?` · ${Math.round(b.wins/b.trades*100)} %`:""}</b></div><div>Heute<b class="num ${cls(b.today)}">${sg(b.today)}</b></div><div>Offen<b class="num ${cls(b.unrealized)}">${sg(b.unrealized)}</b></div></div>
   <div class="pos">${pos}</div><div class="open">Dashboard öffnen →</div></a>`;
}
function render(){
  if(!last) return;
  for(const k of["port","gew","open"])$("b_"+k).className=sort===k?"on":"";
  const bots=[...last.bots];
  if(sort==="gew") bots.sort((a,b)=>(b.ok?b.pct:-1e9)-(a.ok?a.pct:-1e9));
  if(sort==="open") bots.sort((a,b)=>((b.ok?b.positions.length:-1)-(a.ok?a.positions.length:-1))||a.port-b.port);
  const on=bots.filter(b=>b.ok);
  const k=on.reduce((s,b)=>s+b.konto,0), g=on.reduce((s,b)=>s+b.gewinn,0), st=on.reduce((s,b)=>s+b.start,0);
  $("s_konto").textContent=eur(k,0);$("s_start").textContent="Start: "+eur(st,0);
  $("s_gew").textContent=sg(g);$("s_gew").className="v num "+cls(g);$("s_pct").innerHTML=st?`<span class="${cls(g)}">${g>0?"+":""}${eur(g/st*100,2)} %</span>`:"&nbsp;";
  const np=on.reduce((s,b)=>s+b.positions.length,0);$("s_pos").textContent=np;$("s_open").textContent=`bei ${on.filter(b=>b.positions.length).length} Bots`;
  const r=[...on].sort((a,b)=>b.pct-a.pct);
  if(r.length){$("s_best").innerHTML=`<span class="up">${esc(r[0].name)} ${sg(r[0].pct,1)} %</span>`;$("s_worst").innerHTML=`<span class="down">${esc(r.at(-1).name)} ${sg(r.at(-1).pct,1)} %</span>`}
  const bad=on.filter(b=>b.status!=="läuft").length;
  $("s_on2").textContent=`${on.length} / ${bots.length} laufen`;$("dot").className="dot"+(on.length<bots.length?" bad":bad?" warn":"");
  $("list").innerHTML=bots.map(card).join("");
}
async function load(){
  try{
    const r=await fetch("/api/alle",{cache:"no-store"}); if(!r.ok) throw new Error("HTTP "+r.status);
    last=await r.json(); render();
    $("upd").textContent="Stand "+new Date().toLocaleTimeString("de-DE");$("err").style.display="none";
  }catch(e){const el=$("err");el.textContent="Keine Verbindung zum Bot-PC ("+e.message+"). Läuft der PC und Tailscale?";el.style.display="block"}
}
const ACTS={
  reload:{url:"/api/alle_neu_laden",ask:"Alle Bots neu laden?\nOffene Trades bleiben gespeichert. Die Bots sind ca. 1 Minute nicht erreichbar.",ok:"Alle Bots starten neu – in ca. 1 Minute wieder da."},
  pause:{url:"/api/alle_pause",ask:"Alle Bots pausieren?\nSie machen keine neuen Trades mehr, passen aber weiter auf die offenen Trades auf (Stops bleiben).",ok:"Alle Bots pausiert. Mit „Alle weiter“ geht es wieder los."},
  weiter:{url:"/api/alle_weiter",ask:"Alle Bots wieder handeln lassen?",ok:"Alle Bots handeln wieder."},
  stop:{url:"/api/alle_beenden",ask:"Alle Bots wirklich BEENDEN?\n\nWieder starten: über den Bot-Wächter (Link unten auf dieser Seite).\nSolange sie aus sind, achtet niemand auf die Verlust-Grenzen der offenen Trades.\n\nTipp: „Alle pausieren“ ist meistens besser.",ok:"Alle Bots beendet. Wieder starten über den Bot-Wächter (Port 8020) oder am PC (alle_starten.bat)."}
};
async function act(k){
  const a=ACTS[k]; if(!confirm(a.ask))return;
  if(k==="stop"&&!confirm("Sicher? Die Bots bleiben aus, bis du am PC bist."))return;
  const btns=[...document.querySelectorAll(".acts button")]; btns.forEach(b=>b.disabled=true);
  const msg=$("actmsg"); msg.textContent="Läuft …";
  try{
    const r=await fetch(a.url,{method:"POST",headers:{"x-bot-action":"1"}});
    const j=await r.json();
    msg.textContent=a.ok+(j.missing&&j.missing.length?` (Nicht erreicht: ${j.missing.join(", ")})`:"");
  }catch(e){msg.textContent="Keine Antwort vom PC: "+e.message}
  setTimeout(()=>btns.forEach(b=>b.disabled=false),k==="reload"?60000:3000);
  setTimeout(load,1500);
}
try{document.getElementById("wl").href=location.protocol+"//"+location.hostname+":8020/"}catch(e){}
load(); let busyA=false; setInterval(async()=>{if(busyA||document.hidden)return;busyA=true;try{await load()}finally{busyA=false}},2000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)load()});
</script></body></html>
"""


def _clean(x):
    """Ungueltige Zahlen (NaN/unendlich, z. B. bei ganz neuen Coins ohne genug Kursdaten) -> None,
    sonst kann der Browser die Daten nicht lesen."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item"):          # numpy-Zahlen
        return _clean(x.item())
    return x


def create_app(engine, password=None):
    app = FastAPI()
    password = (password or "").strip()
    fails = {}   # IP -> Zeitpunkte falscher Passwoerter

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.client.host if request.client else ""
        kind = client_kind(host)
        if kind == "local":
            return await call_next(request)
        if kind != "tailscale" or not password:
            return PlainTextResponse("Kein Zugriff. Das Dashboard ist nur ueber Tailscale mit Passwort erreichbar.", 403)
        now = time.time()
        recent = [t for t in fails.get(host, []) if now - t < FAIL_WINDOW]
        if len(recent) >= MAX_FAILS and now - recent[-1] < LOCK_SECONDS:
            return PlainTextResponse("Zu viele falsche Passwoerter. Bitte in 15 Minuten nochmal versuchen.", 429)
        if not password_ok(request.headers.get("authorization", ""), password):
            if request.headers.get("authorization"):
                fails[host] = recent + [now]
                if len(fails[host]) in (3, MAX_FAILS):
                    engine._log(f"Fernzugriff: {len(fails[host])}x falsches Passwort von {host}", "warn")
                await asyncio.sleep(1)   # bremst Passwort-Raten
            return Response("Passwort noetig", 401, headers={"WWW-Authenticate": 'Basic realm="Trading Bot", charset="UTF-8"'})
        fails.pop(host, None)
        return await call_next(request)

    board = EngineBoard(engine)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(board_html(), headers={"Cache-Control": "no-cache"})   # neues Dashboard (wie im Video)

    @app.get("/klassisch", response_class=HTMLResponse)
    def klassisch():
        return HTML                  # alte, ausfuehrliche Seite (Einstellungen, Lernen, Backtest)

    @app.get("/board/lwc.js")
    def lwc():
        return Response(lwc_js(), media_type="application/javascript", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/board")
    def api_board(symbol: str = ""):
        return JSONResponse(_clean(board.board(symbol or None)))

    @app.get("/api/board/candles")
    def api_board_candles(symbol: str = ""):
        return JSONResponse(_clean(board.candles(symbol or None)))

    @app.get("/api/state")
    def state():
        return JSONResponse(_clean(engine.snapshot()))

    @app.get("/alle", response_class=HTMLResponse)
    def alle_page():
        return HTMLResponse(OVERVIEW_HTML, headers={"Cache-Control": "no-cache"})

    @app.get("/api/kurz")
    def kurz():
        return JSONResponse(_clean(engine.overview()))

    @app.get("/api/alle")
    def alle():
        own = engine.cfg.get("dashboard_port")

        def one(port):
            if port == own:
                return {**engine.overview(), "ok": True}
            try:
                # Die anderen Bots laufen auf demselben PC -> lokal fragen (kein Passwort noetig)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/kurz", timeout=3) as r:
                    return {**json.loads(r.read().decode("utf-8")), "ok": True}
            except Exception:
                return {"port": port, "ok": False, "name": BOT_NAMES.get(port)}
        with ThreadPoolExecutor(max_workers=len(OVERVIEW_PORTS)) as pool:
            bots = list(pool.map(one, OVERVIEW_PORTS))
        # Ein Bot kann weitere Konten mitliefern (Copy-Bot: Vergleichs-Konten mit anderem Risiko/Hebel)
        flat = []
        for b in bots:
            extra = b.pop("extra", None) or []
            flat.append(b)
            flat += [{**x, "ok": True, "sub": True} for x in extra]
        return JSONResponse(_clean({"bots": flat}))

    def _exit_soon(reason, code):
        """Zustand speichern und das Programm beenden.
        code 1: das Start-Fenster (start_*.bat) startet den Bot nach 30 Sekunden neu (neuester Stand).
        code 3: der Bot bleibt aus, bis er am PC wieder gestartet wird (alle_starten.bat)."""
        engine._log(reason, "warn")
        try:
            with engine.lock:
                engine._save()
        except Exception:
            pass

        def bye():
            time.sleep(1.5)
            os._exit(code)
        threading.Thread(target=bye, daemon=True).start()

    def _action_ok(request):
        # Eigener Kopf noetig: fremde Webseiten koennen ihn nicht mitschicken
        return request.headers.get("x-bot-action") == "1"

    def _fan_out(path):
        """Dieselbe Aktion bei allen anderen Bots auf diesem PC ausloesen (lokal, ohne Passwort)."""
        own = engine.cfg.get("dashboard_port")

        def one(port):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}", method="POST",
                                             headers={"x-bot-action": "1", "content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=3):
                    return port, True
            except Exception:
                return port, False
        others = [p for p in OVERVIEW_PORTS if p != own]
        with ThreadPoolExecutor(max_workers=len(others) or 1) as pool:
            res = dict(pool.map(one, others))
        res[own] = True
        return {"ok": True, "done": sorted(p for p, v in res.items() if v),
                "missing": sorted(p for p, v in res.items() if not v)}

    FORBIDDEN = {"ok": False, "msg": "Nicht erlaubt."}

    @app.post("/api/neu_laden")
    def neu_laden(request: Request):
        if not _action_ok(request):
            return JSONResponse(FORBIDDEN, 403)
        _exit_soon("Neu laden über die Webseite. Der Bot ist in ca. 1 Minute wieder da.", 1)
        return {"ok": True}

    @app.post("/api/beenden")
    def beenden(request: Request):
        if not _action_ok(request):
            return JSONResponse(FORBIDDEN, 403)
        _exit_soon("Beendet über die Webseite. Wieder starten über den Bot-Wächter (Port 8020) oder am PC (alle_starten.bat).", 3)
        return {"ok": True}

    @app.post("/api/alle_neu_laden")
    def alle_neu_laden(request: Request):
        if not _action_ok(request):
            return JSONResponse(FORBIDDEN, 403)
        res = _fan_out("/api/neu_laden")
        _exit_soon("Neu laden: alle Bots über die Übersicht. In ca. 1 Minute wieder da.", 1)
        return res

    @app.post("/api/alle_beenden")
    def alle_beenden(request: Request):
        if not _action_ok(request):
            return JSONResponse(FORBIDDEN, 403)
        res = _fan_out("/api/beenden")
        _exit_soon("Beendet: alle Bots über die Übersicht. Wieder starten über den Bot-Wächter (Port 8020) oder am PC (alle_starten.bat).", 3)
        return res

    @app.post("/api/alle_pause")
    def alle_pause(request: Request):
        if not _action_ok(request):
            return JSONResponse(FORBIDDEN, 403)
        res = _fan_out("/api/pause")
        engine.pause()
        return res

    @app.post("/api/alle_weiter")
    def alle_weiter(request: Request):
        if not _action_ok(request):
            return JSONResponse(FORBIDDEN, 403)
        res = _fan_out("/api/resume")
        engine.resume()
        return res

    @app.post("/api/pause")
    def pause():
        engine.pause(); return {"ok": True}

    @app.post("/api/resume")
    def resume():
        engine.resume(); return {"ok": True}

    @app.post("/api/close_all")
    def close_all():
        engine.close_all(); return {"ok": True}

    @app.post("/api/scan")
    def scan():
        engine.scan_now(); return {"ok": True}

    @app.post("/api/settings")
    async def settings(req: Request):
        return engine.update_settings(await req.json())

    @app.post("/api/backtest")
    def backtest(symbol: str = "BTC", days: int = 30, timeframe: str = "15m"):
        try:
            return engine.backtest(symbol, days, timeframe)
        except Exception as e:
            return {"error": f"Kursdaten konnten nicht geladen werden: {e}"}

    @app.post("/api/deposit")
    async def deposit(request: Request):
        data = await request.json()
        return engine.deposit(data.get("amount"))

    @app.post("/api/close/{symbol:path}")
    def close_one(symbol: str):
        engine.close_one(symbol); return {"ok": True}

    @app.post("/api/telegram_test")
    def telegram_test():
        return engine.telegram_test()

    @app.post("/api/learn/analyze")
    def learn_analyze():
        engine.learn_analyze(); return {"ok": True}

    @app.post("/api/learn/tune")
    def learn_tune():
        return {"ok": engine.learn_tune_start()}

    @app.post("/api/learn/apply/{sid}")
    def learn_apply(sid: str):
        return engine.learn_apply(sid)

    @app.post("/api/learn/dismiss/{sid}")
    def learn_dismiss(sid: str):
        return engine.learn_dismiss(sid)

    @app.post("/api/learn/undo/{rid:path}")
    def learn_undo(rid: str):
        return engine.learn_undo(rid)

    @app.post("/api/learn/reset")
    def learn_reset():
        return engine.learn_reset()

    return app
