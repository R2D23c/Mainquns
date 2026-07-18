"""ls_watchdog.py — keeps Linken Sphere 2 alive between cycles.

Runs every 5 min via Task Scheduler. Логика:
  1. .notified_done exists → pipeline закончился, LS не нужна → exit.
  2. LinkenSphereWarmup task сейчас Running → warmup_api отвечает за LS
     (свой retry pyramid, force_stop, unlock) → exit. Не лезем.
  3. Ping http://127.0.0.1:36555/sessions.
     - OK → счётчик сброс. Дополнительно проверяем stuck-state (см. ниже).
     - Fail → инкремент .watchdog_fail_count.
  4. Счётчик >= 3 (3 подряд провала = ~15 мин downtime) → kill LS + launch
     через ls_launch.bat (с правильной cwd для evo:// protocol). Сброс.

  5. STUCK DETECTION: если API живая И main task НЕ Running И LastRunTime
     старше STUCK_THRESHOLD_MIN → force trigger main task через schtasks /run.
     Покрывает E-19 квирк Task Scheduler: после reboot Time trigger с
     RepetitionInterval иногда "забывает" расписание и не тикает часами.
     На VPS 66.135.22.253 наблюдался 4-часовой gap между циклами без причины.
     С этим фиксом max задержка между циклами 5 мин (interval watchdog'а).

Почему 3, а не 1: краткий network blip или LS-handshake может дать
false-positive. 15 мин tolerated downtime безопасно — следующий
Task Scheduler tick для warmup_api всё равно через 45 мин в худшем случае.

Скрипт намеренно ничего не импортит из warmup_api / warmup — должен
работать в любом состоянии репо, даже если основные модули битые.

ТАЙМИНГИ (consciously conservative для избежания конфликтов):
  - Watchdog tick interval: 5 мин (из schedule_hourly.ps1)
  - Watchdog AtStartup delay: 3 мин (даёт LS время подняться)
  - Boot guard:               UPTIME_GUARD_MIN = 10 мин
                              (не force-trigger пока система свежая —
                               AtStartup trigger main task должен сработать сам)
  - Stuck threshold:          STUCK_THRESHOLD_MIN = 75 мин
                              (45 мин interval + 37 мин cycle = 82 мин max,
                               с запасом 75 — fire только если реально стоп)
"""

import datetime
import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FAIL_COUNTER_FILE = ROOT / ".watchdog_fail_count"
LOG_FILE = ROOT / "watchdog.log"
NOTIFIED_DONE = ROOT / ".notified_done"
LAUNCH_BAT = ROOT / "ls_launch.bat"
TASK_NAME = "LinkenSphereWarmup"
API_URL = "http://127.0.0.1:36555/sessions"
HTTP_TIMEOUT = 5
RESTART_THRESHOLD = 3

# ntfy push topic — тот же что в warmup_api.py / notify_boot.py. Watchdog
# намеренно НЕ импортит warmup_api (чтобы работать в любом состоянии репо),
# поэтому duplicate'им константы и send_ntfy локально.
NTFY_TOPIC = "warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE"
NTFY_URL = "https://ntfy.sh"
NTFY_TIMEOUT = 15

# STUCK DETECTION тайминги.
# 75 мин: нормальный цикл (45 мин interval + 37 мин cycle = 82 мин worst case)
# минус 7 мин buffer. Если LastRunTime старше 75 мин — точно что-то залипло.
STUCK_THRESHOLD_MIN = 75
# 10 мин: после boot не лезем форсить, даже если LastRunTime старый. AtStartup
# trigger main task имеет delay 2 мин + LS поднимается + warmup_api стартует.
# Дать ВСЕЙ цепочке 10 мин на естественное восстановление прежде чем нам
# вмешиваться. Иначе риск конфликта: мы force-trigger ровно когда AtStartup
# trigger уже фигачит → IgnoreNew пропустит наш или дубль, состояние мутное.
UPTIME_GUARD_MIN = 10


