# CONFIG_GUIDE.md — гайд по изменению настроек пайплайна

**Этот файл — справочник для оператора и для будущего меня (Claude / другая нейросеть)** по тому, как поменять параметры прогрева, fingerprint'ы, URL pool — без сюрпризов.

Перед любой правкой прочитай **раздел в самом конце** "Чек-лист после любого изменения" — там про rollout на парк VPS и про state-миграции.

---

## 0. Мульти-профиль: несколько сессий на одной VPS (ветка `multi-profile`)

**Сейчас (ветка multi-profile):** одна VPS прогревает N профилей (LS-сессий) вместо одного. Все N импортируются **одним** Mass Import'ом (одна xlsx с N строками, у каждой свой рандомный fingerprint), греются **последовательно** — один профиль на 45-мин tick, ротация по наименее прогретому.

**Почему не параллельно:** LS API держит глобальный лок на аккаунт (см. 9-step pyramid) — два одновременных `start_warmup` с одной машины устроят self-409-шторм. Плюс два Chromium-warmup'а не влезают в 2c/4gb VPS.

### Способ A (рекомендуется) — через env при установке, файл не трогаешь

`setup.ps1` при установке впишет твои значения в `config.ini` ДО первого запуска:

```powershell
$env:WARMUP_BRANCH    = "multi-profile"
$env:Profiles_LS      = '10'        # сколько профилей на VPS
$env:URLS_PER_PROFILE = '150-300'   # цель URL на профиль: число ИЛИ диапазон
iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/multi-profile/setup.ps1 | iex
```

- `Profiles_LS` → `[profiles] count`
- `URLS_PER_PROFILE` → цель URL на каждый профиль:
  - `'200'` — ровно 200 на профиль (фикс)
  - `'150-300'` — случайно из диапазона на каждый профиль
- `URLS_MIN`/`URLS_MAX` → альтернатива диапазону двумя переменными (перекрывают границу, если заданы)

Задал только часть — остальное берётся из `config.ini` по умолчанию. Env читаются **один раз при установке**; на уже установленной машине значения зафиксированы (нужен `freshstart.bat` чтобы переустановить с новыми).

### Способ B — правкой файла

**Файл:** `config.ini`, секция `[profiles]`
```ini
[profiles]
count = 3
```

**Связанные настройки:** `[api] urls_total_target_min/max` в этой ветке трактуются **как per-profile** (по умолчанию 150-250 → ~2.5-3.5k cookies на профиль). При 3 профилях суммарно 450-750 URL ≈ 5-6 часов wall-clock. При 10 профилях × 150-250 = 1500-2500 URL ≈ 12-20 часов — учитывай бюджет времени, для больших N опусти target до ~100-150.

**Что происходит после изменения `count`:**
| Сценарий | Эффект |
|---|---|
| **Новая VPS** (`.session_name` не существует) | Сгенерится N имён, импорт N профилей. ✓ |
| **Уже запущенная VPS** | `count` игнорируется — состав профилей зафиксирован в `.session_name` (N строк). Менять поздно. |

**State-файлы мульти-режима** (все gitignored, чистятся `freshstart.bat`):
- `.session_name` / `.session_imported` — N строк (1 строка = легаси одиночный режим, код идёт по старому пути со старыми файлами)
- `.warmup_target.<имя>` / `.warmup_count.<имя>` — per-profile счётчики
- `.cookies_exported.<имя>` — флаг успешного export'а cookies профиля (пишется ТОЛЬКО после успеха; если export упал — следующий tick ретраит)

**Уведомления:** ⚙️ per-cycle показывает активный профиль + прогресс всех; 🎉 `profile done k/N` (priority default) — профиль готов, cookies уже в `cookies_export/`; финальный 🎉 `warmup all done` (priority high, звук) — когда готовы ВСЕ.

