@echo off
cd /d "%~dp0"
title Trading Bot - Gold Tag
if not exist .venv\Scripts\python.exe (
  echo Bitte zuerst einmal start.bat ausfuehren, das richtet Python ein.
  pause
  exit /b
)
timeout /t 250 /nobreak >nul

:loop
.venv\Scripts\python run.py config_gold.yaml
if errorlevel 3 goto ende
echo.
echo Gold-Bot wurde beendet oder ist abgestuerzt. Automatischer Neustart in 30 Sekunden.
echo Zum endgueltigen Beenden dieses Fenster schliessen.
timeout /t 30 /nobreak >nul
goto loop

:ende
pause
