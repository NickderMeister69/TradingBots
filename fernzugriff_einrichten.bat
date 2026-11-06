@echo off
rem Fernzugriff aufs Dashboard (nur ueber Tailscale, mit Passwort) einrichten.
rem Braucht Administrator-Rechte fuer die Firewall-Regel.
net session >nul 2>&1
if errorlevel 1 (
  echo Frage nach Administrator-Rechten ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
echo.
.venv\Scripts\python.exe fernzugriff_einrichten.py
if errorlevel 1 goto ende
echo.
echo Firewall: Dashboards (Ports 8000-8020) NUR fuer Tailscale-Geraete freigeben ...
netsh advfirewall firewall delete rule name="Tradebot Dashboard (Tailscale)" >nul 2>&1
netsh advfirewall firewall add rule name="Tradebot Dashboard (Tailscale)" dir=in action=allow protocol=TCP localport=8000-8020 remoteip=100.64.0.0/10 profile=any
echo.
echo Fertig. Jetzt alle 3 Bots neu starten.
echo Am iPhone: Tailscale an, dann in Safari http://TAILSCALE-ADRESSE:8000 (Adresse steht in der Tailscale-App).
:ende
echo.
pause
