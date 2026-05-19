# Linken Sphere 2 — Warm-up Automation

Автоматизирует функцию Warm-up в Linken Sphere 2 на Windows. Раз в час запускает прогрев профиля по случайному списку URL'ов, без участия пользователя.

## Что делает скрипт

1. Запускает Linken Sphere 2 (если не запущен)
2. Авторизуется (email/пароль из `credentials.ini`)
3. Открывает меню Warm up
4. Ставит `viewing depth = 3`, `time per url = 7`
5. Снимает галку "use most popular"
6. Выбирает случайный `.txt` файл с URL'ами из `urls/`
7. Удаляет первые 7 строк (по умолчанию) из загруженного списка
8. Жмёт START
9. Переносит использованный файл в `urls/done/`
10. Когда `urls/` пустеет — собирает все URL'ы из `done/`, перемешивает и нарезает новые порции

## Требования

- **Windows 10 / 11**
- **Python 3.12** (скачать с https://python.org/downloads/release/python-3127/ — при установке отметить **Add Python to PATH**)
- **Linken Sphere 2** — установится автоматически из `install.bat`
- **Подписка Linken Sphere** — любая (используется UI-автоматизация, API не нужен)
- Права администратора для первичной установки

## Установка

1. **Установить Python 3.12** с https://python.org/downloads/release/python-3127/ (галочка Add to PATH)
2. **Склонировать репозиторий**:
   ```
   git clone <repo-url> C:\warmup
   cd C:\warmup
   ```
3. **Правой кнопкой на `install.bat` → "Запуск от имени администратора"**
   - Поставит зависимости Python (`pyautogui`, `opencv-python`, `Pillow`, `numpy`)
   - Скачает и тихо установит Linken Sphere 2 (~150 MB)
   - Создаст `credentials.ini` и откроет его в Блокноте — впиши email и пароль от Linken Sphere
   - Откроет `config.ini` для проверки путей (обычно менять ничего не нужно)
4. **Запустить `schedule_hourly.bat` от имени администратора** — регистрирует задачу в Планировщике Windows на каждый час.

## Запуск вручную

```
run.bat
```

или напрямую:

```
py warmup.py
```

## Файлы проекта

| Файл | Назначение |
|---|---|
| `warmup.py` | Главный скрипт |
| `config.ini` | Настройки (пути, viewing_depth, time_per_url и т.д.) |
| `credentials.ini` | Email/пароль (не коммитится в git) |
| `credentials.ini.example` | Шаблон credentials.ini |
| `urls/*.txt` | Списки URL'ов для прогрева (91 файл по 100 URL'ов) |
| `urls/done/` | Использованные файлы (создаётся автоматически) |
| `templates/*.png` | Шаблоны UI-элементов для template matching |
| `requirements.txt` | Python-зависимости |
| `install.bat` | Первичная установка (Python deps + Linken Sphere 2 + credentials) |
| `run.bat` | Однократный ручной запуск |
| `schedule_hourly.bat` | Регистрация задачи в Планировщике (раз в час) |
| `warmup.log` | Лог работы |
| `screenshots/` | Скриншоты каждого шага для отладки |
| `HOW_IT_WORKS.txt` | Подробное описание архитектуры и используемых библиотек |

## Настройки (`config.ini`)

```ini
[paths]
files_dir = urls                # папка с .txt файлами; может быть абсолютной
file_glob = *.txt
regenerate_lines_per_file = 100 # сколько URL'ов в регенерированном файле

[startup]
linken_sphere_path = C:\Program Files (x86)\Linken Sphere 2\Linken Sphere 2.exe
launch_wait_seconds = 60

[warmup]
viewing_depth = 3
time_per_url = 7
reset_clicks = 25                # сколько раз кликнуть "-" чтобы сбросить stepper
uncheck_use_most_popular = true
remove_first_n_lines = 7         # удалить первые N строк из textarea
```

## Как это работает (кратко)

- **Pillow** делает скриншот экрана
- **OpenCV** ищет на скриншоте PNG-шаблоны из `templates/` (кнопки, поля)
- **pyautogui** двигает мышь и печатает на клавиатуре (через системные API — для ОС неотличимо от живого юзера)
- **ctypes** — Windows API напрямую: `FindWindow`, `GetWindowRect` для поиска окна авторизации, `ShellExecuteW` для запуска Linken Sphere, `SetProcessDpiAwarenessContext` для DPI
- **PowerShell Set-Clipboard** — вставка пароля через буфер обмена (нужно для `@`, `!` и других спецсимволов)

Подробнее — см. `HOW_IT_WORKS.txt`.

## Возможные проблемы

**`PyAutoGUI fail-safe triggered`** — клики ушли в угол экрана. Чаще всего из-за свёрнутого окна Linken Sphere. Скрипт автоматически разворачивает, но если не помогло — открой Linken Sphere вручную перед запуском.

**`элемент 'three_dots' не появился`** — Linken Sphere не показал главный экран. Проверь что вошёл (не висит на экране входа), и что окно НЕ свёрнуто.

**Установщик Linken Sphere показывает диалоги** — `install.bat` использует флаги Inno Setup `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`. Если у вас сборка другого типа — закройте диалог установщика, он продолжит автоматически.

**Кракозябры в консоли** — в `.bat` стоит `chcp 65001` для UTF-8. Если всё равно не работает — попробуй переключить язык консоли вручную: `chcp 65001`.

**Запуск из Планировщика не работает** — Task Scheduler по умолчанию может запускать без интерактивной сессии, тогда `pyautogui` не увидит экран. В свойствах задачи проверь:
- "Run only when user is logged on" (а не "whether user is logged on or not")
- "Run with highest privileges"

## Безопасность

`credentials.ini` находится в `.gitignore` и НИКОГДА не должен попасть в git. Только `credentials.ini.example` (с заглушками) коммитится в репозиторий.
