@echo off
chcp 65001 >nul
REM Entry point for Task Scheduler. Logic:
REM   - first trigger on fresh machine - install not done yet
REM     (.api_activated / .session_imported missing) - call warmup.py
REM     (UI install: login + API port activation + session import).
REM   - subsequent triggers - both flags exist - call warmup_api.py
REM     (single ~100 random URL warmup from 40k_all_urls.txt).
REM
REM User flow on fresh VPS:
REM   install.bat -> enter email/password -> schedule_hourly.bat -> walk away.
cd /d "%~dp0"

set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)

if not exist "%~dp0.api_activated"    goto need_install
if not exist "%~dp0.session_imported" goto need_install

REM Install already done - run API flow.
%PY% warmup_api.py
exit /b %errorlevel%

:need_install
REM Install not done yet - warmup.py handles login + API port + import.
echo [run_api] install not complete yet, running UI flow (warmup.py)
%PY% warmup.py
exit /b %errorlevel%
