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
import glob
import logging
import os
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pyautogui
from PIL import ImageGrab

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


def screenshot(step: str, enabled: bool) -> None:
    if not enabled:
        return
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = SCREENSHOTS_DIR / f"{ts}_{step}.png"
    ImageGrab.grab().save(path)
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
    if double:
        pyautogui.doubleClick(x, y)
    else:
        pyautogui.click(x, y)
    time.sleep(cfg.getfloat("matching", "step_delay"))


def set_numeric_field(template_name: str, value: int, cfg: configparser.ConfigParser) -> None:
    """Кликает по полю, чистит и вводит новое значение."""
    conf = cfg.getfloat("matching", "confidence")
    timeout = cfg.getfloat("matching", "wait_seconds")
    x, y = wait_for(template_name, conf, timeout)
    log.info("set field %s=%d at (%d,%d)", template_name, value, x, y)
    pyautogui.tripleClick(x, y)
    time.sleep(0.2)
    pyautogui.press("delete")
    time.sleep(0.1)
    pyautogui.typewrite(str(value), interval=0.05)
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

        # 3. viewing depth
        set_numeric_field(
            "viewing_depth_field",
            cfg.getint("warmup", "viewing_depth"),
            cfg,
        )
        screenshot("03_viewing_depth_set", shots)

        # 4. time per url
        set_numeric_field(
            "time_per_url_field",
            cfg.getint("warmup", "time_per_url"),
            cfg,
        )
        screenshot("04_time_per_url_set", shots)

        # 5. снять галку
        if cfg.getboolean("warmup", "uncheck_use_most_popular"):
            click("use_most_popular_checkbox", cfg)
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
