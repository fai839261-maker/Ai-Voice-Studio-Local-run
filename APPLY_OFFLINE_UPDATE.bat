@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0Apply-Offline-Update.ps1"
if errorlevel 1 (
  echo Offline update build failed. See the selected project's Build-Windows.log.
  echo If build packages are missing, run BUILD_INSTALLER.bat in that project while online.
  pause
  exit /b 1
)
echo Check the selected project's BuildOut folder for the new installer.
pause
