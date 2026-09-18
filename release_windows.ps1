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

param([switch]$AllowUnsignedRelease)

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
#
# $args is copied into $argv before being splatted, and that copy is the whole
# point. Splatting the automatic @args straight into a native command silently
# passes nothing under Windows PowerShell 5.1: wrangler is started with no
# arguments at all, prints its top-level help, and exits 0. Every step here
# then "succeeds" while doing nothing - the 4.9.5 release uploaded no
# installer, deployed no Worker and ran no migration, and still announced
# itself live. PowerShell 7 passes @args correctly, which is why this went
# unnoticed for as long as releases were cut from pwsh.
function Invoke-Wrangler {
  $argv = @($args)
  if ($argv.Count -eq 0) { throw "Invoke-Wrangler called with no arguments." }
  if (Get-Command wrangler -ErrorAction SilentlyContinue) { wrangler @argv }
  elseif (Get-Command npx -ErrorAction SilentlyContinue) { npx --yes wrangler @argv }
  else { throw "Neither wrangler nor npx is available. Install Node.js, then re-run." }
}

try {
  if (-not (Test-Path ".git")) { throw "This script must sit in the Pit-box repository folder." }
  if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "No .venv found. Run install_windows.ps1 first." }
  $python = ".\.venv\Scripts\python.exe"

  Step "Require production signing configuration" {
    if ($AllowUnsignedRelease) {
      Write-Warning "Owner-authorized unsigned direct download. Publish an unsigned notice and SHA-256; never tell users to disable security protections."
    } else {
    Run "signing preflight" { & $python -c "from distribution.packaging.signing import configuration; configuration(required=True)" }
    }
  }

  # Licensing tests generate temporary keys. The production private key is
  # only needed to issue paid licences, never to test or build the app.

  Step "Check the branch" {
    $branch = & git branch --show-current
    if ($LASTEXITCODE -ne 0) { throw "git branch failed (exit $LASTEXITCODE)." }
    if ($branch -ne "main") { throw "On branch '$branch'. Release from main: git checkout main" }
  }

  Step "Pull the release commit" {
    $changes = & git status --porcelain
    if ($LASTEXITCODE -ne 0 -or $changes) { throw "Release requires a clean checkout. Preserve and commit reviewed changes first." }
    Run "git pull" { git pull --ff-only }
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

  Step "Verify the publisher before uploading anything" {
    . (Join-Path $PSScriptRoot 'distribution/packaging/verify-production-signature.ps1')
    $frozenApp = Join-Path (Split-Path (Split-Path $installer)) 'dist/Your Pit Box/Your Pit Box.exe'
    if ($AllowUnsignedRelease) {
      foreach ($artifact in @($installer, $frozenApp)) {
        $signature = Get-AuthenticodeSignature -LiteralPath $artifact
        if ($signature.Status -ne 'NotSigned') {
          Assert-ProductionSignature -Path $artifact -ExpectedThumbprint $env:PITBOX_WINDOWS_CERT_SHA1
        }
      }
      Write-Warning "Publishing under explicit unsigned-release authorization, not as a verified publisher-signed build."
    } else {
    Assert-ProductionSignature -Path $installer -ExpectedThumbprint $env:PITBOX_WINDOWS_CERT_SHA1
    Assert-ProductionSignature -Path $frozenApp -ExpectedThumbprint $env:PITBOX_WINDOWS_CERT_SHA1
    }
  }

  Step "Upload the installer to R2 (before the site, always)" {
    Run "wrangler r2 object put" {
      Invoke-Wrangler r2 object put pitwall-downloads/PitWall-Setup.exe --file "$installer" --content-type application/vnd.microsoft.portable-executable --remote
    }
  }

  Step "Prepare release notification schema before the new Worker" {
    Push-Location distribution\activation-server
    try {
      Run "release metadata schema" { Invoke-Wrangler d1 execute pitwall-licenses --remote --yes --file migrations/0008_release_manifest.sql }
    } finally { Pop-Location }
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
      Write-Host "WARNING: wrangler read back '$remote', not $sha. wrangler reads are known to serve stale objects, and an empty read back usually means the put never happened - see distribution\HANDOVER.md. The release is not allowed to reach the site on this alone: the download gate below fetches and hashes the file a visitor would get." -ForegroundColor Yellow
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

  # CREATE TABLE IF NOT EXISTS / INSERT OR IGNORE: safe to run on every
  # release. --yes answers wrangler's "run this on the remote database?"
  # confirmation.
  Step "Apply the D1 migrations (mailing list, settings)" {
    Push-Location distribution\activation-server
    try {
      Run "wrangler d1 execute (0002_subscribers)" {
        Invoke-Wrangler d1 execute pitwall-licenses --remote --yes --file migrations/0002_subscribers.sql
      }
      Run "wrangler d1 execute (0003_settings)" {
        Invoke-Wrangler d1 execute pitwall-licenses --remote --yes --file migrations/0003_settings.sql
      }
    }
    finally { Pop-Location }
  }

  # The installer just uploaded is a free-edition build, so the site must stop
  # showing the shared activation code that bridged the pre-4.9 installer.
  # This is what turns the bridge off; the Worker reads it on every request.
  Step "Tell the site the installer no longer needs a code" {
    Push-Location distribution\activation-server
    try {
      Run "wrangler d1 execute (installer_needs_code=0)" {
        Invoke-Wrangler d1 execute pitwall-licenses --remote --yes --command "UPDATE settings SET value = '0' WHERE key = 'installer_needs_code'"
      }
    }
    finally { Pop-Location }
  }

  Step "Confirm the free download answers before the site points at it" {
    $probe = Invoke-WebRequest -Uri "$ActivationApi/installer" -Method Head -UseBasicParsing
    $length = [int64]($probe.Headers["Content-Length"] | Select-Object -First 1)
    if ($probe.StatusCode -ne 200) {
      throw "$ActivationApi/installer answered $($probe.StatusCode). The Worker or the R2 object is wrong; do not deploy the site."
    }
    # A size floor is not a check. The 4.9.5 release published a page
    # describing a build the download did not contain: the R2 put had silently
    # done nothing (the stored Cloudflare login had every scope except r2), and
    # the eight-day-old object it left in place was also well over a megabyte,
    # so this gate waved it through. The only check worth making here is the
    # one a visitor makes - fetch the file and hash it.
    if ($length -ne $bytes) {
      throw "$ActivationApi/installer serves $length bytes, not the $bytes just built. The upload did not land; do not deploy the site. distribution\HANDOVER.md lists the causes seen so far."
    }
    $served = Join-Path $env:TEMP "pitwall-release-served.exe"
    Remove-Item $served -Force -ErrorAction SilentlyContinue
    Invoke-WebRequest -Uri "$ActivationApi/installer" -Headers @{Range='bytes=0-'} -OutFile $served -UseBasicParsing
    $servedSha = (Get-FileHash $served -Algorithm SHA256).Hash
    Remove-Item $served -Force -ErrorAction SilentlyContinue
    if ($servedSha -ne $sha) {
      throw "$ActivationApi/installer serves SHA-256 $servedSha, not the $sha just built. Visitors would download a different build than this release; do not deploy the site."
    }
    Write-Host "Download verified: $length bytes, SHA-256 $sha - visitors get exactly this build." -ForegroundColor Green
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
  Step "Publish verified metadata for the in-app update flag" {
    & (Join-Path $PSScriptRoot 'publish_release.ps1') -Platform windows -Version $version -Artifact $installer -Python $python
  }

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
