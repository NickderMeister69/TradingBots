@echo off
title Bots beenden
echo Beendet alle Trading Bots (auch ihre Fenster, damit sie nicht von selbst neu starten).
echo Offene Trades bleiben gespeichert und laufen beim naechsten Start weiter.
echo.
taskkill /F /T /FI "WINDOWTITLE eq Trading Bot*" >nul 2>&1
timeout /t 2 /nobreak >nul
taskkill /F /IM python.exe >nul 2>&1
tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if errorlevel 1 (echo Alle Bots sind beendet.) else (echo Achtung: Es laeuft noch ein Python-Programm. Bitte die Bot-Fenster von Hand schliessen.)
echo.
echo Wieder starten: alle_starten.bat
pause
