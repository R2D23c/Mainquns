# Контекст для Claude

Этот файл автоматически загружается Claude Code в начале каждой сессии.
Прочитай его до того, как предлагать изменения в коде.

## Что это за репо

Конвейер прогрева антидетект-сессий Linken Sphere 2 на парке Windows VPS.
Одна PowerShell команда → 10-15 минут setup → 3-9 часов автономного warmup
→ файл с 5-7k cookies + push-уведомление 🎉. **Без оператора после старта.
Без RDP-вмешательств. Выживает после Kernel-Power 41 / Windows Update / hard reset.**

**Репо публичный** (`r2d23c/mainquns`). Никаких токенов / паролей в
коммитах. `credentials.ini` в `.gitignore`, проверяй перед коммитом.

## Главный документ — architecture.pdf

В корне репо лежит **architecture.pdf** (v3.0 Deep Dive, ~470 KB) — полная
презентационная архитектура: 14 секций, 22 edge case'а E-01..E-22,
10 unhandled U-01..U-10, история коммитов, operational guide.

Если оператор просит "обновить PDF" или "пересобрать архитектуру" —
скрипт генерации в `/tmp/build_pdf.py` (если есть), либо пересоздай
по образцу.

## Гайд по настройкам — CONFIG_GUIDE.md

**Прочитай ОБЯЗАТЕЛЬНО, если оператор просит изменить**: количество
прогреваемых URL (300-500 → другой диапазон), разнообразить XLSX
fingerprint'ы (видеокарты, CPU, RAM, экраны, Windows версии), заменить
или дополнить URL pool (40k_all_urls.txt), поменять тайминги, ntfy
топик, расписание Task Scheduler.

CONFIG_GUIDE.md покрывает: какой файл править, что произойдёт на новых
VPS vs работающих, нужна ли миграция state-файла, чек-лист после
изменения. Не дублируй эту инфу в CLAUDE.md — обновляй CONFIG_GUIDE.md.

## Ветки

- **main** — то что качают машины через `setup.ps1` (`raw.../main/setup.ps1`)
- **claude/linken-sphere-warmup-config-E3xW7** — рабочая ветка

После коммита **пуш в обе**: оператор использует main, harness требует
claude. Локальное состояние может сбрасываться между ходами — если
push отказался, делай `git fetch origin && git rebase origin/main`.

## Критичные правила

### 1. Не правь UI flow без подтверждения оператора
Особенно: `login_if_needed`, `_dismiss_customize_wizard_step`,
`ensure_linken_sphere_running`, `import_session_if_needed`,
`activate_api_port_if_needed`. Каждое "улучшение" здесь имеет шанс
сломать установку на 10+ машинах.

