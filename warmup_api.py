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
import ctypes
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


def load_profile_names() -> list[str]:
    """Имена профилей этой машины (1..N, по строке в файле). Источники
    по приоритету:
      1. .session_name (создаёт warmup.py на первом запуске; N строк
         в мульти-профильном режиме, 1 строка — легаси одиночный)
      2. .session_imported (миграция со старого формата — там было только имя)
    Если ни одного — фейл: install не отработал."""
    if SESSION_NAME_FILE.exists():
        names = [
            ln.strip()
            for ln in SESSION_NAME_FILE.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        if names:
            return names
    if SESSION_IMPORTED_FLAG.exists():
        names = []
        for ln in SESSION_IMPORTED_FLAG.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            # старый формат: только имя, либо «<uuid>\t<name>»
            _, _, name = ln.partition("\t")
            names.append((name or ln).strip())
        if names:
            return names
    raise RuntimeError(
        f"не найден ни .session_name, ни .session_imported — "
        f"запусти install/run.bat хотя бы раз, чтобы инициализировать сессию."
    )


def load_session_name() -> str:
    """Легаси-обёртка: все профили одной строкой (для header'ов/логов)."""
    return ", ".join(load_profile_names())


# Эмодзи в Title по типу события (по первому тегу). Telegram-бридж НЕ
# подставляет эмодзи из ntfy-тегов, поэтому кладём их прямо в заголовок.
_TAG_EMOJI = {"white_check_mark": "✅", "tada": "🎉", "warning": "⚠️",
              "hourglass_flowing_sand": "⏳", "gear": "⚙️", "wrench": "🔧"}
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
        # Retry на transient SSL/timeout hiccups. ntfy.sh на здоровом VPS
        # отвечает <1с, но изредка TLS handshake забуксует (был реальный
        # случай: 03:39:41 ntfy failed handshake timeout) — без retry'я
        # уведомление теряется навсегда. 3 попытки с timeout=30с и
        # backoff'ом 2с/5с покрывают любой разумный transient hiccup,
        # в норме (handshake OK с первой попытки) занимают те же <1с.
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                log.info("ntfy отправлен (%s, %s)", disp_title, priority)
                return
            except Exception as e:
                last_err = e
                if attempt < 2:
                    backoff = 2 if attempt == 0 else 5
                    log.info("notify_ntfy попытка %d/3 не прошла (%s) — backoff %dс",
                             attempt + 1, e, backoff)
                    time.sleep(backoff)
        log.warning("notify_ntfy failed после 3 попыток: %s", last_err)
    except Exception as e:
        log.warning("notify_ntfy failed: %s", e)


# --- Console banners for the detached warmup_api.py window ------------------
# Когда warmup.py спавнит warmup_api.py с DETACHED_PROCESS + stdout=DEVNULL,
# Windows ИНОГДА открывает консольное окно автоматически (это видно как
# чёрный квадрат с заголовком python.exe), а иногда — нет. Поведение
# капризное, зависит от версии Windows Server и timing'а. Раньше окно
# зияло пустотой даже когда открывалось.
#
# Теперь:
#   1. _ensure_console() — явный AllocConsole если консоли нет вообще.
#      На уже-аллоцированной (cycle 2+ через Task Scheduler cmd) — no-op.
#   2. _to_console() — пишет в CONOUT$ напрямую через Win32 CreateFileW
#      + WriteConsoleW, минуя Python's open() который иногда подвисал
#      из-за DEVNULL-state у sys.stdout.
#
# sys.stdout не трогаем — redirection в run_api.log для cycle 2+
# (Task Scheduler) сохраняется.

def _ensure_console() -> None:
    """Гарантирует наличие attached console на Windows.
    При DETACHED_PROCESS Windows может не создать консоль автоматически —
    AllocConsole форсит создание. No-op если консоль уже есть (cycle 2+
    через cmd.exe). Silent ignore любых ошибок."""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        if not kernel32.GetConsoleWindow():
            kernel32.AllocConsole()
    except Exception:
        pass


def _to_console(msg: str) -> None:
    """Write a line directly to the attached console via Win32 CreateFileW.
    Bypasses Python's open() to avoid quirks with DEVNULL-state stdout.
    Silent no-op if no console attached."""
    if sys.platform != "win32":
        print(msg, flush=True)
        return
    try:
        kernel32 = ctypes.windll.kernel32
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        INVALID_HANDLE = -1
        h = kernel32.CreateFileW(
            "CONOUT$", GENERIC_WRITE, FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
        if h == INVALID_HANDLE or h == 0:
            return
        try:
            data = (msg + "\r\n").encode("utf-16-le")
            n = ctypes.c_ulong(0)
            # WriteConsoleW expects count in chars, not bytes
            kernel32.WriteConsoleW(h, data, len(msg) + 2, ctypes.byref(n), None)
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        pass


def _print_running_banner(session_label: str, machine_id: str) -> None:
    bar = "=" * 62
    for line in (
        "",
        bar,
        "",
        "   #####  WARMUP IS RUNNING  #####",
        "",
        bar,
        "",
        f"      profiles  :  {session_label}",
        f"      machine   :  {machine_id}",
        "",
        bar,
        "",
        "",
        "      *********************************************",
        "      *                                           *",
        "      *      RDP CAN BE DISCONNECTED NOW          *",
        "      *      Warmup continues in background       *",
        "      *                                           *",
        "      *********************************************",
        "",
        "",
        "  Telegram push will arrive when warmup is complete (~3-4h).",
        "",
        bar,
        "",
    ):
        _to_console(line)


def _print_complete_banner(
    session_label: str,
    machine_id: str,
    total_time_en: str,
    urls_done: int,
    target: int,
    cookies_str: str,
) -> None:
    bar = "=" * 62
    for line in (
        "",
        bar,
        "",
        "   #####  WARMUP COMPLETE  #####",
        "",
        bar,
        "",
        f"      profiles    :  {session_label}",
        f"      machine     :  {machine_id}",
        f"      total time  :  {total_time_en}",
        f"      URLs        :  {urls_done}/{target}",
        f"      cookies     :  {cookies_str}",
        "",
        bar,
        "",
        "",
        "      *********************************************",
        "      *                                           *",
        "      *      ALL DONE -- YOU CAN CLOSE             *",
        "      *      ALL WINDOWS NOW                       *",
        "      *                                           *",
        "      *********************************************",
        "",
        "",
        f"  Cookies saved at: {COOKIES_EXPORT_DIR}",
        "  See Telegram for full report.",
        "",
        bar,
        "",
    ):
        _to_console(line)


def _fmt_total_elapsed_en(started_at: int | None) -> str:
    if started_at is None:
        return "n/a"
    secs = max(0, int(time.time() - started_at))
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h {m}min"
    return f"{m} min"


# ---------------------------------------------------------------------------


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


# --- LS auto-recovery (E-16: Electron renderer crash mid-cycle) ----------
#
# Когда LS API на 36555 умирает посреди цикла (Chromium renderer crash,
# OOM, etc), warmup_api пытается start_warmup → WinError 10061 connection
# refused. Раньше эта ошибка попадала в общий ApiError и улетала в
# 9-step pyramid — 27 минут впустую (pyramid ждёт что 409 lock рассосётся,
# но тут не lock, а мёртвая LS), потом ⚠️ failed (api).
#
# Теперь детектим тип ошибки. Если connection refused — НЕ pyramid,
# а немедленный kill+launch LS через ls_launch.bat (тот же скрипт, что
# использует watchdog и Startup folder). Один LS recovery занимает
# 30-90 сек, после чего цикл продолжается с того же места.

_LS_RECOVERY_MAX_PER_CHUNK = 2  # больше двух раз на чанк не пытаемся,
                                # значит что-то системное, эскалация

# Ретрай поиска сессии в GET /sessions. LS-cloud отдаёт каталог НЕ атомарно:
# сессия может на секунды-минуты пропасть из списка, оставаясь живой (25.07.2026,
# 5-профильная машина .176: GUI показывает CL-11045013 в статусе warmup, а
# /sessions её не возвращает → цикл падал ⚠️ high, хотя через ~час всё шло само).
#
# Это был ЕДИНСТВЕННЫЙ шаг цикла без ретрая: ping (5 попыток), 409 (9-step
# pyramid), connection refused (Layer 4) — обвешаны, а lookup падал с первой
# промашки. Плюс цепочка сократила паузу между циклами с ~8 мин до 90с, так
# что запаса на «LS ещё дописывает состояние» не осталось.
#
# Расписание — в духе _POST_IMPORT_POLL_SCHEDULE из warmup.py (тот же класс
# проблемы: ждём пока cloud sync догонит). 5 попыток, ~220с суммарно —
# укладывается в бюджет цикла, 45-мин tick не задевает.
_SESSION_LOOKUP_BACKOFFS = [10, 30, 60, 120]


def _is_ls_api_dead(err: Exception) -> bool:
    """True если ошибка свидетельствует о смерти/зависании LS API локально
    (НЕ серверный 409 conflict, НЕ HTTP-валидационная ошибка от LS).

    Расширено в коммите по падениям 172.86.110.195 и 172.86.88.228:
    раньше ловили только connection refused (LS не принимает соединения),
    но есть ещё 3 сценария "LS мёртвая" с другими признаками:

    1) "10061" / "connection refused" / "actively refused"
       → LS API не принимает соединения. Electron renderer убит/crashed,
          порт 36555 не слушает. Классический случай.

    2) "timed out" / "timeout"
       → LS API НЕ закрыла порт, но НЕ отвечает за http_timeout_seconds=30с.
          Renderer hung, event loop заклинило. Restart нужен.

    3) "10053" / "aborted"
       → LS убила соединение посреди запроса. Electron crashed mid-render,
          сокет закрылся ungracefully. Restart нужен.

    4) "10054" / "reset by peer" / "forcibly closed"
       → LS reset соединение. Internal state corrupted, network stack
          в LS Electron'е сломался. Restart нужен.

    Все 4 сценария лечатся одинаково — kill+launch LS через ls_launch.bat.
    Поэтому ОДНА функция-детектор и ОДНА recovery-стратегия.

    НЕ срабатывает на:
    - HTTP 409 (Session is used) → pyramid retry, не recovery
    - HTTP 400/404/500 (LS ответила и сказала "no") → НЕ recovery
    """
    msg = str(err).lower()
    # Network-level "LS dead" patterns
    if "10061" in msg or "connection refused" in msg:
        return True
    if "10053" in msg or "aborted" in msg:
        return True
    if "10054" in msg or "reset by peer" in msg or "forcibly closed" in msg:
        return True
    if "timed out" in msg or "timeout" in msg:
        # Ловим только сетевой timeout. HTTP-error от LS "timeout" в JSON-body
        # не должны провоцировать recovery, но они приходят как "HTTP 400/500
        # body содержит timeout" — наш _request формирует "HTTP {code}: {body}"
        # для HTTPError, и "сеть/таймаут: {e}" для всего остального. Проверяем
        # что сообщение не начинается с "http " — это HTTP-error от LS.
        if not msg.startswith("post ") or "→ сеть/таймаут:" in msg or "urlopen error" in msg:
            return True
    # "actively refused" pattern (Win11 verbose form of connection refused)
    if "refused" in msg and "actively" in msg:
        return True
    return False


# Обратная совместимость для existing usages — старое имя ссылается на новое
_is_connection_refused = _is_ls_api_dead


def _ls_kill_and_relaunch(max_wait_seconds: float = 90.0) -> bool:
    """Kill all LS + Popen ls_launch.bat + ping API до RESTART_GRACE.
    Возвращает True если API живая после relaunch.

    Использует ls_launch.bat (тот же что watchdog и Startup folder) —
    единый путь для launching LS с правильной cwd для evo:// protocol."""
    import subprocess as _sp
    import urllib.request as _ureq

    log.warning("LS recovery: taskkill /F LS")
    try:
        _sp.run(
            ["taskkill", "/F", "/IM", "Linken Sphere 2.exe", "/T"],
            capture_output=True, text=True, timeout=15, errors="replace",
        )
    except Exception as e:
        log.warning("LS recovery: taskkill failed: %s", e)

    bat = ROOT / "ls_launch.bat"
    if not bat.exists():
        log.error("LS recovery: %s не найден — recovery невозможен", bat)
        return False

    log.warning("LS recovery: %s", bat.name)
    try:
        _sp.Popen(
            ["cmd", "/c", str(bat)],
            cwd=str(ROOT),
            creationflags=_sp.DETACHED_PROCESS | _sp.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as e:
        log.error("LS recovery: ls_launch.bat не запустился: %s", e)
        return False

    # Ждём пока API оживёт. Polling каждые 5 сек до max_wait_seconds.
    deadline = time.time() + max_wait_seconds
    ping_url = "http://127.0.0.1:36555/sessions"
    while time.time() < deadline:
        time.sleep(5)
        try:
            with _ureq.urlopen(ping_url, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    elapsed = max_wait_seconds - (deadline - time.time())
                    log.info("LS recovery: API ОЖИЛА за %.0fс", elapsed)
                    return True
        except Exception:
            continue

    log.error("LS recovery: API не ожила за %.0fс", max_wait_seconds)
    return False


def _notify_ls_recovered(chunk_idx: int, total_chunks: int, recovery_n: int) -> None:
    """Шлёт 🔧 push когда LS auto-recovery прошёл успешно."""
    try:
        notify_ntfy(
            _ntfy_header()
            + f"LS API легла на чанке {chunk_idx}/{total_chunks}\n"
            + f"перезапустил LS, продолжаю прогрев\n"
            + f"recovery #{recovery_n}",
            title="LS auto-recovered",
            priority="low",
            tags="wrench",
        )
    except Exception as e:
        log.warning("notify ls-recovered upalo: %s", e)


def _notify_ls_recovery_failed(chunk_idx: int, total_chunks: int) -> None:
    """Шлёт ⚠️ push если recovery не сработал — API не ожила за timeout."""
    try:
        notify_ntfy(
            _ntfy_header()
            + f"LS API легла на чанке {chunk_idx}/{total_chunks}\n"
            + f"попытка перезапуска не помогла — API так и не ожила",
            title="LS recovery failed",
            priority="high",
            tags="warning",
        )
    except Exception as e:
        log.warning("notify ls-recovery-failed upalo: %s", e)


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
    pool: list[str], cfg: configparser.ConfigParser,
    max_n: int | None = None,
) -> tuple[Path, list[str]]:
    """Сэмплит ~100 случайных URL из пула (urls_per_run_min..max), пишет
    файлом в urls_generated/run_<ts>.txt для audit. Возвращает (файл, urls).

    max_n — если задан и меньше случайно выбранного n, ограничиваем выборку
    этим числом. Нужно для финального цикла, когда до target осталось
    меньше полного цикла — иначе перебираем на +50-90 URL сверх задумки.
    Нижняя граница — chunk_size (7 URL = 1 чанк), чтобы как минимум один
    проход всё-таки сделать (random.sample требует n>=1, и downstream chunk-
    loop ожидает непустой список)."""
    n_min = cfg.getint("api", "urls_per_run_min", fallback=95)
    n_max = cfg.getint("api", "urls_per_run_max", fallback=105)
    n = min(random.randint(n_min, n_max), len(pool))
    if max_n is not None and max_n < n:
        chunk_size = cfg.getint("api", "urls_per_chunk_max", fallback=7)
        n = max(chunk_size, max_n)
        log.info("остаток до target = %d URL < полный цикл — clip выборки до %d URL",
                 max_n, n)
    urls = random.sample(pool, n)

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "urls_generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"run_{ts}.txt"
    out_file.write_text("\n".join(urls) + "\n", encoding="utf-8")
    log.info("выбрано %d URL → %s", n, out_file)
    return out_file, urls


# --- Per-profile state (multi-profile ветка) --------------------------------
# При ОДНОМ профиле используются легаси-пути (.warmup_target /
# .warmup_count) — поведение бит-в-бит совпадает со старой одиночной
# схемой, state-файлы уже работающих машин читаются без миграции.
# При N>1 профилях — суффиксованные файлы .warmup_target.<имя> /
# .warmup_count.<имя>: атомарные, независимые, в стиле остальных флагов.

def _target_file_for(name: str, single: bool) -> Path:
    return WARMUP_TARGET_FILE if single else ROOT / f".warmup_target.{name}"


def _count_file_for(name: str, single: bool) -> Path:
    return WARMUP_COUNT_FILE if single else ROOT / f".warmup_count.{name}"


def _exported_flag_for(name: str) -> Path:
    """Флаг «cookies этого профиля успешно экспортированы». Пишется ТОЛЬКО
    после полностью успешного export'а (жёсткая идемпотентность, как
    .api_activated) — если export упал, следующий tick попробует ещё раз."""
    return ROOT / f".cookies_exported.{name}"


def load_or_create_target(cfg: configparser.ConfigParser, name: str,
                          single: bool) -> int:
    """Целевой объём прогрева ОДНОГО профиля. На первом запуске генерится
    random.randint(min, max) независимо для каждого профиля и пишется в
    его target-файл. Дальше всегда читается оттуда — менять задним числом
    нельзя, иначе counter становится неконсистентен."""
    tf = _target_file_for(name, single)
    if tf.exists():
        try:
            return int(tf.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pass
    lo = cfg.getint("api", "urls_total_target_min", fallback=300)
    hi = cfg.getint("api", "urls_total_target_max", fallback=500)
    target = random.randint(lo, hi)
    tf.write_text(str(target), encoding="utf-8")
    if not WARMUP_STARTED_AT_FILE.exists():
        WARMUP_STARTED_AT_FILE.write_text(str(int(time.time())), encoding="utf-8")
    log.info("целевой объём прогрева %s: %d URL (зафиксирован в %s)",
             name, target, tf.name)
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


def load_warmed_count(name: str, single: bool) -> int:
    cf = _count_file_for(name, single)
    if not cf.exists():
        return 0
    try:
        return int(cf.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return 0


def add_warmed_count(n: int, name: str, single: bool) -> int:
    new_total = load_warmed_count(name, single) + n
    _count_file_for(name, single).write_text(str(new_total), encoding="utf-8")
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
            r = subprocess.run(cmd, capture_output=True, timeout=20, text=True, errors="replace")
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


def load_session_imported_flag() -> dict[str, str | None]:
    """Читает .session_imported. Возвращает {name: uuid|None} по всем
    строкам файла. Поддерживает форматы:
      - «<uuid>\\t<name>» — с uuid (записывается после lookup)
      - «<name>» — только имя (warmup.py пишет так сразу после IMPORT)
    Мульти-профиль: по строке на профиль. Если файла нет — пустой dict:
    сессии ещё не импортили."""
    result: dict[str, str | None] = {}
    if not SESSION_IMPORTED_FLAG.exists():
        return result
    for ln in SESSION_IMPORTED_FLAG.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if "\t" in ln:
            uuid, _, name = ln.partition("\t")
            if name.strip():
                result[name.strip()] = uuid.strip() or None
        else:
            result[ln] = None
    return result


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


def wait_for_warmup_done(client: ApiClient, uuid: str, cfg: configparser.ConfigParser,
                          chunk_idx: int = 0, total_chunks: int = 0) -> bool:
    interval = cfg.getfloat("api", "poll_interval_seconds", fallback=5.0)
    timeout = cfg.getfloat("api", "poll_timeout_seconds", fallback=1200.0)
    deadline = time.time() + timeout
    last_status: str | None = None
    # Дать LS пару секунд перейти в "warmup" — иначе можем словить старый
    # status и решить, что прогрев уже закончился.
    time.sleep(min(interval, 3.0))
    # E-16 LS recovery counter — max _LS_RECOVERY_MAX_PER_CHUNK на одно ожидание
    ls_recoveries = 0
    while time.time() < deadline:
        try:
            state = client.get_session_state(uuid)
        except ApiError as e:
            # Polling может словить connection refused если LS легла
            # во время прогрева чанка. Попытка recovery.
            if _is_connection_refused(e) and ls_recoveries < _LS_RECOVERY_MAX_PER_CHUNK:
                ls_recoveries += 1
                log.warning(
                    "polling: API connection refused — LS мёртвая, recovery #%d/%d",
                    ls_recoveries, _LS_RECOVERY_MAX_PER_CHUNK,
                )
                if _ls_kill_and_relaunch():
                    _notify_ls_recovered(chunk_idx, total_chunks, ls_recoveries)
                    # API ожила — продолжаем polling. Status узнаем на след. итер.
                    last_status = None  # форс log следующего status
                    continue
                else:
                    _notify_ls_recovery_failed(chunk_idx, total_chunks)
                    raise  # признаём что recovery не помог, цикл прерывается
            # Не connection refused (другой ApiError) ИЛИ исчерпали recoveries
            raise
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


# --- Single-instance guard + запуск по готовности ---------------------------
#
# Циклы больше не ждут 45-мин Task Scheduler tick: после успешного цикла
# warmup_api сразу (после короткого rest'а) гонит следующий, пока все
# профили не добьют свои target'ы. Выигрыш двойной:
#   1. Убирает ~8-мин простой между циклами (37-мин цикл / 45-мин сетка).
#   2. Убирает 90-мин эффективный интервал: раньше если цикл переваливал
#      за 45 мин (409 pyramid, медленная LS), следующий tick пропускался
#      IgnoreNew'ом и прогрев вставал до следующей сетки.
#
# Task Scheduler (Time 45 мин + AtStartup) ОСТАЁТСЯ как каскад-fallback:
# при любой ошибке цепочка выходит (как раньше), и следующий tick /
# watchdog подхватывают. Если цепочка жива — тики становятся no-op'ами.
#
# Защита от параллельных инстансов — named mutex. Сценарии, где без него
# было бы два warmup_api на одной LS (self-409-шторм):
#   - цепочка запущена detached из warmup.py (после install), а через
#     45 мин стреляет Task Scheduler tick → run_api.bat → второй экземпляр.
#     MultipleInstances=IgnoreNew тут НЕ спасает: detached-процесс не
#     является инстансом задачи.
#   - watchdog stuck-detection force-trigger'ит задачу пока цепочка жива.
# Mutex авто-освобождается при смерти процесса-владельца (в отличие от
# lock-файла нет проблемы stale state после kill/BSOD/hard reset).
#
# Watchdog совместим из коробки: при main task Running он не вмешивается
# (early-exit), а no-op тики обновляют LastRunTime → stuck-detection
# не сработает и на detached-цепочке.

_MUTEX_NAME = "Local\\LinkenSphereWarmupApiSingleton"
_mutex_handle = None  # держим ссылку до конца жизни процесса

# Отдых между циклами цепочки: LS дофлашивает cookies на диск, Chromium
# делает GC. 90с — компромисс: заметно меньше старого 8-мин gap'а, но LS
# не молотит непрерывно. Плюс анти-collision jitter 0-120с в начале
# каждого цикла остаётся — суммарная пауза между циклами 1.5-3.5 мин.
_CHAIN_REST_SECONDS = 90
# Предохранитель от бесконечной цепочки при любом непредвиденном багe
# счётчиков: 750 URL worst-case (5 профилей × 150) / ~95 URL за цикл ≈ 8
# циклов; 30 — с запасом x3.
_CHAIN_MAX_CYCLES = 30


def _acquire_single_instance() -> bool:
    """True = мы единственный warmup_api на машине (mutex захвачен и
    держится до смерти процесса). False = другой экземпляр уже работает.
    На не-Windows и при любой ошибке CreateMutex — fail-open True
    (лучше маловероятный parallel, чем полный отказ прогрева)."""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        if not handle:
            return True
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception:
        return True


def run() -> int:
    """Entry point: цепочка циклов по готовности под single-instance guard'ом.
    Возвращает 0 при успехе/no-op, 1 при ошибке (код последнего цикла)."""
    if not _acquire_single_instance():
        # Другой warmup_api уже крутит цепочку. Это штатно: 45-мин tick /
        # force-trigger при живой цепочке. Тихий no-op — без push'ей,
        # без баннера (не затираем консоль работающего экземпляра).
        log.info("другой warmup_api уже работает (mutex busy) — no-op, выход")
        return 0
    for cycle_i in range(1, _CHAIN_MAX_CYCLES + 1):
        rc = _run_cycle()
        if rc == 2:
            # Все профили добили target'ы — задача disabled, 🎉 отправлен.
            return 0
        if rc != 0:
            # Ошибка: сессия освобождена, ⚠️ push ушёл (внутри _run_cycle).
            # Выходим — 45-мин каскад / watchdog подхватят как раньше.
            return rc
        log.info("цепочка: цикл %d завершён, есть незавершённые профили — "
                 "rest %dс и продолжаем", cycle_i, _CHAIN_REST_SECONDS)
        time.sleep(_CHAIN_REST_SECONDS)
    log.warning("цепочка: предохранитель %d циклов — выход, каскад продолжит",
                _CHAIN_MAX_CYCLES)
    return 0


def _run_cycle() -> int:
    """Один полный цикл прогрева (~37 мин): ping → signin → выбор профиля →
    чанки → счётчики → push'и. Коды возврата:
      0 — цикл успешен, остались незавершённые профили (цепочка продолжает)
      2 — ВСЕ профили достигли target'ов (цепочка останавливается)
      1 — ошибка (⚠️ push отправлен, цепочка останавливается)"""
    log.info("=" * 60)
    log.info("Linken Sphere warm-up via API: старт")

    # Гарантируем что консольное окно attached. При DETACHED_PROCESS
    # Windows капризничает — иногда создаёт console window сам, иногда
    # нет. AllocConsole форсит создание если ещё нет. На уже-аллоцированной
    # консоли (cycle 2+ через Task Scheduler cmd) — no-op.
    _ensure_console()

    try:
        cfg = load_config()
        creds = load_credentials()
        profile_names = load_profile_names()
        single = len(profile_names) == 1
        session_label = ", ".join(profile_names)
        email = creds.get("account", "email").strip()
        password = creds.get("account", "password")

        # Сразу при старте пишем большой видимый баннер в окно консоли,
        # чтобы оператор знал что процесс жив и RDP можно отключать.
        _print_running_banner(session_label, _machine_id())

        # Per-profile state: target каждого профиля независимый
        # (random 150-250 из config), counter — свой файл на профиль.
        # При одном профиле — легаси .warmup_target/.warmup_count.
        targets = {n: load_or_create_target(cfg, n, single) for n in profile_names}
        counts = {n: load_warmed_count(n, single) for n in profile_names}

        def _progress_lines() -> str:
            """Строка прогресса всех профилей для push'ей: имя, count/target,
            ✔ у завершённых."""
            out = []
            for n in profile_names:
                mark = " ✔" if counts[n] >= targets[n] else ""
                out.append(f"{n}: {counts[n]}/{targets[n]} URL{mark}")
            return "\n".join(out)

        total_current = sum(counts.values())
        total_target = sum(targets.values())

        # One-shot гейт: ВСЕ профили прогреты? — JOB DONE, на выход.
        # Уведомление шлём РОВНО один раз (флаг .notified_done). Если scheduler
        # всё ещё стреляет (significa disable не сработал на прошлом запуске)
        # — пытаемся ещё раз, но без спама в ntfy.
        unfinished = [n for n in profile_names if counts[n] < targets[n]]
        if not unfinished:
            if already_notified_done():
                log.info("ALL JOBS DONE: %d/%d, уже уведомлял — тихий ретрай disable",
                         total_current, total_target)
                disable_scheduled_task()
                _print_complete_banner(
                    session_label, _machine_id(),
                    _fmt_total_elapsed_en(load_started_at()),
                    total_current, total_target, "see telegram",
                )
                return 2
            log.info("ALL JOBS DONE: %d/%d URL прогрето — disable + notify",
                     total_current, total_target)
            disabled = disable_scheduled_task()
            tail = ("scheduled task disabled. All jobs done 🎉"
                    if disabled else
                    "could NOT disable schedule — run manually:\n"
                    f"  schtasks /change /tn {TASK_NAME} /disable")
            notify_ntfy(
                _ntfy_header() +
                _progress_lines() + "\n" +
                f"total: {total_current}/{total_target} URL warmed\n" + tail,
                title="warmup all done",
                priority="high",
                tags="tada",
            )
            mark_notified_done()
            _print_complete_banner(
                session_label, _machine_id(),
                _fmt_total_elapsed_en(load_started_at()),
                total_current, total_target, "see telegram",
            )
            return 2

        # Выбор активного профиля на этот tick: наименее прогретый
        # (по доле от target) из незавершённых. Ротация получается сама:
        # тик прогревает отстающего, на следующем тике отстаёт другой.
        # Прогрев СТРОГО последовательный — один профиль за tick: LS API
        # держит глобальный лок на аккаунт (см. 9-step pyramid), два
        # параллельных warmup'а с одной машины устроили бы self-409-шторм.
        active = min(unfinished, key=lambda n: counts[n] / max(1, targets[n]))
        session_name = active  # для error-handler'а и legacy-хинтов
        target = targets[active]
        current = counts[active]
        if not single:
            log.info("активный профиль этого tick'а: %s (%d/%d незавершённых)",
                     active, len(unfinished), len(profile_names))
        log.info("прогресс %s: %d/%d URL (осталось ~%d)",
                 active, current, target, max(0, target - current))

        base_url = cfg.get("api", "base_url", fallback="http://127.0.0.1:36555")
        http_timeout = cfg.getfloat("api", "http_timeout_seconds", fallback=15.0)
        view_depth = cfg.getint("warmup", "viewing_depth", fallback=3)
        time_per_url = cfg.getint("warmup", "time_per_url", fallback=7)

        client = ApiClient(base_url, http_timeout)

        # Initial ping с 5 попытками + backoff 5с/10с/15с/20с (~50с total).
        # ЗАЧЕМ: warmup_api перезапускается каждые 45 минут через Task
        # Scheduler. Первый ping LS API может попасть в момент когда LS
        # только что закрыла предыдущий чанк и делает internal cleanup
        # (Chromium GC, cloud sync с ls.app, ребалансировка renderer'ов).
        # API thread занят на 2-30 секунд → connection refused / timeout.
        # Один shot без retry давал false ⚠️ push: оператор RDP'нется,
        # LS жива и работает, что за паника. Наблюдалось .27 02:45 UTC.
        #
        # 5 попыток × ~50с backoff (плюс время самого ping'а) покрывают
        # любой разумный transient hiccup, включая тяжёлый GC и долгий
        # cloud sync. Если за минуту LS не отозвалась совсем — это
        # действительно LS down (Electron crash, logoff, OOM): тогда
        # честный ⚠️ push, дальше Layer 5 watchdog подхватит за ~15 мин.
        #
        # Тайминги проверены — не конфликтуют ни с чем:
        # - 45-мин Task Scheduler tick: +50с = ничтожно
        # - warmup_api cycle: 26-37 мин + 50с = в норме укладывается
        # - LS watchdog ping (каждые 5 мин): независимый процесс
        # - http_timeout=30с * 5 worst-case = ~3 мин ещё в бюджете tick'а
        log.info("ping API %s …", base_url)
        ping_backoffs = [5, 10, 15, 20]  # между attempt 1→2, 2→3, 3→4, 4→5
        max_attempts = len(ping_backoffs) + 1  # = 5
        last_ping_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                client.ping()
                if attempt > 1:
                    log.info("ping API OK с %d-й попытки (transient hiccup переждали)",
                             attempt)
                last_ping_err = None
                break
            except Exception as e:
                last_ping_err = e
                if attempt < max_attempts:
                    backoff = ping_backoffs[attempt - 1]
                    log.info("ping API попытка %d/%d не прошла (%s) — backoff %dс",
                             attempt, max_attempts, e, backoff)
                    time.sleep(backoff)
        if last_ping_err is not None:
            raise last_ping_err

        log.info("signin как %s", email)
        client.signin(email, password)

        imported_map = load_session_imported_flag()

        def _resolve_uuid(profile: str, *, retry: bool = True) -> str:
            """uuid профиля: сперва из .session_imported (если там формат
            с uuid), иначе поиск по имени в /sessions. Коллизия имён на
            общем LS-аккаунте → ApiError сразу (safety-net как раньше).

            Ретрай по _SESSION_LOOKUP_BACKOFFS: LS-cloud отдаёт каталог
            не атомарно, сессия может временно пропасть из GET /sessions.
            retry=False — для best-effort вызовов (догон export'ов), чтобы
            не тратить минуты на профиль, которым займётся следующий цикл."""
            backoffs = _SESSION_LOOKUP_BACKOFFS if retry else []
            last_err: Exception | None = None
            for attempt in range(len(backoffs) + 1):
                try:
                    stored = imported_map.get(profile)
                    if stored:
                        log.info("uuid профиля %s из .session_imported: %s", profile, stored)
                        client.find_session_by_uuid(stored)  # валидация что жива
                        return stored
                    log.info("ищу сессию по имени %r (uuid в .session_imported не записан)",
                             profile)
                    s = client.find_session_by_name(profile)
                    u = s.get("uuid")
                    if not u:
                        raise ApiError(f"в сессии {profile!r} нет uuid: {s}")
                    if attempt:
                        log.info("сессия %s нашлась с %d-й попытки (cloud sync доехал)",
                                 profile, attempt + 1)
                    return u
                except ApiError as e:
                    # Коллизия имён — ретрай не поможет, нужен оператор
                    # (удалить дубль в LS). Падаем сразу, как раньше.
                    if "коллизия" in str(e):
                        raise
                    last_err = e
                    if attempt < len(backoffs):
                        b = backoffs[attempt]
                        log.warning(
                            "сессия %s не видна в /sessions (%s) — LS-cloud ещё "
                            "не синхронизировал? backoff %dс, попытка %d/%d",
                            profile, e, b, attempt + 2, len(backoffs) + 1,
                        )
                        time.sleep(b)
            raise last_err  # type: ignore[misc]

        # Догоняем незакрытые export'ы: профиль мог достичь target'а на
        # прошлом tick'е, а export cookies упасть (409 от LS / timeout).
        # Флаг .cookies_exported.<имя> пишется только после успеха —
        # здесь тихо ретраим все завершённые-но-не-экспортнутые.
        for done_name in profile_names:
            if counts[done_name] < targets[done_name]:
                continue
            if _exported_flag_for(done_name).exists():
                continue
            log.info("профиль %s завершён, но cookies не экспортированы — ретрай", done_name)
            try:
                # retry=False: это best-effort догон, ждать здесь минуты
                # незачем — следующий цикл попробует снова.
                _u = _resolve_uuid(done_name, retry=False)
                _n_cookies = count_session_cookies(client, _u, pre_wait_seconds=10)
                if _n_cookies is not None:
                    _exported_flag_for(done_name).write_text("1", encoding="utf-8")
                    notify_ntfy(
                        _ntfy_header() +
                        f"profile {done_name}: cookies экспортированы со второй "
                        f"попытки ({_n_cookies} шт) → {COOKIES_EXPORT_DIR}",
                        title=f"cookies exported — {done_name}",
                        priority="low",
                        tags="wrench",
                    )
            except Exception as e:
                log.warning("ретрай export'а %s не прошёл: %s — следующий tick попробует",
                            done_name, e)

        uuid = _resolve_uuid(active)
        log.info("используем uuid=%s (профиль %s)", uuid, active)

        pool = load_url_pool(cfg)
        # remaining > 0 гарантировано: выше был return 0 при current >= target
        remaining = target - current
        run_file, urls = materialize_run_urls(pool, cfg, max_n=remaining)
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
        # CREATE_NO_WINDOW: python.exe для _firewall_watcher запускается БЕЗ
        # видимого console window. Раньше без флага мог мерцать чёрный квадрат
        # на каждом polling (15 сек) — мешало pyautogui clicks в UI flow.
        _no_window = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        fw_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "_firewall_watcher.py"), "15"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_no_window,
        )
        # Анти-collision jitter: на крупных парках (5+ VPS на одном
        # LS-аккаунте) машины часто синхронизируются по времени —
        # одновременно завершают unattended install, затем каждые 45 мин
        # одновременно тикает scheduler. Без jitter все делают первый
        # start_warmup одновременно → один проходит, остальные ловят 409.
        # Случайная пауза 0-120 сек ПЕРЕД первым чанком размывает старты
        # по 2-мин окну. Каждый цикл получает свой случайный сдвиг —
        # синхронизация не накапливается даже если все машины стартанули
        # одной командой unattended-режима. Цикл удлиняется в среднем на
        # 60 сек (37 → 38 мин), всё ещё в 45-мин scheduler-интервале.
        # Совместно с 7-step retry pyramid держит систему молчаливой
        # для 10+ машин на одном аккаунте.
        jitter = random.randint(0, 120)
        if jitter:
            log.info("анти-collision jitter: sleep %dс перед первым start_warmup", jitter)
            time.sleep(jitter)

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
                # Расширенный 9-step pyramid для multi-VPS контентов: когда
                # несколько VPS на одном LS-аккаунте (наблюдалось до 8 параллельных),
                # чанк другой машины держит глобальный API-лок до своего завершения
                # (~2 мин на один чанк, иногда больше). Базовый 7-step не всегда
                # дотягивает — добавили ступени 8 и 9 с большими ожиданиями (6 и
                # 8 мин). Worst case на один чанк: ~17 мин ожидания (30+60+120+
                # 240+360+480 sleeps + nuclear actions).
                # Task Scheduler interval 45 мин + IgnoreNew: если цикл из-за
                # удлинённого pyramid'а превысит 45 мин, следующий tick просто
                # пропустится (без collision/crash), эффективный интервал
                # станет 90 мин. Trade-off: медленнее, но стабильнее на multi-VPS
                # аккаунте — меньше ⏳ cycle paused alerts.
                _last_err: Exception | None = None
                ls_recoveries = 0  # max _LS_RECOVERY_MAX_PER_CHUNK на чанк
                for attempt in range(9):
                    try:
                        client.start_warmup(uuid, chunk, view_depth, time_per_url)
                        _last_err = None
                        break
                    except ApiError as e:
                        msg = str(e)
                        _last_err = e

                        # E-16 LS recovery: connection refused = LS локально мёртвая
                        # (Electron renderer crash, OOM etc), а НЕ серверный 409.
                        # Pyramid тут не поможет — нужен kill+launch LS.
                        if _is_connection_refused(e) and ls_recoveries < _LS_RECOVERY_MAX_PER_CHUNK:
                            ls_recoveries += 1
                            log.warning(
                                "чанк %d: API connection refused — LS мёртвая, "
                                "запускаю recovery #%d/%d",
                                i, ls_recoveries, _LS_RECOVERY_MAX_PER_CHUNK,
                            )
                            if _ls_kill_and_relaunch():
                                _notify_ls_recovered(i, len(chunks), ls_recoveries)
                                # Retry немедленно — recovery вернулся когда API ожила
                                continue
                            else:
                                _notify_ls_recovery_failed(i, len(chunks))
                                # Fall through к pyramid — но он тоже скорее всего
                                # не помог: если recovery провалился, API мёртвая
                                # надолго. break чтобы не тратить ещё 27 мин.
                                break

                        retriable = "HTTP 409" in msg and "Session is used" in msg
                        if not retriable or attempt == 8:
                            break
                        if attempt == 0:
                            log.warning("чанк %d: 409 'session in use' — sleep 30с, ретрай 2/9", i)
                            time.sleep(30)
                        elif attempt == 1:
                            log.warning("чанк %d: 409 — sleep 60с, ретрай 3/9", i)
                            time.sleep(60)
                        elif attempt == 2:
                            log.warning("чанк %d: 409 после 90с — sleep 2 мин (возможно другой VPS на этом LS-аккаунте), ретрай 4/9", i)
                            time.sleep(120)
                        elif attempt == 3:
                            log.warning("чанк %d: 409 ещё держится — sleep 4 мин, ретрай 5/9", i)
                            time.sleep(240)
                        elif attempt == 4:
                            log.warning("чанк %d: lock не уходит после 7 мин ожидания — unlock_blocked_sessions + ретрай 6/9", i)
                            try:
                                client.unlock_blocked_sessions()
                                time.sleep(3)
                            except Exception as unlock_e:
                                log.warning("unlock_blocked_sessions упал: %s", unlock_e)
                        elif attempt == 5:
                            log.warning("чанк %d: nuclear — force_stop своей сессии + ретрай 7/9", i)
                            try:
                                client.force_stop_session(uuid)
                                time.sleep(5)
                                client.unlock_blocked_sessions()
                                time.sleep(3)
                            except Exception as nuke_e:
                                log.warning("nuclear recovery упал: %s", nuke_e)
                        elif attempt == 6:
                            log.warning("чанк %d: nuclear не помог — sleep 6 мин (затяжной lock от другой VPS) + ретрай 8/9", i)
                            time.sleep(360)
                        else:  # attempt == 7
                            log.warning("чанк %d: финальный — sleep 8 мин + повторный nuclear + ретрай 9/9", i)
                            time.sleep(480)
                            try:
                                client.force_stop_session(uuid)
                                time.sleep(5)
                                client.unlock_blocked_sessions()
                                time.sleep(3)
                            except Exception as final_e:
                                log.warning("final nuclear recovery упал: %s", final_e)
                if _last_err is not None:
                    raise ApiError(f"чанк {i}/{len(chunks)}: start_warmup упал → {_last_err}") from _last_err
                ok = wait_for_warmup_done(client, uuid, cfg, i, len(chunks))
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
        new_total = add_warmed_count(urls_warmed_now, active, single)
        counts[active] = new_total
        profile_done = new_total >= target
        all_done = all(counts[n] >= targets[n] for n in profile_names)

        log.info("сценарий завершён успешно за %.0fм (%d/%d чанков, +%d URL → %s %d/%d)",
                 elapsed / 60, chunks_done, len(chunks), urls_warmed_now,
                 active, new_total, target)

        if profile_done:
            # Профиль добил свой target — сразу экспортируем ЕГО cookies
            # (deliverable появляется как можно раньше; если export упал —
            # флаг не пишем, ретрай на следующем tick'е, см. блок выше).
            cookies_n = count_session_cookies(client, uuid)
            cookies_str = f"{cookies_n}" if cookies_n is not None else "?"
            if cookies_n is not None:
                _exported_flag_for(active).write_text("1", encoding="utf-8")

            if all_done:
                disabled = disable_scheduled_task()
                total_elapsed = _fmt_total_elapsed(load_started_at())
                tail = ("scheduled task disabled. All jobs done."
                        if disabled else
                        "could NOT disable schedule — run manually:\n"
                        f"  schtasks /change /tn {TASK_NAME} /disable")
                notify_ntfy(
                    _ntfy_header() +
                    f"последний профиль {active}: {new_total}/{target} URL, "
                    f"cookies: {cookies_str}\n"
                    + _progress_lines() + "\n"
                    f"total: {sum(counts.values())}/{sum(targets.values())} URL — "
                    f"все {len(profile_names)} профилей готовы 🎉\n"
                    f"total time: {total_elapsed}\n"
                    f"cookies (все профили) → {COOKIES_EXPORT_DIR}\n" + tail,
                    title="warmup all done",
                    priority="high",
                    tags="tada",
                )
                mark_notified_done()
                # Финальный баннер в консоль — заменяет «RUNNING» в окне.
                _print_complete_banner(
                    session_label, _machine_id(),
                    _fmt_total_elapsed_en(load_started_at()),
                    sum(counts.values()), sum(targets.values()), cookies_str,
                )
            else:
                # Готов один из N — деливерабл уже лежит в cookies_export,
                # оператор может забрать не дожидаясь остальных. priority
                # default (не low): это милстоун, но не будим ночью как high.
                n_done = sum(1 for n in profile_names if counts[n] >= targets[n])
                notify_ntfy(
                    _ntfy_header() +
                    f"профиль {active} готов: {new_total}/{target} URL "
                    f"({elapsed/60:.0f} мин последний цикл)\n"
                    f"cookies: {cookies_str} → {COOKIES_EXPORT_DIR}\n"
                    + _progress_lines() + "\n"
                    f"готово {n_done}/{len(profile_names)} профилей, "
                    f"цепочка продолжит остальные через ~2 мин.",
                    title=f"profile done {n_done}/{len(profile_names)} — {active}",
                    priority="default",
                    tags="tada",
                )
        else:
            notify_ntfy(
                _ntfy_header() +
                f"profile: {active}\n"
                f"chunks: {chunks_done}/{len(chunks)} × до {chunk_size} = {urls_warmed_now} URL\n"
                + _progress_lines() + "\n"
                f"total: {sum(counts.values())}/{sum(targets.values())} URL "
                f"({100*sum(counts.values())//max(1, sum(targets.values()))}%)\n"
                f"elapsed: {elapsed/60:.0f} мин",
                title="warmup cycle",
                priority="low",
                tags="gear",
            )
        # 2 = все профили готовы (цепочка останавливается),
        # 0 = цикл успешен, работа осталась (цепочка продолжает).
        return 2 if all_done else 0

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

        # Категоризация ошибки определяет приоритет пуша:
        # - 409 'Session is used' (multi-VPS contention / зомби-сессия) →
        #   priority="low", title="cycle paused — next tick will retry".
        #   Не пробивает ночной режим телефона. На практике система сама
        #   поднимается на следующем 45-мин тике в 95% случаев (другая VPS
        #   к тому моменту освобождает лок).
        # - LS API down на initial ping (после 3 retry'ев) — отдельная
        #   категория: LS реально мёртвая (Electron crash / logoff / OOM).
        #   Дать оператору понятное объяснение и точные команды для
        #   ручного восстановления, плюс упомянуть что Layer 5 watchdog
        #   её всё равно подхватит автоматически за ~15 мин.
        # - всё остальное (signin failed, network error, code crash) →
        #   priority="high", title="warmup failed". Зовёт человека.
        is_409 = "HTTP 409" in str(exc) and "Session is used" in str(exc)
        is_ping_down = (
            "GET /sessions" in str(exc) and
            ("10061" in str(exc) or "refused" in str(exc).lower() or
             "сеть/таймаут" in str(exc))
        )

        hint = ""
        if is_ping_down:
            hint = (
                "LS API на 127.0.0.1:36555 не отвечает после 5 попыток "
                "(~50с total backoff между ping'ами + время самих ping'ов).\n"
                "Это значит LS реально мёртвая, не transient hiccup.\n\n"
                "Причины:\n"
                "- Electron crash (Chromium renderer убил parent на OOM/heavy GC)\n"
                "- Оператор сделал Sign out / Log off в RDP вместо Close ✕\n"
                "- Windows Update auto-reboot\n\n"
                "Что произойдёт само:\n"
                "- ls_watchdog (Layer 5) детектит 3 fail-ping подряд за 15 мин\n"
                "- → kill зомби LS + ls_launch.bat → LS поднимется за ~30с\n"
                "- Следующий 45-мин tick (~через 30 мин) подхватит цикл\n\n"
                "Если хочешь ускорить (опционально):\n"
                "1. RDP в машину через 1440p .rdp (НЕ другой mstsc)\n"
                "2. Test-NetConnection 127.0.0.1 -Port 36555\n"
                "3. Если TcpTestSucceeded=False → cd C:\\warmup; .\\ls_launch.bat\n"
                "4. Подожди 60с: Test-NetConnection 127.0.0.1 -Port 36555\n"
                "5. Когда True → schtasks /run /tn LinkenSphereWarmup\n"
                "6. Закрой RDP ✕ окном — НЕ Sign out / Log off!\n\n"
                "На будущее: если каждый день получаешь такой push на одной\n"
                "и той же машине — LS на ней нестабильная (мало RAM, slow CPU),\n"
                "стоит заменить VPS."
            )
            notify_ntfy(
                _ntfy_header() +
                f"reason: LS API не отвечает после 5 retry на ping\n" + hint + "\n"
                f"tail:\n{tail}",
                title="LS API down — auto-recovery via watchdog",
                priority="high",
                tags="warning",
            )
            return 1

        if is_409:
            _our_name = locals().get("session_name") or "(см. session: выше)"
            hint = (
                "\nlock на API-аккаунте после 7 ретраев (max ~7 мин ожидания).\n"
                "обычно это другая твоя VPS на том же LS-аккаунте крутит свой\n"
                "цикл — следующий scheduler-tick через 45 мин подхватит сам,\n"
                "ничего делать НЕ надо.\n\n"
                "если несколько тиков подряд приходит это сообщение —\n"
                "тогда открой LS UI на этой машине, посмотри:\n"
                f" • если {_our_name} 'Saving data...' застряло → удали её → freshstart.bat\n"
                " • если другая CL-* (от старого freshstart) → удали её\n"
                " • если чужая сессия с другого аккаунта → не трогай\n"
            )
            notify_ntfy(
                _ntfy_header() +
                f"reason: 409 lock after 7 retries\n" + hint + "\n"
                f"tail:\n{tail}",
                title="cycle paused — next tick will retry",
                priority="low",
                tags="hourglass_flowing_sand",
            )
        else:
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
