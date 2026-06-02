@echo off
chcp 65001 >nul
REM Install Python dependencies for warmup.py + Linken Sphere 2 + credentials.ini.
REM Prefers Python 3.12 (stable, all wheels available).

setlocal enabledelayedexpansion

REM 1) try py -3.12
py -3.12 --version >nul 2>&1
if not errorlevel 1 goto use_py312

REM 2) try any py -3
py -3 --version >nul 2>&1
if not errorlevel 1 goto use_py3

REM 3) fallback: python from PATH
where python >nul 2>&1
if errorlevel 1 goto no_python
set "PY=python"
echo Using python from PATH.
echo [WARN] Python 3.12 is recommended.
goto run_install

:use_py312
set "PY=py -3.12"
echo Using Python 3.12 via py launcher.
goto run_install

:use_py3
set "PY=py -3"
echo Using py launcher (any Python 3).
echo [WARN] Python 3.12 is recommended. If pip fails - install 3.12 from python.org.
goto run_install

:no_python
echo [ERROR] Python not found. Install Python 3.12 from https://python.org/downloads/ and check 'Add to PATH'.
pause
exit /b 1

:run_install
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto install_failed

REM Resolve %PY% to an ABSOLUTE python.exe path. Task Scheduler service can
REM run with a stale PATH (env block captured before Python was installed),
REM so 'py' / 'python' may not resolve when the task fires - Last Result 1
REM with no log entry. Absolute path is immune to PATH state.
for /f "delims=" %%i in ('%PY% -c "import sys; print(sys.executable)"') do set "PYEXE=%%i"
> "%~dp0.python_cmd" echo "%PYEXE%"
echo.
echo [OK] dependencies installed via %PY% (resolved to "%PYEXE%").

REM --- Linken Sphere 2 ---
set "LS_PATH=C:\Program Files (x86)\Linken Sphere 2\Linken Sphere 2.exe"
if exist "%LS_PATH%" (
    echo [OK] Linken Sphere 2 already installed.
    goto :after_ls
)

echo.
echo Linken Sphere 2 not found, downloading installer (~150 MB)...
set "LS_INSTALLER=%TEMP%\LinkenSphere2Setup.exe"
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://cdn.ls.app/Linken%%20Sphere%%202%%20Setup.exe' -OutFile '%LS_INSTALLER%' -UseBasicParsing"
if errorlevel 1 (
    echo [ERROR] Failed to download Linken Sphere 2.
    pause
    exit /b 1
)

echo Running installer silently...
REM Inno Setup: /VERYSILENT = no UI, /SUPPRESSMSGBOXES = auto-accept EULA,
REM /NORESTART = do not reboot. Installs for all users (admin required).
start /wait "" "%LS_INSTALLER%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

echo Waiting for .exe to appear (up to 90 seconds)...
set /a "WAITED=0"
:wait_install
if exist "%LS_PATH%" goto :install_done
timeout /t 3 /nobreak >nul
set /a "WAITED+=3"
if %WAITED% LSS 90 goto :wait_install
echo [WARN] Installer did not finish within 90 seconds.
echo Run it manually: %LS_INSTALLER%
echo Or set your path in config.ini -^> startup.linken_sphere_path
goto :after_ls
:install_done
echo [OK] Linken Sphere 2 installed at "%LS_PATH%"
del /q "%LS_INSTALLER%" 2>nul
:after_ls

REM --- credentials.ini ---
if not exist "%~dp0credentials.ini" (
    copy "%~dp0credentials.ini.example" "%~dp0credentials.ini" >nul
    echo.
    echo [!] credentials.ini created - enter your Linken Sphere 2 email and password.
    notepad "%~dp0credentials.ini"
) else (
    echo [OK] credentials.ini already exists.
)

echo.
echo Install complete.
echo Next: run schedule_hourly.bat (as admin) and walk away.
echo First scheduled trigger does login + API port activation + session import.
echo Subsequent triggers - chunked API warmup.
pause
exit /b 0

:install_failed
echo.
echo [ERROR] Failed to install dependencies.
echo Most likely Python 3.14+ which has no opencv-python wheel yet.
echo Install Python 3.12 from https://python.org/downloads/release/python-3127/
echo (check 'Add Python to PATH') and re-run install.bat.
pause
exit /b 1
