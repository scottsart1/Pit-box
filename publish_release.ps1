# Final notification step for either platform, after download and site deployment.
param(
  [Parameter(Mandatory=$true)][ValidateSet('windows','android')][string]$Platform,
  [Parameter(Mandatory=$true)][string]$Version,
  [Parameter(Mandatory=$true)][string]$Artifact,
  [string]$NotesFile,
  [switch]$NoEmail,
  [string]$Python = '.\.venv\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
$releasePreviousToken = $env:PITBOX_RELEASE_PUBLISH_TOKEN
try {
  if (-not $env:PITBOX_RELEASE_PUBLISH_TOKEN) {
    $releaseCredentialFile = Join-Path $env:LOCALAPPDATA 'YourPitBoxRelease/release-management/access-key.dpapi'
    if (-not (Test-Path -LiteralPath $releaseCredentialFile)) { throw 'Release-management credential is not configured.' }
    $releaseSecureToken = [IO.File]::ReadAllText($releaseCredentialFile) | ConvertTo-SecureString
    $env:PITBOX_RELEASE_PUBLISH_TOKEN = [System.Net.NetworkCredential]::new('', $releaseSecureToken).Password
  }
  $releaseArguments = @('-m', 'distribution.tools.publish_release', '--platform', $Platform, '--version', $Version, '--artifact', $Artifact)
  if ($NotesFile) { $releaseArguments += @('--notes-file', $NotesFile) }
  if ($NoEmail) { $releaseArguments += '--no-email' }
  & $Python @releaseArguments
  if ($LASTEXITCODE -ne 0) { throw 'Release notification publication failed. Do not claim that notifications are live.' }
} finally {
  $env:PITBOX_RELEASE_PUBLISH_TOKEN = $releasePreviousToken
  $releaseSecureToken = $null
}
