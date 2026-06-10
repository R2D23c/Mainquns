@echo off
REM ls_launch.bat — idempotent launcher for Linken Sphere 2 + boot notification.
REM Запускается из Startup folder при логоне Admin. Логика:
REM   1. Фоном дёрнуть notify_boot.py (анти-спам по .last_boot, шлёт ntfy
REM      при свежем boot'е). НЕ блокируем запуск LS — оба выполняются
REM      параллельно. notify_boot сам разберётся был это reboot или relogin.
REM   2. Если LS уже работает (после reboot + autologin или после первого
REM      запуска) — НИЧЕГО НЕ ДЕЛАЕМ. Защита от двойного инстанса при RDP
REM      logon на Windows Server (создаёт новую session → Startup folder
REM      выстреливает повторно → второй LS показывает alert "already running").
REM   3. Если LS не найдена — запускаем как двойной клик в Explorer
REM      (через start, без блокировки родительского процесса).
setlocal

REM Boot detection в background — не задерживаем LS launch.
REM .python_cmd содержит абсолютный путь к py.exe (пишется install.bat).
REM Если файла нет — пробуем `python` из PATH; если и его нет — silent skip.
set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)
if exist "%~dp0notify_boot.py" (
    start "" /B "%PY%" "%~dp0notify_boot.py" >nul 2>&1
)

tasklist /FI "IMAGENAME eq Linken Sphere 2.exe" 2>nul | find /I "Linken Sphere 2.exe" >nul
if not errorlevel 1 (
    >> "%~dp0ls_launch.log" echo [%DATE% %TIME%] LS already running — skip
    exit /b 0
)

set "LS=C:\Program Files (x86)\Linken Sphere 2\Linken Sphere 2.exe"
if not exist "%LS%" set "LS=C:\Program Files\Linken Sphere 2\Linken Sphere 2.exe"
if not exist "%LS%" (
    >> "%~dp0ls_launch.log" echo [%DATE% %TIME%] LS executable not found
    exit /b 1
)

start "" "%LS%"
>> "%~dp0ls_launch.log" echo [%DATE% %TIME%] launched %LS%
endlocal
