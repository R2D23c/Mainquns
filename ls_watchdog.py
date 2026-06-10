"""ls_watchdog.py — keeps Linken Sphere 2 alive between cycles.

Runs every 5 min via Task Scheduler. Логика:
  1. .notified_done exists → pipeline закончился, LS не нужна → exit.
  2. LinkenSphereWarmup task сейчас Running → warmup_api отвечает за LS
     (свой retry pyramid, force_stop, unlock) → exit. Не лезем.
  3. Ping http://127.0.0.1:36555/sessions.
     - OK → счётчик сброс, exit.
     - Fail → инкремент .watchdog_fail_count.
  4. Счётчик >= 3 (3 подряд провала = ~15 мин downtime) → kill LS + launch
     через ls_launch.bat (с правильной cwd для evo:// protocol). Сброс.

Почему 3, а не 1: краткий network blip или LS-handshake может дать
false-positive. 15 мин tolerated downtime безопасно — следующий
Task Scheduler tick для warmup_api всё равно через 45 мин в худшем случае.

Скрипт намеренно ничего не импортит из warmup_api / warmup — должен
работать в любом состоянии репо, даже если основные модули битые.
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
        subprocess.Popen(
            ["cmd", "/c", str(LAUNCH_BAT)],
            cwd=str(ROOT),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return True
    except Exception as e:
        _log(f"restart: ls_launch.bat не запустился: {e!r}")
        return False


def main() -> int:
    if sys.platform != "win32":
        return 0

    if NOTIFIED_DONE.exists():
        # Pipeline завершён — LS не нужна, watchdog отдыхает.
        _write_counter(0)
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
