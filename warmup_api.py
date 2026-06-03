"""
Linken Sphere 2 — прогрев через локальный HTTP-API (порт 36555).

Предусловие: API-порт уже включён в LS (Settings → Network → API port).
Активацию делает one-time UI-инсталляция в warmup.py (этап будет добавлен,
когда придут скриншоты этого экрана). Этот файл — только API-runtime.

Сценарий каждого запуска (расписание):
  1. Пинг API → если порт не отвечает, фейлим с понятной ntfy-ошибкой.
  2. POST /auth/signin — логинимся (email/password из credentials.ini).
  3. GET /sessions — находим сессию по имени (credentials.ini [session] name).
  4. Берём 4-6 рандомных URL из общего пула urls/*.txt (Вариант A — без архивации).
  5. POST /sessions/start_warmup — запускаем прогрев на найденной сессии.
  6. Поллим состояние, пока прогрев не закончится (или таймаут).
  7. ntfy: каждый успех = low priority пинг; каждое падение = high priority.

Точные имена эндпоинтов LS API могут отличаться — флаг ENDPOINTS ниже легко
правится без переписывания клиента.
"""

from __future__ import annotations

import configparser
import json
import logging
import random
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "warmup_api.log"
CONFIG_PATH = ROOT / "config.ini"
CREDS_PATH = ROOT / "credentials.ini"
# Имя сессии этой машины (CL-XXXXXXXX), пишется warmup.py при первой инсталляции.
SESSION_NAME_FILE = ROOT / ".session_name"
# Флаг «сессия импортирована», пишется warmup.py после UI-импорта.
# Формат: «<uuid>\t<name>» (новый) либо просто «<name>» (старый — fallback).
SESSION_IMPORTED_FLAG = ROOT / ".session_imported"
# One-shot режим: целевой объём прогрева и текущий счётчик.
# .warmup_target — random.randint(min, max), фиксируется один раз.
# .warmup_count — суммарно прогрето URL с момента инсталляции.
WARMUP_TARGET_FILE = ROOT / ".warmup_target"
WARMUP_COUNT_FILE = ROOT / ".warmup_count"
# UNIX-таймстемп ПЕРВОГО запуска (когда сгенерили target). Нужен чтобы
# в финальной "all done" нотификации показать общее время задачи
# целиком — от первого тика scheduler до достижения target.
WARMUP_STARTED_AT_FILE = ROOT / ".warmup_started_at"
# Дата первого старта этой машины (пишется один раз). Нужна потому, что
# у разных VPS часто одинаковый hostname (admin/admin) — по дате старта
# их легко различать в ленте уведомлений.
# Флаг «done-уведомление уже отправлено». Если scheduler по какой-то
# причине ещё стреляет (schtasks /disable не сработал), мы НЕ шлём
# повторные «all jobs done» — просто тихо пытаемся ещё раз отключить
# задачу и выходим. Юзер получает уведомление РОВНО один раз.
NOTIFIED_DONE_FLAG = ROOT / ".notified_done"
# Папка, куда LS пишет экспортнутые cookies при достижении target. Не
# чистится — машины одноразовые (1 цикл прогревов = 1 машина), файлы
# могут пригодиться оператору (забрать готовый cookie-jar).
COOKIES_EXPORT_DIR = ROOT / "cookies_export"
# Имя задачи в Task Scheduler — должно совпадать с тем, что регистрирует
# schedule_hourly.ps1. После достижения target скрипт сам её disable'нёт.
TASK_NAME = "LinkenSphereWarmup"

# Тот же топик, что и UI-флоу, — чтобы все push'ы шли в один канал.
NTFY_TOPIC = "warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE"

# Имена эндпоинтов вынесены сюда — если API LS отдаёт другие пути,
# правим только это место.
ENDPOINTS = {
    "signin": "/auth/signin",
    "sessions": "/sessions",
    "start_warmup": "/sessions/start_warmup",
    # Реальные эндпоинты остановки из LS API docs (PDF a83dad2a):
    # /sessions/stop_warmup не существует — POST туда даёт 405.
    "stop": "/sessions/stop",                            # мягкий стоп сессии
    "force_stop": "/sessions/force_stop",                # принудительный
    # Разблокировка зависших сессий ТОЛЬКО на нашем desktop'е (этой VPS).
    # Сессии других VPS на том же аккаунте — на их desktop'ах, не задеваются.
    "unlock_blocked": "/desktops/unlock_stopped_sessions",
    "export_cookies": "/sessions/export_cookies",
}


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("warmup_api")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.handlers.clear()
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


log = setup_logging()


def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    return cfg


def load_credentials() -> configparser.ConfigParser:
    if not CREDS_PATH.exists():
        raise FileNotFoundError(
            "credentials.ini не найден. Скопируй credentials.ini.example и заполни."
        )
    creds = configparser.ConfigParser()
    creds.read(CREDS_PATH, encoding="utf-8")
    return creds


