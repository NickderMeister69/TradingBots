"""Bot-Waechter (Port 8020): kleine Seite, mit der du beendete Bots vom Handy aus wieder startest.
Er laeuft unabhaengig von den Bots, "Alle beenden" beendet ihn also nicht.
Er handelt nicht und kennt keine Schluessel. Er startet nur die vorhandenen start_*.bat-Dateien.
Aufruf: start_waechter.bat"""
import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from bot.dashboard import FAIL_WINDOW, LOCK_SECONDS, MAX_FAILS, client_kind, password_ok

load_dotenv()
ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8020
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
REMOTE = len(PASSWORD) >= 10
START_WAIT = 8 * 60      # die start_*.bat warten bis zu 4 Minuten, bevor der Bot erreichbar ist

# Port -> (Name, Start-Datei)
BOTS = {
    8000: ("Hauptbot", "start.bat"), 8001: ("Bybit EU", "start_bybit.bat"), 8002: ("Kraken Tag", "start_5min.bat"),
    8003: ("Scanner-Test", "start_scanner.bat"), 8004: ("Daytrade riskant", "start_daytrade.bat"),
    8005: ("Daytrade sicher", "start_daytrade_sicher.bat"), 8006: ("Daytrade mittel", "start_daytrade_mittel.bat"),
    8007: ("4h ohne Nachkaufen", "start_4h_einfach.bat"), 8008: ("Riskant 4h", "start_riskant.bat"),
    8009: ("15 Minuten", "start_15min.bat"), 8010: ("Copy-Bot", "start_copybot.bat"),
    8011: ("Copy sicher", "start_copy_sicher.bat"), 8012: ("Copy vorsichtig", "start_copy_vorsichtig.bat"),
    8013: ("Copy mutig", "start_copy_mutig.bat"), 8014: ("Copy riskant", "start_copy_riskant.bat"),
    8015: ("Copy konzentriert", "start_copy_konzentriert.bat"), 8016: ("Copy Zufall", "start_copy_zufall.bat"),
    8017: ("SMA-Kreuz (Video)", "start_sma.bat"), 8018: ("2 % Risiko", "start_2prozent.bat"),
    8019: ("Gold Tag", "start_gold.bat"), 8021: ("4h + Trendlinien", "start_trend.bat"),
}
launched = {}            # Port -> Zeitpunkt, an dem der Waechter ihn gestartet hat
lock = threading.Lock()
LOG = os.path.join(ROOT, "data", "waechter.log")


def log(msg):
    line = f"{datetime.now():%d.%m. %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def is_up(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def status():
    now = time.time()
    out = []
    for port, (name, bat) in BOTS.items():
        up = is_up(port)
        t = launched.get(port)
        if up:
            st = "läuft"
        elif t and now - t < START_WAIT:
            st = f"startet … (seit {int((now - t) // 60)} min)"
        else:
            st = "aus"
        out.append({"port": port, "name": name, "status": st, "up": up,
                    "exists": os.path.exists(os.path.join(ROOT, bat))})
    return out


def start_bot(port):
    """Startet die start_*.bat eines Bots in einem neuen, kleinen Fenster. Nie doppelt."""
    name, bat = BOTS[port]
    path = os.path.join(ROOT, bat)
    with lock:
        if is_up(port):
            return "läuft schon"
        t = launched.get(port)
        if t and time.time() - t < START_WAIT:
            return "startet schon"
        if not os.path.exists(path):
            return "Start-Datei fehlt"
        if sys.platform == "win32":
            subprocess.Popen(f'start "" /min "{path}"', shell=True, cwd=ROOT)
        else:                                   # nur fuer Tests
            subprocess.Popen(["true"], cwd=ROOT)
        launched[port] = time.time()
    log(f"Gestartet: {name} ({port})")
    return "gestartet"


app = FastAPI()
fails = {}


@app.middleware("http")
async def guard(request: Request, call_next):
    host = request.client.host if request.client else ""
    kind = client_kind(host)
    if request.method == "POST" and request.headers.get("x-bot-action") != "1":
        return PlainTextResponse("Nicht erlaubt.", 403)
    if kind == "local":
        return await call_next(request)
    if kind != "tailscale" or not REMOTE:
        return PlainTextResponse("Kein Zugriff. Nur ueber Tailscale mit Passwort.", 403)
    now = time.time()
    recent = [t for t in fails.get(host, []) if now - t < FAIL_WINDOW]
    if len(recent) >= MAX_FAILS and now - recent[-1] < LOCK_SECONDS:
        return PlainTextResponse("Zu viele falsche Passwoerter. Bitte in 15 Minuten nochmal versuchen.", 429)
    if not password_ok(request.headers.get("authorization", ""), PASSWORD):
        if request.headers.get("authorization"):
            fails[host] = recent + [now]
            await asyncio.sleep(1)
        return Response("Passwort noetig", 401, headers={"WWW-Authenticate": 'Basic realm="Trading Bot", charset="UTF-8"'})
    fails.pop(host, None)
    return await call_next(request)


