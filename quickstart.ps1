# quickstart.ps1 — установка для машин, где Linken Sphere 2 уже был
# запущен ранее, и мастер первого запуска / 28-шаговый product tour /
# финальный close_x уже пройдены (вручную оператором или предыдущим
# прогоном что свалился позже).
#
# Делает ТОЧНО то же что setup.ps1, плюс одно отличие:
# создаёт флаг C:\warmup\.wizards_done — warmup.py видит его и
# мгновенно возвращает False из _dismiss_customize_wizard_step, не
# тратя время на поллинг отсутствующих шаблонов. Это убирает риск
# false-positive в шапке LS и таймаутов в фазах 1.3 / 1.5.
#
# Использование (то же что setup.ps1, только URL другой):
#   $env:LS_EMAIL='your@email'
#   $env:LS_PASSWORD='your-password'
#   iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/main/quickstart.ps1 | iex
#
# Когда НЕ использовать:
#   - Свежий VPS, LS никогда не запускался → setup.ps1 (обычный)
#   - Не уверен показывался ли мастер → setup.ps1 (обычный, безопаснее)
#
# Когда использовать:
#   - LS уже был запущен на этой машине, мастер первого запуска пройден
#   - setup.ps1 свалился где-то ПОСЛЕ login (например на import xlsx),
#     перезапускаем без риска зацепиться за уже-несуществующий wizard

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "[quickstart] LS wizards will be SKIPPED on this machine" -ForegroundColor Cyan
Write-Host ""

# Прокручиваем setup.ps1 целиком, как будто оператор запустил его сам.
$setupUrl = "https://raw.githubusercontent.com/r2d23c/mainquns/main/setup.ps1"
$setupContent = (Invoke-WebRequest -Uri $setupUrl -UseBasicParsing).Content
Invoke-Expression $setupContent

# После setup.ps1 репо клонирован в C:\warmup, Task Scheduler уже
# зарегистрирован и тикнет через ~15с. Ставим маркер ДО первого
# тика — warmup.py его прочитает и пропустит wizard-поллинг.
$marker = "C:\warmup\.wizards_done"
if (Test-Path "C:\warmup") {
    New-Item -Path $marker -ItemType File -Force | Out-Null
    Write-Host ""
    Write-Host "[quickstart] marker written: $marker" -ForegroundColor Green
    Write-Host "[quickstart] warmup.py will SKIP wizard / tour / close_x scanning" -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "[quickstart] WARNING: C:\warmup not found -- marker NOT written" -ForegroundColor Yellow
    Write-Host "[quickstart] setup.ps1 may have failed before cloning repo" -ForegroundColor Yellow
    Write-Host ""
}
