# -*- coding: utf-8 -*-
"""Превью уведомлений. Шлёт все 4 реальных типа сообщений через ту же
функцию notify_ntfy и тот же заголовок _ntfy_header, что и боевой код.
Эмодзи и кириллица живут в этом UTF-8 файле, а не в консоли, поэтому
не ломаются.

Запуск на машине:  py test_notify.py
"""
from warmup_api import notify_ntfy, _ntfy_header

hdr = _ntfy_header()  # session: ... \n machine: hostname · дата \n

# 1) UI install OK — первый milestone, машина готова — тихое, ✅
notify_ntfy(
    hdr +
    "UI install OK 1/2\n"
    "Запускаю первый API-прогрев в фоне.\n"
    "RDP можно отключать — дальше всё само.",
    title="warmup OK",
    priority="low",
    tags="white_check_mark",
)

# 2) cycle complete с прогрессом — тихое, ⚙️
notify_ntfy(
    hdr +
    "chunks: 5/5 × до 7 = 33 URL\n"
    "progress: 200/450 URL (44%)\n"
    "elapsed: 38 мин",
    title="warmup cycle",
    priority="low",
    tags="gear",
)

# 3) цель достигнута — тихое, 🎉
notify_ntfy(
    hdr +
    "this run: 96 URL (41 мин)\n"
    "total: 450/450 URL — target reached 🎉\n"
    "scheduled task disabled. All jobs done.",
    title="warmup all done",
    priority="low",
    tags="tada",
)

# 4) падение — со звуком, ⚠️
notify_ntfy(
    hdr +
    "error: чанк 3/14: start_warmup → HTTP 409 "
    "Session is used by another client or operation",
    title="warmup failed (api)",
    priority="high",
    tags="warning",
)

print("4 уведомления отправлены. Проверь Telegram / ntfy.")
