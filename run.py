"""Startet Bot und Dashboard.
Aufruf: python run.py                     -> config.yaml
        python run.py config_bybit.yaml   -> zweiter Bot mit eigener Config (eigener Port + data_dir!)"""
import os
import socket
import sys
import threading

import uvicorn
import yaml
from dotenv import load_dotenv

from bot.engine import Engine
from bot.dashboard import create_app

class SafeStream:
    """Schreibt ins Konsolenfenster UND in eine Logdatei. Ist das Fenster nicht erreichbar
    (Windows-Fehler 233 'Kein Prozess ist am anderen Ende der Pipe'), wird nur die Datei
    beschrieben, statt dass der Bot abstuerzt."""

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


def _quiet_windows_disconnects():
    """Windows: Wenn ein Browser/iPhone die Dashboard-Verbindung hart trennt, schreibt Python einen harmlosen
    'WinError 10054'-Fehlerblock ins Fenster. Der Bot laeuft dabei normal weiter. Hier wird nur diese Meldung stummgeschaltet."""
    if sys.platform != "win32":
        return
    try:
        from asyncio import proactor_events
        orig = proactor_events._ProactorBasePipeTransport._call_connection_lost

        def safe(self, exc):
            try:
                orig(self, exc)
            except ConnectionResetError:
                pass
        proactor_events._ProactorBasePipeTransport._call_connection_lost = safe
    except Exception:
        pass


_quiet_windows_disconnects()
load_dotenv()
config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
cfg = yaml.safe_load(open(config_path, encoding="utf-8"))

# Ausgaben zusaetzlich in <data_dir>/bot.log (wird bei > 5 MB neu angefangen)
_log_dir = cfg.get("data_dir", "data")
os.makedirs(_log_dir, exist_ok=True)
_log_path = os.path.join(_log_dir, "bot.log")
try:
    if os.path.getsize(_log_path) > 5_000_000:
        os.replace(_log_path, _log_path + ".alt")
except OSError:
    pass
sys.stdout = SafeStream(sys.stdout, _log_path)
sys.stderr = SafeStream(sys.stderr, _log_path)
port = cfg["dashboard_port"]

# Schutz: laeuft schon ein Bot? Dann NICHT starten (sonst wird doppelt gehandelt).
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    if s.connect_ex(("127.0.0.1", port)) == 0:
        print(f"\n!!! Es laeuft bereits ein Bot (Port {port} ist belegt).")
        print("    Schliesse das alte Bot-Fenster oder beende 'Python' im Task-Manager")
        print("    (Strg+Shift+Esc), dann starte neu. Dieser Start wird abgebrochen.\n")
        sys.exit(3)   # 3 = start.bat startet NICHT neu

if cfg["mode"] == "live":
    # Fuer unbeaufsichtigten Betrieb (Autostart): LIVE_CONFIRM=JA in .env, sonst Rueckfrage im Fenster
    if os.getenv("LIVE_CONFIRM", "").strip() == "JA":
        print("\n!!! ECHTGELD-MODUS !!! (bestaetigt ueber LIVE_CONFIRM=JA in .env)\n")
    else:
        print("\n!!! ECHTGELD-MODUS !!! Tippe JA zum Bestaetigen:")
        if input("> ").strip() != "JA":
            print("Abgebrochen.")
            sys.exit(3)

engine = Engine(cfg, config_path)
threading.Thread(target=engine.run, daemon=True).start()
threading.Thread(target=engine.run_prices, daemon=True).start()
if os.path.basename(config_path) == "config.yaml":
    # Wochen-Tagebuch aller Bots per Telegram (Sonntag 19 Uhr), nur vom Hauptbot aus
    from bot.dashboard import OVERVIEW_PORTS
    from bot.journal import Journal
    Journal(engine.notify, OVERVIEW_PORTS, os.path.join(cfg.get("data_dir", "data"), "journal.json"),
            log=lambda m: print(m, flush=True)).start()
    # Bot-Waechter (Port 8020) mitstarten, falls er noch nicht laeuft: damit gehen beendete Bots vom Handy aus wieder an
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            w_up = s.connect_ex(("127.0.0.1", 8020)) == 0
        w_bat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start_waechter.bat")
        if not w_up and sys.platform == "win32" and os.path.exists(w_bat):
            import subprocess
            subprocess.Popen(f'start "" /min "{w_bat}"', shell=True, cwd=os.path.dirname(w_bat))
            print("Bot-Waechter (Port 8020) gestartet.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"Bot-Waechter konnte nicht gestartet werden: {e}", flush=True)
# Fernzugriff nur mit Passwort (fernzugriff_einrichten.bat). Ohne Passwort: nur von diesem PC aus.
remote_pw = os.getenv("DASHBOARD_PASSWORD", "").strip()
if remote_pw and len(remote_pw) < 10:
    print("!!! DASHBOARD_PASSWORD ist kuerzer als 10 Zeichen -> Fernzugriff bleibt AUS.")
    remote_pw = ""
host = "0.0.0.0" if remote_pw else "127.0.0.1"
print(f"\nDashboard: http://localhost:{port}")
if remote_pw:
    ts_ips = []
    try:
        ts_ips = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if ip.startswith("100.")]
    except OSError:
        pass
    print("Fernzugriff (iPhone mit Tailscale): " + (", ".join(f"http://{ip}:{port}" for ip in ts_ips)
          or f"http://<Tailscale-Adresse dieses PCs>:{port}") + "  (nur mit Passwort)")
print()
uvicorn.run(create_app(engine, remote_pw), host=host, port=port, log_level="warning")
