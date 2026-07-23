# setup.ps1 - bootstrap for fresh Windows VPS for Linken Sphere warmup.
#
# Run from PowerShell as Admin on Windows 10/11/Server 2019+:
#   $env:LS_EMAIL = "..."
#   $env:LS_PASSWORD = "..."
#   $env:WINDOWS_ADMIN_PASSWORD = "..."   # optional, enables auto-recovery
#   iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/main/setup.ps1 | iex
#
# Canary/test install from a feature branch (e.g. multi-profile):
#   $env:WARMUP_BRANCH = "multi-profile"
#   iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/multi-profile/setup.ps1 | iex
#
# Multi-profile: сколько профилей и сколько URL/профиль — через env (до старта):
#   $env:Profiles_LS      = '10'       # профилей на VPS
#   $env:URLS_PER_PROFILE = '200'      # фикс. цель URL на профиль
#   $env:URLS_PER_PROFILE = '150-300'  # или диапазон (случайно на профиль)
#
# What it does:
#   - check/install Git and Python 3.12 (via winget; falls back to direct
#     installer downloads from github/python.org if winget unavailable on VPS)
#   - git clone repo to C:\warmup
#   - install.bat: Python deps + Linken Sphere + credentials.ini
#   - 4.5 firewall pre-authorize LS binaries (no Defender popups mid-cycle)
#   - 4.6 Windows Update — disable auto-reboot (preventive)
#   - 4.6.5 account lockout policy off (anti-lockout after hard reset)
#   - 4.7 AutoAdminLogon (enables auto-recovery after any reboot)
#   - 4.8 Startup folder shortcut → ls_launch.bat (auto-launch LS on logon,
#         + boot detection → 🔄 ntfy push после Kernel-Power 41)
#   - register Task Scheduler job (every 45 min)

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
# WARMUP_BRANCH env var -> install from a feature branch (e.g. multi-profile)
# instead of main. For canary/test VPS only; production machines omit it.
$repoDir = "C:\warmup"
$branch = $env:WARMUP_BRANCH
if (-not (Test-Path $repoDir)) {
    Write-Step "Cloning https://github.com/r2d23c/mainquns -> $repoDir"
    Set-Location C:\
    if ($branch) {
        Write-Step "WARMUP_BRANCH=$branch -- cloning that branch"
        git clone -b $branch https://github.com/r2d23c/mainquns warmup
    } else {
        git clone https://github.com/r2d23c/mainquns warmup
    }
} else {
    Write-Step "$repoDir already exists - git pull"
    Set-Location $repoDir
    if ($branch) {
        Write-Step "WARMUP_BRANCH=$branch -- checkout + pull that branch"
        git fetch origin $branch
        git checkout $branch
        git pull origin $branch
    } else {
        git pull
    }
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

# 3.6 Install-time overrides из env -> config.ini (ветка multi-profile).
# Оператор регулирует ДО первого запуска, файлы руками не правит:
#   $env:Profiles_LS       — сколько профилей греть на этой VPS ([profiles] count)
#   $env:URLS_PER_PROFILE  — фикс. цель URL на профиль (min=max=это число)
#   $env:URLS_MIN / URLS_MAX — вместо фикс.: случайный диапазон на профиль
# Значения пишутся в config.ini и фиксируются в state-файлах при первом
# запуске warmup.py. На УЖЕ установленной машине менять бесполезно (профили
# и target'ы зафиксированы) — нужен freshstart.bat. Код Python не трогаем:
# он и так читает [profiles] count и urls_total_target_min/max.

function Set-IniValue {
    param([string]$Path, [string]$Section, [string]$Key, [string]$Value)
    if (-not (Test-Path $Path)) { Write-Warning "config не найден: $Path"; return $false }
    $lines = Get-Content -Path $Path
    $out = New-Object System.Collections.Generic.List[string]
    $inSection = $false; $done = $false
    foreach ($line in $lines) {
        if ($line -match '^\s*\[(.+)\]\s*$') {
            $inSection = ($matches[1] -eq $Section)
        }
        elseif ($inSection -and -not $done -and
                $line -match "^\s*$([regex]::Escape($Key))\s*=") {
            $line = "$Key = $Value"; $done = $true
        }
        $out.Add($line)
    }
    # UTF-8 без BOM — Python configparser ломается на BOM.
    [System.IO.File]::WriteAllLines($Path, $out, [System.Text.UTF8Encoding]::new($false))
    if (-not $done) { Write-Warning "не нашёл [$Section] $Key в config.ini — пропуск" }
    return $done
}

function Get-EnvInt {
    param([string]$Name)
    $v = [Environment]::GetEnvironmentVariable($Name)
    if (-not $v) { return $null }
    $n = 0
    if ([int]::TryParse($v.Trim(), [ref]$n)) { return $n }
    Write-Warning "env $Name='$v' не целое число — игнорирую"
    return $null
}

$cfgPath = Join-Path $repoDir "config.ini"

$envProfiles = Get-EnvInt 'Profiles_LS'
if ($null -ne $envProfiles) {
    if ($envProfiles -lt 1) { $envProfiles = 1 }
    if (Set-IniValue $cfgPath 'profiles' 'count' $envProfiles) {
        Write-Step "profiles count = $envProfiles (из env Profiles_LS)"
    }
    if ($envProfiles -gt 5) {
        Write-Warning ("Profiles_LS={0}: >5 профилей = N*target URL, прогрев может занять много часов (10 профилей x 150-250 = 1500-2500 URL ~ 12-20 ч). Убедись что осознанно." -f $envProfiles)
    }
}

# URLS_PER_PROFILE понимает и одно число '200' (фикс), и диапазон '150-300'
# (случайно на профиль). URLS_MIN/URLS_MAX — альтернатива двумя переменными;
# если заданы, перекрывают соответствующую границу.
$uMin = $null; $uMax = $null
$rawUrls = [Environment]::GetEnvironmentVariable('URLS_PER_PROFILE')
if ($rawUrls) {
    $rawUrls = $rawUrls.Trim()
    if ($rawUrls -match '^\d+$') {
        $uMin = [int]$rawUrls; $uMax = $uMin
    }
    elseif ($rawUrls -match '^(\d+)\s*-\s*(\d+)$') {
        $uMin = [int]$matches[1]; $uMax = [int]$matches[2]
    }
    else {
        Write-Warning "env URLS_PER_PROFILE='$rawUrls' не число и не диапазон 'N-M' — игнорирую"
    }
}
$eMin = Get-EnvInt 'URLS_MIN'; $eMax = Get-EnvInt 'URLS_MAX'
if ($null -ne $eMin) { $uMin = $eMin }
if ($null -ne $eMax) { $uMax = $eMax }
if (($null -ne $uMin) -or ($null -ne $uMax)) {
    if ($null -eq $uMin) { $uMin = $uMax }
    if ($null -eq $uMax) { $uMax = $uMin }
    if ($uMin -lt 1) { $uMin = 1 }
    if ($uMax -lt 1) { $uMax = 1 }
    if ($uMin -gt $uMax) { $t = $uMin; $uMin = $uMax; $uMax = $t }
    Set-IniValue $cfgPath 'api' 'urls_total_target_min' $uMin | Out-Null
    Set-IniValue $cfgPath 'api' 'urls_total_target_max' $uMax | Out-Null
    if ($uMin -eq $uMax) {
        Write-Step "urls per profile = $uMin (фикс, из env)"
    } else {
        Write-Step "urls per profile = ${uMin}..${uMax} (диапазон, из env)"
    }
}

# 4. install.bat - Python deps + Linken Sphere + opens credentials.ini
Write-Step "Running install.bat (deps + Linken Sphere + credentials.ini)"
if (-not $unattended) {
    Write-Host "  -> When Notepad opens, enter your email/password, save (Ctrl+S), close." -ForegroundColor Yellow
    Write-Host "  -> Then press any key in this window to continue." -ForegroundColor Yellow
}
cmd /c "install.bat"

# 4.5 Pre-authorize ВСЕХ binaries в LS install folder в Windows Defender
# Firewall. Это устраняет Defender popup'ы на:
#   - main процесс Linken Sphere 2.exe (при первом старте через ShellExecuteW)
#   - bundled Chromium (всплывает позже, типично на ~20-м чанке прогрева,
#     когда session-browser открывает достаточно tab'ов / debug-port'ов)
#   - Squirrel.exe updater и прочие helper'ы внутри install folder
# Без preauth каждый popup риск что _dismiss_firewall_alert его пропустит
# (новый Windows build с непокрытым PNG-стилем, отличающийся title и т.п.)
# → ⚠️ failed (ui) на машине.
# С preauth Defender молчаливо разрешает inbound для наших бинарей,
# никаких popup'ов не возникает, и pipeline идёт без задержек.
Write-Step "Pre-authorizing LS binaries in Windows Firewall"
$lsFolder = "C:\Program Files (x86)\Linken Sphere 2"
if (Test-Path $lsFolder) {
    $exeList = Get-ChildItem -Path $lsFolder -Filter *.exe -Recurse -ErrorAction SilentlyContinue
    if (-not $exeList) {
        Write-Host "  [WARN] no .exe found in $lsFolder" -ForegroundColor Yellow
    }
    foreach ($exe in $exeList) {
        foreach ($proto in @("TCP","UDP")) {
            $ruleName = "LS preauth: $($exe.Name) $proto inbound"
            # Идемпотентность: повторный setup.ps1 правила не дублирует
            if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
                New-NetFirewallRule -DisplayName $ruleName `
                    -Direction Inbound -Action Allow `
                    -Protocol $proto -Program $exe.FullName `
                    -Profile Any `
                    -ErrorAction SilentlyContinue | Out-Null
                Write-Host "  [OK] $($exe.Name) $proto" -ForegroundColor Green
            }
        }
    }
} else {
    Write-Host "  [WARN] $lsFolder not found -- firewall preauth skipped" -ForegroundColor Yellow
}

