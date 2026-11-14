@echo off
rem Double-click me. This only exists because Windows refuses to run a
rem downloaded .ps1 by double-click; it runs install.ps1 with that block lifted
rem for this one call only, which changes no policy on the machine.
setlocal
cd /d "%~dp0"
rem Full path: a PATH without System32's PowerShell folder is rare but real.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
