@echo off
setlocal
set "SCRIPT=%~dp0package_share.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
pause