### 2. Не понижай PNG-template thresholds без причины
`skip` теперь **0.76** (понижено эмпирически 0.85 → 0.80 → 0.76 под новые
LS tutorial overlay'и), close_x `0.90`, get_started/2/2_v2 `0.80`.
Дальнейшее понижение → false positives → клики в пустоту.

### 3. Не вводи новые auto-set флаги
Флаги вроде `.wizard_dismissed` (которые код **сам** ставит) — фрагильны:
если что-то пошло не так до их записи, ломается следующий запуск.
Предпочитай явные операторские флаги или жёсткую идемпотентность
(`.api_activated`, `.session_imported`, `.notified_done` — пишутся только
после полностью успешной фазы).

### 4. Сохраняй обратную совместимость с уже-запущенными машинами
Если оператор запустил 10 VPS со старой версией, а ты выкатил новую —
старые цикл-warmup'ы должны продолжать работать. Не меняй структуру
state-файлов / xlsx fingerprint'ов без миграции.

### 5. Думай про парк машин, не одну
Все VPS под одним LS-аккаунтом. Конфликты по сессиям, лок-storm'ы —
реальные проблемы. Anti-collision jitter 0-120с и 9-step 409 retry pyramid
это решают, не трогай без понимания.

### 6. Спрашивай оператора перед коммитом архитектурных изменений
Оператор хочет видеть план/обсуждение **до** коммита. Только мелкие
очевидные fixes (typo, syntax) можно коммитить сразу.

### 7. Тайминги после reboot — будь консервативным
LS нужно 1-3 мин на запуск + автологин в кэш. Task Scheduler AtStartup
имеет Delay=PT2M. Watchdog имеет Delay=PT3M + UPTIME_GUARD_MIN=10. Не
делай агрессивных таймингов которые могут конфликтовать с естественным
recovery после boot.

## Архитектура в одну схему

```
[OPERATOR]
   │ iex setup.ps1 + 3 env vars
   ▼
[SETUP] (10-15 мин)
   │ Git/Python/Repo/install.bat/Steps 4.5-5
   ▼
[UI INSTALL] warmup.py (5-10 мин, ОДНОКРАТНО)
   │ ensure_LS → login + Autologin toggle → API port → session import
   │ → SIGN IN → запись .api_activated + .session_imported
   ▼
[CYCLES] warmup_api.py (~37 мин/цикл, каждые 45 мин)
   │ ping → signin "Already signed in" → 15 чанков × 7 URL
   │ → ⚙️ warmup cycle push после каждого
   │
   ▼ (3-9 циклов до .warmup_target=random(300,500))
   │
[FINISH] target reached
   │ schtasks /change /disable LinkenSphereWarmup
   │ LsWatchdog self-disable при .notified_done
   │ count_session_cookies → export → 🎉 push
   ▼
[IDLE] оператор забирает cookies, списывает VPS
```

## Слои защиты (6 layers)

```
LAYER 6  Watchdog stuck detection: если main task не тикала >75 мин
         → force-trigger через schtasks /run

LAYER 5  Watchdog API ping: если 36555 down 3 раза подряд (15 мин)
         → kill LS + ls_launch.bat (CREATE_NO_WINDOW)

LAYER 4  warmup_api self-recovery: connection refused в pyramid →
         kill+launch LS + retry. Max 2 recoveries per chunk.
         🔧 push на успешный recovery, ⚠️ если не помогло.

LAYER 3  9-step retry pyramid для 409 conflict: 10s/30/60/120/240/360/
         force_stop/360/480+nuclear. Покрывает 8+ VPS на одном LS аккаунте.

LAYER 2  Task Scheduler: Time (45 мин) + AtStartup (Delay PT2M).
         AtStartup критичен для recovery после reboot — Time trigger
         с UnifiedSchedulingEngine иногда "забывает" после reboot.

LAYER 1  Boot recovery: AutoAdminLogon → Startup folder → ls_launch.bat
         (с правильной cwd /D для evo:// protocol) → notify_boot.py → 🔄

LAYER 0  Preventive: NoAutoRebootWithLoggedOnUsers, lockoutthreshold=0,
         firewall preauth ВСЕХ LS .exe.
```

## Структура файлов

```
# Bootstrap
setup.ps1              ← master installer (Steps 0-5)
quickstart.ps1         ← wrapper с TLS 1.2
quickstart-retry.ps1   ← ретрай UI install без bootstrap
install.bat            ← Python deps + LS Inno Setup silent (150MB)

# Pipeline
warmup.py              ← UI install (~1900 строк) — login, API port, session
                         ВАЖНО: на старте делает ShowWindow(SW_HIDE) консоли
                         (она пустая, мешает pyautogui clicks)
warmup_api.py          ← боевой цикл API (~1400 строк) — НЕ скрывает console
                         (banner "WARMUP IS RUNNING" от AllocConsole нужен)
session_template.py    ← XLSX fingerprint с ADAPTER_PROFILES
_firewall_watcher.py   ← фоновый sentinel дисмиссит Defender (CREATE_NO_WINDOW)
run_api.bat            ← Task Scheduler dispatcher: warmup.py vs warmup_api.py

# Recovery & monitoring
ls_launch.bat          ← Startup folder wrapper. /D <install_dir> для evo://
                         + спавнит notify_boot.py в background. CRITICAL:
                         %PY% БЕЗ кавычек (.python_cmd уже с кавычками).
notify_boot.py         ← boot detection через LastBootUpTime FileTime.
                         Skip-first / skip-same-boot / sent.
ls_watchdog.py         ← 5-мин ping API + stuck detection + self-disable
                         после .notified_done. CREATE_NO_WINDOW для restart.
run_watchdog.bat       ← entry point Task Scheduler через wscript+VBS hidden
run_hidden.vbs         ← VBS wrapper для невидимого запуска cmd
                         (Task Scheduler с Interactive создаёт visible cmd)

# Scheduling
schedule_hourly.ps1    ← регистрирует ОБЕ задачи: LinkenSphereWarmup +
                         LsWatchdog. LinkenSphereWarmup имеет 2 триггера
                         (Time + AtStartup PT2M). LsWatchdog запускается
                         через wscript.exe + run_hidden.vbs.

# Maintenance
freshstart.bat         ← clean restart state (включая .last_boot,
                         .watchdog_fail_count, watchdog.log)
fix_pycmd.bat          ← починить .python_cmd
restart.bat            ← soft restart

# Data
templates/             ← 24 PNG для cv2.matchTemplate
                         + autologin_toggle.png, get_started2_v2.png, allow_access3.png
urls/40k_all_urls.txt  ← 40 144 уникальных URL для прогрева
session_imports/       ← _template.xlsx + сгенерированные CL-*.xlsx
cookies_export/        ← финальные cookie-файлы (deliverable)

# Docs
architecture.pdf       ← v3.0 Deep Dive (470 KB, 14 секций, 22 edge cases)
PIPELINE.md            ← старый pipeline doc (опционально читать)
ИНСТРУКЦИЯ.md          ← user-facing docs
README.md              ← repo intro
CLAUDE.md              ← этот файл
```

## State-файлы (все gitignored)

| Файл | Содержимое | Когда пишется |
|---|---|---|
| `.api_activated` | 36555 | UI install |
| `.session_imported` | CL-XXXXXXXX | UI install |
| `.session_name` | CL-XXXXXXXX | warmup.py first |
| `.warmup_target` | random(300,500) | первый запуск warmup_api |
| `.warmup_count` | int | после каждого цикла |
| `.warmup_started_at` | unix timestamp | вместе с .warmup_target |
| `.notified_done` | пустой | target reached |
| `.last_boot` | Win FileTime | каждый запуск notify_boot |
| `.watchdog_fail_count` | 0..3 | при API ❌ |
| `credentials.ini` | email/password | install.bat |

## Ключевые edge cases (топ-10 которые ломали продакшн)

| ID | Симптом | Fix |
|---|---|---|
| E-04 | iwr fails "SSL/TLS" | TLS 1.2 first line `[Net.ServicePointManager]::SecurityProtocol = ...Tls12` |
| E-07 | AutoAdminLogon молчит | `$env:USERNAME` + `$env:COMPUTERNAME`, не "Administrator" |
| E-10 | LS показывает `evo://gui/...` ERR | `start "" /D <install_dir>` в ls_launch.bat |
| E-12 | "Windows cannot find C:\Program" | %PY% БЕЗ кавычек (.python_cmd уже закавычен) |
| E-13 | TS не тикает после reboot | AtStartup trigger Delay PT2M |
| E-14 | Windows Update reboot прерывает | NoAutoRebootWithLoggedOnUsers=1 |
| E-15 | RDP не пускает после hard reset | net accounts /lockoutthreshold:0 |
| E-16 | LS API death mid-cycle ⚠️ failed (api) | warmup_api detects connection refused → kill+launch LS, retry. Max 2/chunk |
| E-19 | Time trigger drift после reboot — 4ч gap | Watchdog stuck-detection: force-trigger если LastRunTime > 75 мин |
| E-22 | notify_boot silent на ошибках | notify_boot.log с outcome states |

## Тайминги (consciously conservative)

**Ничего не делай в этой зоне без понимания зачем именно так.**

| Параметр | Значение | Почему |
|---|---|---|
| schedule_hourly.ps1 LinkenSphereWarmup interval | 45 мин | 37 мин цикл + 8 мин запас |
| LinkenSphereWarmup AtStartup Delay | PT2M | LS init + warmup_api start |
| schedule_hourly.ps1 LsWatchdog interval | 5 мин | дёшевая проверка |
| LsWatchdog AtStartup Delay | PT3M | LS из Startup folder + 2-3 мин на API |
| Watchdog UPTIME_GUARD_MIN | 10 мин | не лезть пока AtStartup main task ещё в работе |
| Watchdog STUCK_THRESHOLD_MIN | 75 мин | 45 + 37 = 82 минус 7 buffer |
| Watchdog RESTART_THRESHOLD | 3 strikes | 15 мин tolerated downtime |
| handle_open_file_dialog char-by-char | 120мс/char | 2c/4gb VPS + LS Electron lag |
| handle_open_file_dialog settle pause | 7с | autocomplete/navigation settled |
| Anti-collision jitter | 0-120с | на 8+ VPS одновременных start_warmup |
| cookies_export pre-wait | 60с | LS дофлашит cookies на диск |

## Стиль коммитов

Один аспект в коммите, тело объясняет **почему**, не **что**. Примеры
из истории main, которые хорошо читаются:

- `warmup_api: LS auto-recovery при connection refused (E-16)`
- `watchdog: stuck task detection — force-trigger когда TS застрял`
- `warmup.py: char-by-char delay 30мс -> 120мс для file dialog`

Если коммит правит регрессию — упомяни конкретный кейс (timestamp / IP).

## Ключевые уроки этой сессии (для будущего меня)

1. **Phantom Kernel-Power 41 events** существуют — Event 41 + 6005 + 6008
   в EventLog **не означают реальный boot**. Проверяй через LS process StartTime.
   На phantom events AtStartup НЕ срабатывает (это плюс).

2. **LastBootUpTime ≠ EventLog Event 6005** на VPS-хостингах с
   кешированием WMI после некоторых host-level операций.

3. **Windows file dialog `:` интерпретация** — на некоторых VPS символ ":"
   в пути запускает auto-navigation. Защита: кавычки + char-by-char + 7с settle.

4. **CMD windows visibility:**
   - Task Scheduler с LogonType=Interactive создаёт visible cmd по умолчанию
   - `warmup.py` сама прячет inherited console (она пустая)
   - `warmup_api.py` оставляет visible (banner полезен)
   - `LsWatchdog` через VBS wrapper полностью скрыт
   - subprocess.Popen с `CREATE_NO_WINDOW` для все вспомогательных спавнов

5. **На медленных VPS** (2c/4gb):
   - LS Electron crash'ится чаще — нужен warmup_api self-recovery
   - File dialog обрабатывает символы медленно — нужен char-by-char
   - Task Scheduler timing может drift'ить — нужен watchdog stuck detection

6. **Оператор хочет видеть прогресс через push'и**, не через RDP. Каждое
   recovery событие → notification (🔄 reboot, 🔧 LS recovered, ⚠️ failed).
   Молчание = "может работает, может нет" — не делай это default.

## Сессия 13 июня 2026 — FHD-render quirk + polling /sessions

### FHD-render проблема и решение

Часть VPS (наблюдалось на Tier-2 хостингах) рендерит LS UI с другими
subpixel artefact'ами — даже при том же 2560×1440 / DPI 100% / тех же
templates. cv2.matchTemplate с TM_CCOEFF_NORMED падает с 0.99 до 0.4-0.7
на ВСЕХ templates, scale=0.75 best. Это **не fix'ится** ни понижением
threshold (false positives), ни blur'ом (помогает +0.15, не дотягивает),
ни регенерацией templates (надо на каждой VPS).

**Решение: FHD-fallback templates `{name}_fhd.png` + код-fallback**

В warmup.py три функции matching'а (`find_template`,
`_match_template_in_region`, `_find_template_box`) после фейла primary
автоматически пробуют `{name}_fhd.png`. Если есть — confidence на FHD-render
VPS поднимается до 0.99. На working VPS primary template даёт 0.99 сразу,
fallback не активируется. Threshold ОСТАЁТСЯ 0.80, никаких false-positive
рисков.

