@echo off
cd /d "%~dp0"
title Trading Bot
if not exist .venv ( python -m venv .venv )
.venv\Scripts\python -m pip install -q -U -r requirements.txt

:loop
.venv\Scripts\python run.py
if errorlevel 3 goto ende
echo.
echo Bot wurde beendet oder ist abgestuerzt. Automatischer Neustart in 30 Sekunden.
echo Zum endgueltigen Beenden dieses Fenster schliessen.
timeout /t 30 /nobreak >nul
goto loop

:ende
pause
