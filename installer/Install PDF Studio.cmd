@echo off
rem Double-click launcher. The work is in install.ps1 beside this file; this
rem wrapper only exists so nobody has to know how to run a PowerShell script.
setlocal
set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%install.ps1"
if errorlevel 1 (
  echo.
  echo Something went wrong. The messages above say what.
  pause
)
endlocal