# 4.6 Disable Windows Update auto-reboot — критично для VPS которые
# Windows Update прерывал посреди цикла прогрева (наблюдалось 8 июня
# на 3 из 6 машин: KB5066790 + KB5066791 + KB5066130 installed →
# svchost.exe "Service pack (Planned)" reboot → потеря состояния цикла).
# Updates всё равно качаются и устанавливаются — НО reboot откладывается
# пока залогинен Administrator (а он залогинен пока есть RDP-сессия
# в Active или Disconnected состоянии; только manual "Sign Out" её снимает).
# Работает на Win 10 / Win 11 / Server 2016+ единообразно.
Write-Step "Disabling Windows Update auto-reboot (NoAutoRebootWithLoggedOnUsers)"
$wuKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
if (-not (Test-Path $wuKey)) { New-Item -Path $wuKey -Force | Out-Null }
Set-ItemProperty -Path $wuKey -Name "NoAutoRebootWithLoggedOnUsers" -Value 1 -Type DWord -Force
Set-ItemProperty -Path $wuKey -Name "AlwaysAutoRebootAtScheduledTime" -Value 0 -Type DWord -Force
Write-Host "  [OK] auto-reboot blocked while user is logged in (security patches still install)" -ForegroundColor Green

# 4.6.5 Disable account lockout policy.
# Если hard reset (Kernel-Power 41 / провайдерский reset) случится в момент
# когда registry transaction для DefaultPassword не успел flushed на диск,
# AutoAdminLogon может пытаться с битым паролем. По умолчанию Windows
# Server лочит аккаунт после ~10 failed attempts → даже корректный RDP
# login потом не пускает. Случилось ровно это с 155.138.253.219.
# net accounts /lockoutthreshold:0 — полностью отключает lockout policy.
# Аккаунт никогда не залочится, даже при 1000 failed attempts.
# Это не security-проблема для нашего сценария: VPS одноразовые, snapshot
# делается сразу после warmup, и доступ только через RDP по нашему паролю.
Write-Step "Disabling account lockout policy (anti-lockout after hard reset)"
$lockoutOut = cmd /c "net accounts /lockoutthreshold:0" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] account lockout threshold = 0 (никогда не лочится)" -ForegroundColor Green
} else {
    Write-Host "  [WARN] net accounts вернул rc=$LASTEXITCODE :" -ForegroundColor Yellow
    Write-Host "  $lockoutOut" -ForegroundColor Yellow
}

