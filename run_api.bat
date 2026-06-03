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

REM --- Diagnostic log on EVERY invocation -----------------------------------
REM run_api.log captures the env so we can compare Task Scheduler vs manual
REM run. Critical when scheduler returns Last Result 1 with no useful detail.
REM Особенно важно SESSIONNAME — пустое значит Session 0 (не интерактивный
REM desktop), 'Console' / 'RDP-Tcp#N' значит окей.
set "RUNLOG=%~dp0run_api.log"
>> "%RUNLOG%" echo.
>> "%RUNLOG%" echo [%DATE% %TIME%] ============ run_api.bat invoked ============
>> "%RUNLOG%" echo [%DATE% %TIME%] cwd       : %CD%
>> "%RUNLOG%" echo [%DATE% %TIME%] user      : %USERNAME%
>> "%RUNLOG%" echo [%DATE% %TIME%] session   : "%SESSIONNAME%"
>> "%RUNLOG%" echo [%DATE% %TIME%] computer  : %COMPUTERNAME%

set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)
>> "%RUNLOG%" echo [%DATE% %TIME%] PY        : %PY%
>> "%RUNLOG%" echo [%DATE% %TIME%] flags     : api_activated=%exist:.api_activated% session_imported=%exist:.session_imported%

if not exist "%~dp0.api_activated"    goto need_install
if not exist "%~dp0.session_imported" goto need_install

>> "%RUNLOG%" echo [%DATE% %TIME%] dispatch  : warmup_api.py
%PY% warmup_api.py >> "%RUNLOG%" 2>&1
set "RC=%errorlevel%"
>> "%RUNLOG%" echo [%DATE% %TIME%] exit      : warmup_api.py rc=%RC%
exit /b %RC%

:need_install
>> "%RUNLOG%" echo [%DATE% %TIME%] dispatch  : warmup.py (install not complete)
echo [run_api] install not complete yet, running UI flow (warmup.py)
%PY% warmup.py >> "%RUNLOG%" 2>&1
set "RC=%errorlevel%"
>> "%RUNLOG%" echo [%DATE% %TIME%] exit      : warmup.py rc=%RC%
exit /b %RC%
