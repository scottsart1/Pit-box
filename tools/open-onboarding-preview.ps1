param([switch]$NoBrowser)

# Run the development frontend in an isolated local profile. Nothing is installed.
$ErrorActionPreference = 'Stop'
$previewRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$previewWorkspace = Split-Path -Parent $previewRepo
$previewPython = Join-Path $previewWorkspace 'qa-venv/Scripts/python.exe'
$previewData = Join-Path $previewWorkspace 'onboarding-preview-data'
if (-not (Test-Path -LiteralPath $previewPython -PathType Leaf)) {
    throw "Preview Python environment is missing: $previewPython"
}

# These changes apply only to this launcher's process, not Windows settings.
# Do not inherit the installed app's keys, data paths, ports or custom endpoints.
$previewVariableNames = @((Get-ChildItem Env: | Where-Object {
    $_.Name -like 'PITWALL_*' -or $_.Name -in @(
        'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'DEEPSEEK_API_KEY',
        'ANTHROPIC_API_KEY', 'KIMI_API_KEY', 'MOONSHOT_API_KEY', 'CUSTOM_LLM_API_KEY'
    )
}).Name)
foreach ($previewVariable in $previewVariableNames) {
    [Environment]::SetEnvironmentVariable($previewVariable, $null, 'Process')
}

New-Item -ItemType Directory -Path $previewData -Force | Out-Null
$env:PYTHONPATH = Join-Path $previewRepo 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PITWALL_DATA_DIR = $previewData
$env:PITWALL_ENV_FILE = Join-Path $previewData '.env'
$env:PITWALL_STATIC_DIR = Join-Path $previewRepo 'static'
$env:PITWALL_WEB_HOST = '127.0.0.1'
$env:PITWALL_WEB_PORT = '18004'
$env:PITWALL_WEB_LAN_ACCESS = 'false'
$env:PITWALL_UDP_BIND_HOST = '0.0.0.0'
$env:PITWALL_UDP_PORT = '20784'
$env:PITWALL_RAW_CAPTURE = 'off'
$env:PITWALL_NATIVE_VOICE = 'false'
$env:PITWALL_WAKE_ENABLED = 'false'
$env:PITWALL_PROACTIVE_ENABLED = 'true'
$env:PITWALL_DB_MAINTENANCE_ON_START = 'false'
$env:PITWALL_OPEN_BROWSER = if ($NoBrowser) { 'false' } else { 'true' }

Write-Host 'ONBOARDING PREVIEW - separate data, microphone off, UDP port 20784.'
Write-Host 'Step 2 shows this device IP and port for optional PS5 telemetry testing.'
Write-Host 'Open http://127.0.0.1:18004/#settings, then Open setup walkthrough.'
Write-Host 'You can skip every step. This does not upgrade the installed app.'
Write-Host 'Use Quit Your Pit Box in the preview or Ctrl+C here to stop it.'
Set-Location -LiteralPath $previewData
& $previewPython -m pitwall.main
exit $LASTEXITCODE
