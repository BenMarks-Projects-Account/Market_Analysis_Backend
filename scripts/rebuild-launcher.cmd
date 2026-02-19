@echo off
:: Double-click friendly wrapper for rebuild-launcher.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rebuild-launcher.ps1" -Launch
pause