def _log(msg: str) -> None:
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ---- ntfy helpers (self-contained, как в notify_boot.py) ----
# Чтобы оператор видел КАЖДОЕ recovery событие в push'ах, не только
# warmup_api'шные. Раньше watchdog kill+launch / force-trigger были
# полностью silent — только в watchdog.log. Это создавало "молчаливые
# гэпы" где watchdog уже всё разрулил, но оператор узнавал об этом
# только через ⚙️ цикл-push через 45+ минут.


def _machine_id() -> str:
    """Публичный IP — стабильный уникальный ID VPS (как в warmup_api/notify_boot).
    Hostname часто бесполезен ('WIN-XXX'). Два провайдера IP с timeout 5с;
    fallback на hostname если нет сети."""
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode("ascii", errors="ignore").strip()
                if ip:
                    return ip
        except Exception:
            continue
    try:
        return socket.gethostname() or "no-ip"
    except Exception:
        return "no-ip"


def _load_session_name() -> str:
    """Имена профилей (CL-XXXXXXXX) из .session_name. Для watchdog event'ов
    это оператору ориентир 'какая VPS прислала push' в скоплении уведомлений.
    Мульти-профиль: в файле N строк — показываем все через запятую."""
    f = ROOT / ".session_name"
    try:
        if f.exists():
            names = [
                ln.strip()
                for ln in f.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            if names:
                return ", ".join(names)
    except Exception:
        pass
    return "<unknown>"


def _send_ntfy(title: str, body: str, priority: int, tags: list[str]) -> bool:
    """POST на ntfy.sh с короткой обёрткой ошибок. priority: 1..5 (low=2)."""
    payload = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": body,
        "priority": priority,
        "tags": tags,
    }
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=NTFY_TIMEOUT) as resp:
            resp.read()
            return 200 <= resp.status < 300
    except Exception as e:
        _log(f"ntfy push failed: {e!r}")
        return False


def _notify_ls_restarted(reason: str = "API down 3 strikes") -> None:
    """🔧 watchdog kill+launch отработал успешно."""
    body = (
        f"session: {_load_session_name()}\n"
        f"machine: {_machine_id()}\n"
        f"watchdog перезапустил LS\n"
        f"причина: {reason}"
    )
    _send_ntfy(
        title="🔧 watchdog: LS restarted",
        body=body,
        priority=2,  # low
        tags=["wrench"],
    )


def _notify_ls_restart_failed(reason: str = "API down 3 strikes") -> None:
    """⚠️ watchdog kill+launch попытался, но LS не поднимается (ls_launch.bat
    отсутствует/не запустился). High priority — оператору надо RDP'нуться."""
    body = (
        f"session: {_load_session_name()}\n"
        f"machine: {_machine_id()}\n"
        f"watchdog НЕ смог поднять LS\n"
        f"причина: {reason}\n"
        f"требуется RDP-диагностика"
    )
    _send_ntfy(
        title="⚠️ watchdog: LS restart failed",
        body=body,
        priority=4,  # high
        tags=["warning"],
    )


def _notify_force_trigger(age_min: float, uptime_min: float | None) -> None:
    """🔧 watchdog force-trigger'нул stuck main task. Low priority — событие
    нормальное (TS квирк), просто чтобы оператор видел что watchdog работает."""
    body = (
        f"session: {_load_session_name()}\n"
        f"machine: {_machine_id()}\n"
        f"main task застрял (last run {age_min:.0f} мин назад)\n"
        f"watchdog force-triggered task"
    )
    if uptime_min is not None:
        body += f"\nuptime: {uptime_min:.0f} мин"
    _send_ntfy(
        title="🔧 watchdog: stuck task triggered",
        body=body,
        priority=2,  # low
        tags=["wrench"],
    )


