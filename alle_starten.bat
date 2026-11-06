@echo off
cd /d "%~dp0"
title Alle Trading Bots starten
netstat -an | find ":8020 " | find "LISTENING" >nul
if errorlevel 1 if exist "start_waechter.bat" start "" /min "%~dp0start_waechter.bat"
netstat -an | find ":8000 " | find "LISTENING" >nul
if not errorlevel 1 (
  echo Es laufen schon Bots. Doppelt starten geht nicht ^(Port belegt^).
  echo Zum Neuladen bitte bots_neu_laden.bat benutzen.
  pause
  exit /b
)
echo Starte alle 21 Bots. Jeder bekommt ein eigenes Fenster ^(unten in der Taskleiste^).
echo Sie starten nacheinander, bis alle laufen dauert es etwa 5 Minuten.
echo.
if exist "start.bat" start "" /min "%~dp0start.bat"
echo   gestartet: start.bat
if exist "start_bybit.bat" start "" /min "%~dp0start_bybit.bat"
echo   gestartet: start_bybit.bat
if exist "start_5min.bat" start "" /min "%~dp0start_5min.bat"
echo   gestartet: start_5min.bat
if exist "start_scanner.bat" start "" /min "%~dp0start_scanner.bat"
echo   gestartet: start_scanner.bat
if exist "start_daytrade.bat" start "" /min "%~dp0start_daytrade.bat"
echo   gestartet: start_daytrade.bat
if exist "start_daytrade_sicher.bat" start "" /min "%~dp0start_daytrade_sicher.bat"
echo   gestartet: start_daytrade_sicher.bat
if exist "start_daytrade_mittel.bat" start "" /min "%~dp0start_daytrade_mittel.bat"
echo   gestartet: start_daytrade_mittel.bat
if exist "start_4h_einfach.bat" start "" /min "%~dp0start_4h_einfach.bat"
echo   gestartet: start_4h_einfach.bat
if exist "start_riskant.bat" start "" /min "%~dp0start_riskant.bat"
echo   gestartet: start_riskant.bat
if exist "start_15min.bat" start "" /min "%~dp0start_15min.bat"
echo   gestartet: start_15min.bat
if exist "start_copybot.bat" start "" /min "%~dp0start_copybot.bat"
echo   gestartet: start_copybot.bat
if exist "start_copy_sicher.bat" start "" /min "%~dp0start_copy_sicher.bat"
echo   gestartet: start_copy_sicher.bat
if exist "start_copy_vorsichtig.bat" start "" /min "%~dp0start_copy_vorsichtig.bat"
echo   gestartet: start_copy_vorsichtig.bat
if exist "start_copy_mutig.bat" start "" /min "%~dp0start_copy_mutig.bat"
echo   gestartet: start_copy_mutig.bat
if exist "start_copy_riskant.bat" start "" /min "%~dp0start_copy_riskant.bat"
echo   gestartet: start_copy_riskant.bat
if exist "start_copy_konzentriert.bat" start "" /min "%~dp0start_copy_konzentriert.bat"
echo   gestartet: start_copy_konzentriert.bat
if exist "start_copy_zufall.bat" start "" /min "%~dp0start_copy_zufall.bat"
echo   gestartet: start_copy_zufall.bat
if exist "start_sma.bat" start "" /min "%~dp0start_sma.bat"
echo   gestartet: start_sma.bat
if exist "start_2prozent.bat" start "" /min "%~dp0start_2prozent.bat"
echo   gestartet: start_2prozent.bat
if exist "start_gold.bat" start "" /min "%~dp0start_gold.bat"
echo   gestartet: start_gold.bat
if exist "start_trend.bat" start "" /min "%~dp0start_trend.bat"
echo   gestartet: start_trend.bat
echo.
echo Fertig. Dieses Fenster kannst du schliessen. Die Bot-Fenster bitte offen lassen.
timeout /t 15
