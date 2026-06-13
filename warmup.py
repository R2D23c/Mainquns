"""
Linken Sphere 2 — автоматизация Warm up.

Сценарий:
  0. Запустить Linken Sphere 2 если ещё не открыт, дождаться главного экрана.
  1. Кликнуть кнопку "три точки" → пункт "Warm up".
  2. Изменить viewing depth и time per url на значения из config.ini.
  3. Снять галочку "use most popular".
  4. Browse file → выбрать случайный .txt из указанной папки.
  5. Удалить первые N строк из URL-textarea (опционально).
  6. Нажать START.

Поиск элементов — по template matching (картинки в templates/).
Диалог открытия файла — нативный Windows, обрабатывается через клавиатуру.
"""

from __future__ import annotations

import configparser
import ctypes
import json
import logging
import os
import random
import subprocess
import sys
import time
from pathlib import Path

# Hide inherited console window — на запуске Task Scheduler нас спавнит через
# `cmd.exe /c run_api.bat` с visible cmd window. Все output redirect'нуты в
# warmup.log (см. run_api.bat), поэтому окно ВИЗУАЛЬНО ПУСТОЕ и только мешает:
# - перекрывает LS UI на 5-10 минут UI install
# - может перехватить focus от pyautogui clicks
# Прячем сразу. warmup_api.py в конце UI install спавнится как DETACHED
# subprocess, у него своя AllocConsole + WriteConsoleW banner — она будет
# видна (новое окно), а наше пустое — больше нет.
if sys.platform == "win32":
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        SW_HIDE = 0
        console_hwnd = kernel32.GetConsoleWindow()
        if console_hwnd:
            user32.ShowWindow(console_hwnd, SW_HIDE)
    except Exception:
        pass

