param(
    [string]$TaskName = 'LinkenSphereWarmup',
    [string]$ScriptDir = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'

$runCmd = Join-Path $ScriptDir 'run.bat'
if (-not (Test-Path $runCmd)) {
    Write-Host "[ERROR] Не найден $runCmd"
    exit 1
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

$action = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument "/c `"$runCmd`"" `
    -WorkingDirectory $ScriptDir

$trigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours 1)

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
Write-Host "[OK] Задача '$TaskName' создана."
Write-Host " - Первый запуск через минуту, далее каждый час."
Write-Host " - Окно cmd будет видно."
Write-Host " - Работает только когда ты залогинен в Windows."
Write-Host ""
Write-Host "Проверить сейчас: Win+R -> taskschd.msc -> Task Scheduler Library"
Write-Host "                  -> $TaskName -> правой кнопкой -> Run."
