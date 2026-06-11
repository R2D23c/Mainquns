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

# ============================================================================
# LsWatchdog — следит за LS между циклами warmup_api. Если API на 36555 не
# отвечает 3 проверки подряд (≈15 мин downtime) И при этом warmup_api не
# работает (свой retry pyramid не запущен) — kill LS + перезапуск через
# ls_launch.bat. На 2c/4gb VPS Electron-renderer LS периодически падает
# без видимой причины — watchdog покрывает эту проблему.
# ============================================================================
$WatchdogTaskName = 'LsWatchdog'
$watchdogCmd = Join-Path $ScriptDir 'run_watchdog.bat'
$hiddenVbs = Join-Path $ScriptDir 'run_hidden.vbs'
if (Test-Path $watchdogCmd) {
    Unregister-ScheduledTask -TaskName $WatchdogTaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

    # Watchdog запускается каждые 5 мин — если открывать видимый cmd на каждом
    # tick, оператор видит мерцающие чёрные окна. Особенно плохо во время
    # warmup.py UI install: cmd может перехватить focus или визуально
    # перекрыть LS. Поэтому wscript.exe + run_hidden.vbs: запуск полностью
    # невидимый. Если VBS файла нет (старая установка) — fallback на cmd.
    if (Test-Path $hiddenVbs) {
        $wdAction = New-ScheduledTaskAction `
            -Execute 'wscript.exe' `
            -Argument "`"$hiddenVbs`" `"$watchdogCmd`"" `
            -WorkingDirectory $ScriptDir
    } else {
        $wdAction = New-ScheduledTaskAction `
            -Execute 'cmd.exe' `
            -Argument "/c `"$watchdogCmd`"" `
            -WorkingDirectory $ScriptDir
    }

    # Каждые 5 минут — частая проверка дёшево (Python startup + HTTP ping = <2с).
    # Первый tick через 3 мин после регистрации чтобы дать install.bat закончиться.
    $wdTimeTrigger = New-ScheduledTaskTrigger `
        -Once -At (Get-Date).AddMinutes(3) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)

    # AtStartup с delay 3 мин — после reboot watchdog не должен пытаться
    # стартовать LS пока сама LS не запустится из Startup folder и не получит
    # 2-3 мин на инициализацию своего API. Иначе watchdog увидит "down" и
    # начнёт паниковать ещё до того как LS успеет встать.
    $wdBootTrigger = New-ScheduledTaskTrigger -AtStartup
    $wdBootTrigger.Delay = "PT3M"

    Register-ScheduledTask `
        -TaskName $WatchdogTaskName `
        -Action $wdAction `
        -Trigger @($wdTimeTrigger, $wdBootTrigger) `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null

    Write-Host ""
    Write-Host "[OK] Task '$WatchdogTaskName' registered."
    Write-Host " - Runs every 5 minutes (and 3 min after every boot)."
    Write-Host " - Pings http://127.0.0.1:36555/sessions."
    Write-Host " - If API down 3 checks in a row AND main task not running:"
    Write-Host "     kill LS + relaunch via ls_launch.bat."
    Write-Host " - Skipped silently while warmup_api cycle is running."
    Write-Host " - Skipped silently after .notified_done (pipeline complete)."
}

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
