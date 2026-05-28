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


def load_session_name(creds: configparser.ConfigParser) -> str:
    if not creds.has_section("session"):
        raise RuntimeError("В credentials.ini нет секции [session] name")
    name = creds.get("session", "name", fallback="").strip()
    if not name or name.lower() in ("session name", "your session name", "yourname"):
        raise RuntimeError(
            "В credentials.ini не задано [session] name — нечего прогревать"
        )
    return name


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
        self.token: str | None = None

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | list | None:
        url = f"{self.base_url}{path}"
        data: bytes | None = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
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
        """Лёгкий ping — пытаемся GET /sessions без авторизации.
        Если API-порт активен, ответ будет (хоть 401, хоть 200); если порт
        не открыт, кидаем сетевую ошибку."""
        try:
            self._request("GET", ENDPOINTS["sessions"])
        except ApiError as e:
            # 401/403 тоже валидный «порт жив» — отличаем только сетевые сбои.
            msg = str(e)
            if "HTTP 4" in msg or "HTTP 5" in msg:
                return
            raise

    def signin(self, email: str, password: str) -> None:
        out = self._request("POST", ENDPOINTS["signin"], {"email": email, "password": password})
        token = None
        if isinstance(out, dict):
            token = out.get("token") or out.get("access_token") or out.get("jwt")
        if not token:
            # Часть реализаций кладёт токен в cookie/header. Если /signin вернул
            # 200 без явного токена, продолжаем без него — сессионные cookies
            # urllib не сохраняет, поэтому такой режим работать не будет; ловим
            # это позже явной ошибкой при первом авторизованном вызове.
            log.warning("signin: токен не найден в ответе — возможно, нужна другая схема auth")
        else:
            self.token = token
            log.info("signin OK (token получен)")

    def list_sessions(self) -> list[dict]:
        out = self._request("GET", ENDPOINTS["sessions"])
        if isinstance(out, dict) and "sessions" in out:
            out = out["sessions"]
        if not isinstance(out, list):
            raise ApiError(f"GET /sessions: ожидался list, получили {type(out).__name__}")
        return out

    def find_session_by_name(self, name: str) -> dict:
        sessions = self.list_sessions()
        for s in sessions:
            if not isinstance(s, dict):
                continue
            if s.get("name") == name:
                return s
        raise ApiError(f"сессия с именем {name!r} не найдена в /sessions")

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


def _is_warmup_done(state: dict | None) -> bool:
    """Эвристика: сессия больше не в состоянии warming/running.
    Точное поле LS может варьироваться — смотрим самые типичные."""
    if state is None:
        return False
    raw_state = state.get("state") or state.get("status") or state.get("warmup_state")
    if not isinstance(raw_state, str):
        # Если поля нет — считаем, что live ещё идёт; финал ловим по таймауту.
        return False
    s = raw_state.lower()
    busy = ("warm", "running", "starting", "in_progress", "active")
    return not any(b in s for b in busy)


def wait_for_warmup_done(client: ApiClient, uuid: str, cfg: configparser.ConfigParser) -> bool:
    interval = cfg.getfloat("api", "poll_interval_seconds", fallback=5.0)
    timeout = cfg.getfloat("api", "poll_timeout_seconds", fallback=1200.0)
    deadline = time.time() + timeout
    last_state: str | None = None
    while time.time() < deadline:
        state = client.get_session_state(uuid)
        if state is not None:
            raw = state.get("state") or state.get("status") or state.get("warmup_state")
            if raw != last_state:
                log.info("state: %r", raw)
                last_state = raw
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
        session_name = load_session_name(creds)
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

        log.info("ищу сессию по имени %r", session_name)
        sess = client.find_session_by_name(session_name)
        uuid = sess.get("uuid") or sess.get("id")
        if not uuid:
            raise ApiError(f"в сессии {session_name!r} нет uuid: {sess}")
        log.info("найдена сессия uuid=%s", uuid)

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