# DPI awareness ОБЯЗАТЕЛЬНО до импорта pyautogui/Pillow.
# Иначе на Windows со scaling != 100% ImageGrab отдаёт физические пиксели,
# а pyautogui.click трактует их как логические — клики уходят не туда.
if sys.platform == "win32":
    for _setter in (
        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            _setter()
            break
        except (AttributeError, OSError):
            continue

import cv2
import numpy as np
import pyautogui
from PIL import ImageDraw, ImageGrab

# Помечает последние N точек клика на скриншоте, чтобы было видно куда летели курсоры.
_CLICK_TRAIL: list[tuple[int, int, str]] = []
_TRAIL_LIMIT = 6

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
SCREENSHOTS_DIR = ROOT / "screenshots"
LOG_FILE = ROOT / "warmup.log"

# На задиссконекченной/долго простаивавшей RDP-сессии курсор паркуется
# в (0,0). При первом же click() pyautogui кидает FailSafeException ещё
# до того, как мы успеем что-то сделать. Для unattended-автоматизации
# fail-safe бесполезен — выключаем.
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.15

# Push-уведомления через ntfy.sh: на каждой машине без настройки.
# Топик играет роль «адреса» — кто знает строку, может слать push в эту тему.
# Подписаться: приложение ntfy → Subscribe to topic → ввести значение ниже.
NTFY_TOPIC = "warmup-r2d2-7m9k4n2p8q5xFx168xx1QQE"
# Сколько первых УСПЕШНЫХ запусков на новой машине ещё подтверждаем push'ем —
# чтобы убедиться, что setup отработал. Дальше — тишина (только при падениях).
SUCCESS_NOTIFY_COUNT = 2
SUCCESS_STATE_FILE = ROOT / ".warmup_state"
# Сгенерированное имя сессии этой машины (формат CL-XXXXXXXX, 8 цифр).
# Создаётся один раз — на первой инсталляции — и больше не меняется.
SESSION_NAME_FILE = ROOT / ".session_name"
# Флаг, что в LS активирован API-порт (Settings → Network → Api port).
# Если флаг есть — UI-активацию пропускаем, дальше всё через HTTP.
API_ACTIVATED_FLAG = ROOT / ".api_activated"
# Флаг, что сессия уже импортирована в LS на этой машине (хранит имя).
# Первый запуск импортит xlsx из session_imports/, дальше только warmup.
SESSION_IMPORTED_FLAG = ROOT / ".session_imported"
SESSION_IMPORTS_DIR = ROOT / "session_imports"


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("warmup")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


log = setup_logging()


def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg_path = ROOT / "config.ini"
    if not cfg_path.exists():
        log.error("config.ini не найден рядом со скриптом")
        sys.exit(2)
    cfg.read(cfg_path, encoding="utf-8")
    return cfg


def _record_click(x: int, y: int, label: str) -> None:
    _CLICK_TRAIL.append((x, y, label))
    if len(_CLICK_TRAIL) > _TRAIL_LIMIT:
        _CLICK_TRAIL.pop(0)


def screenshot(step: str, enabled: bool) -> None:
    if not enabled:
        return
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = SCREENSHOTS_DIR / f"{ts}_{step}.png"
    try:
        img = ImageGrab.grab()
    except OSError as e:
        # screen grab падает в отключённой RDP-сессии — не валим из-за этого
        # ВЕСЬ скрипт, тем более в except-блоке при логировании ошибки.
        log.warning("screenshot не сделан (%s) — пропускаю", e)
        return
    if _CLICK_TRAIL:
        draw = ImageDraw.Draw(img)
        r = 18
        for x, y, label in _CLICK_TRAIL:
            draw.ellipse([x - r, y - r, x + r, y + r], outline="red", width=3)
            draw.line([(x - r, y), (x + r, y)], fill="red", width=2)
            draw.line([(x, y - r), (x, y + r)], fill="red", width=2)
            draw.text((x + r + 3, y - r), label, fill="red")
    img.save(path)
    log.info("screenshot → %s", path.name)


def grab_screen_bgr() -> np.ndarray | None:
    """Скриншот всего экрана как cv2 BGR-массив. Возвращает None если
    ImageGrab упал OSError'ом — это происходит при отключённой RDP-сессии
    (нет attached desktop'а → нечего скриншотить). Caller обязан обработать
    None как «шаблон не найден» и продолжить polling."""
    try:
        img = np.array(ImageGrab.grab())
    except OSError as e:
        log.warning("grab_screen_bgr: %s — пропуск", e)
        return None
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def _try_match_single(name: str, screen: np.ndarray, confidence: float) -> tuple[int, int] | None:
    """Внутренний хелпер: один template (`{name}.png`) vs screen.
    Возвращает координаты центра матча или None если confidence ниже
    порога / файла нет."""
    tpl_path = TEMPLATES_DIR / f"{name}.png"
    if not tpl_path.exists():
        return None
    tpl = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
    if tpl is None:
        log.error("не удалось прочитать %s", tpl_path)
        return None
    res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    log.info("match %s: confidence=%.3f", name, max_val)
    if max_val < confidence:
        return None
    h, w = tpl.shape[:2]
    return (max_loc[0] + w // 2, max_loc[1] + h // 2)


def _fhd_fallback_names(name: str) -> list[str]:
    """Возвращает список FHD-вариантов для primary template `{name}`,
    которые имеет смысл пробовать как fallback.

    Правила:
    1. Всегда пробуем `{name}_fhd.png` (если существует)
    2. Дополнительно пробуем `{name}N_fhd.png` (N=2..9) — это альтернативные
       FHD-нарезки той же кнопки (другой crop, другой стиль фона и т.п.)
    3. КРИТИЧНО: пропускаем `{name}N_fhd` если существует primary
       `{name}N.png` — это означает что `{name}N` это **самостоятельный**
       template другого UI-элемента (например get_started vs get_started2),
       и их FHD-варианты к get_started не относятся.

    Пример:
      `multiple_button` → пробуем multiple_button_fhd, потом multiple_button2_fhd
         (нет primary multiple_button2.png — значит это альтернативный crop)
      `get_started`     → пробуем только get_started_fhd
         (get_started2.png это OTHER primary — get_started2_fhd НЕ для нас)"""
    candidates: list[str] = []
    primary_fhd = f"{name}_fhd"
    if (TEMPLATES_DIR / f"{primary_fhd}.png").exists():
        candidates.append(primary_fhd)
    for n in range(2, 10):
        # Если есть primary с этой нумерацией — она "своя" для другого
        # template'а, не путаем
        if (TEMPLATES_DIR / f"{name}{n}.png").exists():
            continue
        variant = f"{name}{n}_fhd"
        if (TEMPLATES_DIR / f"{variant}.png").exists():
            candidates.append(variant)
    return candidates


def find_template(name: str, confidence: float) -> tuple[int, int] | None:
    """Возвращает центр найденного шаблона на экране или None.

    FHD-fallback: если primary `{name}.png` не сматчил, пробует
    `{name}_fhd.png` и нумерованные варианты `{name}N_fhd.png` (см.
    `_fhd_fallback_names`). Файлы _fhd опциональны — если их нет,
    behavior identical to legacy. На VPS с нормальным рендером primary
    template даёт 0.99 → fallback не активируется. На VPS с "FHD-render"
    primary даёт 0.5-0.7 (ниже порога) → fallback пробует _fhd template
    который снят с такой же VPS → должен дать 0.99.

    Threshold не меняется (остаётся 0.80). Никакого false-positive risk
    из понижения порога — мы вместо этого подкладываем правильный
    template под рендер."""
    tpl_path = TEMPLATES_DIR / f"{name}.png"
    if not tpl_path.exists():
        log.error("шаблон не найден: %s", tpl_path)
        return None

    screen = grab_screen_bgr()
    if screen is None:
        return None  # RDP отключена, screen grab упал → шаблон «не найден»

    pt = _try_match_single(name, screen, confidence)
    if pt is not None:
        return pt
    # Fallback: пробуем FHD-варианты по очереди
    for fhd_name in _fhd_fallback_names(name):
        log.info("match %s primary не прошёл, пробую %s", name, fhd_name)
        pt = _try_match_single(fhd_name, screen, confidence)
        if pt is not None:
            return pt
    return None


def wait_for(name: str, confidence: float, timeout: float) -> tuple[int, int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pt = find_template(name, confidence)
        if pt is not None:
            return pt
        time.sleep(0.5)
    raise TimeoutError(f"элемент '{name}' не появился за {timeout}s")


def click(name: str, cfg: configparser.ConfigParser, *, double: bool = False) -> None:
    conf = cfg.getfloat("matching", "confidence")
    timeout = cfg.getfloat("matching", "wait_seconds")
    x, y = wait_for(name, conf, timeout)
    log.info("click %s@(%d,%d)%s", name, x, y, " ×2" if double else "")
    _record_click(x, y, name)
    if double:
        pyautogui.doubleClick(x, y)
    else:
        pyautogui.click(x, y)
    time.sleep(cfg.getfloat("matching", "step_delay"))


def click_at_offset(name: str, xf: float, yf: float, cfg: configparser.ConfigParser) -> None:
    """Кликает в точку (xf, yf) внутри найденного шаблона, доли 0..1 от ширины/высоты."""
    left, top, w, h = _find_template_box(name, cfg)
    x, y = int(left + w * xf), int(top + h * yf)
    log.info("click %s @offset(%.2f,%.2f) → (%d,%d)", name, xf, yf, x, y)
    _record_click(x, y, name)
    pyautogui.click(x, y)
    time.sleep(cfg.getfloat("matching", "step_delay"))


def _find_template_box(name: str, cfg: configparser.ConfigParser) -> tuple[int, int, int, int]:
    """Возвращает (left, top, width, height) найденного шаблона.

    FHD-fallback: если primary `{name}.png` не сматчил, пробует
    `{name}_fhd.png` и нумерованные варианты — та же логика что в
    find_template (см. `_fhd_fallback_names`). Возвращает box первого
    варианта который прошёл порог."""
    conf = cfg.getfloat("matching", "confidence")
    timeout = cfg.getfloat("matching", "wait_seconds")
    tpl_path = TEMPLATES_DIR / f"{name}.png"
    if not tpl_path.exists():
        raise FileNotFoundError(f"шаблон {tpl_path} не читается")

    # Загружаем primary + все FHD-варианты заранее, чтобы не делать
    # imread() в цикле каждые 0.5с
    variants: list[tuple[str, np.ndarray]] = []
    primary_tpl = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
    if primary_tpl is not None:
        variants.append((name, primary_tpl))
    for fhd_name in _fhd_fallback_names(name):
        fhd_tpl = cv2.imread(str(TEMPLATES_DIR / f"{fhd_name}.png"), cv2.IMREAD_COLOR)
        if fhd_tpl is not None:
            variants.append((fhd_name, fhd_tpl))

    deadline = time.time() + timeout
    while time.time() < deadline:
        screen = grab_screen_bgr()
        if screen is None:
            time.sleep(0.5)
            continue
        # Пробуем каждый вариант: primary, потом _fhd, потом _fhd2 и т.д.
        for variant_name, variant_tpl in variants:
            res = cv2.matchTemplate(screen, variant_tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val >= conf:
                if variant_name != name:
                    log.info("match %s primary не прошёл, %s confidence=%.3f",
                             name, variant_name, max_val)
                else:
                    log.info("match %s: confidence=%.3f", name, max_val)
                vh, vw = variant_tpl.shape[:2]
                return (max_loc[0], max_loc[1], vw, vh)
        time.sleep(0.5)
    raise TimeoutError(f"элемент '{name}' не появился за {timeout}s")


def set_stepper(template_name: str, target: int, minimum: int, cfg: configparser.ConfigParser) -> None:
    """
    Stepper-поле: сначала кликаем '-' много раз (сброс к минимуму),
    затем '+' нужное число раз. Координаты +/- относительно box'а шаблона.
    """
    left, top, w, h = _find_template_box(template_name, cfg)
    plus_xf = cfg.getfloat("stepper_offsets", "plus_x")
    minus_xf = cfg.getfloat("stepper_offsets", "minus_x")
    yf = cfg.getfloat("stepper_offsets", "y_center")
    plus_pt = (int(left + w * plus_xf), int(top + h * yf))
    minus_pt = (int(left + w * minus_xf), int(top + h * yf))
    reset_n = cfg.getint("warmup", "reset_clicks")
    delta = max(0, target - minimum)

    log.info("stepper %s: reset to min via %d × '-' @%s", template_name, reset_n, minus_pt)
    _record_click(*minus_pt, f"{template_name}-")
    pyautogui.click(minus_pt[0], minus_pt[1], clicks=reset_n, interval=0.1)
    time.sleep(0.5)  # дать UI устаканиться после серии "-"

    log.info("stepper %s: increment %d × '+' @%s → target=%d", template_name, delta, plus_pt, target)
    _record_click(*plus_pt, f"{template_name}+")
    if delta > 0:
        pyautogui.click(plus_pt[0], plus_pt[1], clicks=delta, interval=0.15)
    time.sleep(cfg.getfloat("matching", "step_delay"))


def remove_first_lines_from_list(n: int, cfg: configparser.ConfigParser) -> None:
    """
    Удаляет первые n строк из URL-textarea в окне Warm up.
    Якоримся на browse_file_button, кликаем со сдвигом внутрь списка,
    Ctrl+Home → Shift+Down × n → Delete.
    """
    if n <= 0:
        return
    left, top, w, h = _find_template_box("browse_file_button", cfg)
    bx = left + w // 2
    by = top + h // 2
    dx = cfg.getint("url_list_offset", "dx_from_browse")
    dy = cfg.getint("url_list_offset", "dy_from_browse")
    click_x, click_y = bx + dx, by + dy

    log.info("removing first %d lines: click URL list @(%d,%d)", n, click_x, click_y)
    _record_click(click_x, click_y, "url_list")
    pyautogui.click(click_x, click_y)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "home")
    time.sleep(0.15)
    pyautogui.keyDown("shift")
    for _ in range(n):
        pyautogui.press("down")
        time.sleep(0.04)
    pyautogui.keyUp("shift")
    time.sleep(0.15)
    pyautogui.press("delete")
    time.sleep(cfg.getfloat("matching", "step_delay"))


def load_credentials() -> configparser.ConfigParser:
    """Читает credentials.ini рядом со скриптом."""
    creds = configparser.ConfigParser()
    path = ROOT / "credentials.ini"
    if not path.exists():
        raise FileNotFoundError(
            "credentials.ini не найден. "
            "Скопируй credentials.ini.example в credentials.ini и заполни данные."
        )
    creds.read(path, encoding="utf-8")
    return creds


def _read_success_count() -> int:
    try:
        return int(SUCCESS_STATE_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_success_count(n: int) -> None:
    try:
        SUCCESS_STATE_FILE.write_text(str(n), encoding="utf-8")
    except OSError as e:
        log.warning("не удалось записать %s: %s", SUCCESS_STATE_FILE, e)


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
    import urllib.request
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


# Эмодзи в Title по типу события (по первому тегу). Telegram-бридж НЕ
# подставляет эмодзи из ntfy-тегов, поэтому кладём их прямо в заголовок.
_TAG_EMOJI = {"white_check_mark": "✅", "tada": "🎉", "warning": "⚠️",
              "hourglass_flowing_sand": "⏳", "gear": "⚙️"}
_PRIORITY_NUM = {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5, "urgent": 5}


def notify_ntfy(message: str, title: str = "warmup failed (ui)",
                priority: str = "high", tags: str = "warning") -> None:
    """Шлёт push через ntfy.sh JSON-публикацией, без настройки на машине.
    Все ошибки (сети нет, сервис лежит, и т.д.) глотаются — нотификация
    никогда не должна мешать основному логированию.

    Дефолт priority/tags = high/warning рассчитан на сообщения о падении.
    Для успешных уведомлений вызывающий передаёт low/white_check_mark.

    JSON, а не HTTP-заголовки: в заголовки нельзя положить эмодзи (только
    ASCII), поэтому Title в Telegram приходил без иконки. В JSON-теле
    json.dumps экранирует Unicode в \\uXXXX — тело уходит чистым ASCII."""
    try:
        import urllib.request
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
        # отвечает <1с, но изредка TLS handshake забуксует — без retry'я
        # уведомление теряется навсегда. 3 попытки с timeout=30с и
        # backoff'ом 2с/5с покрывают любой разумный transient hiccup.
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                log.info("ntfy-уведомление отправлено (%s)", disp_title)
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


def _configure_clipboard_signatures() -> None:
    """Выставляет argtypes/restype для Win32 API.

    КРИТИЧНО на 64-битной Windows: без явных restype ctypes считает,
    что функция возвращает C `int` (32 бита), и для функций возвращающих
    HANDLE/HGLOBAL/LPVOID (которые на x64 имеют ширину 64 бита) ctypes
    обрезает верхние 32 бита. Указатель превращается в мусор/0 →
    memmove(ptr, …) → access violation."""
    if getattr(_configure_clipboard_signatures, "_done", False):
        return
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    u32.OpenClipboard.argtypes = [ctypes.c_void_p]
    u32.OpenClipboard.restype = ctypes.c_int
    u32.EmptyClipboard.restype = ctypes.c_int
    u32.CloseClipboard.restype = ctypes.c_int
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.GetClipboardData.argtypes = [ctypes.c_uint]
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.restype = ctypes.c_int
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.restype = ctypes.c_void_p
    _configure_clipboard_signatures._done = True


def _set_clipboard_win32(text: str) -> bool:
    """Кладёт текст в Windows clipboard через ctypes — без spawn'а PowerShell
    (раньше дочерний процесс мог стащить фокус с LS, и Ctrl+V улетал не туда)."""
    _configure_clipboard_signatures()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    GMEM_MOVEABLE = 0x0002
    CF_UNICODETEXT = 13
    buf = (text + "\0").encode("utf-16-le")
    # 5 попыток — clipboard может быть кем-то открыт (антивирус, другой процесс)
    for _ in range(5):
        if user32.OpenClipboard(0):
            break
        time.sleep(0.1)
    else:
        return False
    h = None
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buf))
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ptr, buf, len(buf))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        # после успешного SetClipboardData владелец памяти — система,
        # GlobalFree больше не зовём.
        h = None
        return True
    finally:
        user32.CloseClipboard()


def _get_clipboard_win32() -> str | None:
    """Читает текст из clipboard для проверки, что Set прошёл."""
    _configure_clipboard_signatures()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    if not user32.OpenClipboard(0):
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUT_UNION)]


