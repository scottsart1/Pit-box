@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Pit Wall is not installed. Run install_windows.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pitwall.main
pause
