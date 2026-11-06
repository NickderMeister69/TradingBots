@echo off
cd /d "%~dp0"
title Trading Bot - Bybit EU
rem Teilt sich die Python-Umgebung mit start.bat. Kurz warten, falls start.bat gerade installiert.
if not exist .venv\Scripts\python.exe (
  echo Bitte zuerst einmal start.bat ausfuehren, das richtet Python ein.
  pause
  exit /b
)
timeout /t 45 /nobreak >nul

:loop
.venv\Scripts\python run.py config_bybit.yaml
if errorlevel 3 goto ende
echo.
echo Bybit-Bot wurde beendet oder ist abgestuerzt. Automatischer Neustart in 30 Sekunden.
echo Zum endgueltigen Beenden dieses Fenster schliessen.
timeout /t 30 /nobreak >nul
goto loop

:ende
pause