def load_session_name() -> str:
    """Имя сессии этой машины. Источники по приоритету:
      1. .session_name (создаёт warmup.py на первом запуске)
      2. .session_imported (миграция со старого формата — там было только имя)
    Если ни одного — фейл: install не отработал."""
    if SESSION_NAME_FILE.exists():
        name = SESSION_NAME_FILE.read_text(encoding="utf-8").strip()
        if name:
            return name
    if SESSION_IMPORTED_FLAG.exists():
        raw = SESSION_IMPORTED_FLAG.read_text(encoding="utf-8").strip()
        if raw:
            # старый формат: только имя, либо новый — «<uuid>\t<name>»
            _, _, name = raw.partition("\t")
            return (name or raw).strip()
    raise RuntimeError(
        f"не найден ни .session_name, ни .session_imported — "
        f"запусти install/run.bat хотя бы раз, чтобы инициализировать сессию."
    )


# Эмодзи в Title по типу события (по первому тегу). Telegram-бридж НЕ
# подставляет эмодзи из ntfy-тегов, поэтому кладём их прямо в заголовок.
_TAG_EMOJI = {"white_check_mark": "✅", "tada": "🎉", "warning": "⚠️"}
# ntfy JSON-priority — число 1..5. Маппим из наших строковых уровней.
_PRIORITY_NUM = {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5, "urgent": 5}


