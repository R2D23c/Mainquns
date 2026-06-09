@echo off
REM ls_launch.bat — idempotent launcher for Linken Sphere 2.
REM Запускается из Startup folder при логоне Admin. Логика:
REM   1. Если LS уже работает (после reboot + autologin или после первого
REM      запуска) — НИЧЕГО НЕ ДЕЛАЕМ. Защита от двойного инстанса при RDP
REM      logon на Windows Server (создаёт новую session → Startup folder
REM      выстреливает повторно → второй LS показывает alert "already running").
REM   2. Если LS не найдена — запускаем как двойной клик в Explorer
REM      (через start, без блокировки родительского процесса).
setlocal

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
