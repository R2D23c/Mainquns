@echo off
chcp 65001 >nul
REM Registers the Task Scheduler job via PowerShell - correct Win8+ mode, visible cmd window.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule_hourly.ps1"

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to register the task.
    pause
    exit /b 1
)

pause
