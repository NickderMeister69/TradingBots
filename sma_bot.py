"""Startet den SMA-Kreuz-Bot (Strategie aus dem Video) mit dem neuen Dashboard. Port 8017.
Aufruf: start_sma.bat  (oder: .venv\\Scripts\\python sma_bot.py)"""
import asyncio
import os
import socket
import sys
import threading
import time

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from bot import board as BD
from bot.dashboard import FAIL_WINDOW, LOCK_SECONDS, MAX_FAILS, client_kind, password_ok
from bot.notify import Notifier
from bot.sma_bot import SmaBot

load_dotenv()
PORT = 8017
NAME = "SMA-Kreuz (Video)"
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
REMOTE = len(PASSWORD) >= 10


class Tee:
    def __init__(self, stream, path):
        self.stream, self.path = stream, path

    def write(self, text):
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
        try:
            if self.stream:
                self.stream.write(text)
        except (OSError, ValueError):
            self.stream = None
        return len(text)

    def flush(self):
        try:
            if self.stream:
                self.stream.flush()
        except (OSError, ValueError):
            self.stream = None

    def isatty(self):
        return False


os.makedirs("data_sma", exist_ok=True)
sys.stdout = Tee(sys.stdout, os.path.join("data_sma", "bot.log"))
sys.stderr = Tee(sys.stderr, os.path.join("data_sma", "bot.log"))

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    if s.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"\n!!! Es laeuft bereits ein Bot auf Port {PORT}. Dieser Start wird abgebrochen.\n")
        sys.exit(3)

bot = SmaBot("data_sma", Notifier(print, prefix=NAME), PORT, NAME)
threading.Thread(target=bot.run, daemon=True).start()
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


@app.get("/")
def page():
    return HTMLResponse(BD.board_html(), headers={"Cache-Control": "no-cache"})


@app.get("/klassisch")
def klassisch():
    return RedirectResponse("/")


@app.get("/board/lwc.js")
def lwc():
    return Response(BD.lwc_js(), media_type="application/javascript", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/board")
async def api_board(symbol: str = ""):
    return JSONResponse(await asyncio.to_thread(bot.board))


@app.get("/api/board/candles")
def api_candles(symbol: str = ""):
    return JSONResponse(bot.candle_data())


@app.post("/api/board/settings")
async def api_settings(request: Request):
    return {"ok": True, "changed": bot.update_settings(await request.json())}


@app.post("/api/board/reset")
async def api_reset(request: Request):
    bot.reset((await request.json()).get("start"))
    return {"ok": True}


@app.get("/api/kurz")
def kurz():
    return JSONResponse(bot.overview())


@app.get("/alle")
def alle(request: Request):
    host = request.url.hostname or "localhost"
    return RedirectResponse(f"http://{host}:8000/alle")


@app.post("/api/pause")
def pause():
    bot.pause()
    return {"ok": True}


@app.post("/api/resume")
def resume():
    bot.resume()
    return {"ok": True}


def _exit_soon(code, msg):
    bot._log(msg, "warn")

    def bye():
        time.sleep(1.5)
        os._exit(code)
    threading.Thread(target=bye, daemon=True).start()


@app.post("/api/neu_laden")
def neu_laden():
    _exit_soon(1, "Neu laden über die Übersicht. In ca. 1 Minute wieder da.")
    return {"ok": True}


@app.post("/api/beenden")
def beenden():
    _exit_soon(3, "Beendet über die Übersicht. Wieder starten nur am PC (alle_starten.bat).")
    return {"ok": True}


if __name__ == "__main__":
    print(f"\n{NAME}: http://localhost:{PORT}\n")
    uvicorn.run(app, host="0.0.0.0" if REMOTE else "127.0.0.1", port=PORT, log_level="warning")
