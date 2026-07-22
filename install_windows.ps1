$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Write-Host "Python launcher not found. Install 64-bit Python 3.11 or 3.12 from python.org and enable the Python launcher." -ForegroundColor Red
  Read-Host "Press Enter to close"
  exit 1
}

$selected = $null
foreach ($version in @("-3.12", "-3.11")) {
  # Use cmd.exe for this probe so a missing runtime cannot terminate the
  # PowerShell installer when ErrorActionPreference is Stop.
  $probe = "py $version -c `"import sys; print(sys.version)`" >nul 2>&1"
  & cmd.exe /d /c $probe
  if ($LASTEXITCODE -eq 0) {
    $selected = $version
    break
  }
}

if (-not $selected) {
  Write-Host "The Python launcher is installed, but Python 3.11 or 3.12 is not." -ForegroundColor Red
  Write-Host "Install the 64-bit Python 3.12 runtime, reopen PowerShell, and run this script again." -ForegroundColor Red
  Read-Host "Press Enter to close"
  exit 1
}

Write-Host "Creating the virtual environment with Python $selected..." -ForegroundColor Cyan
& py $selected -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
}

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
  Write-Host "Installation completed, but the self-tests failed. Copy the output before closing this window." -ForegroundColor Red
  Read-Host "Press Enter to close"
  exit 1
}

Write-Host "Pit Wall 3.3.0 installed and tested successfully." -ForegroundColor Green
Write-Host "Next: edit .env, run the firewall script as Administrator, then start_pitwall.bat." -ForegroundColor Green
Read-Host "Press Enter to close"
