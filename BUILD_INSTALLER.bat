@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Build-Windows.ps1"
if errorlevel 1 (
  echo.
  echo BUILD FAILED. See Build-Windows.log for full details.
  if exist "%~dp0Build-Windows.log" type "%~dp0Build-Windows.log"
  pause
  exit /b 1
)
echo.
echo Build completed. See BuildOut folder.
pause
