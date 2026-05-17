@echo off
REM Установка зависимостей для warmup.py
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python не найден в PATH. Установи Python 3.10+ с python.org и поставь галку "Add to PATH".
    pause
    exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] не удалось установить зависимости.
    pause
    exit /b 1
)
echo.
echo [OK] зависимости установлены.
pause
