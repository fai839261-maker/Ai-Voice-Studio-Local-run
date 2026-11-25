@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Build-Windows.ps1" -Offline
if errorlevel 1 (
  echo.
  echo OFFLINE BUILD FAILED. See Build-Windows.log.
  pause
  exit /b 1
)
echo.
echo Offline update installer created in BuildOut.
pause
