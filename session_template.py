"""
session_template.py

Генератор xlsx-пресета сессии Linken Sphere из шаблона.
Архитектурно аналогичен sample_urls_for_run() из warmup_api.py:
stateless функция, читает шаблон → рандомит fingerprint-поля →
сохраняет per-machine файл.

Что фиксировано:
    System=windows, Browser=chrome, Connection=direct, WebRTC=fake
    DNS=1.1.1.1;1.0.0.1 (Cloudflare)
    Canvas/WebGL/ClientRects/Audio/WebGPU/MediaDevices = все fake
    Description, Import cookies, Import passwords = пусто

Что рандомится per-machine:
    System version: 10 / 11
    Video adapter:  8 позиций строго из каталога LS для Windows
                    (Intel iGPU, Nvidia GTX/RTX, AMD RX 6600)
    CPU / RAM / Screen: выбираются ИЗ ПРОФИЛЯ выбранной видеокарты
                       (см. ADAPTER_PROFILES). Так исключены абсурдные
                       сборки типа RTX 4070 + 4 ядра + 8 GB + 1080p.

Колонки строки 3 в шаблоне:
    A=session_name  B=description     C=connection      D=ip
    E=port          F=login           G=password        H=webrtc
    I=change_ip_url J=custom_dns      K=system          L=browser
    M=cpu           N=ram             O=screen          P=system_version
    Q=video_adapter R=canvas          S=webgl           T=client_rects
    U=audio         V=webgpu          W=media_devices   X=import_cookies
    Y=import_passwords
"""
from __future__ import annotations

import random
from pathlib import Path

import openpyxl


# --- фиксированные значения (одинаково для всех машин) ---
FIXED = {
    "B": "",                 # description пусто
    "C": "direct",           # connection protocol
    "H": "fake",             # webrtc
    "J": "1.1.1.1;1.0.0.1",  # custom DNS — Cloudflare
    "K": "windows",          # system
    "L": "chrome",           # browser
    "R": "fake",             # canvas
    "S": "fake",             # webgl   (было "noise" в шаблоне)
    "T": "fake",             # client rects (было "noise")
    "U": "fake",             # audio   (было "direct" — главный риск)
    "V": "fake",             # webgpu
    "W": "fake",             # media devices
    "X": "",                 # import cookies пусто
    "Y": "",                 # import passwords пусто
}

SYS_VERSIONS = [10, 11]

# Профили sano-комбинаций: для каждой видеокарты — допустимые CPU/RAM/Screen.
# Названия строго из официального каталога LS (см. лист "Инструкция RU"
# в _template.xlsx). Профили построены чтобы исключить абсурдные сборки
# (например, RTX 4070 с 4 ядрами или 8 GB RAM, GTX 1660 с i3 и т.д.).
ADAPTER_PROFILES: dict[str, tuple[list[int], list[int], list[str]]] = {
    # --- Intel iGPU: офисные машины и лёгкие ноуты, любые комбинации валидны ---
    "Intel, UHD Graphics 770":     ([4, 6, 8], [8, 16], ["1920x1080", "2560x1440"]),
    "Intel, UHD Graphics 630":     ([4, 6, 8], [8, 16], ["1920x1080", "2560x1440"]),
    "Intel, Iris(R) Xe Graphics":  ([4, 6, 8], [8, 16], ["1920x1080", "2560x1440"]),
    "Intel, UHD Graphics":         ([4, 6, 8], [8, 16], ["1920x1080", "2560x1440"]),

    # --- старый бюджетный геймер 2019-2021 ---
    # GTX 1660 + i3/Ryzen 3 (4 ядра) бывает крайне редко → только 6/8
    # 8 GB RAM в эту эпоху ещё валидно для бюджета
    "Nvidia, GeForce GTX 1660":    ([6, 8],    [8, 16], ["1920x1080", "2560x1440"]),

    # --- современный мидл 2021+ ---
    # RTX 3060 / RX 6600: только 6-8 ядер, только 16 GB
    # (8 GB с современной картой = редкость уровня сборщик-некрофил)
    "Nvidia, GeForce RTX 3060":    ([6, 8],    [16],    ["1920x1080", "2560x1440"]),
    "AMD, Radeon RX 6600":         ([6, 8],    [16],    ["1920x1080", "2560x1440"]),

    # --- high-end 2023+ ---
    # RTX 4070 ставят только в собранные системы с i7/Ryzen 7+ (8 ядер)
    # и монитор 1080p с такой картой — позорный мисматч, только 2K
    "Nvidia, GeForce RTX 4070":    ([8],       [16],    ["2560x1440"]),
}


def build_session_xlsx(
    template: Path,
    target: Path,
    session_name: str,
    *,
    rng: random.Random | None = None,
) -> Path:
    """Сгенерировать per-machine xlsx из шаблона.

    Args:
        template: путь к session_imports/_template.xlsx
        target: куда сохранить готовый файл
        session_name: имя сессии (попадает в A3)
        rng: опциональный random.Random для тестов / детерминизма.
             По умолчанию использует системный random (true random).

    Returns:
        Path == target (для chain-style вызова).
    """
    if rng is None:
        rng = random.Random()

    if not template.exists():
        raise FileNotFoundError(f"шаблон не найден: {template}")

    wb = openpyxl.load_workbook(template)
    ws = wb[wb.sheetnames[0]]

    ws["A3"] = session_name

    for cell, val in FIXED.items():
        ws[f"{cell}3"] = val

    # Сначала выбираем видеокарту — она определяет реалистичный набор
    # CPU / RAM / Screen из своего профиля. Это исключает абсурдные
    # сочетания (RTX 4070 с 4 ядрами и т.п.).
    adapter = rng.choice(list(ADAPTER_PROFILES.keys()))
    cpu_options, ram_options, screen_options = ADAPTER_PROFILES[adapter]

    ws["M3"] = rng.choice(cpu_options)
    ws["N3"] = rng.choice(ram_options)
    ws["O3"] = rng.choice(screen_options)
    ws["P3"] = rng.choice(SYS_VERSIONS)
    ws["Q3"] = adapter

    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)
    return target


if __name__ == "__main__":
    # smoke-тест: показать 10 рандомных распределений
    import sys
    root = Path(__file__).resolve().parent
    tpl = root / "session_imports" / "_template.xlsx"
    if len(sys.argv) > 1:
        tpl = Path(sys.argv[1])

    print(f"шаблон: {tpl}")
    print(f"генерим 10 примеров:\n")
    for i in range(10):
        out = Path(f"/tmp/test_session_{i}.xlsx")
        build_session_xlsx(tpl, out, f"test-{i:02d}")
        wb = openpyxl.load_workbook(out)
        ws = wb[wb.sheetnames[0]]
        print(
            f"  [{i:02d}] sysver=Win{ws['P3'].value}  CPU={ws['M3'].value}c  "
            f"RAM={ws['N3'].value}GB  screen={ws['O3'].value}  "
            f"video={ws['Q3'].value}"
        )
