# Linken Sphere Warmup — полный pipeline по фазам

Этот файл — **источник истины** по тому как устроен наш конвейер. Перед
изменением любой UI / scheduler / API логики **прочитай его целиком**.
История регрессий (см. секцию "Уроки") показывает что точечные правки
без понимания всего пайплайна ломают вещи в неожиданных местах.

---

## Phase 0 — Bootstrap (`setup.ps1` / `quickstart.ps1` / `quickstart-retry.ps1`)

Юзер запускает в PowerShell на чистом VPS:

```powershell
$env:LS_EMAIL='xx'; $env:LS_PASSWORD='xx'
iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/main/setup.ps1 | iex
```

Три варианта команд:

| Команда | Когда |
|---|---|
| `setup.ps1` | Свежий VPS, LS никогда не запускался |
| `quickstart.ps1` | LS уже был запущен раньше (мастер пройден) — ставит `.wizards_done`, остальное то же что setup.ps1 |
| `quickstart-retry.ps1` | UI install уже частично прошёл, упал где-то — ставит `.wizards_done`, триггерит `schtasks /run` |

setup.ps1 делает:
1. **Установка Python** — winget → fallback на python.org direct
2. **Установка Git** — то же самое
3. **Установка Linken Sphere** — `cdn.ls.app` через PowerShell + принудительный TLS 1.2 (нужно для Server 2019)
4. **Clone репо** в `C:\warmup` (или `git pull` если уже есть)
5. **`credentials.ini`** из env vars `LS_EMAIL` / `LS_PASSWORD`
6. **`pip install -r requirements.txt`** (pyautogui, pillow, opencv, openpyxl)
7. **`.python_cmd`** — абсолютный путь к `python.exe` (иммунитет от PATH-state)
8. **Task Scheduler** регистрация — каждые 45 мин, `IgnoreNew`, `RunLevel Highest`, `LogonType Interactive`
9. **Первый trigger через 15с**

---

## Phase 1 — UI Install (`warmup.py`, один раз на машину)

Триггерится Task Scheduler'ом через `run_api.bat`, который смотрит на
флаги `.api_activated` + `.session_imported`. Если хоть один отсутствует
→ дispatches в `warmup.py`. Полный pipeline:

```
START
  │
  ├─ 1.1 ensure_linken_sphere_running
  │       └─ taskkill старого LS → ShellExecuteW LS.exe → ждём window
  │
  ├─ 1.2 dismiss Windows Defender Firewall Alert
  │       └─ BM_CLICK через SendMessage (Win32 message, не клик мышью)
  │
  ├─ 1.3 dismiss Customize wizard  [одноразовый, .wizards_done пропускает]
  │       └─ NEXT STEP × 2 → GET STARTED
  │
  ├─ 1.4 login_if_needed
  │       ├─ _find_auth_window → проверка размера (≥600×400)
  │       ├─ click @ y=43.4% от высоты окна → paste email
  │       ├─ click @ y=52.1% → paste password
  │       └─ click @ y=67.8% (SIGN IN)
  │
  ├─ 1.5 dismiss post-login wizards  [одноразовый, .wizards_done пропускает]
  │       ├─ GET STARTED2 (Welcome screen)
  │       ├─ SKIP (28-step product tour)
  │       └─ close_x (финальный крестик ✕, только в 30с окне после SKIP)
  │
  ├─ 1.6 activate_api_port_if_needed (Settings → Network → API port)
  │       ├─ settings_gear → scroll preferences (3×Ctrl+End + 15×PgDn)
  │       ├─ api_port_field → ввод порта 36555
  │       ├─ Save
  │       └─ Esc + verify _check_api_port_alive
  │       → пишет .api_activated
  │
  ├─ 1.7 load_session_name → CL-XXXXXXXX (или из .session_name если есть)
  │       └─ пишет .session_name
  │
  ├─ 1.8 import_session_if_needed
  │       ├─ prepare_session_xlsx → клон _template.xlsx + рандомный
  │       │   fingerprint (CPU/RAM/Screen/SysVer + GPU-tied profile,
  │       │   A3=CL-XXXXXXXX) через session_template.build_session_xlsx
  │       ├─ click MULTIPLE button
  │       ├─ click BROWSE FILE (правая, XLSX)
  │       ├─ handle_open_file_dialog
  │       │   ├─ poll окна 'Open'/'Открытие' до 30с (resilience против
  │       │   │   медленного отображения диалога на слабых VPS)
  │       │   ├─ +1.5с после нахождения окна
  │       │   ├─ Alt+N → paste path → Enter
  │       └─ click IMPORT
  │       → пишет .session_imported (name + uuid)
  │
  ├─ 1.9 notify ✅ "UI install OK 1/5"  (low priority, white_check_mark)
  │
  └─ 1.10 spawn warmup_api.py как DETACHED_PROCESS
          └─ начинает первый цикл сразу, не ждёт следующего scheduler-tick
          → exit warmup.py
```

**Длительность:** 2-3 минуты на нормальном VPS, 5-8 на слабом.

---

