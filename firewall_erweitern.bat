@echo off
rem Gibt die Dashboards 8000-8030 in der Windows-Firewall frei, NUR fuer Tailscale-Geraete (100.64.0.0/10).
net session >nul 2>&1
if errorlevel 1 (
  echo Frage nach Administrator-Rechten ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="Tradebot Dashboard (Tailscale)" >nul 2>&1
netsh advfirewall firewall add rule name="Tradebot Dashboard (Tailscale)" dir=in action=allow protocol=TCP localport=8000-8030 remoteip=100.64.0.0/10 profile=any
echo.
echo Fertig: Ports 8000-8030 fuer Tailscale freigegeben. Dein Passwort bleibt wie es ist.
pause
