@echo off
chcp 65001 >nul
REM Manual single run of warmup.py - uses the interpreter chosen by install.bat.
cd /d "%~dp0"

REM --- credentials.ini: create from template and open if not filled ---
if not exist "%~dp0credentials.ini" (
    copy "%~dp0credentials.ini.example" "%~dp0credentials.ini" >nul
    echo [!] credentials.ini created. Enter your Linken Sphere 2 email and password, save, close Notepad.
    notepad "%~dp0credentials.ini"
) else (
    findstr /c:"your@email.com" "%~dp0credentials.ini" >nul
    if not errorlevel 1 (
        echo [!] credentials.ini still has placeholder values. Enter your email/password, save, close Notepad.
        notepad "%~dp0credentials.ini"
    )
)

set "PY=python"
if exist "%~dp0.python_cmd" (
    set /p PY=<"%~dp0.python_cmd"
)

%PY% warmup.py
exit /b %errorlevel%
