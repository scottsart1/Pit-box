$ErrorActionPreference = "Stop"
$rule = Get-NetFirewallRule -DisplayName "Pit Wall F1 UDP 20777" -ErrorAction SilentlyContinue
if (-not $rule) {
  New-NetFirewallRule -DisplayName "Pit Wall F1 UDP 20777" -Direction Inbound -Protocol UDP -LocalPort 20777 -Action Allow -Profile Private
  Write-Host "Windows Firewall rule created for UDP 20777." -ForegroundColor Green
} else { Write-Host "Firewall rule already exists." -ForegroundColor Green }
Read-Host "Press Enter to close"
