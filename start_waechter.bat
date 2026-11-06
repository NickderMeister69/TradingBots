@echo off
cd /d "%~dp0"
title Bot-Waechter
if not exist .venv\Scripts\python.exe (
  echo Bitte zuerst einmal start.bat ausfuehren, das richtet Python ein.
  pause
  exit /b
)

:loop
.venv\Scripts\python waechter.py
if errorlevel 3 goto ende
echo.
echo Waechter wurde beendet. Automatischer Neustart in 30 Sekunden.
timeout /t 30 /nobreak >nul
goto loop

:ende
timeout /t 10