## Phase 2 — Warmup Cycle (`warmup_api.py`, каждые 45 мин Task Scheduler)

```
START (DETACHED, без RDP)
  │
  ├─ 2.1 _ensure_console (AllocConsole — баннер RUNNING)
  │
  ├─ 2.2 load_or_create_target
  │       └─ если .warmup_target нет: random.randint(300, 500), пишем
  │       → пишет .warmup_started_at (UNIX ts первого тика)
  │
  ├─ 2.3 current = load_warmed_count() из .warmup_count
  │
  ├─ 2.4 GATE: если current >= target
  │       ├─ disable_scheduled_task (3-tier: schtasks → PS cmdlet → delete)
  │       ├─ count_session_cookies (60с wait + retry на 409)
  │       ├─ notify 🎉 "warmup all done" (low, tada) + cookies count
  │       └─ mark_notified_done → пишет .notified_done → exit 0
  │
  ├─ 2.5 ping LS API на 127.0.0.1:36555
  │
  ├─ 2.6 signin email/password
  │       └─ HTTP 400 "Already signed in" = OK (LS app держит сессию)
  │
  ├─ 2.7 найти uuid сессии
  │       ├─ из .session_imported (если uuid там есть)
  │       └─ fallback: find_session_by_name(CL-XXX)
  │
  ├─ 2.8 materialize_run_urls(pool, cfg, max_n=remaining)
  │       ├─ n = random.randint(95, 105)
  │       ├─ remaining = target - current
  │       └─ ЕСЛИ remaining < n: clip до max(7, remaining)
  │       → urls_generated/run_TS.txt (audit, удаляется по завершении)
  │
  ├─ 2.9 chunks = [urls[i:i+7] for i in range(0, len(urls), 7)]
  │       → обычно 14-15 чанков по 7 URL
  │
  ├─ 2.10 anti-collision jitter: sleep(random.randint(0, 120))
  │       └─ размывает старты на парке VPS, ставит интервал между чанками
  │          разных VPS, минимизирует HTTP 409
  │
  ├─ 2.11 spawn _firewall_watcher.py (15с polling, на всё время прогрева)
  │
  ├─ 2.12 LOOP по чанкам:
  │   │
  │   ├─ start_warmup(uuid, chunk, view_depth=3, time_per_url=7)
  │   │
  │   ├─ На HTTP 409 «Session is used by another client» —
  │   │   7-ступенчатый retry pyramid:
  │   │   [1] sleep 30с → retry
  │   │   [2] sleep 60с → retry
  │   │   [3] sleep 120с → retry
  │   │   [4] sleep 240с → retry
  │   │   [5] unlock_blocked_sessions → retry  (LS endpoint для этого)
  │   │   [6] NUCLEAR: force_stop(own_uuid) → unlock → retry
  │   │   ── если всё ещё 409 → notify ⏳ "cycle paused" → исключение
  │   │
  │   ├─ wait_for_warmup_done — polling status пока не 'stopped'
  │   ├─ chunks_done += 1
  │   └─ sleep(10) между чанками
  │
  ├─ 2.13 urls_warmed_now = sum(len(c) for c in chunks[:chunks_done])
  │        new_total = add_warmed_count(urls_warmed_now) → пишет .warmup_count
  │
  ├─ 2.14 firewall_watcher terminate
  │
  ├─ 2.15 ЕСЛИ target_reached (new_total >= target):
  │       ├─ disable_scheduled_task
  │       ├─ count_session_cookies (60с wait, парсит JSON)
  │       └─ notify 🎉 "warmup all done" + cookies count + total time
  │
  └─ 2.16 ИНАЧЕ:
          └─ notify ⚙️ "warmup cycle" (low) с прогрессом N/target
```

**Длительность цикла:** ~37 мин в норме, до 70 мин при тяжёлых 409-retry'ях.

---

## Phase 3 — Финальный экспорт cookies (только при target reached)

```
count_session_cookies(client, uuid):
  │
  ├─ sleep(60)              ← LS дофлашит cookies на диск
  │                            (критично: иначе спровоцируем "Saving data..." lock)
  ├─ before = текущее содержимое C:\warmup\cookies_export\
  ├─ retry 3 раза:
  │   ├─ POST /sessions/export_cookies (path=C:\warmup\cookies_export\)
  │   ├─ break при успехе
  │   └─ HTTP 409 → sleep 30 → retry
  ├─ new_files = что появилось
  └─ return sum(_count_cookies_in_payload(f) for f in new_files)
       └─ парсер ест три формата: list, dict.values(), Netscape txt
```

Финальный артефакт: `C:\warmup\cookies_export\CL-XXXXXXXX_DD-MM-YYYY.txt`
(200-500 cookies). **Не удаляется автоматически** — машины одноразовые,
оператор забирает по RDP.

---

## State-флаги (файлы в C:\warmup\)

