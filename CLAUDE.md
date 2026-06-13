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
