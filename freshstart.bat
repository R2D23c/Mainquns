@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Fresh restart from the "after notepad" phase. Use when LS hangs or you
REM want to re-run warmup from a clean slate WITHOUT reinstalling Git /
REM Python / Linken Sphere / re-entering credentials.
REM
REM What this does:
REM   1. Kills any running Linken Sphere processes (frees hung session).
REM   2. Deletes warmup state: .session_name / .session_imported /
REM      .api_activated / .warmup_state / .warmup_target / .warmup_count /
REM      .warmup_started_at / .notified_done / .first_start /
REM      session_imports\CL-*.xlsx /
REM      screenshots\ / *.log
REM   3. Re-enables the scheduled task (in case it was auto-disabled).
REM   4. Triggers the task immediately - no need to wait 45 minutes.
REM
REM Does NOT touch:
REM   credentials.ini, config.ini, templates\, urls\, requirements.txt.

echo.
echo [fresh] killing Linken Sphere processes...
taskkill /F /IM "Linken Sphere 2.exe" /T >nul 2>&1

echo [fresh] cleaning warmup state...
if exist "%~dp0.session_name"     del /q "%~dp0.session_name"
if exist "%~dp0.session_imported" del /q "%~dp0.session_imported"
if exist "%~dp0.api_activated"    del /q "%~dp0.api_activated"
if exist "%~dp0.warmup_state"     del /q "%~dp0.warmup_state"
if exist "%~dp0.warmup_target"    del /q "%~dp0.warmup_target"
if exist "%~dp0.warmup_count"     del /q "%~dp0.warmup_count"
if exist "%~dp0.warmup_started_at" del /q "%~dp0.warmup_started_at"
if exist "%~dp0.notified_done"    del /q "%~dp0.notified_done"
if exist "%~dp0.first_start"      del /q "%~dp0.first_start"
if exist "%~dp0.wizard_dismissed" del /q "%~dp0.wizard_dismissed"
if exist "%~dp0.watchdog_fail_count" del /q "%~dp0.watchdog_fail_count"

for /f "delims=" %%f in ('dir /b /a-d "%~dp0session_imports\CL-*.xlsx" 2^>nul') do (
    del /q "%~dp0session_imports\%%f"
)

if exist "%~dp0screenshots\"     rmdir /s /q "%~dp0screenshots\"
if exist "%~dp0cookies_export\"  rmdir /s /q "%~dp0cookies_export\"
if exist "%~dp0warmup.log"       del /q "%~dp0warmup.log"
if exist "%~dp0warmup_api.log"   del /q "%~dp0warmup_api.log"
if exist "%~dp0watchdog.log"     del /q "%~dp0watchdog.log"

echo [fresh] deleting old scheduled tasks (if exists)...
REM Полное уничтожение существующих LinkenSphereWarmup и LsWatchdog. Безопасно
REM даже если их нет — /f подавляет "не найдено" в return code, 2>nul глушит
REM stdout/stderr. Это убирает любые leftover-состояния (stale principal,
REM trigger time из прошлого, action указывающий на устаревший путь и
REM т.п.) которые могли конфликтовать со свежим прогоном.
schtasks /delete /tn LinkenSphereWarmup /f >nul 2>&1
schtasks /delete /tn LsWatchdog /f >nul 2>&1

echo [fresh] registering FRESH scheduled task (auto-triggers in 15s)...
REM schedule_hourly.ps1 умеет это полностью: Unregister + Register-Force +
REM first trigger через 15с + RepetitionInterval 45 мин. После этого
REM schtasks /run не нужен — fresh trigger сам сработает.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule_hourly.ps1"

echo.
echo [fresh] done. UI flow starts within seconds.
echo         Watch ntfy / Telegram for the next push.
echo         Note: the LS ghost session may stay inside Linken Sphere -
echo         it is harmless, next run creates a fresh CL-XXXXXXXX next to it.
