@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Full state reset for debugging from scratch.
REM
REM Deletes:
REM   .session_name        - generated session name CL-XXXXXXXX
REM   .session_imported    - flag "xlsx already imported in LS"
REM   .api_activated       - flag "API port activated in LS"
REM   .warmup_state        - success counter for first-N push notifications
REM   session_imports\CL-*.xlsx - clones of template with unique names
REM   screenshots\         - all screenshots from previous runs
REM   warmup.log, warmup_api.log
REM
REM Does NOT touch:
REM   credentials.ini      - email/password (filled by user)
REM   templates\           - template-matching images
REM   session_imports\_template.xlsx - import template
REM   config.ini
REM   urls\                - URL pool

echo.
echo [reset] cleaning machine state...

if exist "%~dp0.session_name"     ( del /q "%~dp0.session_name"     && echo  - .session_name )
if exist "%~dp0.session_imported" ( del /q "%~dp0.session_imported" && echo  - .session_imported )
if exist "%~dp0.api_activated"    ( del /q "%~dp0.api_activated"    && echo  - .api_activated )
if exist "%~dp0.warmup_state"     ( del /q "%~dp0.warmup_state"     && echo  - .warmup_state )
if exist "%~dp0.warmup_target"    ( del /q "%~dp0.warmup_target"    && echo  - .warmup_target )
if exist "%~dp0.warmup_count"     ( del /q "%~dp0.warmup_count"     && echo  - .warmup_count )
if exist "%~dp0.notified_done"    ( del /q "%~dp0.notified_done"    && echo  - .notified_done )
if exist "%~dp0.first_start"      ( del /q "%~dp0.first_start"      && echo  - .first_start )

REM Re-enable the scheduled task if it was auto-disabled after target reached.
schtasks /change /tn LinkenSphereWarmup /enable >nul 2>&1 && echo  - LinkenSphereWarmup re-enabled

for /f "delims=" %%f in ('dir /b /a-d "%~dp0session_imports\CL-*.xlsx" 2^>nul') do (
    del /q "%~dp0session_imports\%%f" && echo  - session_imports\%%f
)

if exist "%~dp0screenshots\" (
    rmdir /s /q "%~dp0screenshots\" && echo  - screenshots\
)
if exist "%~dp0warmup.log"     ( del /q "%~dp0warmup.log"     && echo  - warmup.log )
if exist "%~dp0warmup_api.log" ( del /q "%~dp0warmup_api.log" && echo  - warmup_api.log )

echo.
echo [!] The imported session ghost stays inside Linken Sphere.
echo     For full LS cleanup - open LS, right-click the session -^> Delete.
echo     Otherwise next run.bat will create a new session next to the old one.
echo.
echo [reset] done. Run .\run.bat - clean slate.
pause
