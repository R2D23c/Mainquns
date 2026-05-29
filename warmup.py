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
import glob
import logging
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

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

pyautogui.FAILSAFE = True  # двинуть мышь в угол — аварийный стоп
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
    img = ImageGrab.grab()
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


def grab_screen_bgr() -> np.ndarray:
    img = np.array(ImageGrab.grab())
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def find_template(name: str, confidence: float) -> tuple[int, int] | None:
    """Возвращает центр найденного шаблона на экране или None."""
    tpl_path = TEMPLATES_DIR / f"{name}.png"
    if not tpl_path.exists():
        log.error("шаблон не найден: %s", tpl_path)
        return None

    tpl = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
    if tpl is None:
        log.error("не удалось прочитать %s", tpl_path)
        return None

    screen = grab_screen_bgr()
    res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    log.info("match %s: confidence=%.3f", name, max_val)
    if max_val < confidence:
        return None
    h, w = tpl.shape[:2]
    return (max_loc[0] + w // 2, max_loc[1] + h // 2)


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
    """Возвращает (left, top, width, height) найденного шаблона."""
    conf = cfg.getfloat("matching", "confidence")
    timeout = cfg.getfloat("matching", "wait_seconds")
    tpl_path = TEMPLATES_DIR / f"{name}.png"
    tpl = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
    if tpl is None:
        raise FileNotFoundError(f"шаблон {tpl_path} не читается")
    h, w = tpl.shape[:2]

    deadline = time.time() + timeout
    while time.time() < deadline:
        res = cv2.matchTemplate(grab_screen_bgr(), tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= conf:
            log.info("match %s: confidence=%.3f", name, max_val)
            return (max_loc[0], max_loc[1], w, h)
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


def notify_ntfy(message: str, title: str = "warmup failed") -> None:
    """Шлёт push через ntfy.sh без какой-либо настройки на машине.
    Все ошибки (сети нет, сервис лежит, и т.д.) глотаются — нотификация
    никогда не должна мешать основному логированию."""
    try:
        import urllib.request
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        # Тело — текст уведомления. Header Title — заголовок (ASCII, чтобы
        # без проблем пролезть через HTTP-заголовки). Priority/Tags —
        # косметика для приложения ntfy.
        req = urllib.request.Request(
            url,
            data=message[:4000].encode("utf-8"),
            method="POST",
            headers={
                "Title": title,
                "Priority": "high",
                "Tags": "warning",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        log.info("ntfy-уведомление отправлено")
    except Exception as e:
        log.warning("notify_ntfy failed: %s", e)


def _set_clipboard_win32(text: str) -> bool:
    """Кладёт текст в Windows clipboard через ctypes — без spawn'а PowerShell
    (раньше дочерний процесс мог стащить фокус с LS, и Ctrl+V улетал не туда)."""
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
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buf))
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, buf, len(buf))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


def _get_clipboard_win32() -> str | None:
    """Читает текст из clipboard для проверки, что Set прошёл."""
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


def _type_via_clipboard(text: str, hwnd: int | None = None) -> None:
    """Вставляет текст в активный input через clipboard. Если clipboard
    отказал (записалось не то / не записалось) — fallback на typewrite.

    Если передан hwnd, перед Ctrl+V форсим окно в foreground — чтобы
    случайно перебитый фокус не съел вставку."""
    if sys.platform != "win32":
        pyautogui.typewrite(text, interval=0.03)
        return
    ok = _set_clipboard_win32(text)
    if ok:
        # верификация: то ли в clipboard'е, что мы клали
        got = _get_clipboard_win32()
        if got != text:
            log.warning("clipboard verify mismatch: got=%r len=%d ≠ %d",
                        got[:20] if got else None, len(got or ""), len(text))
            ok = False
    if not ok:
        log.warning("clipboard fallback на typewrite (Unicode/спецсимволы могут поехать)")
        pyautogui.typewrite(text, interval=0.03)
        time.sleep(0.2)
        return
    # форсим окно в foreground прямо перед Ctrl+V — на случай если
    # за время clipboard-операций фокус куда-то ушёл
    if hwnd:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)


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


