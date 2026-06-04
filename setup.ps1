# setup.ps1 - bootstrap for fresh Windows VPS for Linken Sphere warmup.
#
# Run from PowerShell as Admin on Windows 10/11/Server 2019+:
#   iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/main/setup.ps1 | iex
#
# What it does:
#   - check/install Git and Python 3.12 (via winget; falls back to direct
#     installer downloads from github/python.org if winget unavailable on VPS)
#   - git clone repo to C:\warmup
#   - run install.bat (Python deps + Linken Sphere + credentials.ini)
#   - register task in Task Scheduler

$ErrorActionPreference = 'Stop'

# UTF-8 console encoding - keep ASCII strings only just in case
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

function Install-GitDirect {
    Write-Step "Downloading Git installer directly from github.com..."
    $url = "https://github.com/git-for-windows/git/releases/download/v2.54.0.windows.1/Git-2.54.0-64-bit.exe"
    $tmp = Join-Path $env:TEMP "git-installer.exe"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp
    Write-Step "Running Git installer silently (1-2 min)..."
    Start-Process -FilePath $tmp `
        -ArgumentList "/VERYSILENT","/NORESTART","/SUPPRESSMSGBOXES","/NOCANCEL","/SP-","/CLOSEAPPLICATIONS" `
        -Wait -NoNewWindow
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

function Install-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Step "Git already installed -- skip"
        return
    }
    $wingetOk = $false
    if (Has-Winget) {
        Write-Step "Installing Git via winget (winget catalog only, skip msstore)..."
        # --source winget — на свежей Win11/Server VPS у msstore часто
        # битый cert ('did not match any of the expected values') → весь
        # winget валится. Заставляем использовать только нативный
        # winget-catalog, msstore не нужен.
        $global:LASTEXITCODE = 0
        try {
            winget install -e --id Git.Git --silent `
                --accept-source-agreements --accept-package-agreements `
                --scope machine --source winget
            if ($LASTEXITCODE -eq 0) { $wingetOk = $true }
        } catch { $wingetOk = $false }
        if (-not $wingetOk) {
            Write-Step "[warn] winget failed (rc=$LASTEXITCODE), falling back to direct download..."
        }
    }
    if (-not $wingetOk) {
        Install-GitDirect
    }
    Refresh-Path
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git installed but 'git' not in PATH. Reopen PowerShell as admin and retry."
    }
}

function Install-PythonDirect {
    Write-Step "Downloading Python 3.12 installer directly from python.org..."
    $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    $tmp = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp
    Write-Step "Running Python installer silently (1-3 min)..."
    Start-Process -FilePath $tmp `
        -ArgumentList "/quiet","InstallAllUsers=1","PrependPath=1","Include_test=0","Include_doc=0","Include_launcher=1" `
        -Wait -NoNewWindow
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

function Install-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Write-Step "Python already installed -- skip"
        return
    }
    $wingetOk = $false
    if (Has-Winget) {
        Write-Step "Installing Python 3.12 via winget (winget catalog only, skip msstore)..."
        $global:LASTEXITCODE = 0
        try {
            winget install -e --id Python.Python.3.12 --silent `
                --accept-source-agreements --accept-package-agreements `
                --scope machine --source winget
            if ($LASTEXITCODE -eq 0) { $wingetOk = $true }
        } catch { $wingetOk = $false }
        if (-not $wingetOk) {
            Write-Step "[warn] winget failed (rc=$LASTEXITCODE), falling back to direct download..."
        }
    }
    if (-not $wingetOk) {
        Install-PythonDirect
    }
    Refresh-Path
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Python installed but 'py' not in PATH. Reopen PowerShell as admin and retry."
    }
}

# 0. Admin rights required
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] Admin rights required." -ForegroundColor Red
    Write-Host "  Close this window, right-click Start -> PowerShell (Admin), and retry." -ForegroundColor Red
    exit 1
}

# 0.5 Unattended mode detection.
# Если перед запуском оператор выставил $env:LS_EMAIL и $env:LS_PASSWORD —
# работаем БЕЗ интерактивных пауз: пишем credentials.ini сами, install.bat
# пропускает Notepad-step, финальный PRESS-ANY-KEY баннер тоже не показываем.
# Это позволяет открыть N RDP параллельно и поставить везде одну команду —
# каждая машина сама всё сделает до конца.
$preloadEmail = $env:LS_EMAIL
$preloadPassword = $env:LS_PASSWORD
$unattended = $false
if ($preloadEmail -and $preloadPassword) {
    Write-Step "Unattended mode: LS_EMAIL/LS_PASSWORD detected -- Notepad will be skipped"
    $unattended = $true
    $env:WARMUP_UNATTENDED = "1"
}

# 1. Git
Install-Git

# 2. Python 3.12
Install-Python

# 3. Clone repo
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

# 3.5 Pre-write credentials.ini in unattended mode — install.bat увидит что
# файл уже существует и не будет открывать Notepad.
if ($unattended) {
    $credPath = Join-Path $repoDir "credentials.ini"
    $iniContent = "[account]`r`nemail = $preloadEmail`r`npassword = $preloadPassword`r`n"
    # UTF-8 БЕЗ BOM — Python configparser плохо реагирует на BOM в первой строке.
    [System.IO.File]::WriteAllText($credPath, $iniContent, [System.Text.UTF8Encoding]::new($false))
    Write-Step "credentials.ini written from env vars (Notepad step skipped)"
}

# 4. install.bat - Python deps + Linken Sphere + opens credentials.ini
Write-Step "Running install.bat (deps + Linken Sphere + credentials.ini)"
if (-not $unattended) {
    Write-Host "  -> When Notepad opens, enter your email/password, save (Ctrl+S), close." -ForegroundColor Yellow
    Write-Host "  -> Then press any key in this window to continue." -ForegroundColor Yellow
}
cmd /c "install.bat"

# 5. schedule_hourly.bat - register the Task Scheduler job
Write-Step "Registering Task Scheduler job"
cmd /c "schedule_hourly.bat"

Write-Host ""
Write-Host "[setup] DONE. You can close windows and walk away." -ForegroundColor Green
Write-Host "        First trigger fires in 15 seconds." -ForegroundColor Green
Write-Host "        Subscribe in ntfy app to topic:" -ForegroundColor Green
Write-Host "        warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE" -ForegroundColor Green
