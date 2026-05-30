# setup.ps1 — bootstrap для свежей Windows-VPS под Linken Sphere warmup.
#
# Запуск с любой Windows 10/11/Server 2019+ от АДМИНА:
#   iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/main/setup.ps1 | iex
#
# Что делает:
#   - проверяет/ставит Git и Python 3.12 (через winget, fallback на прямые
#     инсталляторы с GitHub/python.org, если winget недоступен на VPS)
#   - клонирует репозиторий в C:\warmup
#   - запускает install.bat (Python deps + Linken Sphere + credentials.ini)
#   - регистрирует задачу в Task Scheduler

$ErrorActionPreference = 'Stop'

# UTF-8 в консоли — чтобы кириллица в логах не превращалась в ????
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new() } catch {}

function Write-Step($msg) {
    Write-Host ""
    Write-Host "[setup] $msg" -ForegroundColor Cyan
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
}

function Has-Winget {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Install-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Step "Git already installed — skip"
        return
    }
    if (Has-Winget) {
        Write-Step "Installing Git via winget..."
        winget install -e --id Git.Git --silent --accept-source-agreements --accept-package-agreements --scope machine
    } else {
        Write-Step "winget not available — downloading Git installer directly..."
        $url = "https://github.com/git-for-windows/git/releases/download/v2.46.0.windows.1/Git-2.46.0-64-bit.exe"
        $tmp = Join-Path $env:TEMP "git-installer.exe"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp
        Write-Step "Running Git installer silently (1-2 min)..."
        Start-Process -FilePath $tmp `
            -ArgumentList "/VERYSILENT","/NORESTART","/SUPPRESSMSGBOXES","/NOCANCEL","/SP-","/CLOSEAPPLICATIONS" `
            -Wait -NoNewWindow
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
    Refresh-Path
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git installed but 'git' not in PATH. Reopen PowerShell as admin and retry."
    }
}

function Install-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Write-Step "Python already installed — skip"
        return
    }
    if (Has-Winget) {
        Write-Step "Installing Python 3.12 via winget..."
        winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements --scope machine
    } else {
        Write-Step "winget not available — downloading Python 3.12 installer directly..."
        $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
        $tmp = Join-Path $env:TEMP "python-installer.exe"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp
        Write-Step "Running Python installer silently (1-3 min)..."
        Start-Process -FilePath $tmp `
            -ArgumentList "/quiet","InstallAllUsers=1","PrependPath=1","Include_test=0","Include_doc=0","Include_launcher=1" `
            -Wait -NoNewWindow
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
    Refresh-Path
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Python installed but 'py' not in PATH. Reopen PowerShell as admin and retry."
    }
}

# 0. Админ-права обязательны
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] Admin rights required." -ForegroundColor Red
    Write-Host "  Close this window, right-click Start -> PowerShell (Admin), and retry." -ForegroundColor Red
    exit 1
}

# 1. Git
Install-Git

# 2. Python 3.12
Install-Python

# 3. Клонировать репозиторий
$repoDir = "C:\warmup"
if (-not (Test-Path $repoDir)) {
    Write-Step "Cloning https://github.com/r2d23c/mainquns -> $repoDir"
    Set-Location C:\
    git clone https://github.com/r2d23c/mainquns warmup
} else {
    Write-Step "$repoDir already exists - git pull"
    Set-Location $repoDir
    git pull
}
Set-Location $repoDir

# 4. install.bat — поставит python deps + Linken Sphere + откроет credentials.ini
Write-Step "Running install.bat (deps + Linken Sphere + credentials.ini)"
Write-Host "  -> When Notepad opens, enter your email/password, save (Ctrl+S), close." -ForegroundColor Yellow
Write-Host "  -> Then press any key in this window to continue." -ForegroundColor Yellow
cmd /c "install.bat"

# 5. schedule_hourly.bat — зарегистрировать задачу в Task Scheduler
Write-Step "Registering Task Scheduler job"
cmd /c "schedule_hourly.bat"

Write-Host ""
Write-Host "[setup] DONE. You can close windows and walk away." -ForegroundColor Green
Write-Host "        First trigger fires in 15 seconds." -ForegroundColor Green
Write-Host "        Subscribe in ntfy app to topic:" -ForegroundColor Green
Write-Host "        warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE" -ForegroundColor Green
