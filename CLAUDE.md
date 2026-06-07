# Контекст для Claude

Этот файл автоматически загружается Claude Code в начале каждой сессии.
Прочитай его до того, как предлагать изменения в коде.

## Что это за репо

Конвейер прогрева антидетект-сессий Linken Sphere 2 на парке Windows VPS.
Одна PowerShell команда → 8 минут setup → 3-4 часа background warmup
→ 1 JSON-файл с 200-500 cookies. Без оператора после старта.

**Репо публичный** (`r2d23c/mainquns`). Никаких токенов / паролей в
коммитах. `credentials.ini` в `.gitignore`, проверяй перед коммитом.

## Главный документ — PIPELINE.md

**[PIPELINE.md](./PIPELINE.md)** — полный pipeline по фазам.
Перед любым изменением UI flow / wizard / login / api_port / session
import / warmup cycle / notifications — **прочитай его целиком**.

Точечные правки без понимания всего пайплайна уже несколько раз ломали
production. Уроки задокументированы в секции "Уроки регрессий" внутри
PIPELINE.md.

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

### 2. Не понижай PNG-template thresholds
SKIP `0.85`, close_x `0.90`, next_step/get_started/get_started2 `0.80`.
Понижение → false positives → клики в пустоту → timeout.

### 3. Не вводи новые auto-set флаги
Флаги вроде `.wizard_dismissed` (которые код **сам** ставит) — фрагильны:
если что-то пошло не так до их записи, ломается следующий запуск.
Предпочитай явные операторские флаги (`.wizards_done` через
`quickstart.ps1`) или жёсткую идемпотентность (`.api_activated`,
`.session_imported` — пишутся только после полностью успешной фазы).

### 4. Сохраняй обратную совместимость с уже-запущенными машинами
Если оператор запустил 10 VPS со старой версией, а ты выкатил новую —
старые цикл-warmup'ы должны продолжать работать. Не меняй структуру
state-файлов / xlsx fingerprint'ов без миграции.

### 5. Думай про парк машин, не одну
Все VPS под одним LS-аккаунтом. Конфликты по сессиям, лок-storm'ы —
реальные проблемы. Anti-collision jitter и 7-step 409 retry это
решают, не трогай без понимания.

## Структура

```
warmup.py              ← UI install (Phase 1), однократно
warmup_api.py          ← warmup cycle (Phase 2), каждые 45 мин
session_template.py    ← рандом fingerprint xlsx
setup.ps1              ← bootstrap (Phase 0)
quickstart.ps1         ← bootstrap для машин с пройденным мастером LS
quickstart-retry.ps1   ← ретрай UI install без bootstrap
freshstart.bat         ← clean restart state
run_api.bat            ← dispatcher (Task Scheduler entry)
schedule_hourly.ps1    ← регистрация Task Scheduler
install.bat            ← установка Python deps + LS
templates/             ← 21 PNG для cv2.matchTemplate
PIPELINE.md            ← полный pipeline
ИНСТРУКЦИЯ.md          ← user-facing docs
HOW_IT_WORKS.txt       ← старая overview (актуально не всё)
README.md              ← repo intro
```

## Стиль коммитов

Один аспект в коммите, тело объясняет **почему**, не **что**. Примеры
из истории main, которые хорошо читаются:

- `notify_ntfy: timeout 10→30с + 3 попытки с backoff 2с/5с`
- `api: clip последнего цикла до remaining — без овершута 50-95 URL`
- `warmup: revert UI flow к версии 3 июня + SYS_VERSIONS как строки`

Если коммит правит регрессию — упомяни конкретный кейс (timestamp / IP).