def _send_unicode_to_focused(text: str) -> bool:
    """Шлёт строку через SendInput с KEYEVENTF_UNICODE — каждый символ
    как Unicode-код в текущее focused окно, минуя клавиатурную раскладку
    И минуя Ctrl+V (т.е. нет проблем с тем, что shortcut не докатывается
    до Electron-инпута). Работает на любой language pack Windows.

    ВАЖНО: символы летят в то окно, где сейчас keyboard focus. Перед
    вызовом надо убедиться, что нужный input в фокусе (клик + sleep).
    Возвращает True если SendInput отправил все коды, False если ОС
    отвергла часть."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int]
    user32.SendInput.restype = ctypes.c_uint

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    # Каждый символ — два события (KEYDOWN + KEYUP), wScan = код-поинт.
    # BMP-символы (≤ 0xFFFF) шлём как один scan. Если бы были эмодзи
    # (> 0xFFFF) — нужно было бы surrogate pair; в email/password их нет.
    events: list[_INPUT] = []
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            # surrogate pair
            code -= 0x10000
            high = 0xD800 | (code >> 10)
            low = 0xDC00 | (code & 0x3FF)
            chars = [high, low]
        else:
            chars = [code]
        for scan in chars:
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = _INPUT()
                inp.type = INPUT_KEYBOARD
                inp.u.ki.wVk = 0
                inp.u.ki.wScan = scan
                inp.u.ki.dwFlags = flags
                inp.u.ki.time = 0
                inp.u.ki.dwExtraInfo = None
                events.append(inp)

    arr = (_INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
    if sent != len(events):
        log.warning("SendInput отправил %d из %d событий", sent, len(events))
        return False
    return True


def _type_via_clipboard(text: str) -> None:
    """Вводит текст в текущий focused input через SendInput Unicode.

    Имя функции — историческое; clipboard БОЛЬШЕ НЕ ИСПОЛЬЗУЕТСЯ.

    Каждый символ летит как Unicode-код напрямую через Win32 SendInput
    в focused window: никакого Ctrl+V, нет зависимости от клавиатурной
    раскладки, нет зависимости от содержимого clipboard'а.

    Раньше параллельно писали текст в clipboard как safety net для
    ручного Ctrl+V — но наш pipeline unattended (оператор не сидит у
    экрана с пальцем на Ctrl+V), а clipboard sync в parallel mstsc
    устраивал race между N одновременными UI install'ами и перетирал
    локальный буфер оператора. Удалили — никакого регресса не вызвало
    (на всех успешных машинах работал именно SendInput, не safety net).

    Fallback на pyautogui.typewrite — тоже синтетические keystroke'и
    через Win32, без clipboard. Срабатывает только если SendInput
    отверг часть кодов (на практике не наблюдалось)."""
    if sys.platform != "win32":
        pyautogui.typewrite(text, interval=0.03)
        return
    if _send_unicode_to_focused(text):
        time.sleep(0.3)
        return
    log.warning("SendInput Unicode failed → fallback typewrite")
    pyautogui.typewrite(text, interval=0.03)
    time.sleep(0.2)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _find_window_by_title_substring(substring: str) -> int:
    """Возвращает hwnd видимого top-level окна, заголовок которого содержит подстроку."""
    import ctypes.wintypes
    found = [0]
    sub_lower = substring.lower()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd: int, _: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        if buf.value and sub_lower in buf.value.lower():
            found[0] = hwnd
            return False
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    return found[0]


def _find_window_by_any_title(substrings: list[str]) -> tuple[int, str]:
    """Возвращает (hwnd, matched_title) первого видимого top-level окна,
    заголовок которого содержит любую из подстрок. Регистронезависимо."""
    import ctypes.wintypes
    found: list[tuple[int, str]] = []
    subs_lower = [s.lower() for s in substrings]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd: int, _: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if not title:
            return True
        tl = title.lower()
        for sub in subs_lower:
            if sub in tl:
                found.append((hwnd, title))
                return False
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    return found[0] if found else (0, "")


def _log_visible_titles() -> None:
    """Логирует все видимые окна с непустым заголовком — для отладки."""
    import ctypes.wintypes
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd: int, _: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        if buf.value:
            titles.append(buf.value)
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    log.info("видимые окна (%d): %s", len(titles), titles)


# Кэш, чтобы не спамить лог при каждом тике цикла ожидания
_visible_titles_logged = False


def _click_allow_button_via_message(parent_hwnd: int) -> bool:
    """Найти кнопку 'Allow access' среди дочерних окон Firewall-попапа и
    кликнуть через SendMessage(BM_CLICK). Это синтезирует клик НЕ через
    очередь ввода, поэтому не зависит от:
      • того, какое окно сейчас в фокусе (Alt+A улетал в LS-окно),
      • раскладки клавиатуры (русская/китайская тоже сработают),
      • DPI / RDP-скейлинга (без координат вообще).
    BM_CLICK = 0x00F5 — стандартное сообщение Windows для эмуляции клика
    по button-control'у. Адресуется конкретному child-hwnd, не глобально."""
    if sys.platform != "win32":
        return False

    user32 = ctypes.windll.user32
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.EnumChildWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

    candidates = ("allow access", "разрешить доступ")
    found = [None]

    ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        text = (buf.value or "").lower()
        for c in candidates:
            if c in text:
                found[0] = hwnd
                return False  # stop enum
        return True

    user32.EnumChildWindows(parent_hwnd, ENUM_PROC(_cb), 0)

    if found[0] is None:
        return False

    BM_CLICK = 0x00F5
    user32.SendMessageW(found[0], BM_CLICK, 0, 0)
    log.info("Firewall: BM_CLICK → Allow button hwnd=%s", found[0])
    return True


def _dismiss_firewall_alert() -> bool:
    """При первом запуске LS Windows может показать Defender Firewall Alert.
    Сначала пытается найти кнопку 'Allow access' по шаблону, иначе ищет окно
    по заголовку и кликает по координатам + Alt+A. Возвращает True если
    что-то закрыли."""
    global _visible_titles_logged
    if sys.platform != "win32":
        return False

    # 1) Шаблон allow_access.png — самый надёжный способ, не зависит от локали.
    # Заворачиваем в try, потому что ImageGrab.grab() падает с 'screen grab
    # failed' если RDP-сессия отключена. В этом случае проваливаемся на
    # BM_CLICK (он использует Win32 SendMessage, screen grab не нужен).
    try:
        if _click_allow_access_template():
            return True
    except OSError as e:
        log.warning("template-match для firewall упал (%s) — fallback на BM_CLICK", e)

    # 2) Заголовок зависит от локали Windows — пробуем все известные варианты
    titles = [
        "Windows Security Alert",
        "Windows Defender Firewall",
        "Брандмауэр",
        "Безопасность Windows",
        "Оповещение системы безопасности",
    ]
    hwnd, matched = _find_window_by_any_title(titles)
    if not hwnd:
        if not _visible_titles_logged:
            _log_visible_titles()
            _visible_titles_logged = True
        return False

    log.info("Firewall Alert hwnd=%d title=%r", hwnd, matched)
    user32 = ctypes.windll.user32

    # 3) BM_CLICK через SendMessage — самый надёжный способ, работает
    # независимо от фокуса и раскладки. На Win11 24H2/Server 2025 Alt+A
    # часто улетал в LS-окно (которое перехватывало фокус), а BM_CLICK
    # адресуется кнопке напрямую и не идёт через input queue.
    if _click_allow_button_via_message(hwnd):
        time.sleep(0.6)
        if not user32.IsWindow(hwnd):
            log.info("Firewall popup закрыт через BM_CLICK")
            return True
        log.warning("BM_CLICK отправлен, но окно ещё живо — fallback на Alt+A")

    def _force_foreground(target_hwnd: int) -> bool:
        """Возвращает True если окно ещё живо и попытка поднять отработала.
        Если окно закрылось (например, мы успешно его дисмиссили) — False,
        и дальше отправлять keystroke смысла нет."""
        if not user32.IsWindow(target_hwnd):
            return False
        user32.ShowWindow(target_hwnd, 9)   # SW_RESTORE
        user32.BringWindowToTop(target_hwnd)
        user32.SetForegroundWindow(target_hwnd)
        return True

    if not _force_foreground(hwnd):
        return False
    time.sleep(0.5)

    # 4) Alt+A — Windows-конвенция: 'A' подчёркнуто в 'Allow access', и
    # клавиша срабатывает на любом разрешении/DPI/языке UI (если только
    # язык не китайский с другими mnemonic). Fallback на случай если
    # BM_CLICK по какой-то причине не нашёл кнопку.
    log.info("Firewall dismiss: Alt+A")
    pyautogui.hotkey("alt", "a")
    time.sleep(1.0)

    if user32.IsWindow(hwnd):
        log.warning("Firewall popup ещё жив после Alt+A — возможно нестандартный диалог")
    else:
        log.info("Firewall popup закрыт")
    return True