def _dismiss_firewall_alert() -> bool:
    """При первом запуске LS Windows может показать Defender Firewall Alert.
    Сначала пытается найти кнопку 'Allow access' по шаблону, иначе ищет окно
    по заголовку и кликает по координатам + Alt+A. Возвращает True если
    что-то закрыли."""
    global _visible_titles_logged
    if sys.platform != "win32":
        return False

    # 1) Шаблон allow_access.png — самый надёжный способ, не зависит от локали
    if _click_allow_access_template():
        return True

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
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)

    rect = _RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    # 'Allow access' — справа внизу. Координаты замерены на стандартном виде
    # диалога: x ~ 65% ширины, y ~ 87% высоты.
    bx = rect.left + int(w * 0.65)
    by = rect.top + int(h * 0.87)
    log.info("клик 'Allow access' @(%d,%d) (окно %dx%d)", bx, by, w, h)
    _record_click(bx, by, "fw_allow")
    pyautogui.click(bx, by)
    time.sleep(0.5)

    # Подстраховка: на некоторых системах кнопка отзывается на Alt+A
    pyautogui.hotkey("alt", "a")
    time.sleep(1.0)
    return True


def _match_template_in_region(
    name: str, confidence: float,
    left: int, top: int, right: int, bottom: int,
    scales: tuple[float, ...] = (0.75, 0.85, 0.95, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0),
) -> tuple[int, int] | None:
    """Template-matching внутри прямоугольника экрана с перебором масштабов.
    cv2 не делает scale-invariant matching, поэтому если шаблон сохранён при
    одном DPI, а ищем при другом — нужно подставить размер. Возвращает
    абсолютные координаты центра лучшего матча, либо None."""
    tpl_path = TEMPLATES_DIR / f"{name}.png"
    tpl_orig = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
    if tpl_orig is None:
        log.error("шаблон %s не читается", tpl_path)
        return None
    region_w = right - left
    region_h = bottom - top
    if region_w <= 0 or region_h <= 0:
        return None
    img = ImageGrab.grab(bbox=(left, top, right, bottom))
    region = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

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


