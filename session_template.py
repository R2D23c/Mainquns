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
    CPU:            4 / 6 / 8
    RAM:            8 / 16
    Screen:         1920x1080 / 2560x1440
    Video adapter:  9 реалистичных позиций Intel / NVIDIA / AMD

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

# --- буферы для random.choice ---
SYS_VERSIONS = [10, 11]
CPUS = [4, 6, 8]
RAMS = [8, 16]
SCREENS = ["1920x1080", "2560x1440"]
VIDEO_ADAPTERS = [
    # Intel (~44%)
    "Intel, UHD Graphics 770",
    "Intel, UHD Graphics 630",
    "Intel, Iris Xe Graphics",
    "Intel, UHD Graphics",
    # NVIDIA (~33%)
    "NVIDIA, GeForce RTX 3060",
    "NVIDIA, GeForce RTX 4060",
    "NVIDIA, GeForce GTX 1660",
    # AMD (~22%)
    "AMD, Radeon RX 6600",
    "AMD, Radeon Vega 8",
]


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

    ws["M3"] = rng.choice(CPUS)
    ws["N3"] = rng.choice(RAMS)
    ws["O3"] = rng.choice(SCREENS)
    ws["P3"] = rng.choice(SYS_VERSIONS)
    ws["Q3"] = rng.choice(VIDEO_ADAPTERS)

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