def _multiscale_match_in_region(
    name: str, region: np.ndarray, region_w: int, region_h: int,
    left: int, top: int, confidence: float,
    scales: tuple[float, ...],
) -> tuple[int, int] | None:
    """Внутренний хелпер: multiscale match одного template'а в готовом region.
    Возвращает абсолютные координаты центра или None."""
    tpl_path = TEMPLATES_DIR / f"{name}.png"
    tpl_orig = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
    if tpl_orig is None:
        return None

    best_val = 0.0
    best_loc = (0, 0)
    best_size = (0, 0)
    best_scale = 1.0
    orig_h, orig_w = tpl_orig.shape[:2]
    for s in scales:
        nw, nh = max(1, int(orig_w * s)), max(1, int(orig_h * s))
        if nw >= region_w or nh >= region_h:
            continue
        tpl = cv2.resize(tpl_orig, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
        _, val, _, loc = cv2.minMaxLoc(res)
        if val > best_val:
            best_val = val
            best_loc = loc
            best_size = (nw, nh)
            best_scale = s

    log.info("match %s multiscale best=%.3f scale=%.2f @local(%d,%d) size=%dx%d",
             name, best_val, best_scale, best_loc[0], best_loc[1], *best_size)
    if best_val < confidence or best_size == (0, 0):
        return None
    return (left + best_loc[0] + best_size[0] // 2,
            top + best_loc[1] + best_size[1] // 2)


def _match_template_in_region(
    name: str, confidence: float,
    left: int, top: int, right: int, bottom: int,
    scales: tuple[float, ...] = (0.75, 0.85, 0.95, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0),
) -> tuple[int, int] | None:
    """Template-matching внутри прямоугольника экрана с перебором масштабов.
    cv2 не делает scale-invariant matching, поэтому если шаблон сохранён при
    одном DPI, а ищем при другом — нужно подставить размер. Возвращает
    абсолютные координаты центра лучшего матча, либо None.

    FHD-fallback: если primary `{name}.png` не сматчил, пробует
    `{name}_fhd.png` (нарезка с VPS где LS рендерит UI в режиме 0.75x).
    Та же логика что в find_template — transparent для всех call sites.
    Region-ограничение для FHD-варианта сохраняется такое же."""
    tpl_path = TEMPLATES_DIR / f"{name}.png"
    if not tpl_path.exists():
        log.error("шаблон %s не читается", tpl_path)
        return None
    region_w = right - left
    region_h = bottom - top
    if region_w <= 0 or region_h <= 0:
        return None
    try:
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
    except OSError as e:
        # RDP отключена → нет attached desktop'а → ImageGrab падает.
        # Возвращаем None как «шаблон не найден» — caller (poll-loop)
        # перевызовет нас в следующей итерации и при reconnect RDP
        # template-matching возобновится без перезапуска warmup.py.
        log.warning("_match_template_in_region(%s): %s — пропуск", name, e)
        return None
    region = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    pt = _multiscale_match_in_region(
        name, region, region_w, region_h, left, top, confidence, scales)
    if pt is not None:
        return pt
    # Fallback: пробуем FHD-варианты (включая нумерованные альтернативы)
    for fhd_name in _fhd_fallback_names(name):
        log.info("match %s primary не прошёл, пробую %s в том же region",
                 name, fhd_name)
        pt = _multiscale_match_in_region(
            fhd_name, region, region_w, region_h, left, top, confidence, scales)
        if pt is not None:
            return pt
    return None


def _click_allow_access_template() -> bool:
    """Ищет шаблоны кнопок 'Allow' в Windows Defender Firewall Alert на всём
    экране. Версии Windows отличаются — кнопка может называться
    'Allow access' (со щитом UAC) или просто 'Allow' (Win11-style).
    Перебирает шаблоны по очереди:
      - allow_access   — Win10 стиль (с щитом UAC, текст "Allow access")
      - allow_access2  — Win11 21H2/22H2 стиль (без щита, текст "Allow access")
      - allow_access3  — Win11/Server 2022 24H2+ minimal стиль (просто "Allow",
                         без щита, без фона — встречается на свежих образах)
    Возвращает True если что-то кликнули."""
    if sys.platform != "win32":
        return False

    import ctypes.wintypes
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)

    for tpl_name in ("allow_access", "allow_access2", "allow_access3"):
        if not (TEMPLATES_DIR / f"{tpl_name}.png").exists():
            continue
        # Понижаем порог до 0.70 СПЕЦИАЛЬНО для allow-кнопок: шаблоны 180×53
        # и 249×60 в multi-scale матчинге часто дают best ~0.65-0.72 из-за
        # DPI/UAC-shield render. 0.80 был слишком строгий, ловили миссы.
        pt = _match_template_in_region(
            tpl_name, 0.70,
            0, 0, screen_w, screen_h,
        )
        if pt is None:
            continue
        log.info("%s кнопка найдена @(%d,%d)", tpl_name, *pt)
        _record_click(pt[0], pt[1], tpl_name)
        pyautogui.click(pt[0], pt[1])
        time.sleep(1.0)
        return True
    return False


def _wait_for_firewall_alert(seconds: float, exit_after_close: bool = False) -> bool:
    """Активно следит `seconds` секунд за появлением Windows Firewall Alert.
    Жмёт 'Allow access' двумя способами: сначала шаблон allow_access.png,
    потом fallback на window-title + Alt+A. NEXT STEP в этот момент НЕ жмём.
    Если exit_after_close=True — выходит сразу после первого успешного
    закрытия (плюс 3с grace на случай повторного диалога).
    Возвращает True если закрыл хоть один alert."""
    log.info("ждём до %.0fс появления firewall alert (NEXT STEP пока НЕ жмём)…", seconds)
    deadline = time.time() + seconds
    closed_any = False
    grace_deadline: float | None = None
    while time.time() < deadline:
        if _click_allow_access_template():
            closed_any = True
            if exit_after_close:
                grace_deadline = time.time() + 3.0
            continue
        if _dismiss_firewall_alert():
            closed_any = True
            if exit_after_close:
                grace_deadline = time.time() + 3.0
            continue
        if grace_deadline is not None and time.time() > grace_deadline:
            log.info("firewall alert закрыт, выходим из ожидания пораньше")
            return True
        time.sleep(0.5)
    if closed_any:
        log.info("firewall alert закрыт")
    else:
        log.info("firewall alert не появился за отведённое время")
    return closed_any


def _find_ls_window() -> int:
    """Находит главное окно Linken Sphere (заголовок 'Linken Sphere',
    крупный размер, на экране). Wizard первого запуска показывается ВНУТРИ
    этого же окна, отдельного top-level окна у мастера нет."""
    if sys.platform != "win32":
        return 0
    import ctypes.wintypes
    candidates: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd: int, _: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if title and "linken" in title.lower() and "sphere" in title.lower():
            candidates.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    for hwnd in candidates:
        rect = _RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w >= 600 and h >= 400 and rect.left > -1000 and rect.top > -1000:
            return hwnd
    return 0


# Состояние для close_x: ищем крестик ✕ только в течение этого окна
# после успешного клика на SKIP. Иначе шаблон ложно матчит close-кнопку
# самой LS на повторных запусках, когда попапа со скипом уже нет.
_skip_clicked_at: float = 0.0
_CLOSE_X_GRACE_AFTER_SKIP = 30.0


def _dismiss_customize_wizard_step() -> bool:
    """При первом запуске LS показывает мастер настройки ВНУТРИ окна Linken Sphere
    (отдельного top-level окна у мастера нет). Ищем кнопки внутри окна LS —
    если есть, кликаем. Шаблоны по очереди:
      - next_step       — первые две страницы wizard
      - get_started     — последняя страница wizard ('Get Started >')
      - get_started2    — приветственный экран после логина
                          ('Welcome to Linken Sphere 2', другой фон)
      - get_started2_v2 — та же кнопка, но в новой версии LS (шрифт/рамка
                          рендерятся иначе, старый шаблон даёт ~0.79)
      - skip            — иногда всплывающее окно с малозаметной кнопкой SKIP
      - close_x         — финальный мелкий крестик ✕ на следующем после skip окне;
                          ищется ТОЛЬКО в течение 30с после клика на skip,
                          чтобы не зацепить close-кнопку самого LS
    Когда все исчезнут — функция вернёт False, поток пойдёт дальше.

    Если в C:\\warmup\\ лежит флаг .wizards_done — мгновенно возвращаем False
    без single PNG-скана. Это для машин где LS уже был запущен раньше и
    мастер первого запуска / tour / welcome / close_x уже пройдены вручную
    или предыдущим прогоном. Ставится флаг через quickstart.ps1 один раз,
    дальше переживает любой freshstart."""
    global _skip_clicked_at
    if (ROOT / ".wizards_done").exists():
        return False
    if sys.platform != "win32":
        return False

    hwnd = _find_ls_window()
    if not hwnd:
        return False

    rect = _RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    h = rect.bottom - rect.top
    lower_half_top = rect.top + h // 2

    # (имя шаблона, top границы поиска, confidence)
    # get_started2_v2 — вариант кнопки welcome-экрана для новой версии LS
    # (другое сглаживание шрифта/рамки даёт ~0.79 со старым шаблоном).
    candidates = [
        ("next_step",       lower_half_top, 0.80),
        ("get_started",     lower_half_top, 0.80),
        ("get_started2",    lower_half_top, 0.80),
        ("get_started2_v2", lower_half_top, 0.80),
        ("skip",            rect.top,       0.76),
    ]
    # close_x активен только в окне 30с после успешного клика на skip
    if time.time() - _skip_clicked_at < _CLOSE_X_GRACE_AFTER_SKIP:
        candidates.append(("close_x", rect.top, 0.90))

    for tpl_name, search_top, conf in candidates:
        if not (TEMPLATES_DIR / f"{tpl_name}.png").exists():
            continue

        # close_x опасен: его шаблон визуально близок к close-кнопке самой LS
        # в правом верхнем углу окна. Исключаем верхнюю title-bar полосу и
        # правые 100px (где сидит min/max/close самого LS).
        if tpl_name == "close_x":
            search_left = rect.left
            search_top_eff = rect.top + 50
            search_right = rect.right - 100
        else:
            search_left = rect.left
            search_top_eff = search_top
            search_right = rect.right

        pt = _match_template_in_region(
            tpl_name, conf,
            search_left, search_top_eff, search_right, rect.bottom,
        )
        if pt is not None:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            log.info("wizard: %s найден в окне LS @(%d,%d)", tpl_name.upper(), *pt)
            _record_click(pt[0], pt[1], tpl_name)
            pyautogui.click(pt[0], pt[1])
            if tpl_name == "skip":
                _skip_clicked_at = time.time()
            time.sleep(1.5)
            return True
    return False


def _enable_autologin_toggle(rect_top: int, rect_left: int,
                             rect_right: int, rect_bottom: int,
                             confidence: float) -> bool:
    """Кликает по Autologin toggle в форме логина LS, если он в OFF-состоянии.
    Шаблон templates/autologin_toggle.png — это OFF (тёмная плашка, белый
    кружок слева). Если match не находится — toggle уже включён (или версия
    LS без toggle), не трогаем, чтобы случайно не выключить уже включённый.

    Включённый toggle = LS закэширует credentials → после reboot и Startup
    folder relaunch LS залогинится сама без участия warmup.py UI."""
    if sys.platform != "win32":
        return False
    if not (TEMPLATES_DIR / "autologin_toggle.png").exists():
        log.info("autologin_toggle.png отсутствует — шаг пропущен")
        return False
    # Toggle сидит между password (52%) и SIGN IN (68%), типично на ~58-62%
    # высоты окна. Захватываем чуть шире чтобы быть устойчивым к разным
    # версиям LS-формы.
    h = rect_bottom - rect_top
    search_top    = rect_top + int(h * 0.50)
    search_bottom = rect_top + int(h * 0.72)
    pt = _match_template_in_region(
        "autologin_toggle", confidence,
        rect_left, search_top, rect_right, search_bottom,
    )
    if pt is None:
        log.info("autologin_toggle (OFF) не найден — вероятно уже ON, пропуск")
        return False
    log.info("autologin_toggle (OFF) найден @(%d,%d) — кликаю чтобы включить", *pt)
    _record_click(pt[0], pt[1], "autologin_toggle")
    pyautogui.click(pt[0], pt[1])
    time.sleep(0.5)
    return True


def _find_auth_window() -> int:
    """Ищет окно Linken Sphere 2 с формой входа. Разворачивает если свёрнуто.
    Пропускает мелкие/служебные окна Electron с тем же заголовком."""
    import ctypes.wintypes

    candidates: list[int] = []
    all_titles: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd: int, _: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if not title:
            return True
        all_titles.append(title)
        tl = title.lower()
        if "authentication" in tl or ("linken" in tl and "sphere" in tl):
            candidates.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    log.info("видимые окна: %s", all_titles)
    log.info("кандидатов на окно входа: %d", len(candidates))

    SW_RESTORE = 9
    for hwnd in candidates:
        if ctypes.windll.user32.IsIconic(hwnd):
            log.info("hwnd=%d свёрнуто, разворачиваю", hwnd)
            ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.6)

        rect = _RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        log.info("hwnd=%d %dx%d @ (%d,%d)", hwnd, w, h, rect.left, rect.top)

        # Окно формы логина должно быть крупным и на экране
        if w >= 600 and h >= 400 and rect.left > -1000 and rect.top > -1000:
            return hwnd

    if candidates:
        log.warning("найдены окна Linken Sphere, но ни одно не похоже на форму входа (мелкие/за экраном)")
    return 0


