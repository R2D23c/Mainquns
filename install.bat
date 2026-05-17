@echo off
REM Установка зависимостей для warmup.py.
REM Предпочитает Python 3.12 (стабильный, все wheel-файлы доступны).

setlocal enabledelayedexpansion

REM 1) пробуем py -3.12
py -3.12 --version >nul 2>&1
if not errorlevel 1 goto use_py312

REM 2) пробуем любую py-3
py -3 --version >nul 2>&1
if not errorlevel 1 goto use_py3

REM 3) fallback: python из PATH
where python >nul 2>&1
if errorlevel 1 goto no_python
set "PY=python"
echo Использую python из PATH.
echo [WARN] Рекомендуется Python 3.12.
goto run_install

:use_py312
set "PY=py -3.12"
echo Использую Python 3.12 через py launcher.
goto run_install

:use_py3
set "PY=py -3"
echo Использую py launcher любой версии Python 3.
echo [WARN] Рекомендуется именно 3.12. Если упадёт - поставь 3.12 с python.org.
goto run_install

:no_python
echo [ERROR] Python не найден. Поставь Python 3.12 с https://python.org/downloads/ и отметь Add to PATH.
pause
exit /b 1

:run_install
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto install_failed

echo %PY%> "%~dp0.python_cmd"
echo.
echo [OK] зависимости установлены через %PY%.
echo интерпретатор записан в .python_cmd
pause
exit /b 0

:install_failed
echo.
echo [ERROR] Не удалось установить зависимости.
echo Скорее всего у тебя слишком новый Python 3.14+, под который opencv-python ещё не собран.
echo Поставь Python 3.12 с https://python.org/downloads/release/python-3127/
echo при установке отметь Add Python to PATH и запусти install.bat заново.
pause
exit /b 1
