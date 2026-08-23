# Release Your Pit Box: test, build the installer, publish it, then the site.
#
# The one script for the whole release, in the only safe order. The installer
# must reach R2 BEFORE the site deploys, because the site describes the
# current build - deploying the page first would advertise features the
# download does not have yet.
#
# Run it by double-clicking release_windows.bat, or from PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\release_windows.ps1
#
# Every step stops the release on failure and says which step died. A full
# transcript is written next to this script as release_log.txt (gitignored).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Start-Transcript -Path (Join-Path $PSScriptRoot "release_log.txt") -Force | Out-Null

function Step([string]$Name, [scriptblock]$Body) {
  Write-Host ""
  Write-Host ("== " + $Name) -ForegroundColor Cyan
  & $Body
  if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {
    throw "FAILED at step: $Name (exit $LASTEXITCODE). See release_log.txt; distribution\HANDOVER.md documents the known traps."
  }
}

# wrangler if installed, npx otherwise. Both use the Cloudflare login already
# stored on this machine; if a browser opens asking to authorise, approve it.
function Invoke-Wrangler {
  if (Get-Command wrangler -ErrorAction SilentlyContinue) { wrangler @args }
  elseif (Get-Command npx -ErrorAction SilentlyContinue) { npx --yes wrangler @args }
  else { throw "Neither wrangler nor npx is available. Install Node.js, then re-run." }
}

try {
  if (-not (Test-Path ".git")) { throw "This script must sit in the Pit-box repository folder." }
  if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "No .venv found. Run install_windows.ps1 first." }
  $python = ".\.venv\Scripts\python.exe"

  # A fresh clone is missing two gitignored things previous releases relied
  # on. Failing here, with names, beats twelve confusing test failures later.
  if (-not (Test-Path "distribution\.secrets\signing_key.ed25519")) {
    throw "distribution\.secrets\signing_key.ed25519 is missing. Copy the .secrets folder from your previous Pit-box clone - the licensing tests sign with the real production key."
  }

  Step "Pull the release commit" {
    git pull
    $branch = (git branch --show-current)
    if ($branch -ne "main") { throw "On branch '$branch'. Release from main: git checkout main" }
  }

  Step "Install dependencies (including packaging tools)" {
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -e ".[dev]" --quiet
    & $python -m pip install pyinstaller openpyxl cryptography --quiet
  }

  Step "Compile and run the full test suite" {
    & $python -m compileall -q .\src
    & $python -m pytest -q
  }

  $version = & $python -c "import sys; sys.path.insert(0,'src'); import pitwall; print(pitwall.__version__)"
  Write-Host "Releasing version $version" -ForegroundColor Green

  Step "Build the Windows installer" {
    & $python -m distribution.packaging.build --installer
  }

  $installer = Join-Path $env:LOCALAPPDATA "PitWallBuild\artifacts\PitWall-Setup.exe"
  if (-not (Test-Path $installer)) {
    throw "The build reported success but $installer does not exist. Do not deploy the site."
  }
  if (((Get-Date) - (Get-Item $installer).LastWriteTime).TotalMinutes -gt 30) {
    throw "$installer is more than 30 minutes old - that is a stale artifact from an earlier build, not this one."
  }
  $bytes = (Get-Item $installer).Length
  $sha = (Get-FileHash $installer -Algorithm SHA256).Hash
  Write-Host "Installer: $bytes bytes, SHA-256 $sha" -ForegroundColor Green

  Step "Upload the installer to R2 (before the site, always)" {
    Invoke-Wrangler r2 object put pitwall-downloads/PitWall-Setup.exe --file "$installer" --content-type application/vnd.microsoft.portable-executable --remote
  }

  Step "Verify the uploaded bytes match the build" {
    $check = Join-Path $env:TEMP "r2-release-check.exe"
    Invoke-Wrangler r2 object get pitwall-downloads/PitWall-Setup.exe --file "$check" --remote
    $remote = (Get-FileHash $check -Algorithm SHA256).Hash
    Remove-Item $check -Force -ErrorAction SilentlyContinue
    if ($remote -ne $sha) { throw "R2 round-trip hash mismatch: uploaded $sha but the bucket serves $remote. Do not deploy the site." }
    Write-Host "R2 round-trip verified: buyers get exactly this build." -ForegroundColor Green
  }

  Step "Build the site" {
    & $python -m distribution.website.build_site
  }

  Step "Deploy the site to production" {
    Push-Location distribution\website
    try {
      # --branch main is load-bearing: without it, Pages files the deployment
      # under the local git branch, and anything but the production branch
      # becomes a PREVIEW that never reaches yourpitbox.com.
      Invoke-Wrangler pages deploy _site --project-name pitwall --branch main
    }
    finally { Pop-Location }
  }

  Write-Host ""
  Write-Host "Release $version is live." -ForegroundColor Green
  Write-Host "Check https://yourpitbox.com shows the new content, and keep the SHA-256 with your release notes:"
  Write-Host "  $sha"
}
catch {
  Write-Host ""
  Write-Host $_.Exception.Message -ForegroundColor Red
  Write-Host "Nothing after the failed step was run. Fix the cause and re-run this script; completed steps are safe to repeat." -ForegroundColor Yellow
}
finally {
  Stop-Transcript | Out-Null
  Read-Host "Press Enter to close"
}
