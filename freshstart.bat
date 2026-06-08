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
echo [fresh] killing Linken Sphere processes (+ embedded Chromium / Electron helpers)...
REM Шаг 1: taskkill по имени с /T (kill children) — основной случай, ловит
REM    Linken Sphere 2.exe + его прямых child процессов (renderer/gpu/utility).
taskkill /F /IM "Linken Sphere 2.exe" /T >nul 2>&1

REM Шаг 2: PowerShell-добивка по install path. Ловит то что таскил пропустил:
REM    - detached Chromium subprocess (Electron спавнит renderer/gpu
REM      которые иногда отвязываются от parent'а)
REM    - helper exe внутри LS folder (Squirrel updater, обновлятель)
REM    - зомби-процессы без parent'а (после кривого exit'а warmup'а)
REM    SilentlyContinue потому что если ничего не нашлось — это норма.
powershell -NoProfile -Command "Get-Process -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -like 'C:\Program Files (x86)\Linken Sphere 2\*' } catch { $false } } | Stop-Process -Force -ErrorAction SilentlyContinue" >nul 2>&1

REM Шаг 3: пауза на flush — LS пишет последний state на диск (~2с)
timeout /t 2 /nobreak >nul 2>&1

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

for /f "delims=" %%f in ('dir /b /a-d "%~dp0session_imports\CL-*.xlsx" 2^>nul') do (
    del /q "%~dp0session_imports\%%f"
)

if exist "%~dp0screenshots\"     rmdir /s /q "%~dp0screenshots\"
if exist "%~dp0cookies_export\"  rmdir /s /q "%~dp0cookies_export\"
if exist "%~dp0warmup.log"       del /q "%~dp0warmup.log"
if exist "%~dp0warmup_api.log"   del /q "%~dp0warmup_api.log"

echo [fresh] re-enabling scheduled task...
schtasks /change /tn LinkenSphereWarmup /enable >nul 2>&1

echo [fresh] triggering warmup NOW...
schtasks /run /tn LinkenSphereWarmup

echo.
echo [fresh] done. UI flow starts within seconds.
echo         Watch ntfy / Telegram for the next push.
echo         Note: the LS ghost session may stay inside Linken Sphere -
echo         it is harmless, next run creates a fresh CL-XXXXXXXX next to it.