Расширение: нумерованные альтернативы `{name}N_fhd.png` (N=2..9) — для
случая когда один FHD-crop работает не везде, делаем несколько разных
crop'ов. **Защита от коллизии**: если существует primary `{name}N.png`
(например `get_started2.png` это самостоятельный template welcome screen,
не альтернатива get_started), то `{name}N_fhd` НЕ пробуется как fallback
для `{name}`. См. `_fhd_fallback_names` хелпер.

**Какие templates имеют FHD-вариант на 13.06.2026** (templates/):
- Wizard: next_step_fhd, get_started_fhd
- Auth: autologin_toggle_fhd, get_started2_fhd, skip_fhd, close_x_fhd, three_dots_fhd
- API port: settings_gear_fhd, api_port_field_fhd
- Session import: multiple_button_fhd, multiple_button2_fhd (альт crop), browse_file_fhd, import_button_fhd

Если на новой VPS LS рендерит ещё одним способом — добавь {name}_fhd2.png
или {name}2_fhd.png и проверь логи на confidence.

### Mass Import hang + polling /sessions

LS Mass Import dialog **может реально висеть 5-7 минут** после клика
IMPORT — это cloud sync с ls.app, не баг. На быстрой VPS закрывается
за 5-10 секунд, на медленной — до 7 минут. Симптом: visible "Importing
sessions..." statusbar в LS GUI.

**Проблема**: warmup.py раньше писал `.session_imported` и спавнил
warmup_api сразу после исчезновения firewall popup (10с после клика
IMPORT). warmup_api через секунду делал GET /sessions, не находил
импортированную сессию → ⚠️ false-error push, сразу после ✅ success.

**Решение: polling /sessions перед спавном warmup_api**

В `warmup.py` после клика IMPORT и записи `.session_imported`:
1. Полл GET /sessions с расписанием 10/30/60/120/240 секунд (cumulative ~7.5 мин)
2. Если нашлась — спавним warmup_api как обычно
3. Если не нашлась — ⚠️ push, warmup_api НЕ спавним, следующий 45-мин
   tick подхватит сам через run_api.bat

Расписание побирали эмпирически: на быстрой VPS poll #1 (10с) находит
сразу, на медленной (.184 lineage) poll #5 (cumulative 7.5 мин) ловит
успешный завершённый Mass Import. Если за 7.5 мин не закрылось —
проблема серьёзнее, требует ручного вмешательства.

См. `_POST_IMPORT_POLL_SCHEDULE` и `_wait_for_session_in_catalog` в warmup.py.

### Push timing — ✅ ПОСЛЕ polling'а, не до

