$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  Write-Host "No existing Your Pit Box virtual environment was found. Running the full installer instead." -ForegroundColor Yellow
  & .\install_windows.ps1
  exit $LASTEXITCODE
}

Write-Host "Updating Your Pit Box in the existing virtual environment..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

$modelMigrated = $false
if (Test-Path .env) {
  $envPath = (Resolve-Path .env)
  $envText = [System.IO.File]::ReadAllText($envPath)

  function Set-YourPitBoxEnvValue([string]$Text, [string]$Name, [string]$Value) {
    $pattern = "(?m)^[ \t]*" + [regex]::Escape($Name) + "[ \t]*=.*$"
    $replacement = "$Name=$Value"
    if ([regex]::IsMatch($Text, $pattern)) {
      return [regex]::Replace($Text, $pattern, $replacement)
    }
    if ($Text.Length -gt 0 -and -not $Text.EndsWith("`n")) { $Text += "`r`n" }
    return $Text + $replacement + "`r`n"
  }

  function Migrate-YourPitBoxEnvValue([string]$Text, [string]$Name, [string]$OldValue, [string]$NewValue) {
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

  # 4.7 is multi-provider: PITWALL_LLM_PROVIDER may legitimately be openai,
  # anthropic, deepseek, kimi, custom, or auto, so the provider choice is no
  # longer forced. Only genuinely-legacy values are migrated; a customised
  # PITWALL_MODEL (for example gpt-5.6-terra) is preserved instead of being
  # rewritten on every update.
  $updatedEnvText = Migrate-YourPitBoxEnvValue $envText "PITWALL_LLM_PROVIDER" "none" "openai"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_LLM_FALLBACK_PROVIDER" "auto" "none"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_MODEL" "deepseek-chat" "gpt-5.6-sol"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_MODEL" "deepseek-reasoner" "gpt-5.6-sol"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_MODEL" "gpt-5.6" "gpt-5.6-sol"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_PTT_RELEASE_MODE" "silence" "explicit_or_silence"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_PTT_RELEASE_IGNORE_MS" "450" "120"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_PTT_SILENCE_RELEASE_S" "1.15" "2.20"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_PTT_SPEECH_RMS" "220" "150"
  $updatedEnvText = Migrate-YourPitBoxEnvValue $updatedEnvText "PITWALL_PTT_RELEASE_TAIL_S" "" "0.20"
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

Write-Host "Your Pit Box updated and tested successfully." -ForegroundColor Green
if ($modelMigrated) {
  Write-Host "Migrated legacy engineer-runtime and radio-capture values. API keys, your provider choice, and all unrelated .env values were preserved." -ForegroundColor Green
} else {
  Write-Host "Engineer and radio-capture settings were already current. Your .env values and %USERPROFILE%\PitWallData database were not changed." -ForegroundColor Green
}
Read-Host "Press Enter to close"
