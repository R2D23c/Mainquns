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
"""

import configparser
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

# Топик ntfy — тот же что в warmup_api.py (источник единой правды этого
# скрипта намеренно изолирован, чтобы notify_boot не импортил warmup_api
# и не падал из-за тяжёлых deps при раннем запуске)
NTFY_TOPIC = "warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE"
NTFY_URL = "https://ntfy.sh"

# Не блокируем boot — короткий timeout. Если сети нет (не успела подняться)
# — тихо пропускаем. Следующий warmup_api цикл всё равно сообщит о статусе.
HTTP_TIMEOUT = 15


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


def _send_ntfy(uptime_min: int) -> bool:
    """Шлёт push на ntfy.sh. Возвращает True если ответ 2xx."""
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
            return 200 <= resp.status < 300
    except Exception:
        return False


def main() -> int:
    if sys.platform != "win32":
        return 0

    current_boot = _powershell_boot_filetime()
    if not current_boot:
        return 0

    last_boot: str | None = None
    if LAST_BOOT_FILE.exists():
        try:
            last_boot = LAST_BOOT_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            last_boot = None

    # Обновляем файл ПЕРЕД отправкой ntfy, чтобы при сетевом сбое не было
    # ретраев в следующем logon в той же boot-сессии (анти-спам).
    try:
        LAST_BOOT_FILE.write_text(current_boot, encoding="utf-8")
    except Exception:
        pass

    if last_boot is None:
        # Первый запуск ever — initial install, не recovery. Не шлём.
        return 0

    if last_boot == current_boot:
        # Тот же boot — relogin внутри сессии (RDP reconnect на Server).
        return 0

    # Свежий boot после ребута → пуш. Ждём 5с чтобы сеть успела подняться.
    time.sleep(5)
    _send_ntfy(_uptime_minutes(current_boot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
