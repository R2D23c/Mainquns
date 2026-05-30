@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Полная очистка состояния этой машины для отладки с нуля.
REM
REM Что удаляет:
REM   .session_name        — сгенерированное имя сессии CL-XXXXXXXX
REM   .session_imported    — флаг «xlsx уже импортирован в LS»
REM   .api_activated       — флаг «API-порт активирован в LS»
REM   .warmup_state        — счётчик первых успешных push-уведомлений
REM   session_imports\CL-*.xlsx — клон шаблона с уникальным именем
REM   screenshots\         — все скриншоты прошлых запусков
REM   warmup.log, warmup_api.log
REM
REM Что НЕ трогает:
REM   credentials.ini      — email/password (вписаны юзером)
REM   templates\           — картинки для template-matching
REM   session_imports\_template.xlsx — эталон импорта
REM   config.ini
REM   urls\                — пул URL'ов

echo.
echo [reset] чищу состояние машины...

if exist "%~dp0.session_name"     ( del /q "%~dp0.session_name"     && echo  - .session_name )
if exist "%~dp0.session_imported" ( del /q "%~dp0.session_imported" && echo  - .session_imported )
if exist "%~dp0.api_activated"    ( del /q "%~dp0.api_activated"    && echo  - .api_activated )
if exist "%~dp0.warmup_state"     ( del /q "%~dp0.warmup_state"     && echo  - .warmup_state )

for /f "delims=" %%f in ('dir /b /a-d "%~dp0session_imports\CL-*.xlsx" 2^>nul') do (
    del /q "%~dp0session_imports\%%f" && echo  - session_imports\%%f
)

if exist "%~dp0screenshots\" (
    rmdir /s /q "%~dp0screenshots\" && echo  - screenshots\
)
if exist "%~dp0warmup.log"     ( del /q "%~dp0warmup.log"     && echo  - warmup.log )
if exist "%~dp0warmup_api.log" ( del /q "%~dp0warmup_api.log" && echo  - warmup_api.log )

echo.
echo [!] Призрак сессии останется внутри Linken Sphere (та, что импортилась).
echo     Если хочешь с нуля и в LS — открой её, удали правой кнопкой -^> Delete.
echo     Иначе при следующем run.bat появится новая сессия рядом со старой.
echo.
echo [reset] готово. Теперь .\run.bat — начнётся с чистого листа.
pause