**Запуск по готовности:** циклы идут цепочкой (rest 90с + jitter между ними), НЕ по 45-мин сетке — 3 профиля прогреваются за ~3.5-4.5 ч вместо 5-6. Task Scheduler остаётся как каскад-fallback: при ошибке цепочка выходит, следующий 45-мин tick подхватывает. Тики при живой цепочке — тихие no-op'ы (single-instance mutex). ⚙️ push'и приходят чаще: каждые ~39-40 мин вместо 45.

**Установка тестовой VPS с этой ветки:**
```powershell
$env:WARMUP_BRANCH = "multi-profile"
iwr -useb https://raw.githubusercontent.com/r2d23c/mainquns/multi-profile/setup.ps1 | iex
```

---

## 1. Изменить целевой объём прогрева (target URL count)

**Сейчас:** на каждой VPS при первом запуске генерится `random.randint(300, 500)` и пишется в `.warmup_target`. Цикл идёт пока `.warmup_count >= .warmup_target` → 🎉 + auto-disable.

**Файл:** `config.ini`, секция `[api]`
```ini
urls_total_target_min = 300
urls_total_target_max = 500
```

**Что поменять для 200-300:**
```ini
urls_total_target_min = 200
urls_total_target_max = 300
```

**Что происходит после:**
| Сценарий | Эффект |
|---|---|
| **Новая VPS** (`.warmup_target` не существует) | Возьмёт новый диапазон. ✓ |
| **Уже запущенная VPS** | `.warmup_target` уже записан → НЕ перезапишется. Будет греть до старой цели. |

**Принудительное применение на работающей VPS** (по RDP):
```powershell
cd C:\warmup
git pull                                # подтянуть новый config.ini
Set-Content .warmup_target 250         # вписать желаемое (или удалить если хотим random)
# warmup_api при следующем тике проверит .warmup_count >= 250 → если уже больше, сразу 🎉
```

⚠️ **Не уменьшай target ниже уже накопленного `.warmup_count`** — иначе на следующий tick прилетит 🎉 + auto-disable, прогрев остановится, cookies может быть мало.

**Код, который это читает:** `warmup_api.py:601-617` (`load_or_create_target`).

---

## 2. Изменить размер пакета URL за один цикл

**Сейчас:** один tick (раз в 45 мин) берёт 95-100 URL, режет на чанки по 14, шлёт последовательно.

**Файл:** `config.ini`, секция `[api]`
```ini
urls_per_run_min = 95
urls_per_run_max = 100
urls_per_chunk_max = 14
```

**Кейсы:**
- **Хочешь быстрее закончить (target=300 — за 3 цикла)** → подними `urls_per_run_min/max` до 100-105 (LS API лимит ~99 на одно `/sessions/start_warmup`, выше — `array has too many items`, поэтому chunk_max не больше 14 не уйдёт).
- **Хочешь тише прогрев (target=500 — за 7-10 циклов)** → понизь `urls_per_run` до 50-60.

⚠️ **НЕ трогай `urls_per_chunk_max` без понимания**:
- Понизишь до 7 → больше round-trip, больше 409-conflict-window на парке 10+ VPS.
- Поднимешь до 20 → LS API вернёт 422 `array has too many items`.

**Код:** `warmup_api.py:580-590` (`sample_urls_for_run`).

---

## 3. Разнообразить XLSX fingerprint'ы (видеокарты / CPU / RAM / Screen / Windows)

**Сейчас:** 8 видеокарт, для каждой свой профиль CPU/RAM/Screen чтобы не было абсурдных сочетаний (например RTX 4070 + 4 ядра).

**Файл:** `session_template.py`

### 3.1. Добавить новую видеокарту

В `ADAPTER_PROFILES` добавь строку. Формат:
```python
"Производитель, Модель GPU": (cpu_options, ram_options, screen_options),
```

⚠️ **Имя должно строго совпадать с каталогом LS** (см. лист "Инструкция RU" в `session_imports/_template.xlsx`). Опечатка → LS Mass Import выкинет fingerprint в "error" rows.