@app.get("/api/status")
def api_status():
    return JSONResponse(status())


@app.post("/api/start_all")
def api_start_all():
    res = {BOTS[p][0]: start_bot(p) for p in BOTS}
    started = [n for n, r in res.items() if r == "gestartet"]
    log(f"Alle starten: {len(started)} Bots gestartet.")
    return {"ok": True, "started": started, "result": res}


@app.post("/api/start/{port}")
def api_start(port: int):
    if port not in BOTS:
        return JSONResponse({"ok": False, "msg": "Unbekannter Bot"}, 404)
    return {"ok": True, "result": start_bot(port)}


PAGE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bot-Wächter</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#111827;--muted:#6b7280;--line:#e5e7eb;--up:#16a34a;--down:#dc2626;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b22;--text:#e5e7eb;--muted:#9ca3af;--line:#2a2f3a;--up:#22c55e;--down:#ef4444;--accent:#3b82f6}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:640px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-top:14px}
button{font:inherit;padding:10px 14px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
button.big{width:100%;background:var(--accent);color:#fff;border-color:transparent;font-weight:600;padding:14px}
button:disabled{opacity:.5}
.row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid var(--line)}
.row:first-child{border-top:0}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px;background:var(--muted)}
.up .dot{background:var(--up)}.off .dot{background:var(--down)}.st{color:var(--muted);font-size:13px}
.row button{padding:6px 10px;font-size:13px}#msg{margin-top:10px;font-size:14px}
</style></head><body><main>
<h1>Bot-Wächter</h1><div class="sub">Startet deine Bots wieder, auch nachdem du „Alle beenden“ gedrückt hast. Er handelt selbst nicht.</div>
<div class="card"><button class="big" id="all">Alle starten</button><div id="msg" class="sub"></div>
<div class="sub" style="margin-top:8px">Startet nur Bots, die aus sind. Bis alle laufen, dauert es etwa 5 Minuten, weil sie nacheinander starten.</div></div>
<div class="card" id="list">lädt …</div>
<div class="sub" style="margin-top:14px"><a href="#" id="dash" style="color:inherit">Zum Dashboard (Hauptbot)</a></div>
</main><script>
const $=id=>document.getElementById(id);
$("dash").href=location.protocol+"//"+location.hostname+":8000/";
async function load(){try{const r=await fetch("/api/status",{cache:"no-store"});const b=await r.json();
 const n=b.filter(x=>x.up).length;$("all").textContent=n===b.length?"Alle laufen ("+n+"/"+b.length+")":"Alle starten ("+n+"/"+b.length+" laufen)";
 $("list").innerHTML=b.map(x=>`<div class="row ${x.up?"up":(x.status==="aus"?"off":"")}"><span><span class="dot"></span>${x.name} <span class="st">· ${x.port}</span><br><span class="st">${x.status}${x.exists?"":" · Start-Datei fehlt"}</span></span>${x.up||x.status!=="aus"||!x.exists?"":`<button onclick="one(${x.port})">Starten</button>`}</div>`).join("")}
 catch(e){$("list").textContent="Keine Verbindung: "+e.message}}
async function post(u){const r=await fetch(u,{method:"POST",headers:{"x-bot-action":"1"}});return r.json()}
$("all").onclick=async()=>{$("all").disabled=true;$("msg").textContent="Startet …";
 try{const j=await post("/api/start_all");$("msg").textContent=j.started.length?`${j.started.length} Bots gestartet. In ca. 5 Minuten laufen alle.`:"Es war kein Bot aus."}
 catch(e){$("msg").textContent="Hat nicht geklappt: "+e.message}
 setTimeout(()=>$("all").disabled=false,5000);load()};
async function one(p){try{const j=await post("/api/start/"+p);$("msg").textContent="Port "+p+": "+j.result}catch(e){$("msg").textContent=e.message}load()}
load();setInterval(()=>{if(!document.hidden)load()},3000);
</script></body></html>"""


@app.get("/")
def page():
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    if is_up(PORT):
        print(f"\n!!! Der Waechter laeuft schon auf Port {PORT}.\n")
        sys.exit(3)
    log(f"Waechter gestartet: http://localhost:{PORT}" + ("  (vom Handy ueber Tailscale mit Passwort)" if REMOTE else ""))
    uvicorn.run(app, host="0.0.0.0" if REMOTE else "127.0.0.1", port=PORT, log_level="warning")
