# setup.ps1 — bootstrap для свежей Windows-VPS под Linken Sphere warmup.
#
# Запуск с любой Windows 10/11:
#   1. Открой PowerShell ОТ АДМИНА (правый клик Start → Windows PowerShell (Admin)).
#   2. Вставь одну строку:
#        iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/main/setup.ps1 | iex
#   3. Жди пока всё поставится → впиши email/password в Блокнот, сохрани, закрой.
#   4. Готово. Task Scheduler регистрируется автоматически.
#
# Что делает:
#   - проверяет/ставит Git и Python 3.12 через winget
#   - клонирует репозиторий в C:\warmup
#   - запускает install.bat (deps + LS + открытие credentials.ini)
#   - регистрирует задачу через schedule_hourly.bat

$ErrorActionPreference = 'Stop'

function Write-Step($msg) {
    Write-Host ""
    Write-Host "[setup] $msg" -ForegroundColor Cyan
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
}

# 0. Админ-права обязательны: LS-инсталлер пишет в Program Files,
#    winget --scope machine тоже требует admin.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] Нужны права администратора." -ForegroundColor Red
    Write-Host "  Закрой это окно, открой PowerShell правым кликом → 'Run as administrator'," -ForegroundColor Red
    Write-Host "  и вставь команду снова." -ForegroundColor Red
    exit 1
}

# 1. winget сам должен быть. На Win10 1809+ / Win11 / Server 2022 он есть.
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] winget не найден. Поставь 'App Installer' из Microsoft Store." -ForegroundColor Red
    exit 1
}

# 2. Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Step "Ставлю Git через winget..."
    winget install -e --id Git.Git --silent --accept-source-agreements --accept-package-agreements --scope machine
    Refresh-Path
} else {
    Write-Step "Git уже установлен — пропускаю"
}

# 3. Python 3.12
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Step "Ставлю Python 3.12 через winget..."
    winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements --scope machine
    Refresh-Path
} else {
    Write-Step "Python уже установлен — пропускаю"
}

# 4. Клонировать репозиторий
$repoDir = "C:\warmup"
if (-not (Test-Path $repoDir)) {
    Write-Step "Клонирую https://github.com/r2d23c/mainquns → $repoDir"
    Set-Location C:\
    git clone https://github.com/r2d23c/mainquns warmup
} else {
    Write-Step "$repoDir уже существует — git pull"
    Set-Location $repoDir
    git pull
}
Set-Location $repoDir

# 5. install.bat — поставит python deps + Linken Sphere + откроет credentials.ini
Write-Step "Запускаю install.bat (deps + LS + credentials.ini)"
Write-Host "  → когда откроется Блокнот, впиши свои email/password, сохрани (Ctrl+S) и закрой." -ForegroundColor Yellow
Write-Host "  → затем в этом окне нажми любую клавишу для продолжения." -ForegroundColor Yellow
cmd /c "install.bat"

# 6. schedule_hourly.bat — зарегистрировать задачу в Task Scheduler
Write-Step "Регистрирую задачу в Task Scheduler"
cmd /c "schedule_hourly.bat"

Write-Host ""
Write-Host "[setup] ✅ Готово. Можешь закрывать окна и уходить." -ForegroundColor Green
Write-Host "        Первый автоматический прогон стартует через 15 секунд." -ForegroundColor Green
Write-Host "        Подпишись в ntfy-приложении на топик:" -ForegroundColor Green
Write-Host "        warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE" -ForegroundColor Green