def login_if_needed(cfg: configparser.ConfigParser) -> None:
    """Если видна форма аутентификации — входим по данным из credentials.ini."""
    conf = cfg.getfloat("matching", "confidence")

    if find_template("three_dots", conf) is not None:
        log.info("login_if_needed: уже на главном экране")
        return

    if sys.platform != "win32":
        return

    hwnd = _find_auth_window()
    if not hwnd:
        log.info("login_if_needed: окно аутентификации не найдено — пропуск")
        return

    log.info("login_if_needed: обнаружен экран входа (hwnd=%d)", hwnd)

    # КРИТИЧНО: до клика по форме входа закрыть всё модальное, что может быть
    # поверх LS (Windows Defender Firewall Alert, мастер Customize). Иначе
    # клики по координатам логин-формы попадут в этот диалог, а не в LS.
    for _ in range(10):
        did = False
        if _dismiss_firewall_alert():
            did = True
        if _dismiss_customize_wizard_step():
            did = True
        if not did:
            break

    screenshot("login_screen", cfg.getboolean("logging", "screenshots"))

    rect = _RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    win_w = rect.right - rect.left
    win_h = rect.bottom - rect.top
    cx = rect.left + win_w // 2

    if win_w < 600 or win_h < 400 or rect.left < -1000 or rect.top < -1000:
        log.error("окно %dx%d @ (%d,%d) — невалидное (за экраном/мелкое), отменяю логин",
                  win_w, win_h, rect.left, rect.top)
        return

    # Пропорции замерены по скриншоту: Email=43%, Password=52%, SIGN IN=68% от высоты
    email_y    = rect.top + int(win_h * 0.434)
    password_y = rect.top + int(win_h * 0.521)
    signin_y   = rect.top + int(win_h * 0.678)

    log.info("окно %dx%d @ (%d,%d)", win_w, win_h, rect.left, rect.top)
    log.info("email=(%d,%d) password=(%d,%d) signin=(%d,%d)",
             cx, email_y, cx, password_y, cx, signin_y)

    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)

    creds = load_credentials()

    _record_click(cx, email_y, "email")
    pyautogui.click(cx, email_y)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    _type_via_clipboard(creds.get("account", "email"))

    _record_click(cx, password_y, "password")
    pyautogui.click(cx, password_y)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    _type_via_clipboard(creds.get("account", "password"))

    # Включаем Autologin toggle ДО нажатия SIGN IN. Если уже включён —
    # шаблон не найдётся и шаг тихо пропускается (safety: не выключаем
    # уже включённый). При следующем reboot LS поднимется через Startup
    # folder и сама залогинится из кэша.
    _enable_autologin_toggle(rect.top, rect.left, rect.right, rect.bottom, conf)

    screenshot("login_filled", cfg.getboolean("logging", "screenshots"))

    _record_click(cx, signin_y, "sign_in")
    pyautogui.click(cx, signin_y)
    log.info("нажат SIGN IN, жду главный экран…")
    time.sleep(5.0)  # сетевая сторона signin на слабом VPS отзывается с лагом

    # После SIGN IN ждём three_dots, но параллельно чистим всплывающие диалоги
    timeout = cfg.getfloat("startup", "launch_wait_seconds")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _dismiss_firewall_alert():
            continue
        if _dismiss_customize_wizard_step():
            continue
        if find_template("three_dots", conf) is not None:
            log.info("вход выполнен, главный экран готов")
            time.sleep(1.0)
            return
        time.sleep(0.5)
    raise TimeoutError(f"three_dots не появился за {timeout}s после SIGN IN")


