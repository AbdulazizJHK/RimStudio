@echo off
rem Double-click me. This only exists because Windows refuses to run a
rem downloaded .ps1 by double-click; it runs install.ps1 with that block lifted
rem for this one call only, which changes nothing on the machine.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