**Пример: добавить RTX 4080 high-end**
```python
ADAPTER_PROFILES = {
    # ... существующие ...
    "Nvidia, GeForce RTX 4080":    ([8, 12],     [32],    ["2560x1440", "3840x2160"]),
}
```

**Логика профилей:**
| Видеокарта | Реалистичный CPU | Реалистичный RAM | Реалистичный экран |
|---|---|---|---|
| Intel iGPU | 4-8 ядер | 8-16 GB | 1080p / 1440p |
| GTX 1660 (бюджет 2019-21) | 6-8 | 8-16 | 1080p / 1440p |
| RTX 3060 / RX 6600 (мидл 2021+) | 6-8 | 16 | 1080p / 1440p |
| RTX 4070 (high-end 2023+) | 8 | 16 | 1440p |
| RTX 4080+ (top) | 8-12 | 32+ | 1440p / 4K |

### 3.2. Добавить Windows 12 / Server варианты

```python
SYS_VERSIONS = ["10", "11", "11"]   # дать больше веса Win11 (повторение увеличивает вероятность)
```

⚠️ **Только те значения, которые LS реально принимает.** Проверить можно через ручной импорт `_template.xlsx` в LS — если ругнётся, значение неверное.

### 3.3. Изменить fingerprint-поля (Canvas / WebGL / Audio и т.п.)

Файл `session_template.py`, словарь `FIXED`. Сейчас:
- Canvas, WebGL, ClientRects, WebGPU, MediaDevices = `fake` (все шумят)
- Audio = `direct` (на Server 2022 без звуковухи — `noise` детектируется repeat-call тестом, см. комментарий в коде)
- WebRTC = `fake`, DNS = Cloudflare

**Если LS обновился и появились новые опции** (например `passive_fingerprint`) — посмотри лист "Инструкция RU" в `_template.xlsx` и добавь колонку в `FIXED` или в `ADAPTER_PROFILES` если она должна варьироваться.

### 3.4. Когда применятся новые fingerprint-правила

| Сценарий | Эффект |
|---|---|
| Новая VPS | Возьмёт новый шаблон. ✓ |
| Уже запущенная VPS | Сессия **уже импортирована** в LS cloud → fingerprint застывает. Новые правила не применятся. |

**Если оператор хочет перегенерировать сессии на работающей VPS:**
```powershell
cd C:\warmup
git pull
# Удалить старую сессию из LS GUI (или curl DELETE)
Remove-Item .session_imported, .session_name, .api_activated -Force -EA 0
schtasks /run /tn LinkenSphereWarmup   # warmup.py заново сгенерит и импортирует
```

⚠️ Это сбросит прогрев на этой VPS — `.warmup_count` останется, но сессия будет новая → cookies старой сессии останутся в LS cloud отдельно. Лучше делать на VPS, где ещё не начался цикл.

**Код:** `session_template.py:97-144` (`build_session_xlsx`), `warmup.py` вызов `build_session_xlsx(...)` в `import_session_if_needed`.

---

## 4. Разнообразить пул URL (40 000 → больше / другие)

**Сейчас:** `urls/40k_all_urls.txt` — plain text, по одному URL на строку. 40 144 уникальных URL (плотно дедуплицированы).

**Файл:** `urls/40k_all_urls.txt`

### 4.1. Дописать новые URL

Просто открой текстовый редактор и добавь URL в конец. Дедупликация **автоматическая** (warmup_api.py:553-565). Битые URL пропускаются (line 561).

### 4.2. Полная замена пула

Положи новый файл с тем же именем `urls/40k_all_urls.txt` ИЛИ укажи другой путь в `config.ini`:
```ini
[api]
url_pool_file = urls/200k_diverse_urls.txt
```

### 4.3. Несколько разных пулов (для разных VPS)

