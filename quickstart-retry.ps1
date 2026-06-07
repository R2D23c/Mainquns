# quickstart-retry.ps1 — перезапуск UI install на уже-настроенной C:\warmup
# без повторного скачивания Python / Git / LS / репозитория.
#
# Use case: setup.ps1 (или quickstart.ps1) уже отработал, но warmup.py
# свалился где-то в UI install pipeline — например, file dialog промахнулся
# на импорте сессии. Хотим повторить попытку без полного bootstrap.
#
# Что делает:
#   1. Ставит флаг C:\warmup\.wizards_done — warmup.py пропустит поллинг
#      одноразовых мастеров (1.3 Customize wizard, 1.5 post-login welcome/
#      tour/close_x). На retry-машине эти экраны уже точно пройдены.
#   2. Триггерит Task Scheduler → run_api.bat → warmup.py (если флаги
#      .api_activated / .session_imported ещё неполные).
#
# Что НЕ делает:
#   - Не качает Python / Git / LS / репо (всё уже стоит)
#   - Не трогает уже-успешно-завершённые фазы (флаги .api_activated /
#     .session_imported их защищают: warmup.py пропустит готовое)
#   - Не правит credentials.ini, не перерегистрирует Task Scheduler
#
# Использование (одна команда):
#   iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/main/quickstart-retry.ps1 | iex
#
# Когда НЕ использовать:
#   - C:\warmup ещё нет → setup.ps1 (или quickstart.ps1 если LS уже запускался)
#   - UI install уже полностью прошёл (есть оба флага .api_activated +
#     .session_imported) → нечего ретраить, машина уже на API-цикле

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "[quickstart-retry] retrying UI install on existing C:\warmup" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path 'C:\warmup')) {
    Write-Host "[quickstart-retry] ERROR: C:\warmup not found" -ForegroundColor Red
    Write-Host "[quickstart-retry] run setup.ps1 first (это была бы первая установка)" -ForegroundColor Red
    exit 1
}

# 1. Поставить маркер, что одноразовые wizard-экраны на этой машине уже
#    пройдены — warmup.py пропустит поллинг шаблонов мастеров.
$marker = 'C:\warmup\.wizards_done'
New-Item -Path $marker -ItemType File -Force | Out-Null
Write-Host "[quickstart-retry] marker set: $marker" -ForegroundColor Green

# 2. Триггерим Task Scheduler. run_api.bat проверит .api_activated и
#    .session_imported: если оба есть → дispatch в warmup_api.py (цикл);
#    если хоть один отсутствует → warmup.py (UI install retry).
Write-Host "[quickstart-retry] triggering Task Scheduler -> LinkenSphereWarmup..." -ForegroundColor Cyan
schtasks /run /tn LinkenSphereWarmup

Write-Host ""
Write-Host "[quickstart-retry] DONE. Watch ntfy / Telegram for the next push." -ForegroundColor Green
Write-Host "                    UI install retry starts within seconds." -ForegroundColor Green
Write-Host ""