def _read_counter() -> int:
    if not FAIL_COUNTER_FILE.exists():
        return 0
    try:
        return int(FAIL_COUNTER_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def _write_counter(n: int) -> None:
    try:
        FAIL_COUNTER_FILE.write_text(str(n), encoding="utf-8")
    except Exception:
        pass


def _main_task_running() -> bool:
    """True если LinkenSphereWarmup сейчас исполняется (run_api.bat жив)."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "list"],
            capture_output=True, text=True, timeout=10, errors="replace",
        )
        for line in (result.stdout or "").splitlines():
            ln = line.strip().lower()
            if ln.startswith("status:") and "running" in ln:
                return True
    except Exception:
        pass
    return False


def _system_uptime_minutes() -> float | None:
    """Сколько минут прошло с последнего boot системы."""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToFileTime()",
            ],
            capture_output=True, text=True, timeout=15, errors="replace",
        )
        s = (result.stdout or "").strip()
        if not s.isdigit():
            return None
        boot_dt = datetime.datetime.fromtimestamp(
            (int(s) - 116444736000000000) / 10_000_000
        )
        delta = datetime.datetime.now() - boot_dt
        return max(0.0, delta.total_seconds() / 60.0)
    except Exception:
        return None


def _main_task_last_run_age_minutes() -> float | None:
    """Сколько минут прошло с LastRunTime LinkenSphereWarmup.
    None если не удалось прочитать (task не зарегистрирована или PowerShell
    провалился). Caller трактует None как "не вмешиваемся" — fail-safe."""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "(Get-ScheduledTaskInfo -TaskName '" + TASK_NAME + "').LastRunTime.ToFileTime()",
            ],
            capture_output=True, text=True, timeout=15, errors="replace",
        )
        s = (result.stdout or "").strip()
        # LastRunTime может быть 0 (никогда не запускалась) или валидный FileTime
        if not s.isdigit():
            return None
        ft = int(s)
        if ft <= 0:
            return None
        # Sanity: FileTime до 2001 года — считаем что задача никогда не тикала
        # (Windows иногда отдаёт epoch 1601 для never-run tasks)
        if ft < 126227808000000000:  # 2001-01-01
            return None
        last_dt = datetime.datetime.fromtimestamp(
            (ft - 116444736000000000) / 10_000_000
        )
        delta = datetime.datetime.now() - last_dt
        return max(0.0, delta.total_seconds() / 60.0)
    except Exception:
        return None


def _force_trigger_main_task() -> bool:
    """schtasks /run /tn LinkenSphereWarmup — дёргаем задачу немедленно.
    Возвращает True если schtasks доложил SUCCESS."""
    try:
        result = subprocess.run(
            ["schtasks", "/run", "/tn", TASK_NAME],
            capture_output=True, text=True, timeout=10, errors="replace",
        )
        return result.returncode == 0
    except Exception:
        return False


def _ping_api() -> bool:
    try:
        with urllib.request.urlopen(API_URL, timeout=HTTP_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _restart_ls() -> bool:
    """Kill all LS processes + launch через ls_launch.bat (правильная cwd)."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "Linken Sphere 2.exe", "/T"],
            capture_output=True, text=True, timeout=15, errors="replace",
        )
    except Exception as e:
        _log(f"restart: taskkill failed: {e!r}")

    if not LAUNCH_BAT.exists():
        _log(f"restart: {LAUNCH_BAT} не найден — не могу запустить LS")
        return False

    try:
        # CREATE_NO_WINDOW: cmd выполняется БЕЗ visible window. Раньше был
        # DETACHED_PROCESS, который не всегда скрывает cmd на Windows (создаёт
        # invisible-ish console но иногда мерцает). CREATE_NO_WINDOW гарантирует
        # полную невидимость. Cmd /c заканчивается после `start "" /D ... LS`
        # внутри ls_launch.bat — LS-процесс независимый, переживёт parent.
        subprocess.Popen(
            ["cmd", "/c", str(LAUNCH_BAT)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True
    except Exception as e:
        _log(f"restart: ls_launch.bat не запустился: {e!r}")
        return False


def _check_and_unstick_main_task() -> None:
    """Если main task не тикала >STUCK_THRESHOLD_MIN — дёргаем её вручную.
    Защита от E-19 квирка Task Scheduler (Time trigger drift после reboot).

    Гварды:
    1) uptime < UPTIME_GUARD_MIN — не лезем, дать AtStartup trigger шанс
    2) LastRunTime неизвестен (None) — не лезем (task может быть не настроена)
    3) Возраст LastRunTime < threshold — не лезем (всё в норме)
    4) .notified_done — не дойдёт сюда (early-exit в main)
    5) main task Running — не дойдёт сюда (early-exit в main)
    """
    uptime = _system_uptime_minutes()
    if uptime is not None and uptime < UPTIME_GUARD_MIN:
        # Система свежая — AtStartup trigger ещё в работе, не вмешиваемся
        return

    age = _main_task_last_run_age_minutes()
    if age is None:
        # Не смогли прочитать — fail-safe не вмешиваемся
        return

    if age < STUCK_THRESHOLD_MIN:
        # Норма — task недавно тикала
        return

    # STUCK: API живая, main task Ready, и не тикала уже > порога.
    # Дёргаем руками.
    _log(
        f"stuck: main task last ran {age:.0f} min ago (threshold {STUCK_THRESHOLD_MIN})"
        + f", uptime {uptime:.0f}m" if uptime else ""
        + " — force trigger"
    )
    ok = _force_trigger_main_task()
    _log(f"force-trigger: {'OK' if ok else 'FAILED'}")
    if ok:
        _notify_force_trigger(age, uptime)


def _self_disable() -> None:
    """schtasks /change /disable /tn LsWatchdog — выключаем сами себя.
    Зовётся когда .notified_done существует. После этого Task Scheduler
    не дёргает watchdog → больше нет 5-минутных popup'ов на VPS."""
    try:
        subprocess.run(
            ["schtasks", "/change", "/disable", "/tn", "LsWatchdog"],
            capture_output=True, text=True, timeout=10, errors="replace",
        )
        _log("self-disable: pipeline done — LsWatchdog disabled, больше popup не будет")
    except Exception as e:
        _log(f"self-disable failed: {e!r}")


def main() -> int:
    if sys.platform != "win32":
        return 0

    if NOTIFIED_DONE.exists():
        # Pipeline завершён — LS не нужна, watchdog отдыхает И отключаем
        # сам Task Scheduler entry чтобы прекратить мерцание cmd-окон
        # каждые 5 мин. Идемпотентно: если уже disabled, schtasks
        # промолчит, _log() напишет ещё одну строку (это OK).
        _write_counter(0)
        _self_disable()
        return 0

    if _main_task_running():
        # warmup_api сам разбирается с LS-ошибками — не вмешиваемся.
        _write_counter(0)
        return 0

    if _ping_api():
        prev = _read_counter()
        if prev > 0:
            _log(f"healthy: API отвечает, счётчик сброшен ({prev} → 0)")
        _write_counter(0)

        # API живая, main task НЕ Running. Проверяем не залипла ли задача.
        _check_and_unstick_main_task()
        return 0

    # API не отвечает.
    counter = _read_counter() + 1
    _write_counter(counter)
    _log(f"down: API connection refused, counter={counter}/{RESTART_THRESHOLD}")

    if counter >= RESTART_THRESHOLD:
        _log("threshold reached → restart LS")
        ok = _restart_ls()
        _log(f"restart: {'OK (ls_launch.bat invoked)' if ok else 'FAILED'}")
        # Push в ntfy — оператор видит ВСЕ recovery события, не только
        # warmup_api'шные. Раньше watchdog restart был полностью silent.
        if ok:
            _notify_ls_restarted("API down 3 strikes (~15 мин)")
        else:
            _notify_ls_restart_failed("ls_launch.bat не запустился или отсутствует")
        # Сброс счётчика. Если LS снова умрёт — отсчёт начнётся с нуля.
        _write_counter(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
