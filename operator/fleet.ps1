<#
    fleet.ps1 — one-click RDP ко всему парку VPS с ноутбука оператора.

    ЗАПУСКАЕТСЯ НА ТВОЁМ WINDOWS-НОУТБУКЕ, не на VPS. Только PowerShell +
    встроенные mstsc/cmdkey — никакого стороннего софта, никаких зависимостей.

    Что делает:
      gen      — из hosts.csv генерит rdp\<name>.rdp (каждый c фикс. 2560x1440,
                 smart sizing off — VPS честно рендерит 1440p как требует
                 дисциплина из CLAUDE.md, даже если у тебя монитор 1080p).
      connect  — кэширует креды через cmdkey (mstsc не спросит пароль) и
                 открывает ВСЕ .rdp разом. Это и есть "одна кнопка".
      clean    — удаляет закэшированные креды из Windows Credential Manager.
      all      — gen + connect (действие по умолчанию, просто `.\fleet.ps1`).

    Секреты (пароли) живут ТОЛЬКО в hosts.csv (gitignored). .rdp-файлы
    паролей не содержат — cmdkey кладёт их в Credential Manager локально.

    Использование:
      1. Copy-Item hosts.csv.example hosts.csv ; отредактируй под свой парк
      2. .\fleet.ps1                 # открыть весь парк
      3. работай, потом ЗАКРОЙ окна (disconnect), НЕ Sign out / Log off!
         (logoff убивает LS+watchdog на VPS — см. CLAUDE.md)
      4. .\fleet.ps1 clean           # когда парк списан — убрать креды с ноута

    Примечание про resolution: screen mode id=1 (windowed) + desktop 2560x1440
    даёт 1440p-сессию в окне со скроллом на 1080p-мониторе. На 1440p+ мониторе
    разверни окно. Так VPS-сторона всегда отдаёт 1440p (Get-CimInstance
    Win32_VideoController покажет 2560x1440) — templates матчатся штатно.
#>

[CmdletBinding()]
param(
    [ValidateSet('gen', 'connect', 'clean', 'all')]
    [string]$Action = 'all',

    # Путь к hosts.csv (по умолчанию рядом со скриптом).
    [string]$HostsFile
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $HostsFile) { $HostsFile = Join-Path $root 'hosts.csv' }
$rdpDir = Join-Path $root 'rdp'

function Read-Hosts {
    if (-not (Test-Path $HostsFile)) {
        throw "hosts.csv не найден ($HostsFile). Скопируй hosts.csv.example -> hosts.csv и заполни."
    }
    $rows = Import-Csv $HostsFile
    if (-not $rows) { throw "hosts.csv пуст — добавь хотя бы одну строку." }

    $out = @()
    $i = 0
    foreach ($r in $rows) {
        $i++
        $ip = ("$($r.ip)").Trim()
        if (-not $ip) {
            Write-Warning "строка $i: пустой ip — пропуск"
            continue
        }
        $name = ("$($r.name)").Trim()
        if (-not $name) { $name = $ip -replace '[.:]', '-' }
        $user = ("$($r.user)").Trim()
        if (-not $user) { $user = 'Administrator' }
        $out += [pscustomobject]@{
            Name     = $name
            Ip       = $ip
            User     = $user
            Password = "$($r.password)"   # НЕ .Trim() — пробел может быть частью пароля
        }
    }
    if (-not $out) { throw "в hosts.csv нет валидных строк (нужен непустой ip)." }
    return $out
}

function New-RdpFile($h) {
    # Фиксированный 1440p, windowed, smart sizing/dynamic resolution OFF —
    # чтобы VPS всегда рендерила именно 2560x1440 (дисциплина CLAUDE.md).
    # authentication level=0: не ругаться на self-signed cert одноразовых VPS.
    $lines = @(
        'screen mode id:i:1'
        'use multimon:i:0'
        'desktopwidth:i:2560'
        'desktopheight:i:1440'
        'session bpp:i:32'
        'dynamic resolution:i:0'
        'smart sizing:i:0'
        "full address:s:$($h.Ip)"
        "username:s:$($h.User)"
        'prompt for credentials:i:0'
        'authentication level:i:0'
        'redirectclipboard:i:1'
        'autoreconnection enabled:i:1'
        'audiomode:i:2'
    )
    $path = Join-Path $rdpDir "$($h.Name).rdp"
    # RDP-файлы Windows читает как Unicode (UTF-16LE). ASCII тоже ок, но
    # Unicode безопаснее для не-латинских имён/юзеров.
    Set-Content -Path $path -Value $lines -Encoding Unicode
    return $path
}

function Invoke-Gen($hosts) {
    if (-not (Test-Path $rdpDir)) { New-Item -ItemType Directory -Path $rdpDir | Out-Null }
    foreach ($h in $hosts) {
        $p = New-RdpFile $h
        Write-Host ("  [gen]  {0,-16} -> {1}" -f $h.Name, $p)
    }
    Write-Host ("Сгенерировано {0} .rdp в {1}" -f $hosts.Count, $rdpDir) -ForegroundColor Green
}

function Invoke-Connect($hosts) {
    foreach ($h in $hosts) {
        $rdp = Join-Path $rdpDir "$($h.Name).rdp"
        if (-not (Test-Path $rdp)) {
            Write-Warning "  [skip] $($h.Name): .rdp не найден — запусти сперва gen"
            continue
        }
        # Кэшируем пароль в Windows Credential Manager под ключом TERMSRV/<ip>.
        # mstsc с prompt for credentials=0 подхватит и не спросит пароль.
        # Кавычки в PowerShell сохраняют пароль одним токеном (в т.ч. с пробелами).
        & cmdkey /generic:"TERMSRV/$($h.Ip)" /user:"$($h.User)" /pass:"$($h.Password)" | Out-Null
        Start-Process mstsc -ArgumentList "`"$rdp`""
        Write-Host ("  [conn] {0,-16} {1}@{2}" -f $h.Name, $h.User, $h.Ip)
    }
    Write-Host ("Открыто {0} RDP-окон. Работай, потом ЗАКРОЙ окна (disconnect), НЕ Log off!" -f $hosts.Count) -ForegroundColor Green
}

function Invoke-Clean($hosts) {
    foreach ($h in $hosts) {
        & cmdkey /delete:"TERMSRV/$($h.Ip)" | Out-Null
        Write-Host ("  [clean] $($h.Ip) — креды убраны из Credential Manager")
    }
    Write-Host "Готово. Пароли парка больше не закэшированы на этом ноуте." -ForegroundColor Green
}

$hosts = Read-Hosts
switch ($Action) {
    'gen'     { Invoke-Gen $hosts }
    'connect' { Invoke-Connect $hosts }
    'clean'   { Invoke-Clean $hosts }
    'all'     { Invoke-Gen $hosts; Invoke-Connect $hosts }
}