**Сейчас не поддерживается** — все VPS используют один файл из репо. Если хочешь раздельные пулы:
- Создай `urls/pool_news.txt`, `urls/pool_shopping.txt` и т.д.
- На конкретной VPS правь `config.ini` вручную (НЕ коммить эту правку, иначе все VPS подтянут):
```powershell
cd C:\warmup
(Get-Content config.ini) -replace 'url_pool_file = urls/40k_all_urls.txt', 'url_pool_file = urls/pool_news.txt' | Set-Content config.ini
```
- Чтобы при `git pull` локальная правка не слетела:
```powershell
git update-index --assume-unchanged config.ini
```

### 4.4. Тематика / язык / гео URL

**В коде нет фильтра по тематике.** Если хочешь только "russian news" — отфильтруй файл заранее:
```powershell
Get-Content urls/40k_all_urls.txt | Where-Object { $_ -match '\.ru/|lenta\.ru|tass\.ru' } | Set-Content urls/40k_ru_news.txt
```

⚠️ **Не упирайся в маленький пул.** На парке 10 VPS × target=500 = 5000 URL минимум. 40k достаточно. Если меньше 2000 URL — будут повторы между VPS, прогрев менее эффективен.

**Код:** `warmup_api.py:550-565` (`load_url_pool`), `:570-598` (`sample_urls_for_run`).

---

## 5. Изменить тайминги / поведение по UI

**⚠️ Опасная зона. Прочитай раздел "Тайминги (consciously conservative)" в `CLAUDE.md` ДО изменений.**

Ключевые параметры и где они:

