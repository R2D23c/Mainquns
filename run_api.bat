@echo off
chcp 65001 >nul
REM Точка входа для Task Scheduler. Логика:
REM   - первый триггер на свежей машине → install ещё не сделан
REM     (.api_activated / .session_imported отсутствуют) → зовём warmup.py
REM     (UI install: login + активация API-порта + импорт сессии).
REM   - следующие триггеры → оба флага уже есть → зовём warmup_api.py
REM     (чанковый API-флоу: 4-6 × ~100 URL из 40k_all_urls.txt).
REM
REM То есть юзер делает только: install.bat → впиши email/пароль →
REM schedule_hourly.bat → ушёл от компа. Дальше всё само.
cd /d "%~dp0"

set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)

if not exist "%~dp0.api_activated"    goto need_install
if not exist "%~dp0.session_imported" goto need_install

REM Install уже прошёл — гоняем чанковый API-флоу.
%PY% warmup_api.py
exit /b %errorlevel%

:need_install
REM Install ещё не прошёл — warmup.py сам сделает login + API port + import.
echo [run_api] install ещё не завершён, гоню UI-флоу (warmup.py)
%PY% warmup.py
exit /b %errorlevel%