Изначально ✅ "RDP можно отключать" слался сразу после Import-клика +
10с firewall wait. На медленной VPS оператор получал push **за 5-6 минут
до того** как реально безопасно. Если что-то падало на polling'е — оператор
уже отключился по RDP и пропускал ⚠️.

**Текущая последовательность** (commit `30510b0`):
```
IMPORT click → 10с firewall wait → polling /sessions (до 7.5 мин)
                → ✅ "RDP можно отключать" + spawn warmup_api
                or ⚠️ "session не появилась" + НЕ спавним
```

Push приходит **ровно** в момент когда warmup_api готов запуститься.
Никакого fake "всё ок".

### Recovery-команда встроена в error push'и

В оба error push'а из warmup.py (polling timeout + общий catch-all)
встроена inline команда для оператора. Может скопировать прямо из push'а:
```
taskkill /f /im "Linken Sphere 2.exe" /t 2>$null;
taskkill /f /im python.exe /t 2>$null;
cd C:\warmup; .\freshstart.bat;
schtasks /run /tn LinkenSphereWarmup
```

### Финальный 🎉 push — priority=high

`warmup all done` (target reached, cookies готовы) поднят с
priority=low до priority=high. Звуковой push на телефон — оператор не
пропускает "машина закончила, забирай". ⚙️ per-cycle (каждые 45 мин)
остаётся priority=low — не задалбывает.

### RDP-резолюция дисциплина

Все RDP-сессии оператор обязан открывать через **2560×1440** `.rdp`
файл. Внутри `mstsc.exe` → Display tab → Display configuration → выставить
2560×1440 перед подключением. Сохранить как файл. Использовать ВСЕГДА.

**Logoff'ом не закрывать**, только disconnect (закрыть окно). При
disconnect Windows держит резолюцию RDP-сессии живой; logoff сбрасывает.

VPS с залоченным видеодрайвером (не отдаёт >1080p) — встречается, не
лечится со стороны клиента. Pyautogui увидит ту резолюцию, что отдаёт VPS.
Для таких VPS FHD-templates спасают.

### Расширение state-файлов

Дополнения к таблице state-файлов из основной части:
- `.wizards_done` — пустой, оператор/setup ставит когда wizard
  пройден (manual click через RDP в первый раз). warmup.py
  `_dismiss_customize_wizard_step` мгновенно возвращает False
  если флаг на месте. Полезно когда matching wizard'а нестабилен
  на конкретной VPS — можно прокликать руками + поставить флаг,
  warmup.py не будет туда лезть.

### Sessions на парке — общий каталог через LS-cloud

Под одним LS-аккаунтом все VPS видят **общий список сессий** через
GET /sessions — это cloud sync. Когда диагностируешь "сессия не
найдена" на VPS-A, в curl /sessions ты увидишь сессии и VPS-B, VPS-C —
это нормально. Ищи в списке имя из `.session_imported` на текущей VPS.

### Шаблон коммитов в этой сессии

Длинные коммиты с подробным "почему" — хорошо читаются через месяц,
когда что-то отвалится и надо понять *зачем* вообще такая логика
была сделана. Не stop на "fix bug" — пиши контекст, наблюдение,
trade-off'ы, что отвергли и почему.

## Сессия 16 июня 2026 — slow-VPS timeouts + retry'ы + специфичные recovery push'и

### Полный flow и где сейчас сидят timeout'ы

```
[OPERATOR]
   │ iex setup.ps1 + 3 env vars
   ▼
[SETUP] (10-15 мин)
   │ Step 0: TLS 1.2 + git/python install
   │ Step 1: clone repo
   │ Step 2: install.bat (LS Inno Setup 150MB silent)
   │ Step 3: credentials.ini в .gitignore
   │ Step 4: firewall preauth + AutoAdminLogon registry
   │ Step 4.5: NoAutoRebootWithLoggedOnUsers, lockoutthreshold=0
   │ Step 5: schedule_hourly.ps1 → LinkenSphereWarmup (Time 45м + AtStartup PT2M)
   │                             → LsWatchdog (5м + AtStartup PT3M)
   ▼
[UI INSTALL] warmup.py (~26-37 мин, до ~50 мин worst-case на slow VPS)
   │
   ├─ ensure_LS_running              ← launch_wait_seconds = 600с (config)
   │  └─ poll loop scanning firewall/wizard/three_dots/auth (0.5с sleep)
   │  └─ FHD-fallback на каждом template'е
   │
   ├─ login_if_needed                ← three_dots wait 240с (hardcoded)
   │  ├─ check three_dots (already signed in?)
   │  ├─ click sign_in
   │  ├─ wait three_dots после SIGN IN (240с)
   │  └─ если timeout → ⚠️ "three_dots не появился"
   │
   ├─ activate_api_port_if_needed     ← wait_seconds=30с (config)
   │  ├─ click settings_gear (region-constrained)
   │  ├─ scroll до API port input
   │  ├─ type port + Enter
   │  └─ запись .api_activated
   │
   └─ import_session_if_needed
      ├─ click multiple_button       ← wait_seconds=30с
      ├─ click browse_file (right)   ← region-constrained, _wait_seconds=30с
      ├─ handle_open_file_dialog
      │   ├─ wait dialog up to 30с
      │   ├─ Alt+N → File name field
      │   ├─ Ctrl+A + Delete
      │   ├─ "{path}" char-by-char 120мс/char
      │   ├─ 7с settle
      │   └─ Enter
      ├─ wait_for("import_button")   ← 600с! (pre-import cloud sync ls.app)
      ├─ click import_button
      ├─ _wait_for_firewall_alert(10с)
      ├─ запись SESSION_IMPORTED_FLAG (имя сессии в .session_imported)
      ├─ _wait_for_session_in_catalog ← polling [10,30,60,120,240] = 460с
      │  └─ если не появилась → ⚠️ "session не появилась" + EXIT (НЕ спавним warmup_api)
      ├─ ✅ "UI install OK 1/2, RDP можно отключать" push
      └─ spawn warmup_api.py DETACHED
   ▼
[CYCLES] warmup_api.py — каждые 45 мин Task Scheduler
   │
   ├─ ping API                       ← 5 retry с backoff [5,10,15,20]с = ~50с budget
   │  └─ если все 5 fail → ⚠️ "LS API down" + EXIT
   │
   ├─ signin                          ← http_timeout=30с
   │  └─ HTTP 400 "Already signed in" — нормально, продолжаем
   │
   ├─ find_session_by_name/uuid
   │  └─ если коллизия имён → ⚠️ "коллизия + удали лишнее"
   │
   ├─ load url_pool, generate chunks (7 чанков по 14 URL)
   │
   ├─ for chunk in chunks:
   │  ├─ pause_between_chunks=10с
   │  ├─ POST /sessions/start_warmup  ← http_timeout=30с
   │  │   └─ 409 conflict → 9-step pyramid backoff [10,30,60,120,240,360,force_stop,360,480]
   │  │   └─ connection refused → Layer 4 self-recovery (kill+launch LS, max 2/chunk)
   │  ├─ poll status каждые 5с до poll_timeout=600с
   │  └─ освобождение сессии stop()
   │
   ├─ update .warmup_count
   ├─ ⚙️ push "chunks N/M, progress X%/total"  ← priority=low
   │
   └─ если .warmup_count >= .warmup_target:
      ├─ schtasks /change /tn LinkenSphereWarmup /disable
      ├─ count_session_cookies (XLSX export)
      └─ 🎉 push "warmup all done"  ← priority=HIGH (звуковой!)
```

