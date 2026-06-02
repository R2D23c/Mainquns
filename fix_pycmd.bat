@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM One-shot fix for machines installed BEFORE install.bat started writing
REM absolute paths to .python_cmd. Resolves the current interpreter to its
REM absolute python.exe path and rewrites .python_cmd. Needed because Task
REM Scheduler's env block can have stale PATH (no 'py' visible) and the
REM scheduled task silently fails with Last Result 1.
REM
REM Idempotent - safe to run multiple times.

set "PY=python"
if exist "%~dp0.python_cmd" set /p PY=<"%~dp0.python_cmd"

echo [fix_pycmd] current .python_cmd = %PY%
%PY% -c "import sys; print(sys.executable)" >nul 2>&1
if errorlevel 1 (
    echo [fix_pycmd] [ERROR] cannot run %PY% from this shell.
    echo Open a NEW cmd as admin and re-run, or just re-run install.bat.
    pause
    exit /b 1
)

for /f "delims=" %%i in ('%PY% -c "import sys; print(sys.executable)"') do set "PYEXE=%%i"
> "%~dp0.python_cmd" echo "%PYEXE%"
echo [fix_pycmd] rewrote .python_cmd to "%PYEXE%"
echo [fix_pycmd] done. Next scheduled trigger will use absolute path.
