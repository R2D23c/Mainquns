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
        # Сброс счётчика. Если LS снова умрёт — отсчёт начнётся с нуля.
        _write_counter(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