# 4.7 AutoAdminLogon — БЕССРОЧНЫЙ автологин текущего user'а после reboot.
# Включается ТОЛЬКО если оператор передал $env:WINDOWS_ADMIN_PASSWORD.
# Назначение: пережить любой reboot (Windows Update / Kernel-Power 41 от
# провайдера) без ручного RDP. После reboot Windows сама залогинит user →
# user session есть → Task Scheduler Interactive работает → Startup folder
# триггерится → LS поднимается (Step 4.8) → pipeline идёт сам.
#
# DefaultUserName/DefaultDomainName берутся из текущей сессии ($env:USERNAME
# и $env:COMPUTERNAME). Это критично: на части VPS user не 'Administrator'
# а просто 'Admin' (или другое имя), и hardcoded значение ломает autologin.
#
# AutoLogonCount намеренно удаляется чтобы autologin был БЕССРОЧНЫМ. Иначе
# он самоотключается через N reboot'ов и recovery после второго+ ребута
# не сработает.
#
# Безопасность:
#   - DefaultPassword хранится REG_SZ в plaintext в Winlogon. Локально на VPS,
#     наружу не уходит. Прочитать может только admin (он и так имеет полные
#     права). Если боишься — не задавай WINDOWS_ADMIN_PASSWORD; pipeline всё
#     равно будет работать, просто после ребута потребуется ручной RDP.
if ($env:WINDOWS_ADMIN_PASSWORD) {
    Write-Step "Configuring AutoAdminLogon (permanent, until WINDOWS_ADMIN_PASSWORD unset)"
    $winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    Set-ItemProperty -Path $winlogon -Name "AutoAdminLogon"    -Value "1"
    Set-ItemProperty -Path $winlogon -Name "DefaultUserName"   -Value $env:USERNAME
    Set-ItemProperty -Path $winlogon -Name "DefaultDomainName" -Value $env:COMPUTERNAME
    Set-ItemProperty -Path $winlogon -Name "DefaultPassword"   -Value $env:WINDOWS_ADMIN_PASSWORD
    Remove-ItemProperty -Path $winlogon -Name "AutoLogonCount" -ErrorAction SilentlyContinue
    Write-Host "  [OK] AutoAdminLogon = $env:COMPUTERNAME\$env:USERNAME (no expiry)" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "[setup] WINDOWS_ADMIN_PASSWORD not set -- auto-login skipped" -ForegroundColor Yellow
    Write-Host "        After unexpected reboot (Kernel-Power, provider maintenance)" -ForegroundColor Yellow
    Write-Host "        manual RDP will be required to resume pipeline." -ForegroundColor Yellow
    Write-Host "        To enable auto-login: re-run setup.ps1 with" -ForegroundColor Yellow
    Write-Host "        `$env:WINDOWS_ADMIN_PASSWORD = '<your VPS admin password>'" -ForegroundColor Yellow
}

