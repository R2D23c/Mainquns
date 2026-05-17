@echo off
REM Установка зависимостей для warmup.py
REM Предпочитает Python 3.12 (стабильный, все wheel-файлы доступны).
REM Если 3.12 нет — пробует py -3, затем python из PATH.

setlocal

REM 1) пробуем py -3.12 (Python launcher)
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.12"
    echo Использую Python 3.12 через py launcher.
    goto run_install
)

REM 2) пробуем любую установленную py-версию
py -3 --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=2" %%v in ('py -3 --version') do set "PYVER=%%v"
    set "PY=py -3"
    echo Использую py launcher: Python %PYVER%.
    echo [WARN] Рекомендуется Python 3.12. Если установка пакетов упадёт — поставь 3.12 с python.org.
    goto run_install
)

REM 3) fallback: python из PATH
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python не найден. Поставь Python 3.12 с https://python.org/downloads/ и отметь "Add to PATH".
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version') do set "PYVER=%%v"
set "PY=python"
echo Использую python из PATH: Python %PYVER%.
echo [WARN] Рекомендуется Python 3.12.

:run_install
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [ERROR] Не удалось установить зависимости.
    echo Скорее всего у тебя слишком новый Python (3.14+), под который opencv-python ещё
    echo не собран. Поставь Python 3.12 с https://python.org/downloads/release/python-3127/
    echo при установке отметь "Add Python to PATH" и запусти install.bat заново.
    pause
    exit /b 1
)

echo.
echo [OK] зависимости установлены через %PY%.

REM запомним выбранный интерпретатор для run.bat
echo %PY%> "%~dp0.python_cmd"
pause
endlocal
