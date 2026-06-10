"""notify_boot.py — sends ntfy push on fresh boot detection.

Вызывается из ls_launch.bat при logon (autologin или ручной RDP).
Логика:
  1. Читает LastBootUpTime Windows (через PowerShell WMI).
  2. Сравнивает с сохранённой в .last_boot FileTime.
  3. Если время отличается — это свежий boot после ребута. Шлём ntfy.
  4. Обновляет .last_boot.

Идемпотентность: если в одном boot-сессии случилось несколько logon'ов
(например, RDP relogin на Windows Server), .last_boot уже обновлён —
повторного уведомления не будет.

Первый запуск ever (.last_boot отсутствует) — НЕ шлём (это initial install,
а не recovery). Только обновляем файл, чтобы со следующего boot'а ловить.

Кейс с reset на стороне провайдера (Kernel-Power 41) покрывается: после
boot'а ls_launch.bat дёрнет этот скрипт, boot time изменится — пуш уйдёт.

Логирование:
  Каждый запуск дописывает строку в notify_boot.log (timestamp + outcome).
  Это нужно для пост-факт-диагностики: когда оператор замечает что 🔄 не
  пришло, можно посмотреть в лог и увидеть в каком звене застряло
  (skip-first / skip-same-boot / sent / failed-ntfy / failed-no-boot).
"""

import datetime
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAST_BOOT_FILE = ROOT / ".last_boot"
LOG_FILE = ROOT / "notify_boot.log"

# Топик ntfy — тот же что в warmup_api.py (источник единой правды этого
# скрипта намеренно изолирован, чтобы notify_boot не импортил warmup_api
# и не падал из-за тяжёлых deps при раннем запуске)
NTFY_TOPIC = "warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE"
NTFY_URL = "https://ntfy.sh"

# Не блокируем boot — короткий timeout. Если сети нет (не успела подняться)
# — тихо пропускаем. Следующий warmup_api цикл всё равно сообщит о статусе.
HTTP_TIMEOUT = 15


def _log(outcome: str, **kwargs: object) -> None:
    """Append a single-line entry to notify_boot.log.
    Формат: [YYYY-MM-DD HH:MM:SS] outcome  key1=val1 key2=val2 ..."""
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        extras = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
        line = f"[{ts}] {outcome:<16}  {extras}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _powershell_boot_filetime() -> str | None:
    """Возвращает Windows LastBootUpTime как Int64 FileTime (10⁻⁷ сек от 1601).
    FileTime — детерминистичный и сравнимый, не зависит от форматирования
    DateTime между локалями/таймзонами."""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToFileTime()",
            ],
            capture_output=True, text=True, timeout=20,
        )
        s = (result.stdout or "").strip()
        return s if s.isdigit() else None
    except Exception:
        return None


def _uptime_minutes(boot_ft: str) -> int:
    """Конвертит FileTime → uptime в минутах от текущего времени."""
    try:
        # FileTime epoch = 1601-01-01 UTC. 116444736000000000 = 1970-01-01 в FT.
        boot_dt = datetime.datetime.fromtimestamp(
            (int(boot_ft) - 116444736000000000) / 10_000_000
        )
        delta = datetime.datetime.now() - boot_dt
        return max(0, int(delta.total_seconds() / 60))
    except Exception:
        return -1


def _machine_id() -> str:
    """Внешний IP — стабильный уникальный идентификатор VPS (как в warmup_api.py).
    Hostname (WIN-XXX / HOME) часто одинаков между VPS, IP всегда уникален."""
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode("ascii", errors="ignore").strip()
                if ip:
                    return ip
        except Exception:
            continue
    return socket.gethostname() or "no-ip"


def _load_session_name() -> str:
    """Читает .session_name если есть. Для recovery-уведомлений сессия
    может быть нерелевантна (мы можем сработать ДО первого warmup-цикла),
    но если она есть — кладём для консистентности с прочими ntfy."""
    f = ROOT / ".session_name"
    try:
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return "<unknown>"


def _send_ntfy(uptime_min: int) -> tuple[bool, str]:
    """Шлёт push на ntfy.sh. Возвращает (ok, err_msg). err_msg пустой если ok."""
    sess = _load_session_name()
    machine = _machine_id()
    body = (
        f"session: {sess}\n"
        f"machine: {machine}\n"
        f"система поднялась после reboot.\n"
        f"uptime: {uptime_min}m"
    )
    payload = {
        "topic": NTFY_TOPIC,
        "title": "🔄 system recovered",
        "message": body,
        "priority": 2,  # low
        "tags": ["arrows_counterclockwise"],
    }
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp.read()
            ok = 200 <= resp.status < 300
            return ok, ("" if ok else f"HTTP {resp.status}")
    except Exception as e:
        return False, repr(e)


def main() -> int:
    if sys.platform != "win32":
        _log("skip-not-win32")
        return 0

    current_boot = _powershell_boot_filetime()
    if not current_boot:
        _log("failed-no-boot", err="PowerShell не вернул FileTime")
        return 0

    last_boot: str | None = None
    if LAST_BOOT_FILE.exists():
        try:
            last_boot = LAST_BOOT_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            _log("warn-read-last-boot", err=repr(e))
            last_boot = None

    # Обновляем файл ПЕРЕД отправкой ntfy, чтобы при сетевом сбое не было
    # ретраев в следующем logon в той же boot-сессии (анти-спам).
    try:
        LAST_BOOT_FILE.write_text(current_boot, encoding="utf-8")
    except Exception as e:
        _log("warn-write-last-boot", err=repr(e))

    if last_boot is None:
        # Первый запуск ever — initial install, не recovery. Не шлём.
        _log("skip-first", current=current_boot)
        return 0

    if last_boot == current_boot:
        # Тот же boot — relogin внутри сессии (RDP reconnect на Server).
        _log("skip-same-boot", current=current_boot)
        return 0

    # Свежий boot после ребута → пуш. Ждём 5с чтобы сеть успела подняться.
    time.sleep(5)
    uptime = _uptime_minutes(current_boot)
    ok, err = _send_ntfy(uptime)
    if ok:
        _log("sent", boot=current_boot, prev=last_boot, uptime=f"{uptime}m")
    else:
        _log("failed-ntfy", boot=current_boot, uptime=f"{uptime}m", err=err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
