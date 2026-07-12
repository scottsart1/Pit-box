$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  Write-Host "No existing Pit Wall virtual environment was found. Running the full installer instead." -ForegroundColor Yellow
  & .\install_windows.ps1
  exit $LASTEXITCODE
}

Write-Host "Updating Pit Wall in the existing virtual environment..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

Write-Host "Compiling source..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m compileall -q .\src
if ($LASTEXITCODE -ne 0) {
  Write-Host "Compilation failed. Copy the error output before closing." -ForegroundColor Red
  Read-Host "Press Enter to close"
  exit 1
}

Write-Host "Running self-tests..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) {
  Write-Host "Update installed, but self-tests failed. Copy the output before closing." -ForegroundColor Red
  Read-Host "Press Enter to close"
  exit 1
}

Write-Host "Pit Wall 3.1.0 updated and tested successfully." -ForegroundColor Green
Write-Host "Your .env and %USERPROFILE%\PitWallData database were not changed." -ForegroundColor Green
Read-Host "Press Enter to close"
