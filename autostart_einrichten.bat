@echo off
cd /d "%~dp0"
echo Richtet die Trading Bots so ein, dass sie nach jedem Windows-Login automatisch starten.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Startup'); $w=New-Object -ComObject WScript.Shell; foreach($b in @('start.bat','start_bybit.bat','start_5min.bat','start_scanner.bat','start_daytrade.bat','start_daytrade_sicher.bat','start_daytrade_mittel.bat','start_4h_einfach.bat','start_riskant.bat','start_15min.bat','start_copybot.bat','start_copy_sicher.bat','start_copy_vorsichtig.bat','start_copy_mutig.bat','start_copy_riskant.bat','start_copy_konzentriert.bat','start_copy_zufall.bat','start_sma.bat','start_2prozent.bat','start_gold.bat','start_trend.bat','start_waechter.bat')){ if(Test-Path ('%~dp0'+$b)){ $n=@{'start.bat'='Trading Bot';'start_bybit.bat'='Trading Bot Bybit';'start_5min.bat'='Trading Bot 5min';'start_scanner.bat'='Trading Bot Scanner';'start_daytrade.bat'='Trading Bot Daytrade';'start_daytrade_sicher.bat'='Trading Bot Daytrade sicher';'start_daytrade_mittel.bat'='Trading Bot Daytrade mittel';'start_4h_einfach.bat'='Trading Bot 4h einfach';'start_riskant.bat'='Trading Bot Riskant';'start_15min.bat'='Trading Bot 15min';'start_copybot.bat'='Trading Bot Copy-Bot';'start_copy_sicher.bat'='Trading Bot Copy sicher';'start_copy_vorsichtig.bat'='Trading Bot Copy vorsichtig';'start_copy_mutig.bat'='Trading Bot Copy mutig';'start_copy_riskant.bat'='Trading Bot Copy riskant';'start_copy_konzentriert.bat'='Trading Bot Copy konzentriert';'start_copy_zufall.bat'='Trading Bot Copy Zufall';'start_sma.bat'='Trading Bot SMA-Kreuz';'start_2prozent.bat'='Trading Bot 2 Prozent';'start_gold.bat'='Trading Bot Gold';'start_trend.bat'='Trading Bot Trendlinien';'start_waechter.bat'='Bot-Waechter'}[$b]; $s=$w.CreateShortcut($d+'\'+$n+'.lnk'); $s.TargetPath='%~dp0'+$b; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save(); Write-Host ('Autostart angelegt: '+$n) } }"
echo.
echo Energiesparen am Netzteil abschalten (PC soll nicht einschlafen)...
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo Fertig. Bildschirm darf ausgehen, der PC laeuft weiter.
echo Entfernen: autostart_entfernen.bat
pause
