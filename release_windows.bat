@echo off
cd /d "%~dp0"
rem Double-clickable wrapper: runs the whole release with the policy bypass
rem scoped to this one process, exactly as the manual instructions say.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release_windows.ps1"