def notify_ntfy(message: str, *, title: str, priority: str, tags: str) -> None:
    """Шлёт push через ntfy.sh JSON-публикацией. Ошибки глотаем — нотификация
    не должна валить запуск.

    Почему JSON, а не HTTP-заголовки: в заголовки HTTP нельзя положить
    эмодзи/UTF-8 (только ASCII), поэтому раньше Title в Telegram приходил
    без иконки. В JSON-теле title/tags — обычные строки, json.dumps сам
    экранирует Unicode в \\uXXXX, тело уходит чистым ASCII — ничего не
    ломается ни на каком codepage."""
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        emoji = _TAG_EMOJI.get(tag_list[0], "") if tag_list else ""
        disp_title = f"{emoji} {title}".strip()
        payload = {
            "topic": NTFY_TOPIC,
            "title": disp_title,
            "message": message[:4000],
            "priority": _PRIORITY_NUM.get(priority, 3),
            "tags": tag_list,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://ntfy.sh",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        log.info("ntfy отправлен (%s, %s)", disp_title, priority)
    except Exception as e:
        log.warning("notify_ntfy failed: %s", e)


# Кэш публичного IP — один HTTP-запрос на Python-процесс, потом в памяти.
_machine_ip_cache: str | None = None


def _machine_id() -> str:
    """Публичный IP машины — стабильный уникальный идентификатор VPS.
    Hostname часто бесполезен (по дефолту 'WIN-XXX' или одинаковый
    'AKOPTO' у админ-аккаунта). IP уникален у каждой VPS.

    Кэшируется в in-process переменной на всё время жизни Python-процесса.
    На каждом новом Python-запуске пере-фетчится (provider мог переназначить
    IP после ребута). ipify основной, checkip.amazonaws.com fallback —
    оба отдают plain text IP в теле, без заголовков. Если сеть лежит —
    возвращаем 'no-ip', юзер сразу видит косяк."""
    global _machine_ip_cache
    if _machine_ip_cache:
        return _machine_ip_cache
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode("ascii", errors="ignore").strip()
                if ip:
                    _machine_ip_cache = ip
                    return ip
        except Exception:
            continue
    return "no-ip"


def _ntfy_header() -> str:
    """Единый префикс для всех ntfy-сообщений: session + machine.
    session ВСЕГДА первым — на телефоне его удобно ловить глазом,
    т.к. имена машин (hostname) могут совпадать между VPS."""
    try:
        sess = load_session_name()
    except Exception:
        sess = "<unknown>"
    return f"session: {sess}\nmachine: {_machine_id()}\n"


def load_url_pool(cfg: configparser.ConfigParser) -> list[str]:
    """Читает большой файл-пул из [api] url_pool_file, дедуплицирует."""
    rel = cfg.get("api", "url_pool_file", fallback="urls/40k_all_urls.txt")
    pool_path = ROOT / rel
    if not pool_path.exists():
        raise FileNotFoundError(f"URL-пул не найден: {pool_path}")
    seen: set[str] = set()
    pool: list[str] = []
    for line in pool_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        u = line.strip()
        if u and u not in seen:
            seen.add(u)
            pool.append(u)
    if not pool:
        raise RuntimeError(f"в {pool_path} нет ни одного URL")
    return pool


def materialize_run_urls(
    pool: list[str], cfg: configparser.ConfigParser
) -> tuple[Path, list[str]]:
    """Сэмплит ~100 случайных URL из пула (urls_per_run_min..max), пишет
    файлом в urls_generated/run_<ts>.txt для audit. Возвращает (файл, urls)."""
    n_min = cfg.getint("api", "urls_per_run_min", fallback=95)
    n_max = cfg.getint("api", "urls_per_run_max", fallback=105)
    n = min(random.randint(n_min, n_max), len(pool))
    urls = random.sample(pool, n)

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "urls_generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"run_{ts}.txt"
    out_file.write_text("\n".join(urls) + "\n", encoding="utf-8")
    log.info("выбрано %d URL → %s", n, out_file)
    return out_file, urls


def load_or_create_target(cfg: configparser.ConfigParser) -> int:
    """Целевой суммарный объём прогрева. На первом запуске генерится
    random.randint(min, max) и пишется в .warmup_target. Дальше всегда
    читается оттуда — менять задним числом нельзя, иначе counter
    становится неконсистентен."""
    if WARMUP_TARGET_FILE.exists():
        try:
            return int(WARMUP_TARGET_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pass
    lo = cfg.getint("api", "urls_total_target_min", fallback=300)
    hi = cfg.getint("api", "urls_total_target_max", fallback=500)
    target = random.randint(lo, hi)
    WARMUP_TARGET_FILE.write_text(str(target), encoding="utf-8")
    WARMUP_STARTED_AT_FILE.write_text(str(int(time.time())), encoding="utf-8")
    log.info("целевой объём прогрева: %d URL (зафиксирован в .warmup_target)", target)
    return target


def load_started_at() -> int | None:
    if not WARMUP_STARTED_AT_FILE.exists():
        return None
    try:
        return int(WARMUP_STARTED_AT_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def count_session_cookies(
    client: "ApiClient",
    uuid: str,
    pre_wait_seconds: int = 60,
    max_retries: int = 3,
) -> int | None:
    """Считает cookies в сессии через POST /sessions/export_cookies.
    Прямого 'count' эндпоинта в LS API нет (см. PDF docs).

    Экспорт идёт в постоянную папку COOKIES_EXPORT_DIR (C:\\warmup\\
    cookies_export\\). Не чистим — машины одноразовые (1 цикл = 1 машина),
    оператору может пригодиться сам файл cookies.

    ВАЖНО про тайминг: LS после завершения warmup ещё ~30-50с дописывает
    последние cookies на диск (тот самый internal save, который иногда
    застревает как 'Saving data...'). Если позвать export сразу — либо
    получим неполный файл, либо словим HTTP 409 'Session is used by
    another client or operation' и можем спровоцировать ровно ту
    залипуху, которой боимся. Поэтому:
      - ждём pre_wait_seconds перед первой попыткой (даём LS дофлашить),
      - на 409 — спим 30с и ретраим (без force_stop, чтобы не разрушить
        текущее сохранение).
    Если до max_retries не получилось — отдаём None, в нотификации
    будет 'cookies: ?'. Никаких агрессивных действий не делаем.

    Формат файла LS — наблюдался JSON-массив [{...},{...}] с расширением
    .txt (имя <session_name>_<DD-MM-YYYY>.txt). Парсим JSON-массив /
    JSON-словарь / Netscape-text fallback — см. _count_cookies_in_payload."""
    log.info("ждём %dс — LS дофлашит последние cookies перед export...",
             pre_wait_seconds)
    time.sleep(pre_wait_seconds)

    COOKIES_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    # Запомним содержимое ДО экспорта, чтобы не пересчитать файлы от
    # предыдущих неудачных попыток (на случай если функция вызвалась
    # повторно). Считаем только то, что появится в этот вызов.
    before = {p.name for p in COOKIES_EXPORT_DIR.iterdir() if p.is_file()}

    for attempt in range(max_retries):
        try:
            client.export_cookies(uuid, str(COOKIES_EXPORT_DIR))
            break
        except ApiError as e:
            msg = str(e)
            is_lock = "HTTP 409" in msg and "Session is used" in msg
            if is_lock and attempt < max_retries - 1:
                log.warning(
                    "export_cookies: 409 (LS ещё сохраняет?) — sleep 30с, ретрай %d/%d",
                    attempt + 2, max_retries,
                )
                time.sleep(30)
                continue
            log.warning("export_cookies упал: %s", e)
            return None

    new_files = [
        p for p in COOKIES_EXPORT_DIR.iterdir()
        if p.is_file() and p.name not in before
    ]
    if not new_files:
        log.warning("export_cookies: новых файлов в %s не появилось",
                    COOKIES_EXPORT_DIR)
        return None
    total = 0
    for f in new_files:
        try:
            raw = f.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            log.warning("не прочитал %s: %s", f, e)
            continue
        n = _count_cookies_in_payload(raw)
        log.info("cookies file %s → %d", f.name, n)
        total += n
    log.info("cookies экспортнуты в %s (оставлены оператору)",
             COOKIES_EXPORT_DIR)
    return total


def _count_cookies_in_payload(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        return 0
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            # Может быть {"cookies": [...]} или {"<host>": [...]}.
            for v in data.values():
                if isinstance(v, list):
                    return len(v)
            return 1
    # Netscape-формат либо одна-куки-на-строку.
    return sum(
        1 for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _fmt_total_elapsed(started_at: int | None) -> str:
    if started_at is None:
        return "n/a (нет .warmup_started_at)"
    secs = max(0, int(time.time() - started_at))
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}ч {m}мин"
    return f"{m} мин"


def load_warmed_count() -> int:
    if not WARMUP_COUNT_FILE.exists():
        return 0
    try:
        return int(WARMUP_COUNT_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return 0


def add_warmed_count(n: int) -> int:
    new_total = load_warmed_count() + n
    WARMUP_COUNT_FILE.write_text(str(new_total), encoding="utf-8")
    return new_total


def already_notified_done() -> bool:
    return NOTIFIED_DONE_FLAG.exists()


def mark_notified_done() -> None:
    try:
        NOTIFIED_DONE_FLAG.write_text("1", encoding="utf-8")
    except OSError as e:
        log.warning("не получилось записать %s: %s", NOTIFIED_DONE_FLAG, e)


def disable_scheduled_task() -> bool:
    """Гарантированно гасит scheduled-задачу. Три уровня fallback'а, потому
    что schtasks бывает фейлится тихо (cp1251 окружение, нет прав на
    /change, путь к задаче в подпапке и т.п.) — а если задача продолжает
    стрелять, юзер получает спам «all jobs done» каждые 45 минут.
      1) schtasks /change /tn <name> /disable
      2) powershell Disable-ScheduledTask -TaskName <name>
      3) schtasks /delete /tn <name> /f   (последний рубеж)
    Возвращает True если хоть один шаг отработал returncode=0."""
    import subprocess

    def _run(cmd: list[str], label: str) -> bool:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=20, text=True)
            if r.returncode == 0:
                log.info("disable scheduled task: %s — OK", label)
                return True
            log.warning("disable scheduled task: %s — rc=%d stderr=%r stdout=%r",
                        label, r.returncode, (r.stderr or "").strip(), (r.stdout or "").strip())
            return False
        except Exception as e:
            log.warning("disable scheduled task: %s — exception: %s", label, e)
            return False

    if _run(["schtasks", "/change", "/tn", TASK_NAME, "/disable"], "schtasks /change /disable"):
        return True
    if _run(
        ["powershell", "-NoProfile", "-Command",
         f"Disable-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop"],
        "powershell Disable-ScheduledTask",
    ):
        return True
    if _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], "schtasks /delete /f"):
        return True
    log.error("ВСЕ способы disable schedule failed — задача продолжит стрелять")
    return False


def load_session_imported_flag() -> tuple[str | None, str | None]:
    """Читает .session_imported. Возвращает (uuid, name).
    Поддерживает два формата:
      - новый: «<uuid>\\t<name>» — записывается UI-инсталляцией после lookup
      - старый: «<name>» — записан до перехода на uuid-flow
    Если файла нет, возвращает (None, None) — сессию ещё не импортили."""
    if not SESSION_IMPORTED_FLAG.exists():
        return None, None
    raw = SESSION_IMPORTED_FLAG.read_text(encoding="utf-8").strip()
    if not raw:
        return None, None
    if "\t" in raw:
        uuid, _, name = raw.partition("\t")
        return uuid.strip() or None, name.strip() or None
    return None, raw


class ApiError(Exception):
    pass


class ApiClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | list | None:
        url = f"{self.base_url}{path}"
        data: bytes | None = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            body_full = ""
            try:
                body_full = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            # Полный текст ответа всегда в логе — там можем grep'нуть валидатор
            log.error("HTTP %d on %s %s — full body (%d chars): %s",
                      e.code, method, path, len(body_full), body_full)
            # В исключение (и в ntfy) — хвост: валидатор пишет причину ПОСЛЕ
            # того как зеркалит наш запрос обратно, поэтому начало нам не нужно.
            short = body_full[-1500:] if len(body_full) > 1500 else body_full
            raise ApiError(f"{method} {path} → HTTP {e.code}: {short}") from e
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise ApiError(f"{method} {path} → сеть/таймаут: {e}") from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="ignore")

    def ping(self) -> None:
        """Лёгкий ping — пытаемся GET /sessions. Если API-порт активен, ответ
        будет (хоть 401, хоть 200); если порт не открыт, кидаем сетевую ошибку."""
        try:
            self._request("GET", ENDPOINTS["sessions"])
        except ApiError as e:
            # 401/403 тоже валидный «порт жив» — отличаем только сетевые сбои.
            msg = str(e)
            if "HTTP 4" in msg or "HTTP 5" in msg:
                return
            raise

    def signin(self, email: str, password: str) -> None:
        """Логинит LS-приложение в аккаунт. Авторизация дальше держится
        самим процессом LS — отдельных Bearer-токенов в API нет (см. дока).

        ВАЖНО: после первого успешного signin последующие вызовы возвращают
        HTTP 400 {"error":"Already signed in"} — это НЕ ошибка, это норма
        для LS API. Глотаем такое и идём дальше."""
        try:
            self._request("POST", ENDPOINTS["signin"], {"email": email, "password": password})
            log.info("signin OK")
        except ApiError as e:
            msg = str(e)
            if "Already signed in" in msg or "already signed in" in msg.lower():
                log.info("signin: already signed in (LS app keeps session) — OK")
                return
            raise

    def list_sessions(self) -> list[dict]:
        out = self._request("GET", ENDPOINTS["sessions"])
        if isinstance(out, dict) and "sessions" in out:
            out = out["sessions"]
        if not isinstance(out, list):
            raise ApiError(f"GET /sessions: ожидался list, получили {type(out).__name__}")
        return out

    def find_session_by_name(self, name: str) -> dict:
        """Возвращает единственную сессию с этим именем. Если их несколько —
        ApiError: имя не уникально, ставим прогрев на паузу до ручного разбора.
        Это safety-net на случай, если флаг .session_imported потерян или ещё
        не записан (новый формат хранит uuid и эта функция вообще не вызывается)."""
        sessions = self.list_sessions()
        matches = [s for s in sessions if isinstance(s, dict) and s.get("name") == name]
        if not matches:
            raise ApiError(f"сессия с именем {name!r} не найдена в /sessions")
        if len(matches) > 1:
            uuids = [m.get("uuid") for m in matches]
            raise ApiError(
                f"коллизия имён: {len(matches)} сессий с именем {name!r}, "
                f"uuids={uuids}. Удали лишние в LS или укажи uuid в .session_imported"
            )
        return matches[0]

    def find_session_by_uuid(self, uuid: str) -> dict:
        sessions = self.list_sessions()
        for s in sessions:
            if isinstance(s, dict) and s.get("uuid") == uuid:
                return s
        raise ApiError(f"сессия с uuid={uuid!r} не найдена — удалена вручную?")

    def start_warmup(
        self,
        uuid: str,
        urls: list[str],
        view_depth: int,
        time_per_url: int,
    ) -> dict | list | None:
        body = {
            "uuid": uuid,
            "view_depth": view_depth,
            "time_per_url": time_per_url,
            "urls": urls,
        }
        return self._request("POST", ENDPOINTS["start_warmup"], body)

    def stop_session(self, uuid: str) -> dict | list | None:
        """Мягко останавливает сессию (POST /sessions/stop). Принимает СТРОГО
        наш uuid аргументом — чужие сессии того же LS-аккаунта (на других
        VPS) не задеваются."""
        return self._request("POST", ENDPOINTS["stop"], {"uuid": uuid})

    def force_stop_session(self, uuid: str) -> dict | list | None:
        """Жёстко останавливает сессию (POST /sessions/force_stop). Юзается
        как fallback, если обычный stop не сработал (сессия залипла в
        saving / какое-то долгое внутреннее состояние). Тоже строго наш uuid."""
        return self._request("POST", ENDPOINTS["force_stop"], {"uuid": uuid})

    def export_cookies(self, uuid: str, folder_path: str) -> dict | list | None:
        """Экспортирует cookies сессии в файл внутри folder_path (POST
        /sessions/export_cookies). В доке поле называется uuids — массив
        uuid'ов, в нашем случае всегда один (наш). LS сам выбирает имя
        файла (наблюдалось <uuid>.json/.txt)."""
        return self._request(
            "POST", ENDPOINTS["export_cookies"],
            {"uuids": [uuid], "folder_path": folder_path},
        )

    def unlock_blocked_sessions(self) -> dict | list | None:
        """Разблокировка сессий на ТЕКУЩЕМ desktop'е (POST /desktops/
        unlock_stopped_sessions). Из доки: 'Unlock blocked sessions'. Не
        принимает uuid — оперирует со всем desktop'ом.

        БЕЗОПАСНО ДЛЯ ЧУЖИХ VPS: desktop'ы у каждой VPS свои (хоть LS-аккаунт
        общий), эта операция трогает только тот desktop, который активен в
        локальном LS на этой машине. Сессии других VPS — на ИХ desktop'ах.

        Юзаем как recovery при HTTP 409 'Session is used by another client
        or operation' когда стандартные ретраи не помогают."""
        return self._request("POST", ENDPOINTS["unlock_blocked"], {})

    def get_session_state(self, uuid: str) -> dict | None:
        """Состояние конкретной сессии. Сначала пробуем GET /sessions/{uuid},
        при 404 — fallback на список."""
        try:
            out = self._request("GET", f"{ENDPOINTS['sessions']}/{uuid}")
            if isinstance(out, dict):
                return out
        except ApiError:
            pass
        for s in self.list_sessions():
            if isinstance(s, dict) and s.get("uuid") == uuid:
                return s
        return None


# LS API: поле статуса — `status`. Известные значения (из доки + наблюдения):
#   warmup            — идёт наш прогрев (продолжаем поллить)
#   automationRunning — идёт другая автоматизация (продолжаем поллить)
#   running           — браузер открыт без автоматизации (продолжаем поллить)
#   stopped           — простаивает, ГОТОВА к следующей операции
#   imported          — только что импортирована, ГОТОВА
# Терминальные («можно стартовать следующий чанк»): stopped, imported.
# Всё остальное (включая, например, гипотетический «saving») — ЖДЁМ.
# Если ждать «status != warmup» как раньше, то при переходе warmup→saving
# мы решали «готово» слишком рано → следующий start_warmup ловил 409.
TERMINAL_STATUSES = ("stopped", "imported")
WARMUP_STATUS = "warmup"


def _is_warmup_done(state: dict | None) -> bool:
    if state is None:
        return False
    status = state.get("status")
    if not isinstance(status, str):
        return False
    return status in TERMINAL_STATUSES


def wait_for_warmup_done(client: ApiClient, uuid: str, cfg: configparser.ConfigParser) -> bool:
    interval = cfg.getfloat("api", "poll_interval_seconds", fallback=5.0)
    timeout = cfg.getfloat("api", "poll_timeout_seconds", fallback=1200.0)
    deadline = time.time() + timeout
    last_status: str | None = None
    # Дать LS пару секунд перейти в "warmup" — иначе можем словить старый
    # status и решить, что прогрев уже закончился.
    time.sleep(min(interval, 3.0))
    while time.time() < deadline:
        state = client.get_session_state(uuid)
        if state is not None:
            status = state.get("status")
            if status != last_status:
                log.info("status: %r", status)
                last_status = status
            if _is_warmup_done(state):
                return True
        time.sleep(interval)
    log.warning("поллинг истёк за %.0fс, состояние неизвестно", timeout)
    return False


def run() -> int:
    log.info("=" * 60)
    log.info("Linken Sphere warm-up via API: старт")

    try:
        cfg = load_config()
        creds = load_credentials()
        session_name = load_session_name()
        email = creds.get("account", "email").strip()
        password = creds.get("account", "password")

        # One-shot гейт: уже прогрели целевой объём? — JOB DONE, на выход.
        # Уведомление шлём РОВНО один раз (флаг .notified_done). Если scheduler
        # всё ещё стреляет (significa disable не сработал на прошлом запуске)
        # — пытаемся ещё раз, но без спама в ntfy.
        target = load_or_create_target(cfg)
        current = load_warmed_count()
        if current >= target:
            if already_notified_done():
                log.info("ALL JOBS DONE: %d/%d, уже уведомлял — тихий ретрай disable", current, target)
                disable_scheduled_task()
                return 0
            log.info("ALL JOBS DONE: %d/%d URL прогрето — disable + notify", current, target)
            disabled = disable_scheduled_task()
            tail = ("scheduled task disabled. All jobs done 🎉"
                    if disabled else
                    "could NOT disable schedule — run manually:\n"
                    f"  schtasks /change /tn {TASK_NAME} /disable")
            notify_ntfy(
                _ntfy_header() +
                f"total: {current}/{target} URL warmed\n" + tail,
                title="warmup all done",
                priority="low",
                tags="tada",
            )
            mark_notified_done()
            return 0
        log.info("прогресс: %d/%d URL (осталось ~%d)", current, target, max(0, target - current))

        base_url = cfg.get("api", "base_url", fallback="http://127.0.0.1:36555")
        http_timeout = cfg.getfloat("api", "http_timeout_seconds", fallback=15.0)
        view_depth = cfg.getint("warmup", "viewing_depth", fallback=3)
        time_per_url = cfg.getint("warmup", "time_per_url", fallback=7)

        client = ApiClient(base_url, http_timeout)

        log.info("ping API %s …", base_url)
        client.ping()

        log.info("signin как %s", email)
        client.signin(email, password)

        # Сначала пробуем uuid из флага .session_imported (его пишет UI-инсталляция
        # после импорта xlsx + GET /sessions). Это убирает любые коллизии имён.
        # Если флага нет или в нём только имя — fallback на поиск по имени.
        stored_uuid, stored_name = load_session_imported_flag()
        if stored_uuid:
            log.info("uuid сессии из .session_imported: %s (name=%r)", stored_uuid, stored_name)
            sess = client.find_session_by_uuid(stored_uuid)
            uuid = stored_uuid
        else:
            log.info("ищу сессию по имени %r (uuid в .session_imported не записан)", session_name)
            sess = client.find_session_by_name(session_name)
            uuid = sess.get("uuid")
            if not uuid:
                raise ApiError(f"в сессии {session_name!r} нет uuid: {sess}")
        log.info("используем uuid=%s (name=%r)", uuid, sess.get("name"))

        pool = load_url_pool(cfg)
        run_file, urls = materialize_run_urls(pool, cfg)
        log.info("план: %d URL (пул %d) view_depth=%d time_per_url=%d",
                 len(urls), len(pool), view_depth, time_per_url)

        # LS API не принимает большие массивы URL в одном /start_warmup
        # (валидатор отвечает 'array has too many items' уже на ~99). Так что
        # режем выбранные URL на чанки по urls_per_chunk_max и гоняем их
        # ПОСЛЕДОВАТЕЛЬНО, дожидаясь окончания каждого перед следующим.
        chunk_size = cfg.getint("api", "urls_per_chunk_max", fallback=7)
        chunks: list[list[str]] = [urls[i:i + chunk_size] for i in range(0, len(urls), chunk_size)]
        pause = cfg.getfloat("api", "pause_between_chunks_seconds", fallback=3.0)
        log.info("чанков: %d × до %d URL (всего %d)", len(chunks), chunk_size, len(urls))

        # Firewall watcher на ВСЁ время прогрева (все чанки + паузы).
        import subprocess
        fw_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "_firewall_watcher.py"), "15"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        t_start = time.time()
        chunks_done = 0
        try:
            for i, chunk in enumerate(chunks, start=1):
                log.info("=" * 50)
                log.info("чанк %d/%d (%d URL) — start_warmup", i, len(chunks), len(chunk))
                # Retry на HTTP 409 «Session is used by another client or
                # operation». Ловится либо когда LS внутри ещё не успела
                # перейти в stopped/imported после предыдущего чанка, либо
                # когда зомби-warmup на НАШЕЙ же сессии держит глобальный
                # API-лок (наблюдалось на свежеустановленной LS после
                # неаккуратного выхода).
                # Стратегия (молчаливое восстановление — пользователю шлём
                # ⚠️ только если ВСЕ 5 попыток провалились):
                #   попытка 1 → сразу start_warmup
                #   попытка 2 → sleep 30с, start_warmup
                #   попытка 3 → sleep 60с, start_warmup
                #   попытка 4 (soft recovery) → unlock_blocked_sessions
                #     (LS API: разблокировать брошенные сессии нашего desktop)
                #     + start_warmup
                #   попытка 5 (nuclear recovery) → force_stop НАШЕЙ сессии
                #     (на случай если именно она держит лок зомби-warmup'ом)
                #     + unlock_blocked_sessions + start_warmup
                # Только если и nuclear не помог — поднимаем ошибку.
                _last_err: Exception | None = None
                for attempt in range(5):
                    try:
                        client.start_warmup(uuid, chunk, view_depth, time_per_url)
                        _last_err = None
                        break
                    except ApiError as e:
                        msg = str(e)
                        retriable = "HTTP 409" in msg and "Session is used" in msg
                        _last_err = e
                        if not retriable or attempt == 4:
                            break
                        if attempt < 2:
                            wait = 30 * (attempt + 1)  # 30, 60
                            log.warning("чанк %d: 409 'session in use' — sleep %dс, ретрай %d/5",
                                        i, wait, attempt + 2)
                            time.sleep(wait)
                        elif attempt == 2:
                            log.warning("чанк %d: 409 после 90с — unlock_blocked_sessions + ретрай 4/5",
                                        i)
                            try:
                                client.unlock_blocked_sessions()
                                time.sleep(3)
                            except Exception as unlock_e:
                                log.warning("unlock_blocked_sessions упал: %s", unlock_e)
                        else:  # attempt == 3
                            log.warning("чанк %d: 409 не уходит — force_stop своей сессии + последний ретрай 5/5",
                                        i)
                            try:
                                client.force_stop_session(uuid)
                                time.sleep(5)
                                client.unlock_blocked_sessions()
                                time.sleep(3)
                            except Exception as nuke_e:
                                log.warning("nuclear recovery упал: %s", nuke_e)
                if _last_err is not None:
                    raise ApiError(f"чанк {i}/{len(chunks)}: start_warmup упал → {_last_err}") from _last_err
                ok = wait_for_warmup_done(client, uuid, cfg)
                if not ok:
                    log.warning("чанк %d не подтвердился поллингом", i)
                chunks_done += 1
                if i < len(chunks):
                    time.sleep(pause)
        finally:
            fw_proc.terminate()
            try:
                fw_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fw_proc.kill()
        elapsed = time.time() - t_start

        # Файл прогона больше не нужен (логи фиксируют что когда было).
        try:
            run_file.unlink()
        except OSError as e:
            log.warning("не получилось убрать %s: %s", run_file, e)

        # Считаем сколько URL реально прогрели в этом запуске (только успешные чанки).
        urls_warmed_now = sum(len(c) for c in chunks[:chunks_done])
        new_total = add_warmed_count(urls_warmed_now)
        target_reached = new_total >= target

        log.info("сценарий завершён успешно за %.0fм (%d/%d чанков, +%d URL → %d/%d)",
                 elapsed / 60, chunks_done, len(chunks), urls_warmed_now, new_total, target)

        if target_reached:
            disabled = disable_scheduled_task()
            total_elapsed = _fmt_total_elapsed(load_started_at())
            cookies_n = count_session_cookies(client, uuid)
            cookies_str = f"{cookies_n}" if cookies_n is not None else "?"
            tail = ("scheduled task disabled. All jobs done."
                    if disabled else
                    "could NOT disable schedule — run manually:\n"
                    f"  schtasks /change /tn {TASK_NAME} /disable")
            notify_ntfy(
                _ntfy_header() +
                f"this run: {urls_warmed_now} URL ({elapsed/60:.0f} мин)\n"
                f"total: {new_total}/{target} URL — target reached 🎉\n"
                f"total time: {total_elapsed}\n"
                f"cookies: {cookies_str}\n" + tail,
                title="warmup all done",
                priority="low",
                tags="tada",
            )
            mark_notified_done()
        else:
            notify_ntfy(
                _ntfy_header() +
                f"chunks: {chunks_done}/{len(chunks)} × до {chunk_size} = {urls_warmed_now} URL\n"
                f"progress: {new_total}/{target} URL ({100*new_total//target}%)\n"
                f"elapsed: {elapsed/60:.0f} мин",
                title="warmup OK",
                priority="low",
                tags="white_check_mark",
            )
        return 0

    except Exception as exc:
        log.exception("сценарий упал: %s", exc)
        # Перед выходом ПЫТАЕМСЯ освободить НАШУ сессию, чтобы следующий
        # scheduler-trigger не получил 409 от нашего же зомби. Строго наш
        # uuid из локалов, чужие сессии аккаунта не трогаем. Сначала мягкий
        # stop, на 4xx/5xx — force_stop. Если uuid ещё не определён (упало
        # на signin/lookup) — пропускаем тихо.
        try:
            _our_uuid = locals().get("uuid")
            if _our_uuid and "client" in locals():
                log.info("освобождаю свою сессию: stop(%s)", _our_uuid)
                try:
                    client.stop_session(_our_uuid)  # type: ignore[name-defined]
                except ApiError as _e:
                    log.warning("stop вернул %s → пробую force_stop", _e)
                    client.force_stop_session(_our_uuid)  # type: ignore[name-defined]
        except Exception as e:
            log.warning("освобождение своей сессии не сработало: %s", e)

        tail = ""
        if LOG_FILE.exists():
            try:
                with LOG_FILE.open(encoding="utf-8", errors="ignore") as f:
                    tail = "".join(f.readlines()[-15:])
            except Exception:
                pass

        # Спец-хинт для самой коварной ошибки — 409 «Session is used by
        # another client or operation». Чаще всего значит, что в LS висит
        # зомби-сессия (saving / warmup от прошлого падения) и держит
        # ГЛОБАЛЬНЫЙ API-лок. Чистится только руками через LS UI.
        hint = ""
        if "HTTP 409" in str(exc) and "Session is used" in str(exc):
            _our_name = locals().get("session_name") or "(см. session: выше)"
            hint = (
                "\nкакая-то сессия в LS держит глобальный API-лок.\n"
                "открой Linken Sphere → посмотри, у какой сессии 'Saving data...':\n"
                f" • если это {_our_name} (наша текущая) → удали её в LS UI (правый клик → Delete)\n"
                f"   + в cmd от админа: cd /d C:\\warmup && freshstart.bat\n"
                f"   (прогресс прогрева этой машины сбросится, начнём с новой сессии)\n"
                " • если это старая CL-* этой машины (после прошлого freshstart) → правый клик → Delete\n"
                "   (наш текущий прогрев продолжится с того же места)\n"
                " • если это сессия другой твоей VPS → перезапусти ТУ VPS, эту не трогай\n"
                "после этого следующий scheduler-trigger подхватит сам.\n"
            )

        notify_ntfy(
            _ntfy_header() +
            f"error: {exc}\n" + hint + "\n"
            f"tail:\n{tail}",
            title="warmup failed (api)",
            priority="high",
            tags="warning",
        )
        return 1


if __name__ == "__main__":
    sys.exit(run())