| Параметр | Файл | Когда меняешь |
|---|---|---|
| `launch_wait_seconds = 600` | config.ini:68 | Если LS реально не успевает за 10 мин на новых VPS (редко) |
| `confidence = 0.80` | config.ini:103 | НЕ ПОНИЖАТЬ ниже 0.76 (см. CLAUDE.md правило #2) |
| `wait_seconds = 30` | config.ini:107 | Только в большую сторону |
| `pause_between_chunks_seconds = 10` | config.ini:37 | Поднять до 15-20 если 409 conflict часто |
| `poll_timeout_seconds = 600` | config.ini:42 | Один чанк не должен дольше — иначе bug в LS |
| `http_timeout_seconds = 30` | config.ini:48 | Только в большую |
| `viewing_depth = 3, time_per_url = 7` | config.ini:72-74 | Глубина просмотра одной страницы. Понизишь → меньше cookies. Поднимешь → дольше цикл. |

**Hardcoded таймауты (правка в .py)** — не трогать без обсуждения:
- `wait_for("import_button") timeout=600` в `warmup.py` (для LS cloud sync)
- `_POST_IMPORT_POLL_SCHEDULE = [10, 30, 60, 120, 240]` в `warmup.py`
- ping retry `[5, 10, 15, 20]` в `warmup_api.py`
- 9-step pyramid в `warmup_api.py` (для 409 conflict)

---

## 6. Изменить ntfy push'и (адресат, тон, темы)

**Файл:** `warmup_api.py`, `warmup.py`, `ls_watchdog.py`, `notify_boot.py` — все шлют в один топик через `ntfy_url`.

**ntfy topic** хранится в:
- `setup.ps1` (env var `NTFY_TOPIC`, передаётся в `credentials.ini`)
- `credentials.ini` (читается всеми скриптами)

**Поменять топик на лету (на работающей VPS):**
```powershell
cd C:\warmup
(Get-Content credentials.ini) -replace 'ntfy_topic=.*', 'ntfy_topic=my-new-topic' | Set-Content credentials.ini
```

**Добавить новый push в новом месте кода:**
- Используй существующую функцию `notify(...)` или `_notify_*()` (есть в каждом из 4 скриптов).
- Параметры: `title`, `message`, `priority` (`low`/`high`), `tags` (`check`, `warning`, `gear`, `tada`, `hourglass`, `refresh`).
- **priority=high** только для того, что оператору надо увидеть СЕЙЧАС (звуковой push на телефон). `low` — для прогресса.

См. таблицу "Все push notifications" в CLAUDE.md (секция 16.06.2026).

---

## 7. Изменить расписание Task Scheduler

**Файл:** `schedule_hourly.ps1` — регистрирует обе задачи.

| Что | Сейчас | Где | Когда меняешь |
|---|---|---|---|
| LinkenSphereWarmup интервал | 45 мин | schedule_hourly.ps1 | Только в большую (минимум 45) — иначе worst-case цикл (37 мин) не успеет |
| LinkenSphereWarmup AtStartup Delay | PT2M | schedule_hourly.ps1 | НЕ менять (E-13) |
| LsWatchdog интервал | 5 мин | schedule_hourly.ps1 | Дешевая проверка, не трогай |
| LsWatchdog AtStartup Delay | PT3M | schedule_hourly.ps1 | НЕ менять |

**Применить новое расписание на работающей VPS:**
```powershell
cd C:\warmup
git pull
schtasks /delete /tn LinkenSphereWarmup /f
schtasks /delete /tn LsWatchdog /f
.\schedule_hourly.ps1
```

---

## 8. Чек-лист после ЛЮБОГО изменения

```
[ ] 1. Понимаешь к каким файлам это применяется (config.ini / .py / .ps1)?
[ ] 2. Понимаешь, нужна ли миграция state-файлов на работающих VPS?
[ ] 3. Если меняешь архитектуру / тайминги / UI flow — обсудил с оператором?
[ ] 4. git add → git commit -m "..." → git push в обе ветки (claude/... и main)
[ ] 5. На существующих VPS: оператор делает `cd C:\warmup; git pull` ВРУЧНУЮ
[ ] 6. Если параметр читается один раз (.warmup_target и др.) — нужен ручной reset
[ ] 7. Не закоммитил случайно credentials.ini / .warmup_* / .session_* (они в .gitignore, проверь `git status`)
[ ] 8. Если меняешь push-формат — обнови таблицу в CLAUDE.md
[ ] 9. Если добавляешь новый state-файл — добавь в таблицу CLAUDE.md и в freshstart.bat
```

---

## 9. Где НЕ читать настройки (антипаттерны)

- ❌ **Hardcoded значения раскиданы по коду** — не вписывай магические числа в Python. Используй `cfg.getint("section", "key", fallback=...)`.
- ❌ **Не дублируй параметр в двух местах** — например `urls_per_chunk_max` читается только в одном месте (line 1197). Не копируй в другой файл.
- ❌ **Не правь run_api.bat для перехвата параметров** — это dispatcher, должен быть простой и не знать про конфиг.

---

## 10. Шпаргалка "хочу поменять X" → "редактируй Y"

| Хочу | Файл | Раздел |
|---|---|---|
| Меньше прогрева (200-300 вместо 300-500) | config.ini | [api] urls_total_target_min/max |
| Быстрее закончить (больше URL за tick) | config.ini | [api] urls_per_run_min/max |
| Другие видеокарты в fingerprint | session_template.py | ADAPTER_PROFILES |
| Только Windows 11 | session_template.py | SYS_VERSIONS = ["11"] |
| Только русские сайты | urls/40k_all_urls.txt (заменить) | — |
| Больше глубины (больше cookies/page) | config.ini | [warmup] viewing_depth |
| Другой ntfy топик | credentials.ini | ntfy_topic |
| Цикл реже 45 мин | schedule_hourly.ps1 | Trigger -RepetitionInterval |
| Другой LS API порт | config.ini | [api] base_url + warmup.py logic |
| Disable wizard scan | (вручную) | New-Item .wizards_done |

---

## Связанные документы

- **CLAUDE.md** — главный архитектурный документ (логика, тайминги, edge cases, операторская дисциплина).
- **architecture.pdf** — v3.0 Deep Dive с диаграммами (для глубокого погружения).
- **ИНСТРУКЦИЯ.md** — пользовательская инструкция для оператора (что делать).
- **README.md** — intro репо.

**Если нашёл что-то не покрытое этим гайдом** — добавь раздел сюда, не плоди новые .md.
