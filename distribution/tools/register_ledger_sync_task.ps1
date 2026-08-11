# Register the daily 10:00 ledger sync in Windows Task Scheduler.
#
# Run once, from anywhere:
#   powershell -ExecutionPolicy Bypass -File distribution\tools\register_ledger_sync_task.ps1
#
# What the task does every day at 10:00: read the newest
# distribution\ledger\activation_keys_*.xlsx and push its Status column to the
# live D1 database (Replaced / Void codes are retired; see
# sync_ledger_status.py for the policy and how to widen it). Output is
# appended to distribution\ledger\sync_log.txt, which is gitignored with the
# rest of the ledger.
#
# The task runs as the current user, so it uses the same wrangler login this
# machine already deploys with. It only runs when the machine is on; a missed
# 10:00 runs as soon as the machine is next awake (StartWhenAvailable).

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "distribution\ledger\sync_log.txt"
if (-not (Test-Path $python)) { throw "Not found: $python" }

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$python`" -m distribution.tools.sync_ledger_status >> `"$log`" 2>&1`"" `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At 10:00
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "Your Pit Box ledger sync" `
    -Description "Daily 10:00 sync of the activation-key workbook's Status column to the live licence database. Replaced/Void codes stop activating." `
    -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Registered: 'Your Pit Box ledger sync', daily at 10:00."
Write-Host "Log: $log"
Write-Host "Try it now with:  Start-ScheduledTask -TaskName 'Your Pit Box ledger sync'"
