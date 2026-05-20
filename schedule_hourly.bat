@echo off
chcp 65001 >nul
REM Регистрирует задачу через PowerShell — корректный режим Win8+, видимое окно.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule_hourly.ps1"

if errorlevel 1 (
    echo.
    echo [ERROR] Не удалось создать задачу.
    pause
    exit /b 1
)

pause
