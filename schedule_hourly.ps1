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

# Интервал 45 мин подобран под реальную длительность цикла warmup_api.py
# (~37 мин: 15 чанков × 7 URL × view_depth 3). С запасом 8 мин на
# variance (медленный VPS, лагающие страницы). НЕ ставим 40 мин: при
# MultipleInstances=IgnoreNew любой цикл >40 мин съедает следующий
# триггер и эффективный интервал удваивается до 80 мин.
$timeTrigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date).AddSeconds(15) `
    -RepetitionInterval (New-TimeSpan -Minutes 45)

# Дополнительный триггер на boot — страховка против известной проблемы
# Windows Task Scheduler: при UseUnifiedSchedulingEngine=True + Once +
# RepetitionInterval после reboot, расписание иногда "забывается" и
# следующий tick откладывается на 2-3 часа вместо положенных 45 мин.
# Наблюдалось эмпирически: после reboot в 02:35 ожидаемые тики в 03:02
# и 03:47 не сработали (NumberOfMissedRuns=3), потребовался ручной
# schtasks /run. AtStartup гарантирует что после ЛЮБОГО boot задача
# гарантированно дёргается в течение ~1 минуты после старта системы.
# MultipleInstances=IgnoreNew защищает от дублирования если AtStartup
# совпадает с RepetitionInterval boundary.
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
# Маленькая задержка чтобы LS (запущенная из Startup folder) успела
# инициализировать API на 36555 до того как warmup_api её опросит.
$bootTrigger.Delay = "PT2M"

$trigger = @($timeTrigger, $bootTrigger)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -Compatibility Win8

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host ""
Write-Host "[OK] Task '$TaskName' registered."
Write-Host " - First trigger in 15 seconds, then every 45 minutes."
Write-Host " - PLUS: AtStartup trigger (fires within 1-2 min after every boot)."
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
