<#
    mremoteng-import.ps1 — hosts.csv -> CSV для импорта в mRemoteNG.

    ЗАПУСКАЕТСЯ НА НОУТБУКЕ ОПЕРАТОРА. Массово готовит подключения ко всему
    парку (до 30+ VPS), чтобы не вбивать каждое руками в mRemoteNG.

    ПОЧЕМУ через template: формат CSV у mRemoteNG отличается между версиями
    (разделитель ; или , ; набор колонок; строковые значения Resolution/
    Colors). Вместо угадывания скрипт КЛОНИРУЕТ схему из твоего же экспорта:
    все настройки (резолюция 2560x1440, цвета, auth level) наследуются от
    эталонного подключения, которое ты один раз настроил руками. Меняются
    только Name / Hostname / Username / Password по каждой VPS.

    РАБОЧИЙ ПРОЦЕСС (один раз):
      1. В mRemoteNG настрой ОДНО подключение как надо (в т.ч. Display ->
         Resolution = 2560x1440). Проверь, что оно коннектится.
      2. Выдели его -> File -> Export -> сохрани как template.csv
         (тип "mRemoteNG CSV").
      3. .\mremoteng-import.ps1 -Template template.csv
      4. В mRemoteNG: File -> Import -> Import from CSV -> выбери
         mremoteng-import.csv. Все VPS появятся разом с паролями.

    Дальше правый клик по папке -> Connect открывает весь парк.

    Секреты: пароли только в hosts.csv и в сгенерённом mremoteng-import.csv —
    оба gitignored. После импорта в mRemoteNG сгенерённый csv можно удалить.
#>

[CmdletBinding()]
param(
    # Эталон схемы: экспорт ОДНОГО настроенного подключения из mRemoteNG.
    [Parameter(Mandatory = $true)]
    [string]$Template,

    # Список парка (name,ip,user,password). По умолчанию рядом со скриптом.
    [string]$HostsFile,

    # Куда писать результат для импорта.
    [string]$Out,

    # Необязательно: принудительно выставить Resolution во всех строках
    # (строкой РОВНО как у mRemoteNG, напр. 'Res2560x1440' или 'FitToWindow').
    # По умолчанию наследуется из template — то, что ты выбрал в GUI.
    [string]$Resolution
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $HostsFile) { $HostsFile = Join-Path $root 'hosts.csv' }
if (-not $Out) { $Out = Join-Path $root 'mremoteng-import.csv' }

if (-not (Test-Path $Template)) {
    throw "template не найден ($Template). Экспортируй одно подключение из mRemoteNG: File -> Export -> mRemoteNG CSV."
}
if (-not (Test-Path $HostsFile)) {
    throw "hosts.csv не найден ($HostsFile). Скопируй hosts.csv.example -> hosts.csv и заполни."
}

# --- 1. Определяем разделитель по строке заголовка template ---
$headerLine = (Get-Content -Path $Template -TotalCount 1)
if (-not $headerLine) { throw "template пуст: $Template" }
$semi = ([regex]::Matches($headerLine, ';')).Count
$comma = ([regex]::Matches($headerLine, ',')).Count
$delim = if ($semi -ge $comma) { ';' } else { ',' }
Write-Host "template разделитель: '$delim' (; = $semi, , = $comma)"

# --- 2. Порядок колонок берём ИЗ заголовка (Get-Member сортирует — нельзя) ---
function Split-CsvLine([string]$line, [string]$d) {
    # Простой разбор с учётом кавычек, для строки заголовка достаточно.
    $out = @(); $cur = ''; $inq = $false
    for ($i = 0; $i -lt $line.Length; $i++) {
        $ch = $line[$i]
        if ($ch -eq '"') {
            if ($inq -and $i + 1 -lt $line.Length -and $line[$i + 1] -eq '"') { $cur += '"'; $i++ }
            else { $inq = -not $inq }
        } elseif ($ch -eq $d -and -not $inq) { $out += $cur; $cur = '' }
        else { $cur += $ch }
    }
    $out += $cur
    return $out
}
$header = Split-CsvLine $headerLine $delim
Write-Host "колонок в template: $($header.Count)"

# --- 3. Первая строka данных = эталон значений (defaults) ---
$tpl = Import-Csv -Path $Template -Delimiter $delim
if (-not $tpl) { throw "в template нет ни одной строки данных — экспортируй настроенное подключение." }
$default = $tpl[0]

# --- 4. Находим реальные имена ключевых колонок (без учёта регистра) ---
function Find-Col([string[]]$hdr, [string]$want) {
    foreach ($c in $hdr) { if ($c -ieq $want) { return $c } }
    return $null
}
$colName = Find-Col $header 'Name'
$colHost = Find-Col $header 'Hostname'
$colUser = Find-Col $header 'Username'
$colPass = Find-Col $header 'Password'
$colDom  = Find-Col $header 'Domain'
$colRes  = Find-Col $header 'Resolution'
if (-not $colName -or -not $colHost) {
    throw "в template нет колонок Name/Hostname — это точно экспорт mRemoteNG CSV? Заголовок: $headerLine"
}
if (-not $colPass) { Write-Warning "в template нет колонки Password — пароли не подставятся." }

# --- 5. Читаем парк ---
$hosts = Import-Csv -Path $HostsFile
if (-not $hosts) { throw "hosts.csv пуст." }

# --- 6. CSV-экранирование по RFC4180 ---
function Quote-Field([string]$v, [string]$d) {
    if ($null -eq $v) { $v = '' }
    if ($v.Contains($d) -or $v.Contains('"') -or $v.Contains("`n") -or $v.Contains("`r")) {
        return '"' + $v.Replace('"', '""') + '"'
    }
    return $v
}

# --- 7. Собираем строки: клон default + подстановка per-host ---
$lines = @()
$lines += ($header | ForEach-Object { Quote-Field $_ $delim }) -join $delim

$n = 0
foreach ($h in $hosts) {
    $ip = ("$($h.ip)").Trim()
    if (-not $ip) { Write-Warning "строка без ip — пропуск"; continue }
    $name = ("$($h.name)").Trim(); if (-not $name) { $name = $ip -replace '[.:]', '-' }
    $user = ("$($h.user)").Trim(); if (-not $user) { $user = 'Administrator' }
    $pass = "$($h.password)"   # без Trim — пробел может быть частью пароля

    # значения по всем колонкам из эталона
    $row = @{}
    foreach ($c in $header) { $row[$c] = [string]$default.$c }
    # подстановки
    $row[$colName] = $name
    $row[$colHost] = $ip
    if ($colUser) { $row[$colUser] = $user }
    if ($colPass) { $row[$colPass] = $pass }
    if ($colDom)  { $row[$colDom]  = '' }
    if ($Resolution -and $colRes) { $row[$colRes] = $Resolution }

    $lines += ($header | ForEach-Object { Quote-Field ([string]$row[$_]) $delim }) -join $delim
    $n++
}

# --- 8. Пишем без BOM (BOM ломает имя первой колонки в mRemoteNG) ---
$text = ($lines -join "`r`n") + "`r`n"
[System.IO.File]::WriteAllText($Out, $text, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "Готово: $n подключений -> $Out" -ForegroundColor Green
Write-Host "Импорт: mRemoteNG -> File -> Import -> Import from CSV -> выбери этот файл." -ForegroundColor Green
if ($colRes -and -not $Resolution) {
    Write-Host ("Resolution наследован из template: '{0}' (проверь что это 2560x1440)." -f $default.$colRes)
}
