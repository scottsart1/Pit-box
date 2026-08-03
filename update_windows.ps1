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

$modelMigrated = $false
if (Test-Path .env) {
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
    $modelMigrated = $true
  }
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
  Write-Host "Update installed, but self-tests failed. Copy the output before closing." -ForegroundColor Red
  Read-Host "Press Enter to close"
  exit 1
}

Write-Host "Pit Wall updated and tested successfully." -ForegroundColor Green
if ($modelMigrated) {
  Write-Host "Migrated the engineer runtime and safer radio-capture defaults. API keys and all unrelated .env values were preserved." -ForegroundColor Green
} else {
  Write-Host "OpenAI model and radio-capture settings were already current. Your .env values and %USERPROFILE%\PitWallData database were not changed." -ForegroundColor Green
}
Read-Host "Press Enter to close"