def ensure_linken_sphere_running(cfg: configparser.ConfigParser) -> None:
    """
    Если главный экран Linken Sphere 2 (по three_dots.png) уже виден — ничего не делаем.
    Если уже на экране входа — тоже не перезапускаем (login_if_needed сделает вход).
    Иначе мягко завершаем старые процессы, запускаем через ShellExecuteW и ждём.
    """
    conf = cfg.getfloat("matching", "confidence")

    # Первые 3 секунды: ищем three_dots, но параллельно чистим firewall alert
    # и мастер первого запуска — они могут всплыть с задержкой после старта warmup.
    quick_deadline = time.time() + 3.0
    found_main = False
    while time.time() < quick_deadline:
        if _dismiss_firewall_alert():
            continue
        if _dismiss_customize_wizard_step():
            continue
        if find_template("three_dots", conf) is not None:
            found_main = True
            break
        time.sleep(0.4)
    if found_main:
        log.info("Linken Sphere 2 уже на главном экране")
        return

    # Уже показывает экран входа — не убиваем, просто дадим login_if_needed войти
    if sys.platform == "win32" and _find_auth_window():
        log.info("Linken Sphere 2 уже запущен (экран входа), не перезапускаем")
        return

    # Уже висит мастер первого запуска — пройдём его и продолжим
    if sys.platform == "win32" and _find_window_by_title_substring("Customize your experience"):
        log.info("Linken Sphere 2 уже запущен (мастер первого запуска), прокликиваю")
        deadline_wiz = time.time() + 60.0
        while time.time() < deadline_wiz:
            if _dismiss_firewall_alert():
                continue
            if _dismiss_customize_wizard_step():
                continue
            if find_template("three_dots", conf) is not None:
                log.info("после мастера — главный экран")
                return
            if _find_auth_window():
                log.info("после мастера — экран входа")
                return
            time.sleep(0.5)

    path = cfg.get("startup", "linken_sphere_path")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"не найден путь к Linken Sphere 2: {path}. "
            "поправь startup.linken_sphere_path в config.ini"
        )

    exe_name = os.path.basename(path)

    # Мягкое закрытие (WM_CLOSE)
    # errors="replace" — taskkill пишет в OEM-codepage (cp866 на RU Windows),
    # Python text=True декодит через ANSI cp1251 → UnicodeDecodeError на
    # некоторых байтах. Заменяем неудачные байты, не падаем.
    r = subprocess.run(["taskkill", "/im", exe_name], capture_output=True, text=True, errors="replace")
    log.info("taskkill soft: stdout=%r stderr=%r", r.stdout.strip(), r.stderr.strip())
    time.sleep(3.0)

    # Принудительное завершение остатков
    r = subprocess.run(["taskkill", "/f", "/im", exe_name, "/t"], capture_output=True, text=True, errors="replace")
    log.info("taskkill force: stdout=%r stderr=%r", r.stdout.strip(), r.stderr.strip())
    time.sleep(3.0)

    exe_dir = os.path.dirname(path)
    log.info("запускаю через ShellExecuteW: %s (cwd=%s)", path, exe_dir)

    # ShellExecuteW = точно как двойной клик в Проводнике
    ret = ctypes.windll.shell32.ShellExecuteW(None, "open", path, None, exe_dir, 1)
    log.info("ShellExecuteW → %d (%s)", ret, "OK" if ret > 32 else "ОШИБКА")
    if ret <= 32:
        log.warning("ShellExecuteW вернул ошибку %d, запускаю через Popen", ret)
        subprocess.Popen(
            [path],
            cwd=exe_dir,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    log.info("жду инициализацию Electron (5s базовая пауза)…")
    time.sleep(5.0)

    # Первые 10 секунд после старта LS — следим ТОЛЬКО за firewall alert
    # (Windows Defender Firewall). NEXT STEP в это время НЕ жмём, чтобы
    # системный диалог успел всплыть и был обработан без перекрытия кликами.
    _wait_for_firewall_alert(10.0)

    timeout = cfg.getfloat("startup", "launch_wait_seconds")
    log.info("жду главный экран или экран входа (до %.0fs)…", timeout)
    deadline_ls = time.time() + timeout
    found_screen = None
    while time.time() < deadline_ls:
        # При первом запуске LS на чистой машине могут всплыть диалоги —
        # закрываем их прежде, чем смотреть на основные экраны.
        if _dismiss_firewall_alert():
            continue
        if _dismiss_customize_wizard_step():
            continue
        if find_template("three_dots", conf) is not None:
            found_screen = "main"
            break
        if sys.platform == "win32" and _find_auth_window():
            found_screen = "login"
            break
        time.sleep(0.5)
    if found_screen is None:
        raise TimeoutError("Linken Sphere не показал экран за отведённое время")
    log.info("Linken Sphere готов (экран: %s)", found_screen)
    time.sleep(1.0)


def _files_dir(cfg: configparser.ConfigParser) -> str:
    """Резолвит paths.files_dir: абсолютный путь возвращает как есть, относительный — относительно ROOT."""
    p = cfg.get("paths", "files_dir")
    if not os.path.isabs(p):
        p = str(ROOT / p)
    return p


def pick_random_file(cfg: configparser.ConfigParser) -> str:
    """Сэмплит ~100 случайных URL из [api] url_pool_file (40k_all_urls.txt)
    и материализует временный файл urls_generated/manual_<ts>.txt — он
    привязывается к UI warmup через Browse File. Тот же источник, что
    использует warmup_api.py, симметрия между ручным и автоматическим
    флоу."""
    pool_rel = cfg.get("api", "url_pool_file", fallback="urls/40k_all_urls.txt")
    pool_path = ROOT / pool_rel
    if not pool_path.exists():
        raise FileNotFoundError(f"URL-пул не найден: {pool_path}")
    pool: list[str] = []
    seen: set[str] = set()
    for line in pool_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        u = line.strip()
        if u and u not in seen:
            seen.add(u)
            pool.append(u)
    if not pool:
        raise RuntimeError(f"в {pool_path} нет ни одного URL")
    n_min = cfg.getint("api", "urls_per_run_min", fallback=95)
    n_max = cfg.getint("api", "urls_per_run_max", fallback=105)
    n = min(random.randint(n_min, n_max), len(pool))
    urls = random.sample(pool, n)

    out_dir = ROOT / "urls_generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"manual_{ts}.txt"
    out_file.write_text("\n".join(urls) + "\n", encoding="utf-8")
    log.info("выбрано %d URL из пула %d → %s", n, len(pool), out_file)
    return str(out_file)


def handle_open_file_dialog(file_path: str, cfg: configparser.ConfigParser) -> None:
    """
    Нативный Windows-диалог открытия файла.
    Самый надёжный способ — вставить полный путь в поле "Имя файла" и нажать Enter.

    На слабых VPS (2 ядра, медленный диск) диалог может отрисовываться
    10+ секунд. Поллим появление top-level окна с типичным заголовком
    Open File-dialog (EN/RU) до 30 секунд. Когда окно найдено — даём
    ещё 1.5с на полную отрисовку поля File name, чтобы Alt+N точно
    попал по нужному инпуту, а не в недорисованную форму.
    """
    # 1) Ждём появления окна диалога (заголовок 'Open' / 'Открытие') до 30с
    dialog_substrings = ["open", "открытие", "открыть"]
    poll_deadline = time.time() + 30.0
    t_start = time.time()
    dialog_hwnd = 0
    while time.time() < poll_deadline:
        for sub in dialog_substrings:
            hwnd = _find_window_by_title_substring(sub)
            if hwnd:
                dialog_hwnd = hwnd
                break
        if dialog_hwnd:
            break
        time.sleep(0.4)
    if dialog_hwnd:
        log.info("open-dialog hwnd=%d найден за %.1fс", dialog_hwnd, time.time() - t_start)
        # Дать диалогу полностью отрисовать поле File name
        time.sleep(1.5)
    else:
        # На некоторых сборках Windows заголовок может быть локализованным
        # неожиданно — не падаем, идём вслепую с большим запасом времени.
        log.warning("open-dialog не найден по заголовку за 30с — продолжаем с генерируемым sleep")
        time.sleep(5.0)

    screenshot("06_open_dialog", cfg.getboolean("logging", "screenshots"))
    # focus на поле "File name" — стандартно открыто по умолчанию
    pyautogui.hotkey("alt", "n")  # Alt+N → File name (en)
    time.sleep(1.0)  # было 0.5 — даём фокусу осесть на поле
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    time.sleep(0.3)
    # ТРЁХСЛОЙНАЯ ЗАЩИТА для надёжной передачи пути в IFileDialog
    # (наблюдалось failure на 172.86.109.145: только "C" попадал в поле,
    # диалог жаловался "C - File not found", т.к. Windows воспринимал
    # символ ":" как навигационную команду на диск C:\, сбрасывал поле
    # и остальные символы шли в file list navigation):
    #
    # СЛОЙ 1: ДВОЙНЫЕ КАВЫЧКИ вокруг пути. Tell file dialog "это литерал
    # имени файла, не навигация". Работает в большинстве классических
    # Open dialog Windows.
    #
    # СЛОЙ 2: TYPE CHAR-BY-CHAR с 120мс паузой. На медленных VPS batch
    # SendInput (всё за один call) может конфликтовать с async-обработкой
    # navigation символов в IFileDialog: пока dialog решает что делать с
    # ":", остальные символы могут перехватываться address bar или auto-
    # complete. Per-char timing даёт dialog ГАРАНТИРОВАННОЕ время полностью
    # обработать каждый символ до следующего (120мс с большим запасом для
    # 2c/4gb VPS с лагающим Electron-renderer в LS).
    #
    # СЛОЙ 3: SETTLE PAUSE 7 секунд после ввода ДО нажатия Enter.
    # Даёт Windows полностью обработать всю строку, autocomplete dropdown
    # появиться/исчезнуть, любые async navigation hints отработать.
    # Только когда dialog в spokojnom финальном состоянии — жмём Enter.
    quoted_path = f'"{file_path}"'
    log.info("вводим путь (%d символов) посимвольно с 120мс паузой (~%.1fс)",
             len(quoted_path), len(quoted_path) * 0.12)
    for ch in quoted_path:
        _send_unicode_to_focused(ch)
        time.sleep(0.12)
    log.info("ждём 7с — даём Windows полностью settled state перед Enter")
    time.sleep(7.0)
    pyautogui.press("enter")
    time.sleep(cfg.getfloat("matching", "step_delay"))


def _generate_session_name() -> str:
    """CL-XXXXXXXX, 8 цифр. 10^8 возможных значений — для пары сотен VPS
    вероятность пересечения <0.001%."""
    return f"CL-{random.randint(10_000_000, 99_999_999)}"


def load_session_name() -> str:
    """Имя сессии этой машины. Источники, по приоритету:
      1. .session_name (gitignored) — главный источник, генерируется автоматом
         на первом запуске и больше не меняется.
      2. .session_imported (старый формат «только имя») — миграция со старых
         инсталляций, где имя жило там.
      3. credentials.ini [session] name — миграция со ещё более старого
         формата (когда юзер вписывал имя руками).
      4. Если ничего нет — генерим новое CL-XXXXXXXX и пишем в .session_name.
    Всегда возвращает непустую строку."""
    if SESSION_NAME_FILE.exists():
        name = SESSION_NAME_FILE.read_text(encoding="utf-8").strip()
        if name:
            return name
    if SESSION_IMPORTED_FLAG.exists():
        legacy = SESSION_IMPORTED_FLAG.read_text(encoding="utf-8").strip()
        if legacy and "\t" not in legacy:
            log.info("миграция: имя сессии %r из .session_imported → .session_name", legacy)
            SESSION_NAME_FILE.write_text(legacy, encoding="utf-8")
            return legacy
    try:
        creds = load_credentials()
        if creds.has_section("session"):
            legacy = creds.get("session", "name", fallback="").strip()
            if legacy and legacy.lower() not in ("session name", "your session name", "yourname"):
                log.info("миграция: имя сессии %r из credentials.ini → .session_name", legacy)
                SESSION_NAME_FILE.write_text(legacy, encoding="utf-8")
                return legacy
    except FileNotFoundError:
        pass
    name = _generate_session_name()
    SESSION_NAME_FILE.write_text(name, encoding="utf-8")
    log.info("сгенерировано новое имя сессии: %s", name)
    return name


def prepare_session_xlsx(session_name: str) -> Path:
    """Если session_imports/<name>.xlsx уже есть — возвращаем путь.
    Иначе генерим из session_imports/_template.xlsx с рандомным fingerprint
    (см. session_template.build_session_xlsx). Возвращаем путь к готовому файлу."""
    target = SESSION_IMPORTS_DIR / f"{session_name}.xlsx"
    if target.exists():
        return target
    template = SESSION_IMPORTS_DIR / "_template.xlsx"
    if not template.exists():
        raise FileNotFoundError(
            f"не найден шаблон импорта сессии: {template}. "
            f"Должен лежать в репозитории."
        )
    from session_template import build_session_xlsx
    build_session_xlsx(template, target, session_name)
    log.info("создан xlsx сессии: %s (A3=%s)", target, session_name)
    return target


def _click_import_browse_file(cfg: configparser.ConfigParser) -> None:
    """В окне Mass creation две одинаковые кнопки BROWSE FILE: левая — cookies
    (TXT/JSON), правая — XLSX/CSV. Нам нужна ПРАВАЯ, поэтому ищем шаблон только
    в правой половине экрана."""
    conf = cfg.getfloat("matching", "confidence")
    timeout = cfg.getfloat("matching", "wait_seconds")
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    deadline = time.time() + timeout
    while time.time() < deadline:
        pt = _match_template_in_region("browse_file", conf, sw // 2, 0, sw, sh)
        if pt is not None:
            log.info("BROWSE FILE (правая) @(%d,%d)", *pt)
            _record_click(pt[0], pt[1], "browse_file")
            pyautogui.click(pt[0], pt[1])
            time.sleep(cfg.getfloat("matching", "step_delay"))
            return
        time.sleep(0.5)
    raise TimeoutError("BROWSE FILE (правая, XLSX) не найдена в правой половине экрана")


def _check_api_port_alive(base_url: str) -> bool:
    """Пинг локального LS API — пробуем GET /sessions. 4xx/5xx ок (порт жив),
    сеть/таймаут → порт не открыт."""
    import urllib.request, urllib.error
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/sessions", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


# Расписание поллинга /sessions ПОСЛЕ Mass Import dialog'а в warmup.py.
# Между моментом "warmup.py видит закрытие диалога Importing sessions" и
# моментом "LS API /sessions реально отдаёт новую сессию" — может пройти
# от 1с до нескольких минут (наблюдалось ≥22с на .81 и .229: warmup.py
# сразу спавнил warmup_api, тот через секунду стучался в /sessions, не
# находил → ⚠️ false-error push рядом с ✅ success push). LS-cloud
# синхронизация после Mass Import не атомарна: dialog закрывается раньше
# чем sessions catalog становится consistent через API.
#
# Поллим с прогрессивными интервалами (cumulative ~7.5 мин) перед тем как
# спавнить warmup_api. Если нашлась — спавним нормально. Если не нашлась
# за все попытки — отдельный ⚠️ push с понятной причиной, warmup_api НЕ
# спавним. Следующий 45-мин tick (LinkenSphereWarmup → run_api.bat →
# warmup_api по флагу .session_imported) подхватит сам.
_POST_IMPORT_POLL_SCHEDULE = [10, 30, 60, 120, 240]


def _session_visible_in_catalog(base_url: str, session_name: str) -> bool:
    """GET /sessions → ищем имя в массиве. True если найдена.
    Любая сетевая ошибка / 4xx / 5xx считается за «не найдена» — следующая
    попытка поллинга разрулит."""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/sessions", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if not isinstance(data, list):
            return False
        return any(
            isinstance(s, dict) and s.get("name") == session_name
            for s in data
        )
    except Exception as e:
        log.info("poll /sessions: запрос упал (%s) — считаем «не найдена»", e)
        return False


def _wait_for_session_in_catalog(base_url: str, session_name: str) -> bool:
    """Поллим /sessions до появления сессии или истечения расписания.
    True = нашлась (warmup_api спавнить), False = не нашлась (⚠️ push,
    НЕ спавнить, отдать следующему 45-мин tick'у)."""
    total = 0
    for i, delay in enumerate(_POST_IMPORT_POLL_SCHEDULE, 1):
        time.sleep(delay)
        total += delay
        if _session_visible_in_catalog(base_url, session_name):
            log.info(
                "poll #%d: сессия %r видна в /sessions через ~%dс после import",
                i, session_name, total)
            return True
        log.info(
            "poll #%d/%d: сессия %r ещё не видна (cum %dс) — продолжаю ждать",
            i, len(_POST_IMPORT_POLL_SCHEDULE), session_name, total)
    return False


def _parse_api_port_from_config(cfg: configparser.ConfigParser) -> tuple[str, int]:
    """Тянет base_url из config.ini [api] и вытаскивает оттуда порт.
    Возвращает (base_url, port)."""
    base_url = cfg.get("api", "base_url", fallback="http://127.0.0.1:36555").rstrip("/")
    # base_url вида http://host:port — порт после последнего двоеточия
    try:
        port = int(base_url.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        port = 36555
    return base_url, port


def activate_api_port_if_needed(cfg: configparser.ConfigParser) -> None:
    """Одноразово на машине: открывает Settings → прокручивает вниз →
    вписывает API-порт → Enter → Esc. После успешного пинга API пишет
    флаг .api_activated, дальше пропускает.

    Защитный dismiss-loop в начале — на случай, если после login_if_needed
    всплыл get_started/get_started2/skip/firewall, который ещё не успели
    закрыть."""
    if API_ACTIVATED_FLAG.exists():
        log.info("API-порт уже активирован ранее — пропуск")
        return

    base_url, port = _parse_api_port_from_config(cfg)

    # Быстрая проверка: вдруг порт уже активирован руками — не надо лезть в UI.
    if _check_api_port_alive(base_url):
        log.info("API на %s уже отвечает — ставим флаг без UI-активации", base_url)
        API_ACTIVATED_FLAG.write_text(str(port), encoding="utf-8")
        return

    log.info("активирую API-порт %d через UI", port)
    shots = cfg.getboolean("logging", "screenshots")

    # 1. Закрыть всё что могло всплыть после логина (wizard / firewall).
    for _ in range(10):
        did = False
        if _dismiss_firewall_alert():
            did = True
        if _dismiss_customize_wizard_step():
            did = True
        if not did:
            break

    # 2. Клик на settings_gear (маленькая шестерёнка слева вверху LS).
    #    Шаблон 32×28 — слишком мелкий, чтобы безопасно искать по всему
    #    экрану (любая чёрная иконка в браузере/трее может сматчиться).
    #    Ограничиваем регион верхним-левым углом окна LS.
    ls_hwnd = _find_ls_window()
    conf = cfg.getfloat("matching", "confidence")
    timeout = cfg.getfloat("matching", "wait_seconds")
    pt = None
    if ls_hwnd:
        rect = _RECT()
        ctypes.windll.user32.GetWindowRect(ls_hwnd, ctypes.byref(rect))
        # шестерёнка живёт в шапке LS — слева, в первых ~250×120 пикселях
        deadline = time.time() + timeout
        while time.time() < deadline:
            pt = _match_template_in_region(
                "settings_gear", conf,
                rect.left, rect.top, rect.left + 260, rect.top + 130,
            )
            if pt is not None:
                break
            time.sleep(0.5)
    if pt is None:
        # fallback — попробуем full-screen матч (если LS hwnd не найден или
        # шапка нестандартная); confidence уже 0.80, риск false positive есть,
        # но это последняя надежда.
        log.warning("settings_gear не нашли в шапке LS, пробуем по всему экрану")
        click("settings_gear", cfg)
    else:
        log.info("settings_gear @(%d,%d)", *pt)
        _record_click(pt[0], pt[1], "settings_gear")
        pyautogui.click(pt[0], pt[1])
        time.sleep(cfg.getfloat("matching", "step_delay"))
    screenshot("A1_preferences_open", shots)

    # 3. Скроллим к самому низу страницы Preferences — поле Api port внизу.
    #    Сначала клик в центр области Preferences, чтобы скролл-фокус
    #    точно был на содержимом, а не на каком-нибудь баннере.
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    pyautogui.click(sw // 2, sh // 2)
    time.sleep(0.5)
    # Несколько Ctrl+End — Electron иногда обрабатывает только первый.
    for _ in range(3):
        pyautogui.hotkey("ctrl", "end")
        time.sleep(0.6)
    # Дополнительно PgDn много раз — на случай если Ctrl+End не сработал.
    for _ in range(15):
        pyautogui.press("pagedown")
        time.sleep(0.15)
    time.sleep(1.0)
    screenshot("A2_scrolled_to_api_port", shots)

    # 4. Найти api_port_field (строка «Api port» + input справа).
    #    Кликаем в правую часть найденного бокса — там input.
    box = _find_template_box("api_port_field", cfg)
    field_left, field_top, field_w, field_h = box
    click_x = field_left + int(field_w * 0.80)   # ~80% по ширине — гарантированно в input
    click_y = field_top + field_h // 2
    log.info("Api port input @(%d,%d) box=%dx%d", click_x, click_y, field_w, field_h)
    _record_click(click_x, click_y, "api_port_input")
    pyautogui.click(click_x, click_y)
    time.sleep(0.5)

    # 5. Очистить (вдруг там уже что-то по дефолту) и вписать порт.
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.2)
    pyautogui.press("delete")
    time.sleep(0.2)
    _type_via_clipboard(str(port))
    time.sleep(0.5)
    screenshot("A3_port_typed", shots)

    # 6. Enter — подтвердить значение.
    pyautogui.press("enter")
    time.sleep(1.0)

    # 7. Esc — выйти из Preferences обратно на список сессий.
    pyautogui.press("escape")
    time.sleep(1.5)
    screenshot("A4_back_to_main", shots)

    # 8. Сanity: API теперь должен отвечать. Иначе флаг не ставим.
    if not _check_api_port_alive(base_url):
        raise RuntimeError(
            f"API-порт {port} вписан, Enter нажат, Esc нажат — но {base_url} "
            f"не отвечает. Скорее всего LS не сохранила порт. Проверь "
            f"скриншоты A1-A4 в screenshots/."
        )

    API_ACTIVATED_FLAG.write_text(str(port), encoding="utf-8")
    log.info("API-порт %d активирован, флаг записан", port)


def import_session_if_needed(cfg: configparser.ConfigParser, session_name: str) -> None:
    """Первый запуск на машине: импортит сессию из session_imports/<name>.xlsx
    через MULTIPLE → BROWSE FILE (правая) → диалог → IMPORT. Дальше пропускается."""
    if SESSION_IMPORTED_FLAG.exists():
        prev = SESSION_IMPORTED_FLAG.read_text(encoding="utf-8").strip()
        if prev == session_name:
            log.info("сессия %r уже импортирована ранее — пропуск импорта", session_name)
            return
        log.info("в флаге другое имя (%r != %r) — импортирую заново", prev, session_name)

    xlsx = prepare_session_xlsx(session_name)

    shots = cfg.getboolean("logging", "screenshots")
    log.info("импорт сессии %r из %s", session_name, xlsx)

    # подстраховка: закрыть firewall, если всплыл перед импортом
    _dismiss_firewall_alert()

    # 1. MULTIPLE → окно Mass creation
    click("multiple_button", cfg)
    screenshot("D1_mass_creation", shots)

    # 2. BROWSE FILE (правая, XLSX)
    _click_import_browse_file(cfg)

    # 3. нативный диалог: вписать путь и Enter
    handle_open_file_dialog(str(xlsx), cfg)
    screenshot("D2_file_chosen", shots)

    # 4. IMPORT
    click("import_button", cfg)
    log.info("нажат IMPORT, жду создания сессии (10с) с попутным дисмиссом firewall…")
    # импорт может дёрнуть сеть → возможен firewall alert; ждём 10с и чистим его
    _wait_for_firewall_alert(10.0)
    screenshot("D3_after_import", shots)

    SESSION_IMPORTED_FLAG.write_text(session_name, encoding="utf-8")
    log.info("сессия импортирована, флаг записан")


def search_session(cfg: configparser.ConfigParser, session_name: str) -> None:
    """Фокусирует поиск Сферы (Ctrl+F) и вписывает имя сессии — в списке
    остаётся одна строка, three_dots на ней будет однозначным."""
    log.info("поиск сессии по имени: %r", session_name)
    pyautogui.hotkey("ctrl", "f")
    time.sleep(1.2)  # поле поиска появляется не мгновенно на слабом UI
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    _type_via_clipboard(session_name)
    time.sleep(2.5)  # фильтрация списка на 2-ядерном VPS заметно медленнее
    screenshot("D4_searched", cfg.getboolean("logging", "screenshots"))


def run() -> int:
    cfg = load_config()
    shots = cfg.getboolean("logging", "screenshots")
    log.info("=" * 60)
    log.info("Linken Sphere UI install: старт")
    if (ROOT / ".wizards_done").exists():
        log.info("режим quickstart: флаг .wizards_done — wizard / tour / close_x "
                 "не сканирую (LS уже был запущен ранее на этой машине)")

    try:
        # 0. Запустить Linken Sphere 2, если ещё не запущен
        ensure_linken_sphere_running(cfg)

        # 0.5 Войти, если показан экран аутентификации
        login_if_needed(cfg)

        screenshot("00_initial", shots)

        # 0.6 Активировать API-порт LS (Settings → Network → Api port).
        #     Одноразово на машине. После — все прогревы через HTTP API,
        #     UI больше не дёргаем.
        activate_api_port_if_needed(cfg)

        # 0.7 Сессия машины: уникальное имя CL-XXXXXXXX генерится один раз
        #     и хранится в .session_name. Первый запуск — клонируем шаблон
        #     xlsx с этим именем + импорт в LS через UI.
        session_name = load_session_name()
        import_session_if_needed(cfg, session_name)
        _dismiss_firewall_alert()

        log.info("UI install завершён успешно (логин + API-порт + импорт сессии)")

        # ✅ install OK — юзер видит что машина готова, можно отключаться от
        # RDP. Дальше прогрев идёт через HTTP API (warmup_api.py) и не
        # требует desktop'а / pyautogui / ImageGrab.
        count = _read_success_count()
        if count < SUCCESS_NOTIFY_COUNT:
            count += 1
            _write_success_count(count)
            try:
                notify_ntfy(
                    _ntfy_header() +
                    f"UI install OK {count}/{SUCCESS_NOTIFY_COUNT}\n"
                    f"Запускаю первый API-прогрев в фоне.\n"
                    f"RDP можно отключать — дальше всё само.",
                    title="warmup OK",
                    priority="low",
                    tags="white_check_mark",
                )
            except Exception:
                pass

        # Ждём пока LS реально засветит новую сессию в /sessions catalog.
        # Mass Import dialog'у мало "закрылся" — LS-cloud sync доезжает с
        # задержкой. Без этого ожидания warmup_api спавнится через секунду,
        # сразу падает на find_session_by_name → ⚠️ false-error push.
        # Поллинг работает ПОСЛЕ ✅ success push'а: оператор уже видел
        # "RDP можно отключать", полл-цикл это HTTP-only и переживает
        # disconnect RDP (та же категория что warmup_api).
        base_url_for_poll, _ = _parse_api_port_from_config(cfg)
        if not _wait_for_session_in_catalog(base_url_for_poll, session_name):
            poll_total_min = sum(_POST_IMPORT_POLL_SCHEDULE) // 60
            log.warning(
                "сессия %r не появилась в /sessions за ~%d мин — "
                "warmup_api НЕ спавню. Следующий 45-мин tick подхватит.",
                session_name, poll_total_min)
            try:
                notify_ntfy(
                    _ntfy_header() +
                    f"сессия {session_name!r} не появилась в LS /sessions "
                    f"за ~{poll_total_min} мин после Mass Import.\n"
                    f"warmup_api НЕ стартован. Следующий 45-мин tick "
                    f"попробует через run_api.bat сам.\n"
                    f"Если повторится — проверь машину руками "
                    f"(возможно нужен manual re-import через LS GUI).",
                    title="warmup pending (ui)",
                    priority="high",
                    tags="warning",
                )
            except Exception:
                pass
            return 0

        # Сразу запускаем warmup_api.py как DETACHED subprocess. Он:
        # - не нуждается в desktop'е (HTTP-only, на 127.0.0.1:36555)
        # - продолжит работу когда warmup.py выйдет
        # - продолжит работу когда юзер отключит RDP-сессию
        # Так первый цикл прогрева (~40 мин) пойдёт СРАЗУ, без ожидания
        # следующего scheduler-trigger'а (через 45 мин).
        # MultipleInstances=IgnoreNew у scheduled task защитит от overlap,
        # если TS стрельнёт пока наш фоновый процесс ещё работает.
        try:
            log.info("запускаю warmup_api.py в фоне (detached) — RDP можно отключать")
            popen_kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP → процесс
                # полностью отвязан от parent'а. Переживёт exit warmup.py
                # И disconnect RDP-сессии.
                DETACHED_PROCESS = 0x00000008
                CREATE_NEW_PROCESS_GROUP = 0x00000200
                popen_kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                [sys.executable, str(ROOT / "warmup_api.py")],
                cwd=str(ROOT),
                **popen_kwargs,
            )
        except Exception as e:
            log.warning("не получилось запустить warmup_api.py в фоне: %s", e)

        return 0

    except Exception as exc:
        log.exception("сценарий упал: %s", exc)
        screenshot("ERROR", True)
        try:
            tail_lines: list[str] = []
            if LOG_FILE.exists():
                with LOG_FILE.open(encoding="utf-8", errors="ignore") as f:
                    tail_lines = f.readlines()[-15:]
            notify_ntfy(
                _ntfy_header() +
                f"error: {exc}\n\n"
                f"tail:\n" + "".join(tail_lines)
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(run())