def _click_allow_access_template() -> bool:
    """Ищет шаблоны кнопок 'Allow' в Windows Defender Firewall Alert на всём
    экране. Версии Windows отличаются — кнопка может называться
    'Allow access' (со щитом UAC) или просто 'Allow' (Win11-style).
    Перебирает шаблоны по очереди:
      - allow_access   — Win10 стиль с щитом
      - allow_access2  — Win11 стиль без щита
    Возвращает True если что-то кликнули."""
    if sys.platform != "win32":
        return False

    import ctypes.wintypes
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)

    for tpl_name in ("allow_access", "allow_access2"):
        if not (TEMPLATES_DIR / f"{tpl_name}.png").exists():
            continue
        pt = _match_template_in_region(
            tpl_name, 0.80,
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
      - skip            — иногда всплывающее окно с малозаметной кнопкой SKIP
      - close_x         — финальный мелкий крестик ✕ на следующем после skip окне;
                          ищется ТОЛЬКО в течение 30с после клика на skip,
                          чтобы не зацепить close-кнопку самого LS
    Когда все исчезнут — функция вернёт False, поток пойдёт дальше."""
    global _skip_clicked_at
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
    candidates = [
        ("next_step",    lower_half_top, 0.80),
        ("get_started",  lower_half_top, 0.80),
        ("get_started2", lower_half_top, 0.80),
        ("skip",         rect.top,       0.85),
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
    _type_via_clipboard(creds.get("account", "email"), hwnd=hwnd)

    _record_click(cx, password_y, "password")
    pyautogui.click(cx, password_y)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    _type_via_clipboard(creds.get("account", "password"), hwnd=hwnd)

    screenshot("login_filled", cfg.getboolean("logging", "screenshots"))

    _record_click(cx, signin_y, "sign_in")
    pyautogui.click(cx, signin_y)
    log.info("нажат SIGN IN, жду главный экран…")
    time.sleep(3.0)

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
    r = subprocess.run(["taskkill", "/im", exe_name], capture_output=True, text=True)
    log.info("taskkill soft: stdout=%r stderr=%r", r.stdout.strip(), r.stderr.strip())
    time.sleep(3.0)

    # Принудительное завершение остатков
    r = subprocess.run(["taskkill", "/f", "/im", exe_name, "/t"], capture_output=True, text=True)
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


def _done_dir(cfg: configparser.ConfigParser) -> str:
    return os.path.join(_files_dir(cfg), "done")


def archive_used_file(src_path: str, cfg: configparser.ConfigParser) -> None:
    """Переносит отработанный файл в done/<timestamp>_<имя>."""
    done_dir = _done_dir(cfg)
    os.makedirs(done_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(done_dir, f"{ts}_{os.path.basename(src_path)}")
    try:
        shutil.move(src_path, dst)
        log.info("файл перенесён в done: %s", dst)
    except OSError as e:
        log.warning("не удалось перенести %s в done: %s", src_path, e)


def regenerate_files_from_done(cfg: configparser.ConfigParser) -> int:
    """
    Если files_dir пуст — берёт все URL'ы из done/, перемешивает,
    режет на куски по lines_per_file и кладёт обратно как новые файлы.
    Возвращает число созданных файлов.
    """
    files_dir = _files_dir(cfg)
    done_dir = _done_dir(cfg)
    pattern = cfg.get("paths", "file_glob")
    lines_per_file = cfg.getint("paths", "regenerate_lines_per_file", fallback=100)

    if not os.path.isdir(done_dir):
        return 0

    done_files = glob.glob(os.path.join(done_dir, pattern))
    all_urls: list[str] = []
    for src in done_files:
        try:
            with open(src, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        all_urls.append(line)
        except OSError as e:
            log.warning("не удалось прочитать %s: %s", src, e)

    if not all_urls:
        return 0

    random.shuffle(all_urls)
    ts = time.strftime("%Y%m%d-%H%M%S")
    created = 0
    for i in range(0, len(all_urls), lines_per_file):
        chunk = all_urls[i:i + lines_per_file]
        out_path = os.path.join(files_dir, f"regen_{ts}_{created:04d}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(chunk) + "\n")
        created += 1

    log.info(
        "regenerate: %d URL'ов из %d файлов done/ → %d новых файлов по ~%d строк",
        len(all_urls), len(done_files), created, lines_per_file,
    )
    return created


def pick_random_file(cfg: configparser.ConfigParser) -> str:
    files_dir = _files_dir(cfg)
    pattern = cfg.get("paths", "file_glob")
    candidates = glob.glob(os.path.join(files_dir, pattern))

    if not candidates:
        log.info("files_dir пуст — пробую регенерировать из done/")
        if regenerate_files_from_done(cfg) > 0:
            candidates = glob.glob(os.path.join(files_dir, pattern))

    if not candidates:
        raise FileNotFoundError(
            f"в {files_dir} нет файлов по маске {pattern} (done/ тоже пуст)"
        )
    chosen = random.choice(candidates)
    log.info("случайный файл (%d кандидатов): %s", len(candidates), chosen)
    return chosen


def handle_open_file_dialog(file_path: str, cfg: configparser.ConfigParser) -> None:
    """
    Нативный Windows-диалог открытия файла.
    Самый надёжный способ — вставить полный путь в поле "Имя файла" и нажать Enter.
    """
    time.sleep(1.2)  # дать диалогу открыться
    screenshot("06_open_dialog", cfg.getboolean("logging", "screenshots"))
    # focus на поле "File name" — стандартно открыто по умолчанию
    pyautogui.hotkey("alt", "n")  # Alt+N → File name (en)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    pyautogui.typewrite(file_path, interval=0.01)
    time.sleep(0.3)
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
    Иначе клонируем session_imports/_template.xlsx, ставим A3 = session_name,
    сохраняем под нужным именем. Возвращаем путь к готовому файлу."""
    target = SESSION_IMPORTS_DIR / f"{session_name}.xlsx"
    if target.exists():
        return target
    template = SESSION_IMPORTS_DIR / "_template.xlsx"
    if not template.exists():
        raise FileNotFoundError(
            f"не найден шаблон импорта сессии: {template}. "
            f"Должен лежать в репозитории."
        )
    import openpyxl  # ленивый импорт — нужен только при первой установке
    wb = openpyxl.load_workbook(template)
    ws = wb[wb.sheetnames[0]]
    ws["A3"] = session_name
    wb.save(target)
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
    time.sleep(0.6)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    _type_via_clipboard(session_name)
    time.sleep(1.5)  # дать списку отфильтроваться
    screenshot("D4_searched", cfg.getboolean("logging", "screenshots"))


def run() -> int:
    cfg = load_config()
    shots = cfg.getboolean("logging", "screenshots")
    log.info("=" * 60)
    log.info("Linken Sphere warm-up: старт сценария")

    try:
        file_to_attach = pick_random_file(cfg)

        # 0. Запустить Linken Sphere 2, если ещё не запущен
        ensure_linken_sphere_running(cfg)

        # 0.5 Войти, если показан экран аутентификации
        login_if_needed(cfg)

        screenshot("00_initial", shots)

        # 0.7 Сессия машины: уникальное имя CL-XXXXXXXX генерится один раз
        #     и хранится в .session_name. Первый запуск — клонируем шаблон
        #     xlsx с этим именем + импорт в LS, затем поиск по имени, чтобы
        #     three_dots был однозначным.
        session_name = load_session_name()
        import_session_if_needed(cfg, session_name)
        search_session(cfg, session_name)
        # подстраховка перед three_dots: закрыть firewall, если всплыл
        _dismiss_firewall_alert()

        # 1. меню "три точки"
        click("three_dots", cfg)
        screenshot("01_after_three_dots", shots)

        # 2. пункт Warm up
        click("warm_up_menu", cfg)
        screenshot("02_warmup_window", shots)

        # 3. viewing depth (stepper)
        set_stepper(
            "viewing_depth_field",
            cfg.getint("warmup", "viewing_depth"),
            cfg.getint("warmup", "viewing_depth_min"),
            cfg,
        )
        screenshot("03_viewing_depth_set", shots)

        # 4. time per url (stepper)
        set_stepper(
            "time_per_url_field",
            cfg.getint("warmup", "time_per_url"),
            cfg.getint("warmup", "time_per_url_min"),
            cfg,
        )
        screenshot("04_time_per_url_set", shots)

        # 5. снять галку — toggle слева в шаблоне, не по центру
        if cfg.getboolean("warmup", "uncheck_use_most_popular"):
            click_at_offset(
                "use_most_popular_checkbox",
                cfg.getfloat("checkbox_offset", "toggle_x"),
                cfg.getfloat("checkbox_offset", "toggle_y"),
                cfg,
            )
            screenshot("05_unchecked", shots)

        # 6. Browse file → диалог → ввести путь → Enter
        click("browse_file_button", cfg)
        handle_open_file_dialog(file_to_attach, cfg)
        screenshot("07_after_browse", shots)

        # 6.5. Удалить первые N строк из URL-списка (если задано)
        remove_n = cfg.getint("warmup", "remove_first_n_lines", fallback=0)
        if remove_n > 0:
            remove_first_lines_from_list(remove_n, cfg)
            screenshot("07b_lines_removed", shots)

        # 7. START
        click("start_button", cfg)
        screenshot("08_started", shots)

        # 7.5. После START браузеры начинают ходить по URL'ам — Windows может
        # снова показать firewall alert на разрешение сетевого доступа. Иногда
        # он всплывает не сразу, а через минуту, поэтому окно ожидания 90с.
        # Если alert появился и был закрыт — выходим раньше (без полного ожидания).
        _wait_for_firewall_alert(90.0, exit_after_close=True)

        # 8. отработанный файл — в done/
        archive_used_file(file_to_attach, cfg)

        log.info("сценарий завершён успешно")
        # Первые N успешных запусков подтверждаем push'ем — чтобы убедиться,
        # что setup на новой машине отработал. Дальше тишина (только падения).
        count = _read_success_count()
        if count < SUCCESS_NOTIFY_COUNT:
            count += 1
            _write_success_count(count)
            try:
                import socket
                notify_ntfy(
                    f"machine: {socket.gethostname()}\n"
                    f"успешный запуск {count}/{SUCCESS_NOTIFY_COUNT}",
                    title="warmup OK",
                )
            except Exception:
                pass
        return 0

    except Exception as exc:
        log.exception("сценарий упал: %s", exc)
        screenshot("ERROR", True)
        try:
            import socket
            tail_lines: list[str] = []
            if LOG_FILE.exists():
                with LOG_FILE.open(encoding="utf-8", errors="ignore") as f:
                    tail_lines = f.readlines()[-15:]
            notify_ntfy(
                f"machine: {socket.gethostname()}\n"
                f"error: {exc}\n\n"
                f"tail:\n" + "".join(tail_lines)
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(run())
