@echo off
chcp 65001 >nul
REM Регистрирует задачу в Windows Task Scheduler: запуск run.bat каждый час.
REM Запускается от имени текущего пользователя, БЕЗ повышения прав — окно cmd видно.
REM Админ нужен только если предыдущая задача была создана с /RL HIGHEST.

set "TASK_NAME=LinkenSphereWarmup"
set "RUN_CMD=%~dp0run.bat"

REM --- удалить старую задачу, если есть ---
schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo Удаляю предыдущую версию задачи "%TASK_NAME%"...
    schtasks /Delete /TN "%TASK_NAME%" /F >nul
    if errorlevel 1 (
        echo [ERROR] Не удалось удалить старую задачу. Запусти этот .bat от имени администратора.
        pause
        exit /b 1
    )
)

REM --- создать новую: каждый час, как текущий пользователь, интерактивно ---
schtasks /Create ^
    /TN "%TASK_NAME%" ^
    /TR "\"%RUN_CMD%\"" ^
    /SC HOURLY ^
    /MO 1 ^
    /IT ^
    /F

if errorlevel 1 (
    echo.
    echo [ERROR] Не удалось создать задачу.
    pause
    exit /b 1
)

echo.
echo [OK] Задача "%TASK_NAME%" создана.
echo  - Запускается каждый час, окно cmd будет видно.
echo  - Работает только когда ты залогинен в Windows.
echo.
echo Проверить сейчас: Win+R -^> taskschd.msc -^> Task Scheduler Library
echo                  -^> %TASK_NAME% -^> правой кнопкой -^> Run.
pause
