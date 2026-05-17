"""
Linken Sphere 2 — автоматизация Warm up.

Сценарий:
  1. Найти и активировать окно Linken Sphere 2.
  2. Кликнуть кнопку "три точки" → пункт "Warm up".
  3. Изменить viewing depth и time per url на значения из config.ini.
  4. Снять галочку "use most popular".
  5. Browse file → выбрать случайный .txt из указанной папки.
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


def pick_random_file(cfg: configparser.ConfigParser) -> str:
    files_dir = cfg.get("paths", "files_dir")
    pattern = cfg.get("paths", "file_glob")
    candidates = glob.glob(os.path.join(files_dir, pattern))
    if not candidates:
        raise FileNotFoundError(f"в {files_dir} нет файлов по маске {pattern}")
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


def run() -> int:
    cfg = load_config()
    shots = cfg.getboolean("logging", "screenshots")
    log.info("=" * 60)
    log.info("Linken Sphere warm-up: старт сценария")

    try:
        file_to_attach = pick_random_file(cfg)

        screenshot("00_initial", shots)

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

        # 7. START
        click("start_button", cfg)
        screenshot("08_started", shots)

        log.info("сценарий завершён успешно")
        return 0

    except Exception as exc:
        log.exception("сценарий упал: %s", exc)
        screenshot("ERROR", True)
        return 1


if __name__ == "__main__":
    sys.exit(run())
