@echo off
cd /d "%~dp0"
title Trading Bot - SMA-Kreuz (Video)
if not exist .venv\Scripts\python.exe (
  echo Bitte zuerst einmal start.bat ausfuehren, das richtet Python ein.
  pause
  exit /b
)
rem Startet etwas spaeter, damit nicht alle Bots gleichzeitig Kurse abfragen
timeout /t 200 /nobreak >nul

:loop
.venv\Scripts\python sma_bot.py
if errorlevel 3 goto ende
echo.
echo SMA-Kreuz-Bot wurde beendet oder ist abgestuerzt. Automatischer Neustart in 30 Sekunden.
echo Zum endgueltigen Beenden dieses Fenster schliessen.
timeout /t 30 /nobreak >nul
goto loop

:ende
pause
