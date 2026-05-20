@echo off
chcp 65001 >nul
REM Однократный запуск сценария — использует интерпретатор, выбранный в install.bat.
cd /d "%~dp0"

REM --- credentials.ini: создать из шаблона и открыть, если не заполнен ---
if not exist "%~dp0credentials.ini" (
    copy "%~dp0credentials.ini.example" "%~dp0credentials.ini" >nul
    echo [!] credentials.ini создан. Впиши email и пароль от Linken Sphere 2, сохрани и закрой Блокнот.
    notepad "%~dp0credentials.ini"
) else (
    findstr /c:"your@email.com" "%~dp0credentials.ini" >nul
    if not errorlevel 1 (
        echo [!] credentials.ini ещё с placeholder-значениями. Впиши свои email и пароль, сохрани и закрой Блокнот.
        notepad "%~dp0credentials.ini"
    )
)

set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)

%PY% warmup.py
exit /b %errorlevel%
