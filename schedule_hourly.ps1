param(
    [string]$TaskName = 'LinkenSphereWarmup',
    [string]$ScriptDir = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'

$runCmd = Join-Path $ScriptDir 'run_api.bat'
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
Write-Host "[OK] Задача '$TaskName' создана."
Write-Host " - Первый запуск через 15 секунд, далее каждые 52 минуты."
Write-Host " - Зовётся run_api.bat -> warmup_api.py (чанковый API-флоу)."
Write-Host " - Окно cmd будет видно."
Write-Host " - Работает только когда ты залогинен в Windows."
Write-Host ""
Write-Host "ВАЖНО: до этой команды должен пройти хотя бы один успешный .\run.bat"
Write-Host "       (UI-инсталляция: login + активация API-порта + импорт сессии)."
Write-Host ""
Write-Host "Проверить сейчас: Win+R -> taskschd.msc -> Task Scheduler Library"
Write-Host "                  -> $TaskName -> правой кнопкой -> Run."