| Файл | Пишет | Читает | Что значит |
|---|---|---|---|
| `.session_name` | Phase 1.7 | везде | CL-XXXXXXXX этой машины |
| `.session_imported` | Phase 1.8 | Phase 2.7 + run_api.bat | uuid + name после UI-импорта |
| `.api_activated` | Phase 1.6 | Phase 1.6 (idempotency) + run_api.bat | API порт 36555 включён в LS |
| `.warmup_target` | Phase 2.2 | Phase 2.3 | финальная цель (300-500) — навсегда |
| `.warmup_started_at` | Phase 2.2 | Phase 2.15 (total time) | UNIX ts первого цикла |
| `.warmup_count` | Phase 2.13 | Phase 2.3 | сколько URL уже прогрето |
| `.notified_done` | Phase 2.4 | Phase 2.4 (idempotency) | "all done" отправлен ровно один раз |
| `.wizards_done` | quickstart.ps1 | Phase 1.3, 1.5 (early return) | оператор сказал что wizards уже пройдены |
| `.python_cmd` | install.bat | run_api.bat / freshstart.bat | абсолютный путь к python.exe |

Все эти файлы в `.gitignore`. `freshstart.bat` чистит все state-файлы
кроме `.wizards_done` и `.python_cmd` (это оператор-указанные константы).

---

## Notifications (6 типов, все через `notify_ntfy` → ntfy.sh JSON publish)

| Эмодзи | Title | Когда | Priority |
|---|---|---|---|
| ✅ | warmup OK | UI install прошёл (Phase 1.9) | low |
| ⚙️ | warmup cycle | каждый цикл закончился (Phase 2.16) | low |
| 🎉 | warmup all done | count >= target (Phase 2.4 или 2.15) | low |
| ⏳ | warmup paused | 409 не ушёл за 7 ступеней (Phase 2.12) | low |
| ⚠️ | warmup failed (ui) | exception в Phase 1 | **high** |
| ⚠️ | warmup failed (api) | exception в Phase 2 | **high** |

Только `high` ломает ночной режим телефона. `low` — тихо в ленту.

`notify_ntfy`: timeout 30с, 3 попытки с backoff 2с/5с — выдерживает
случайные SSL handshake hiccups.

---

## Templates (PNG для cv2.matchTemplate)

В `templates/`:
- `allow_access.png`, `allow_access2.png` — Windows Defender кнопки (Win10/Win11 стили)
- `next_step.png`, `get_started.png`, `get_started2.png` — wizard кнопки
- `skip.png`, `close_x.png` — post-login welcome/tour
- `three_dots.png` — индикатор главного экрана LS
- `settings_gear.png`, `api_port_field.png` — Settings → API port
- `multiple_button.png`, `browse_file.png`, `browse_file_button.png`, `import_button.png` — Mass creation flow

Multi-scale matching: пробуем масштабы 0.75 / 0.85 / 0.95 / 1.0 / 1.1 /
1.25 / 1.5 / 1.75 / 2.0× от исходного шаблона. Confidence threshold
обычно 0.80-0.90 в зависимости от риска false-positive.

---

## Уроки регрессий (что НЕ делать)

Эти баги мы уже сделали и откатывали — **не повторяй**.

### 1. Tab-навигация в login вместо проportional clicks
Кажется удобной (resolution-independent), но даёт race condition между
dismiss-loop'ом и wizard re-render → Tab навигирует по wizard'у, не по
форме → email вставляется в случайный input. **Откачено**, теперь
proportional 43%/52%/68%.

### 2. Adaptive SKIP threshold (0.65 после логина)
Понизили threshold с 0.85 до 0.65 "для лучшего ловления tour SKIP".
Tour SKIP на самом деле матчится при 1.0 — порог 0.85 справляется.
0.65 даёт false positive @0.659 в шапке LS → клик в пустоту → close_x
не достигается → timeout. **Откачено**, статичный 0.85.

### 3. `.wizard_dismissed` флаг на close_x клике
Флаг писался ТОЛЬКО при успешном close_x матче. Если по любой причине
close_x не сматчился (медленный VPS, обновлённый LS, разовый PNG drift),
флаг никогда не записывался → каждый запуск опять полный wizard scan
→ риск false-positive на дашборде. **Откачено**.

### 4. SYS_VERSIONS = [10, 11] как int в xlsx
Записывалось как число в Excel-ячейку. LS xlsx parser ждёт строку →
тихо падал на дефолт → system_version в fingerprint не рандомизировался.
**Фикснуто**: `SYS_VERSIONS = ["10", "11"]`.

### Главный урок
Все эти баги — результат **точечных правок без понимания всего пайплайна**.
Прежде чем менять любую из фаз 1.3 / 1.4 / 1.5 / template threshold /
state flag — прочитай этот документ **целиком**, потом проверь что
изменение не ломает соседние фазы.

При сомнении — спроси оператора, не предлагай решение которое "вроде
должно работать".

---

## Total stack для оператора

```
1 PowerShell команда
        ↓ 8 минут setup
1 машина настроена
        ↓ 3-4 часа background warmup (без RDP)
1 JSON-файл с 200-500 cookies в cookies_export/
```

Эффорт оператора: напечатать email/password один раз, прочитать пуш 🎉
в Telegram.