### Где и почему каждый timeout

| Параметр | Значение | Файл | Покрывает |
|---|---|---|---|
| `launch_wait_seconds` | 600с | config.ini | LS Electron cold start + первый экран на slow VPS |
| `wait_seconds` | 30с | config.ini | Стандартные UI клики (gear, multiple, browse) |
| `confidence` | 0.80 | config.ini | Template matching threshold |
| `three_dots wait` | 240с | hardcoded | Sign-in redirect + auth state load |
| `import_button wait` | 600с | hardcoded warmup.py | Pre-import cloud sync xlsx fingerprint'а ls.app |
| `_POST_IMPORT_POLL_SCHEDULE` | [10,30,60,120,240]=460с | hardcoded warmup.py | Post-import cloud sync (sessions catalog update) |
| `pause_between_chunks_seconds` | 10с | config.ini | LS отдыхает между чанками |
| `poll_interval_seconds` | 5с | config.ini | Полл состояния warmup'а |
| `poll_timeout_seconds` | 600с | config.ini | Поллинг одного чанка |
| `http_timeout_seconds` | 30с | config.ini | Сетевой timeout всех API-вызовов |
| `ping_backoffs` | [5,10,15,20]=50с | hardcoded warmup_api.py | Initial ping retry на transient LS hiccup |
| 9-step pyramid | [10,30,60,120,240,360,0,360,480]=1660с | warmup_api.py | 409 Session is used (multi-VPS контентоция) |
| Layer 4 self-recovery | 2/chunk max | warmup_api.py | Connection refused mid-cycle |
| Watchdog STUCK_THRESHOLD | 75 мин | ls_watchdog.py | Main task завис (не тикала) |
| Watchdog UPTIME_GUARD | 10 мин | ls_watchdog.py | Не лезть в первые 10 мин после boot |
| Watchdog RESTART_THRESHOLD | 3 strikes × 5 мин = 15 мин | ls_watchdog.py | LS API down → kill + ls_launch.bat |
| Watchdog AtStartup Delay | PT3M | schedule | LS из Startup folder + 2-3 мин на API |
| LinkenSphereWarmup AtStartup Delay | PT2M | schedule | LS init после boot |
| Task Scheduler Time interval | 45 мин | schedule | Между тиками цикла |
| Anti-collision jitter | 0-120с | warmup_api.py | Парк 8+ VPS старт одновременно |

### Все push notifications — где, когда, priority

| Push | Tag | Priority | Когда (warmup.py / warmup_api / watchdog) |
|---|---|---|---|
| ✅ "UI install OK 1/2" | check | low | warmup.py: после polling /sessions OK |
| ⚠️ "session не появилась в /sessions" | warning | high | warmup.py: polling timeout |
| ⚠️ "warmup failed (ui)" generic | warning | high | warmup.py: любая UI ошибка |
| ⚠️ "warmup failed (ui)" import_button | warning | high | warmup.py: спец-recovery с .session_imported инструкцией |
| ⚙️ "warmup cycle X/N progress" | gear | low | warmup_api.py: каждый успешный цикл |
| ⚠️ "LS API down" | warning | high | warmup_api.py: 5 ping retry fail |
| ⚠️ "cycle paused — 409 lock" | hourglass | low | warmup_api.py: 9-step pyramid exhausted |
| ⚠️ "warmup failed (api)" generic | warning | high | warmup_api.py: остальное |
| 🔧 "LS recovered" | gear | low | warmup_api.py Layer 4 / watchdog Layer 5: LS reborn |
| ⚠️ "LS recovery failed" | warning | high | warmup_api.py Layer 4 / watchdog Layer 5: kill+launch не помог |
| 🔧 "force-trigger main task" | gear | low | watchdog Layer 6: stuck-detection активирован |
| 🔄 "boot detected" | refresh | low | notify_boot.py: первый запуск после reboot |
| 🎉 "warmup all done" | tada | **HIGH (звук!)** | warmup_api.py: target reached, cookies готовы |

### Категоризация ошибок в warmup_api error handler

Три ветки кода в `except Exception` в run():

**1. 409 conflict ("Session is used")**
```python
is_409 = "HTTP 409" in str(exc) and "Session is used" in str(exc)
```
→ priority=low, "cycle paused — next tick will retry". В 95% случаев другая VPS освободит lock к следующему 45-мин tick'у.

