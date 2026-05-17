@echo off
REM Регистрирует задачу в Windows Task Scheduler: запуск warmup.py каждый час.
REM Запускать ОТ ИМЕНИ АДМИНИСТРАТОРА.

set TASK_NAME=LinkenSphereWarmup
set SCRIPT_DIR=%~dp0
set RUN_CMD=%SCRIPT_DIR%run.bat

schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo задача %TASK_NAME% уже существует, удаляю старую...
    schtasks /Delete /TN "%TASK_NAME%" /F
)

schtasks /Create ^
    /TN "%TASK_NAME%" ^
    /TR "\"%RUN_CMD%\"" ^
    /SC HOURLY ^
    /MO 1 ^
    /RL HIGHEST ^
    /F

if errorlevel 1 (
    echo [ERROR] не удалось создать задачу. Запусти этот .bat от имени администратора.
    pause
    exit /b 1
)

echo.
echo [OK] задача "%TASK_NAME%" создана. Будет запускаться каждый час.
echo посмотреть/изменить: taskschd.msc
pause
