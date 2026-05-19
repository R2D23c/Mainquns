@echo off
chcp 65001 >nul
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

REM --- Linken Sphere 2 ---
set "LS_PATH=C:\Program Files (x86)\Linken Sphere 2\Linken Sphere 2.exe"
if exist "%LS_PATH%" (
    echo [OK] Linken Sphere 2 уже установлен.
    goto :after_ls
)

echo.
echo Linken Sphere 2 не найден, скачиваю установщик (~150 MB)...
set "LS_INSTALLER=%TEMP%\LinkenSphere2Setup.exe"
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://cdn.ls.app/Linken%%20Sphere%%202%%20Setup.exe' -OutFile '%LS_INSTALLER%' -UseBasicParsing"
if errorlevel 1 (
    echo [ERROR] Не удалось скачать Linken Sphere 2.
    pause
    exit /b 1
)

echo Запускаю установщик в тихом режиме...
REM Inno Setup: /VERYSILENT = без UI, /SUPPRESSMSGBOXES = автопринятие EULA,
REM /NORESTART = не перезагружать систему. Установка для всех (нужны права админа).
start /wait "" "%LS_INSTALLER%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

echo Жду появления .exe (до 90 секунд)...
set /a "WAITED=0"
:wait_install
if exist "%LS_PATH%" goto :install_done
timeout /t 3 /nobreak >nul
set /a "WAITED+=3"
if %WAITED% LSS 90 goto :wait_install
echo [WARN] За 90 секунд установка не завершилась.
echo Запусти установщик вручную: %LS_INSTALLER%
echo или впиши свой путь в config.ini -^> startup.linken_sphere_path
goto :after_ls
:install_done
echo [OK] Linken Sphere 2 установлен в "%LS_PATH%"
del /q "%LS_INSTALLER%" 2>nul
:after_ls

REM --- credentials.ini ---
if not exist "%~dp0credentials.ini" (
    copy "%~dp0credentials.ini.example" "%~dp0credentials.ini" >nul
    echo.
    echo [!] Создан credentials.ini — впиши свои email и пароль от Linken Sphere 2.
    notepad "%~dp0credentials.ini"
) else (
    echo [OK] credentials.ini уже существует.
)

REM --- config.ini ---
echo.
echo Проверь пути в config.ini (папка с .txt файлами и путь к Linken Sphere 2):
notepad "%~dp0config.ini"

echo.
echo Установка завершена.
echo Для запуска по расписанию (раз в час) запусти schedule_hourly.bat от имени администратора.
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
