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
URLS_DIR = ROOT / "urls"
# Имя сессии этой машины (CL-XXXXXXXX), пишется warmup.py при первой инсталляции.
SESSION_NAME_FILE = ROOT / ".session_name"
# Флаг «сессия импортирована», пишется warmup.py после UI-импорта.
# Формат: «<uuid>\t<name>» (новый) либо просто «<name>» (старый — fallback).
SESSION_IMPORTED_FLAG = ROOT / ".session_imported"

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


def notify_ntfy(message: str, *, title: str, priority: str, tags: str) -> None:
    """Шлёт push через ntfy.sh. Ошибки глотаем — нотификация не должна валить запуск."""
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message[:4000].encode("utf-8"),
            method="POST",
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        log.info("ntfy отправлен (%s, %s)", title, priority)
    except Exception as e:
        log.warning("notify_ntfy failed: %s", e)


def collect_url_pool() -> list[str]:
    """Вариант A: глобальный пул — все URL из всех urls/*.txt в одну кучу,
    без архивации. Дубли убираем."""
    if not URLS_DIR.exists():
        raise FileNotFoundError(f"папка с URL не найдена: {URLS_DIR}")
    seen: set[str] = set()
    pool: list[str] = []
    for f in sorted(URLS_DIR.glob("*.txt")):
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            u = line.strip()
            if u and u not in seen:
                seen.add(u)
                pool.append(u)
    if not pool:
        raise RuntimeError(f"в {URLS_DIR}/*.txt нет ни одного URL")
    return pool


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


def pick_random_urls(pool: list[str], cfg: configparser.ConfigParser) -> list[str]:
    lo = cfg.getint("api", "urls_per_run_min", fallback=4)
    hi = cfg.getint("api", "urls_per_run_max", fallback=6)
    n = random.randint(lo, hi)
    n = min(n, len(pool))
    return random.sample(pool, n)


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
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="ignore")[:500]
            except Exception:
                pass
            raise ApiError(f"{method} {path} → HTTP {e.code}: {body_text}") from e
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
        самим процессом LS — отдельных Bearer-токенов в API нет (см. дока)."""
        self._request("POST", ENDPOINTS["signin"], {"email": email, "password": password})
        log.info("signin OK")

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

        pool = collect_url_pool()
        urls = pick_random_urls(pool, cfg)
        log.info("выбрано %d URL из пула %d", len(urls), len(pool))
        for u in urls:
            log.info("  url: %s", u)

        log.info("start_warmup view_depth=%d time_per_url=%d", view_depth, time_per_url)
        client.start_warmup(uuid, urls, view_depth, time_per_url)

        done = wait_for_warmup_done(client, uuid, cfg)
        if not done:
            # Прогрев мог реально кончиться, но детектор состояния не успел —
            # это не считаем фейлом, шлём как «состояние неизвестно».
            log.warning("прогрев не подтверждён поллингом, но запрос ушёл успешно")

        log.info("сценарий завершён успешно")
        notify_ntfy(
            f"machine: {host}\n"
            f"session: {session_name}\n"
            f"urls: {len(urls)} (view_depth={view_depth}, time_per_url={time_per_url})",
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
            f"machine: {host}\n"
            f"error: {exc}\n\n"
            f"tail:\n{tail}",
            title="warmup failed (api)",
            priority="high",
            tags="warning",
        )
        return 1


if __name__ == "__main__":
    sys.exit(run())
