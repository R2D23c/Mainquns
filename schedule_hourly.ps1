param(
    [string]$TaskName = 'LinkenSphereWarmup',
    [string]$ScriptDir = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'

$runCmd = Join-Path $ScriptDir 'run_api.bat'
if (-not (Test-Path $runCmd)) {
    Write-Host "[ERROR] not found: $runCmd"
    exit 1
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

$action = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument "/c `"$runCmd`"" `
    -WorkingDirectory $ScriptDir

$trigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date).AddSeconds(15) `
    -RepetitionInterval (New-TimeSpan -Minutes 52)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -Compatibility Win8

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host ""
Write-Host "[OK] Task '$TaskName' registered."
Write-Host " - First trigger in 15 seconds, then every 52 minutes."
Write-Host " - Calls run_api.bat. Internal logic:"
Write-Host "     first run (no flags) -> warmup.py (UI install:"
Write-Host "                              login, API port, session import)"
Write-Host "     subsequent           -> warmup_api.py (chunked API flow:"
Write-Host "                              ~100 random URLs from 40k_all_urls.txt)"
Write-Host " - Only runs while you are logged into Windows."
Write-Host ""
Write-Host "You can close all windows and walk away."
Write-Host ""
Write-Host "Check now: Win+R -> taskschd.msc -> Task Scheduler Library"
Write-Host "           -> $TaskName -> right click -> Run."
