# Release Your Pit Box: test, build the installer, publish it, then the site.
#
# The one script for the whole release, in the only safe order. The installer
# must reach R2, and the Worker that serves it must be deployed, BEFORE the
# site deploys: the site describes the current build and links straight to
# the download, so deploying the page first would advertise a file that is
# not there yet.
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

$ActivationApi = "https://pitwall-activation.sarthakvij123450.workers.dev"

function Step([string]$Name, [scriptblock]$Body) {
  Write-Host ""
  Write-Host ("== " + $Name) -ForegroundColor Cyan
  try { & $Body }
  catch {
    throw "FAILED at step: $Name. $($_.Exception.Message) See release_log.txt; distribution\HANDOVER.md documents the known traps."
  }
}

# Native programs (git, python, wrangler) do not throw when they fail;
# $ErrorActionPreference only governs cmdlets. Worse, the next native command
# in the same block overwrites $LASTEXITCODE, so a check at the end of a
# multi-command step only ever saw the last command. Every native call
# therefore goes through here and is checked the moment it returns.
function Run([string]$What, [scriptblock]$Command) {
  $global:LASTEXITCODE = 0
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)." }
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

  Step "Check the branch" {
    $branch = & git branch --show-current
    if ($LASTEXITCODE -ne 0) { throw "git branch failed (exit $LASTEXITCODE)." }
    if ($branch -ne "main") { throw "On branch '$branch'. Release from main: git checkout main" }
  }

  Step "Pull the release commit" {
    Run "git pull" { git pull }
  }

  Step "Install dependencies (including packaging tools)" {
    Run "pip upgrade" { & $python -m pip install --upgrade pip --quiet }
    Run "pip install .[dev]" { & $python -m pip install -e ".[dev]" --quiet }
    Run "pip install packaging tools" { & $python -m pip install pyinstaller openpyxl cryptography --quiet }
  }

  Step "Compile and run the full test suite" {
    Run "compileall" { & $python -m compileall -q .\src }
    Run "pytest" { & $python -m pytest -q }
  }

  $version = & $python -c "import sys; sys.path.insert(0,'src'); import pitwall; print(pitwall.__version__)"
  if ($LASTEXITCODE -ne 0 -or -not $version) {
    throw "Could not read the version from src\pitwall\__init__.py (exit $LASTEXITCODE)."
  }
  Write-Host "Releasing version $version" -ForegroundColor Green

  Step "Build the Windows installer" {
    Run "build.py --installer" { & $python -m distribution.packaging.build --installer }
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
    Run "wrangler r2 object put" {
      Invoke-Wrangler r2 object put pitwall-downloads/PitWall-Setup.exe --file "$installer" --content-type application/vnd.microsoft.portable-executable --remote
    }
  }

  Step "Verify the uploaded bytes match the build" {
    $check = Join-Path $env:TEMP "r2-release-check.exe"
    Run "wrangler r2 object get" {
      Invoke-Wrangler r2 object get pitwall-downloads/PitWall-Setup.exe --file "$check" --remote
    }
    $remote = (Get-FileHash $check -Algorithm SHA256).Hash
    Remove-Item $check -Force -ErrorAction SilentlyContinue
    if ($remote -ne $sha) {
      # wrangler reads can serve a stale object long after a successful put
      # (seen on the 4.6.1 and 4.8.0 releases). The trusted read is the
      # production path: the Worker's /installer route, or the R2 object in
      # the Cloudflare dashboard. Warn, do not abort the release on an
      # untrusted reader.
      Write-Host "WARNING: wrangler read back $remote, not $sha. wrangler reads are known to serve stale objects - verify via $ActivationApi/installer or the Cloudflare dashboard before trusting either hash." -ForegroundColor Yellow
    } else {
      Write-Host "R2 round-trip verified: downloads get exactly this build." -ForegroundColor Green
    }
  }

  # The Worker is what serves /installer and takes /subscribe signups. The
  # site links to both, so it has to be current before the page goes out.
  Step "Deploy the activation Worker" {
    Push-Location distribution\activation-server
    try { Run "wrangler deploy" { Invoke-Wrangler deploy } }
    finally { Pop-Location }
  }

  # CREATE TABLE IF NOT EXISTS: safe to run on every release. --yes answers
  # wrangler's "run this on the remote database?" confirmation.
  Step "Apply the D1 migration for the mailing list" {
    Push-Location distribution\activation-server
    try {
      Run "wrangler d1 execute (0002_subscribers)" {
        Invoke-Wrangler d1 execute pitwall-licenses --remote --yes --file migrations/0002_subscribers.sql
      }
    }
    finally { Pop-Location }
  }

  Step "Confirm the free download answers before the site points at it" {
    $probe = Invoke-WebRequest -Uri "$ActivationApi/installer" -Method Head -UseBasicParsing
    $length = [int64]($probe.Headers["Content-Length"] | Select-Object -First 1)
    if ($probe.StatusCode -ne 200 -or $length -lt 1MB) {
      throw "$ActivationApi/installer answered $($probe.StatusCode) with $length bytes. The Worker or the R2 object is wrong; do not deploy the site."
    }
    Write-Host "Worker serves the installer: $length bytes." -ForegroundColor Green
  }

  Step "Build the site" {
    Run "build_site" { & $python -m distribution.website.build_site }
  }

  Step "Deploy the site to production" {
    Push-Location distribution\website
    try {
      # --branch main is load-bearing: without it, Pages files the deployment
      # under the local git branch, and anything but the production branch
      # becomes a PREVIEW that never reaches yourpitbox.com.
      Run "wrangler pages deploy" { Invoke-Wrangler pages deploy _site --project-name pitwall --branch main }
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