**2. LS API down (initial ping после 5 retry'ев)**
```python
is_ping_down = "GET /sessions" in str(exc) and ("10061" in str(exc) or "refused" in str(exc).lower() or "сеть/таймаут" in str(exc))
```
→ priority=high, "LS API down — auto-recovery via watchdog". Полная инструкция оператору с командами для RDP-recovery.

**3. Всё остальное**
→ priority=high, "warmup failed (api)" с tail логом.

### Recovery push'и из warmup.py (UI install)

Тоже три категории в `except Exception`:

**1. import_button timeout (LS медленно делает pre-import cloud sync)**
```python
if "import_button" in str(exc):
```
→ Спец-инструкция: ручной клик IMPORT + `Set-Content .session_imported '<name>'` + `schtasks /run`. Имя сессии подставляется в команду из `.session_name` автоматически.

**2. Polling /sessions timeout (post-import sync не доехал)**
→ Спец push "warmup pending (ui)". Инструкция: подождать 45 мин или RDP + manual re-import.

**3. Всё остальное (BROWSE FILE не найден, three_dots timeout, и т.д.)**
→ Generic "warmup failed (ui)" с nuke-and-retry командой:
```
taskkill /f /im "Linken Sphere 2.exe" /t 2>$null;
taskkill /f /im python.exe /t 2>$null;
cd C:\warmup; .\freshstart.bat;
schtasks /run /tn LinkenSphereWarmup
```

### 6 архитектурных слоёв — кто что покрывает (обновлено)

```
LAYER 6  Watchdog stuck detection: если main task не тикала >75 мин
         → schtasks /run force-trigger. Защита от Time-trigger drift'а
         после reboot.

LAYER 5  Watchdog API ping (5 мин interval): 3 fail подряд (15 мин) →
         kill LS + ls_launch.bat (CREATE_NO_WINDOW). Сам шлёт 🔧/⚠️ push.

LAYER 4  warmup_api self-recovery: connection refused / timeout в цикле →
         kill+launch LS + retry. Max 2 recoveries per chunk. На initial
         ping НЕ применяется (Layer 4.5 — см. ниже).

LAYER 4.5 (новое 16.06.2026) initial ping retry: 5 попыток с backoff
         [5,10,15,20]с (~50с). На transient LS hiccup (Chromium GC,
         cloud sync) тихо переживаем. Если все 5 fail → ⚠️ "LS API down"
         с детальной инструкцией.

LAYER 3  9-step retry pyramid для 409 conflict: 10/30/60/120/240/360/
         force_stop/360/480+nuclear. Покрывает 8+ VPS на одном LS аккаунте.

LAYER 2  Task Scheduler: Time (45 мин) + AtStartup (Delay PT2M).
         MultipleInstances=IgnoreNew защищает от overlap.

LAYER 1  Boot recovery: AutoAdminLogon → Startup folder ls_launch.bat
         (с правильной cwd /D для evo://) → notify_boot.py → 🔄 push.

LAYER 0  Preventive: NoAutoRebootWithLoggedOnUsers, lockoutthreshold=0,
         firewall preauth ВСЕХ LS .exe.
```

### Worst-case полный pipeline (slow 2c/4gb VPS, FHD-render, парк 10+ VPS)

| Фаза | Worst case |
|---|---|
| UI install (warmup.py) | ~34 мин |
| Первый warmup_api cycle (realistic worst) | ~50 мин |
| Initial ping retry budget | +50с на каждый cycle (мелочь) |
| 4 последующих цикла × 37 мин + 8 мин gap | ~3 часа |
| Финал (count cookies, push) | ~1 мин |
| **Total для target=500 URL** | **~4-5 часов** |

В архитектурном бюджете 3-9 часов. Worst-case с 409-pyramid на каждом
чанке всех циклов = теоретически 24+ часов, но в реальности не
наблюдается.

### Кейсы 13-16 июня 2026 (топ-10 эмпирически собранных)

| ID | Симптом | Fix |
|---|---|---|
| F-01 | Templates не матчатся на FHD-render VPS (best=0.4-0.7, scale=0.75) | _fhd templates + fallback в find_template/_match_template_in_region/_find_template_box |
| F-02 | Кнопка `multiple_button` имеет 2 разных crop'а в LS GUI | `multiple_button2_fhd.png` — альтернативный crop, нумерованный fallback в `_fhd_fallback_names` |
| F-03 | Mass Import dialog висит 4-6 мин после клика IMPORT | polling /sessions [10,30,60,120,240]=460с в warmup.py |
| F-04 | warmup_api спавнится через 1с после Import, не находит сессию | polling /sessions перед spawn'ом + ✅ push после polling, не до |
| F-05 | LS открывает Mass Creation (не Mass Import) — IMPORT кнопка появляется через 5+ мин | wait_for("import_button") timeout=600с (вместо 30с) |
| F-06 | Manual recovery после import_button timeout создавал дубль сессии | В push'е: `Set-Content .session_imported '<name>'` перед `schtasks /run` |
| F-07 | LS Electron cold start на slow VPS = 7-10 мин | launch_wait_seconds 240→600 в config.ini |
| F-08 | Wizard NEXT_STEP кликался прямо у timeout deadline'а (12с до конца) | launch_wait_seconds 600с покрывает + клик переживает |
| F-09 | Transient LS hiccup на initial ping → ⚠️ false push | 5 retry с backoff [5,10,15,20]с (~50с) + спец "LS API down" push |
| F-10 | LS умерла mid-cycle, Layer 4 recovery не помог | Layer 5 watchdog подхватит за 15 мин, ИЛИ manual ls_launch.bat |
| F-11 | "Mass creation" с двумя BROWSE FILE vs "Mass import" с одним | warmup.py кликает правую BROWSE FILE (XLSX panel) — LS сам трансформирует Creation→Import при выборе XLSX |
| F-12 | "коллизия имён: 2 сессии с одним именем" | Manual: удалить дубль в LS GUI + не забыть `Set-Content .session_imported` перед `schtasks /run` |

### Дисциплина оператора (критично!)

**1. RDP — только через сохранённый 1440p `.rdp` файл**
В mstsc → Display tab → Display configuration slider → **2560 by 1440 pixels**. Сохранить как файл. Использовать ВСЕГДА. На FHD-physical мониторе RDP-клиент рендерит downscaled — но VPS-сторона честно отдаёт 1440p (`Get-CimInstance Win32_VideoController` покажет 2560x1440 для Microsoft Remote Display Adapter).

**2. Отключение от RDP — ТОЛЬКО ✕ окном (disconnect)**
- ✅ Close ✕ окна → disconnect, session живёт, LS продолжает работать
- ❌ Start → User icon → Sign out / Log off → **kill всех user процессов** включая LS, watchdog подберёт за 15 мин но это false-alarm push'и
- ❌ Restart / Shutdown через Start menu → reboot, Layer 1 recovery подымет, но 🔄 push прилетит

**3. Что делать когда приходит ⚠️ push**
- Прочитать **fix-блок** в самом push'е — там команды для копипасты
- Если "LS API down" → как правило ls_watchdog Layer 5 сам разрулит за 15 мин, можешь ждать
- Если "import_button" → ручной IMPORT + 2 PowerShell команды из push'а
- Если generic "warmup failed" → стандартный nuke-and-retry из push'а
- Если **повторяется** на одной и той же машине → VPS нестабильная, замени

**4. На новую VPS — всегда подтягивать свежий код**
```powershell
cd C:\warmup
git pull
```
Машины установленные до коммита X живут на снапшоте кода без коммита X. Если оператор не делает `git pull`, новые фиксы на старых машинах не работают. Это **намеренно** (минимизация неожиданных регрессий), но требует дисциплины.

### State-файлы (полная таблица v3, обновлена)

| Файл | Содержимое | Когда пишется | Когда читается |
|---|---|---|---|
| `credentials.ini` | email/password | install.bat (operator input) | warmup.py login_if_needed, warmup_api signin |
| `.python_cmd` | "C:\\Program Files\\Python312\\python.exe" в кавычках | setup.ps1 (fix_pycmd.bat) | ls_launch.bat, run_api.bat |
| `.session_name` | CL-XXXXXXXX | warmup.py first run (generate_session_name) | warmup.py, warmup_api |
| `.session_imported` | имя сессии (НЕ UUID) | warmup.py после успешного IMPORT click | run_api.bat (dispatch), warmup_api (find_session_by_name) |
| `.api_activated` | 36555 | warmup.py после успешной API port activation | warmup.py (skip if exists) |
| `.warmup_target` | random(300, 500) | warmup_api первый запуск | warmup_api каждый цикл |
| `.warmup_count` | int (накопленный URL count) | warmup_api после каждого успешного цикла | warmup_api каждый цикл |
| `.warmup_started_at` | unix timestamp | вместе с .warmup_target | warmup_api финальный 🎉 push |
| `.notified_done` | пустой файл | warmup_api когда target reached | warmup_api (skip notification если есть), watchdog (self-disable если есть) |
| `.last_boot` | Win FileTime | каждый запуск notify_boot.py | notify_boot.py для skip-same-boot |
| `.watchdog_fail_count` | int 0..3 | watchdog при API ❌ | watchdog для restart-threshold |
| `.wizards_done` | пустой | оператор вручную ИЛИ setup quickstart.ps1 | warmup.py `_dismiss_customize_wizard_step` (если есть — пропускаем wizard scan) |

### Логи (полная таблица v3)

| Файл | Что пишет | Когда смотреть |
|---|---|---|
| `warmup.log` | UI install фазы, template matching scores | После ⚠️ UI errors, для диагностики matching'а |
| `warmup_api.log` | API циклы, signin, ping, chunks, 409 retries | Для текущего цикла прогрева, общая видимость |
| `watchdog.log` | 5-мин ping'и LS, recovery actions | Если LS постоянно умирает или не подымается |
| `notify_boot.log` | Boot detection events | Если 🔄 push не приходит после reboot |

### CLI-команды для оператора (cheat sheet)

```powershell
# Проверка где LS / что слушает порт
Get-Process "Linken Sphere 2" -EA SilentlyContinue | Format-Table Id, StartTime, CPU
netstat -ano | findstr 36555
Test-NetConnection 127.0.0.1 -Port 36555

# Проверка sessions в LS catalog
curl.exe -s http://127.0.0.1:36555/sessions | ConvertFrom-Json | Select name, status, uuid | Format-Table

# Проверка resolution VPS (для FHD vs 2K диагностики)
Get-CimInstance Win32_VideoController | Select Name, CurrentHorizontalResolution, CurrentVerticalResolution
python -c "import pyautogui; print(pyautogui.size())"

# Проверка ресурсов
Get-Volume C | Format-Table DriveLetter, SizeRemaining, Size
Get-CimInstance Win32_OperatingSystem | Select FreePhysicalMemory, TotalVisibleMemorySize

# Stop процессов LS
taskkill /f /im "Linken Sphere 2.exe" /t 2>$null
Get-Process | Where Path -Like "*Linken Sphere*" | Stop-Process -Force -EA 0

# Стандартный nuke-and-retry
taskkill /f /im "Linken Sphere 2.exe" /t 2>$null; taskkill /f /im python.exe /t 2>$null; cd C:\warmup; .\freshstart.bat; schtasks /run /tn LinkenSphereWarmup

# Поднять LS вручную (без freshstart!)
cd C:\warmup
.\ls_launch.bat
Start-Sleep -Seconds 60
Test-NetConnection 127.0.0.1 -Port 36555

# Пнуть warmup_api без ожидания 45-мин tick'а
schtasks /run /tn LinkenSphereWarmup

# Запись .session_imported (для recovery после manual IMPORT)
Set-Content C:\warmup\.session_imported 'CL-XXXXXXXX'

# Запись .wizards_done (если оператор сам прокликал wizard)
New-Item C:\warmup\.wizards_done -Force

# Подтянуть свежий код пайплайна
cd C:\warmup; git pull

# Завершить таски (на crash-восстановление)
schtasks /end /tn LinkenSphereWarmup 2>$null
schtasks /end /tn LsWatchdog 2>$null
# и обратно:
schtasks /change /tn LinkenSphereWarmup /enable
schtasks /change /tn LsWatchdog /enable

# Streaming лога в realtime
Get-Content C:\warmup\warmup.log -Tail 30 -Wait
Get-Content C:\warmup\warmup_api.log -Tail 30 -Wait
Get-Content C:\warmup\watchdog.log -Tail 30 -Wait
```

### Архитектурные правила для будущего меня (расширены)

1. **НИКОГДА** не убирать FHD fallback из template matching функций. `_fhd_fallback_names` ловит и `{name}_fhd.png` и нумерованные альтернативы `{name}N_fhd.png` — это всё нужно. Защита от коллизий (с другими primary templates) уже встроена.

2. **НИКОГДА** не делать `_fhd2.png` / `_fhdN.png` именования. Правильно: `{name}N_fhd.png` (число между именем и `_fhd` суффиксом).

3. **НИКОГДА** не понижать threshold confidence без эмпирического обоснования. CLAUDE.md правило #2 строгое — снижение приводит к false positives. Если templates не матчатся — сначала добавлять FHD варианты, потом думать про threshold.

4. **НИКОГДА** не сужать polling /sessions schedule [10,30,60,120,240]. Эмпирически проверено: poll #1 (10с) ловит на fast VPS, poll #5 (cumulative 7.5 мин) на slow.

5. **НИКОГДА** не дёргать .session_imported в коде без понимания run_api.bat dispatch logic'и. Файл влияет на то, спавнится ли warmup.py (UI install) или warmup_api (cycle).

6. **НИКОГДА** не менять порядок push timing'а в warmup.py: ✅ "RDP можно отключать" должен идти ПОСЛЕ polling /sessions OK, не до. Иначе оператор отключается до того как machine реально готова.

7. **НИКОГДА** не делать ping retry budget больше 60-70с в warmup_api без анализа Task Scheduler tick'а. Worst-case timeout × retries должен влезать в 45-мин окно.

8. **НИКОГДА** не дублировать push'и (Layer 4 ⚠️ + generic ⚠️ за тот же fail). Сейчас (16.06.2026) дубль есть в одном edge case (LS recovery failed + warmup failed api) — это уже отмечено как F-10, fix не закоммичен. Если делаешь touch там — объединяй в один push.

9. **ВСЕГДА** пишет recovery-команду прямо в push, чтобы оператор копипастил без перехода в чат/доку. И **session_name подставляй** из `.session_name` файла, не оставляй placeholder.

10. **ВСЕГДА** проверяй тайминги конфликтов когда меняешь любой timeout. Critical: 45-мин Task Scheduler tick, 75-мин watchdog stuck, 600с launch_wait, 600с import_button wait, 50с ping retry, 460с polling /sessions, 1660с 9-step pyramid.

## Сессия 18 июля 2026 — ветка multi-profile (N профилей на VPS)

### Что это

Ветка `multi-profile` (от main): одна VPS греет **N профилей** (default
count=3, расширяемо до 5) вместо одного. Main и старые машины НЕ
затронуты — ветка живёт параллельно до обкатки на canary VPS.

### Ключевые решения (не пересматривать без причины)

1. **Прогрев ПОСЛЕДОВАТЕЛЬНЫЙ, не параллельный.** LS API держит
   глобальный лок на аккаунт (вся история 409/pyramid об этом). Два
   параллельных start_warmup с одной машины = self-409-шторм. Плюс два
   Chromium-warmup'а не влезают в 2c/4gb. Ротация: на каждый 45-мин tick
   выбирается наименее прогретый (по доле target) незавершённый профиль.

2. **Импорт — ОДИН Mass Import.** xlsx с N строками (строки 3, 4, …),
   у каждой свой независимый fingerprint. UI-клики не менялись вообще
   (правило #1 соблюдено). См. `build_sessions_xlsx` в session_template.py.

3. **Target per-profile:** `urls_total_target_min/max` в этой ветке =
   150-250 НА ПРОФИЛЬ (~2.5-3.5k cookies/профиль, cookies ~линейны от
   URL). При 2 профилях суммарно 300-500 → wall-clock как у старой схемы.

4. **Export cookies — сразу по завершении каждого профиля**, не в конце.
   Флаг `.cookies_exported.<имя>` пишется ТОЛЬКО после успешного export'а
   (жёсткая идемпотентность). Упавший export тихо ретраится на следующем
   tick'е (см. блок "Догоняем незакрытые export'ы" в warmup_api.run).

5. **Запуск по готовности (цепочка) + 45-мин каскад как fallback.**
   warmup_api после успешного цикла НЕ выходит: rest 90с → следующий
   цикл, пока все профили не готовы (`run()` → цикл `_run_cycle()`,
   коды 0=продолжай / 2=всё готово / 1=ошибка-выход). Выигрыш: минус
   ~8-мин gap каждого цикла И минус 90-мин эффективный интервал, когда
   цикл переваливал за 45 мин (IgnoreNew съедал тик). Task Scheduler
   (Time 45 мин + AtStartup) остаётся нетронутым — это каскад-fallback:
   при любой ошибке цепочка выходит, тик/watchdog подхватывают.
   **Single-instance named mutex** (`Local\LinkenSphereWarmupApiSingleton`):
   второй экземпляр (тик при живой detached-цепочке, force-trigger
   watchdog'а) молча выходит rc=0. Mutex авто-освобождается при смерти
   процесса — stale-state невозможен. Watchdog совместим из коробки:
   при main task Running он не вмешивается (early-exit в main()), а
   no-op тики обновляют LastRunTime → stuck-detection молчит и на
   detached-цепочке. НЕ заменяй mutex на lock-файл и НЕ убирай
   предохранитель _CHAIN_MAX_CYCLES=30.

### State-файлы ветки

- `.session_name` / `.session_imported` — N строк (по профилю на строку).
  **1 строка = легаси-режим**: код идёт по старому пути со старыми
  файлами `.warmup_target`/`.warmup_count` — бит-в-бит совместимость.
- `.warmup_target.<имя>` / `.warmup_count.<имя>` — per-profile (N>1).
- `.cookies_exported.<имя>` — флаг export'а.
- freshstart.bat чистит всё wildcard'ами.

### Уведомления

- ⚙️ per-cycle: активный профиль + прогресс всех профилей + total.
- 🎉 `profile done k/N — CL-X` (priority **default**): профиль готов,
  cookies уже в cookies_export/. Не high — не будим ночью, не low — милстоун.
- 🎉 `warmup all done` (priority **high**, звук): ВСЕ профили готовы.
- 🔧 `cookies exported — CL-X` (low): export прошёл со второй попытки.

### Установка canary VPS с ветки

```powershell
$env:WARMUP_BRANCH = "multi-profile"
iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/multi-profile/setup.ps1 | iex
```

### Не проверено на реальной VPS (Phase 0 — оператор)

1. Mass Import с xlsx из 2+ строк реально создаёт 2+ сессии (должен —
   это его назначение, но на нашей LS-версии не проверялось).
2. Длительность pre-import cloud sync на N строк (600с wait должен
   покрыть 2-3 строки; для 5 — проверить).
3. Параллельный start_warmup двух локальных сессий → подтвердить 409
   (закрывает вопрос параллельности навсегда).

### Мерж в main

Только после успешного прогона canary VPS end-to-end (2 профиля →
2 cookie-файла → 🎉). При мерже: main-машины со старым `.session_name`
(1 строка) продолжают работать по легаси-пути автоматически.
