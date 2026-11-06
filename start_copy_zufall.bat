@echo off
cd /d "%~dp0"
title Trading Bot - Copy Zufall
if not exist .venv\Scripts\python.exe (
  echo Bitte zuerst einmal start.bat ausfuehren, das richtet Python ein.
  pause
  exit /b
)
rem Der Copy-Bot braucht zwei zusaetzliche Pakete (einmalig)
.venv\Scripts\python -c "import httpx, tzdata" 2>nul || .venv\Scripts\python -m pip install -q httpx tzdata
rem Startet etwas spaeter, damit nicht alle Bots gleichzeitig Hyperliquid abfragen
timeout /t 180 /nobreak >nul

:loop
.venv\Scripts\python copybot\run_copybot.py zufall
if errorlevel 3 goto ende
echo.
echo Copy Zufall wurde beendet oder ist abgestuerzt. Automatischer Neustart in 30 Sekunden.
echo Zum endgueltigen Beenden dieses Fenster schliessen.
timeout /t 30 /nobreak >nul
goto loop

:ende
pause
