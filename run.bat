@echo off
REM Однократный запуск сценария
cd /d "%~dp0"
python warmup.py
exit /b %errorlevel%