# 4.8 Startup folder shortcut → ls_launch.bat wrapper.
# Когда любая user-сессия открывается (autologin после ребута ИЛИ ручной RDP),
# Windows триггерит Startup folder. Наш ярлык запускает ls_launch.bat — он
# сначала проверяет нет ли уже запущенной LS, и только тогда запускает.
# Защита от двойного инстанса при RDP logon на Windows Server (создаёт новую
# session — Startup folder выстреливает повторно, и без проверки получили бы
# 'already running' alert от второго LS).
Write-Step "Configuring LS auto-launch (Startup folder + ls_launch.bat wrapper)"
$wrapperPath = Join-Path $repoDir "ls_launch.bat"
if (-not (Test-Path $wrapperPath)) {
    Write-Host "  [WARN] $wrapperPath not found in repo -- shortcut not created" -ForegroundColor Yellow
} else {
    $startupFolder = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
    if (-not (Test-Path $startupFolder)) {
        New-Item -Path $startupFolder -ItemType Directory -Force | Out-Null
    }
    $shortcutPath = Join-Path $startupFolder "LinkenSphere2.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($shortcutPath)
    $sc.TargetPath = $wrapperPath
    $sc.WorkingDirectory = $repoDir
    # WindowStyle = 7 (Minimized): при logon чёрное окно cmd не мигает поверх.
    $sc.WindowStyle = 7
    $sc.Save()
    Write-Host "  [OK] Startup shortcut: $shortcutPath -> ls_launch.bat" -ForegroundColor Green
}

# 5. schedule_hourly.bat - register the Task Scheduler job
Write-Step "Registering Task Scheduler job"
cmd /c "schedule_hourly.bat"

Write-Host ""
Write-Host "[setup] DONE. You can close windows and walk away." -ForegroundColor Green
Write-Host "        First trigger fires in 15 seconds." -ForegroundColor Green
Write-Host "        Subscribe in ntfy app to topic:" -ForegroundColor Green
Write-Host "        warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE" -ForegroundColor Green
