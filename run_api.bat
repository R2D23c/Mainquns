@echo off
chcp 65001 >nul
REM Запускается Task Scheduler'ом (schedule_hourly). Зовёт warmup_api.py
REM — чанковый API-флоу (4-6 × ~100 URL за раз, источник 40k_all_urls.txt).
REM
REM Предусловие: .api_activated и .session_imported уже есть. Их пишет
REM ручной первый запуск .\run.bat (UI install: login + API port + xlsx).
REM Если API-порт ещё не активирован — warmup_api.py упадёт на ping
REM и пришлёт high-priority ntfy.
cd /d "%~dp0"

set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)

%PY% warmup_api.py
exit /b %errorlevel%
