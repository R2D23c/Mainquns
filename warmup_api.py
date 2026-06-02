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
# Дата первого старта этой машины (пишется один раз). Нужна потому, что
# у разных VPS часто одинаковый hostname (admin/admin) — по дате старта
# их легко различать в ленте уведомлений.
FIRST_START_FILE = ROOT / ".first_start"
# Флаг «done-уведомление уже отправлено». Если scheduler по какой-то
# причине ещё стреляет (schtasks /disable не сработал), мы НЕ шлём
# повторные «all jobs done» — просто тихо пытаемся ещё раз отключить
# задачу и выходим. Юзер получает уведомление РОВНО один раз.
NOTIFIED_DONE_FLAG = ROOT / ".notified_done"
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
    "stop_warmup": "/sessions/stop_warmup",
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


def _machine_id() -> str:
    """hostname + дата первого старта (формат «Akopto · 2026-06-02 14:23»).
    Дата фиксируется один раз в .first_start и больше не меняется — это
    стабильный различитель машин, когда hostname у всех одинаковый."""
    host = socket.gethostname()
    try:
        if FIRST_START_FILE.exists():
            stamp = FIRST_START_FILE.read_text(encoding="utf-8").strip()
        else:
            stamp = time.strftime("%Y-%m-%d %H:%M")
            FIRST_START_FILE.write_text(stamp, encoding="utf-8")
    except OSError:
        stamp = ""
    return f"{host} · {stamp}" if stamp else host


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
    log.info("целевой объём прогрева: %d URL (зафиксирован в .warmup_target)", target)
    return target


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
    стрелять, юзер получает спам «all jobs done» каждые 52 минуты.
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


# LS API: поле статуса — `status`, значения из доки:
#   ["running", "stopped", "imported", "warmup", "automationRunning"]
# Прогрев идёт == status == "warmup". Финал == status вышел из этого значения.
WARMUP_STATUS = "warmup"


def _is_warmup_done(state: dict | None) -> bool:
    if state is None:
        return False
    status = state.get("status")
    if not isinstance(status, str):
        return False
    return status != WARMUP_STATUS


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
    host = socket.gethostname()

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
                # operation». Эта ошибка ловится между чанками: предыдущий
                # warmup уже отдал status != "warmup", но LS внутри ещё
                # закрывает браузерные процессы и держит сессию занятой.
                # 3 попытки: первая сразу, потом ждём 15с / 30с.
                _last_err: Exception | None = None
                for attempt in range(3):
                    try:
                        client.start_warmup(uuid, chunk, view_depth, time_per_url)
                        _last_err = None
                        break
                    except ApiError as e:
                        msg = str(e)
                        retriable = "HTTP 409" in msg and "Session is used" in msg
                        _last_err = e
                        if not retriable or attempt == 2:
                            break
                        wait = 15 * (attempt + 1)  # 15, 30
                        log.warning("чанк %d: 409 'session in use' — sleep %dс, ретрай %d/3",
                                    i, wait, attempt + 2)
                        time.sleep(wait)
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
            tail = ("scheduled task disabled. All jobs done."
                    if disabled else
                    "could NOT disable schedule — run manually:\n"
                    f"  schtasks /change /tn {TASK_NAME} /disable")
            notify_ntfy(
                _ntfy_header() +
                f"this run: {urls_warmed_now} URL ({elapsed/60:.0f} мин)\n"
                f"total: {new_total}/{target} URL — target reached 🎉\n" + tail,
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
        tail = ""
        if LOG_FILE.exists():
            try:
                with LOG_FILE.open(encoding="utf-8", errors="ignore") as f:
                    tail = "".join(f.readlines()[-15:])
            except Exception:
                pass
        notify_ntfy(
            _ntfy_header() +
            f"error: {exc}\n\n"
            f"tail:\n{tail}",
            title="warmup failed (api)",
            priority="high",
            tags="warning",
        )
        return 1


if __name__ == "__main__":
    sys.exit(run())
