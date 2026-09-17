# Dot-source this guard. It performs read-only Authenticode verification.
function Assert-ProductionSignature {
  param([Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$ExpectedThumbprint)
  if ($ExpectedThumbprint -notmatch '^[0-9A-Fa-f]{40}$') {
    throw 'Configure the expected publisher certificate thumbprint before a public release.'
  }
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Release artifact is missing.' }
  $signature = Get-AuthenticodeSignature -LiteralPath $Path
  if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
    throw 'Public deployment blocked: the artifact does not have a valid trusted Authenticode signature.'
  }
  if ($signature.SignerCertificate.Thumbprint -ne $ExpectedThumbprint) {
    throw 'Public deployment blocked: the artifact publisher does not match the configured release identity.'
  }
  if ($null -eq $signature.TimeStamperCertificate) {
    throw 'Public deployment blocked: a trusted timestamp is required.'
  }
}
