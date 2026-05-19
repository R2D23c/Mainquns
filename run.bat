@echo off
chcp 65001 >nul
REM Однократный запуск сценария — использует интерпретатор, выбранный в install.bat.
cd /d "%~dp0"

set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)

%PY% warmup.py
exit /b %errorlevel%
