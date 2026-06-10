@echo off
chcp 65001 >nul
REM Entry point Task Scheduler'а для LsWatchdog (раз в 5 мин + AtStartup).
REM Логика самого watchdog'а в ls_watchdog.py — silently exits если
REM LS жива или цикл warmup_api идёт; перезапускает LS если API мёртвая
REM 3+ проверки подряд.
cd /d "%~dp0"

set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)

REM stdout/stderr → nul чтобы Task Scheduler не зависал на буфере.
REM Структурные события watchdog пишет в watchdog.log сам.
%PY% ls_watchdog.py >nul 2>&1
exit /b %errorlevel%
