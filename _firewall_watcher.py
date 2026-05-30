"""
_firewall_watcher.py — фоновый процесс, который дисмиссит Windows Defender
Firewall Alert на всё время прогрева (~40 мин). Спавнится из warmup.py и
warmup_api.py, убивается по окончанию.

Импортит сам warmup.py — переиспользует там готовый _dismiss_firewall_alert
(шаблон allow_access + title-detection + Alt+A). Запуск headless, никаких
side-эффектов кроме клика по Allow access если firewall всплыл.

Параметр командной строки — интервал между опросами в секундах
(по умолчанию 15). Меньше = больше CPU на template-matching на слабом VPS.
"""

from __future__ import annotations

import sys
import time

# Эти импорты делаются на верхнем уровне модуля warmup и НЕ требуют каких-либо
# артефактов вроде credentials.ini или templates — просто загружают функции.
import warmup


def main() -> None:
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    while True:
        try:
            warmup._dismiss_firewall_alert()
        except Exception as e:
            # Никогда не падаем — watcher должен жить пока его не убьют
            print(f"[fw_watcher] dismiss failed: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
