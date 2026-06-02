# -*- coding: utf-8 -*-
"""Превью уведомлений. Шлёт 3 реальных типа сообщений через ту же функцию
notify_ntfy, что и боевой код. Эмодзи/кириллица живут в этом UTF-8 файле,
а не в консоли, поэтому ничего не ломается.

Запуск на машине:  py test_notify.py
"""
import socket

from warmup_api import notify_ntfy

host = socket.gethostname()

# 1) обычный прогон с прогрессом — тихое, ✅
notify_ntfy(
    f"session: CL-72847084\n"
    f"machine: {host}\n"
    f"chunks: 5/5 × до 7 = 33 URL\n"
    f"progress: 200/450 URL (44%)\n"
    f"elapsed: 38 мин",
    title="warmup OK",
    priority="low",
    tags="white_check_mark",
)

# 2) цель достигнута — тихое, 🎉
notify_ntfy(
    f"session: CL-72847084\n"
    f"machine: {host}\n"
    f"this run: 96 URL (41 мин)\n"
    f"total: 450/450 URL — target reached 🎉\n"
    f"scheduled task disabled. All jobs done.",
    title="warmup all done",
    priority="low",
    tags="tada",
)

# 3) падение — со звуком, ⚠️
notify_ntfy(
    f"session: CL-72847084\n"
    f"machine: {host}\n"
    f"error: чанк 3/14: start_warmup → HTTP 409 "
    f"Session is used by another client or operation",
    title="warmup failed (api)",
    priority="high",
    tags="warning",
)

print("3 уведомления отправлены. Проверь Telegram / ntfy.")
