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

# Migrate older DeepSeek-era settings while preserving keys and unrelated values.
$envPath = (Resolve-Path .env)
$envText = [System.IO.File]::ReadAllText($envPath)
function Set-PitWallEnvValue([string]$Text, [string]$Name, [string]$Value) {
  $pattern = "(?m)^[ \t]*" + [regex]::Escape($Name) + "[ \t]*=.*$"
  $replacement = "$Name=$Value"
  if ([regex]::IsMatch($Text, $pattern)) {
    return [regex]::Replace($Text, $pattern, $replacement)
  }
  if ($Text.Length -gt 0 -and -not $Text.EndsWith("`n")) { $Text += "`r`n" }
  return $Text + $replacement + "`r`n"
}
function Migrate-PitWallEnvValue([string]$Text, [string]$Name, [string]$OldValue, [string]$NewValue) {
  $pattern = "(?m)^[ \t]*" + [regex]::Escape($Name) + "[ \t]*=[ \t]*" + [regex]::Escape($OldValue) + "[ \t]*$"
  if ([regex]::IsMatch($Text, $pattern)) {
    return [regex]::Replace($Text, $pattern, "$Name=$NewValue")
  }
  $present = "(?m)^[ \t]*" + [regex]::Escape($Name) + "[ \t]*="
  if (-not [regex]::IsMatch($Text, $present)) {
    if ($Text.Length -gt 0 -and -not $Text.EndsWith("`n")) { $Text += "`r`n" }
    return $Text + "$Name=$NewValue`r`n"
  }
  return $Text
}
$updatedEnvText = Set-PitWallEnvValue $envText "PITWALL_LLM_PROVIDER" "openai"
$updatedEnvText = Set-PitWallEnvValue $updatedEnvText "PITWALL_LLM_FALLBACK_PROVIDER" "none"
$updatedEnvText = Set-PitWallEnvValue $updatedEnvText "PITWALL_MODEL" "gpt-5.6"
$updatedEnvText = Migrate-PitWallEnvValue $updatedEnvText "PITWALL_PTT_RELEASE_MODE" "silence" "explicit_or_silence"
$updatedEnvText = Migrate-PitWallEnvValue $updatedEnvText "PITWALL_PTT_RELEASE_IGNORE_MS" "450" "120"
$updatedEnvText = Migrate-PitWallEnvValue $updatedEnvText "PITWALL_PTT_SILENCE_RELEASE_S" "1.15" "2.20"
$updatedEnvText = Migrate-PitWallEnvValue $updatedEnvText "PITWALL_PTT_SPEECH_RMS" "220" "150"
$updatedEnvText = Migrate-PitWallEnvValue $updatedEnvText "PITWALL_PTT_RELEASE_TAIL_S" "" "0.20"
if ($updatedEnvText -ne $envText) {
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($envPath, $updatedEnvText, $utf8NoBom)
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

Write-Host "Pit Wall 3.6.1 installed and tested successfully." -ForegroundColor Green
Write-Host "Next: edit .env, run the firewall script as Administrator, then start_pitwall.bat." -ForegroundColor Green
Read-Host "Press Enter to close"
