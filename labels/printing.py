"""Друк наліпки: mock (у файл) або real (на QL-700 через brother_ql)."""
import os
from datetime import datetime

from brother_ql.backends.helpers import send
from brother_ql.conversion import convert
from brother_ql.raster import BrotherQLRaster

from . import config


def print_label(img, qty=1):
    """Друкує (real) або зберігає у файл (mock). Повертає список збережених
    файлів — у real завжди порожній. Старі виклики ігнорують результат."""
    if config.PRINT_MODE == "mock":
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        saved = []
        for i in range(max(1, int(qty))):
            path = os.path.join(config.OUTPUT_DIR,
                                f"{datetime.now():%Y%m%d-%H%M%S}-{i+1}.png")
            img.save(path)
            saved.append(path)
            print(f"[MOCK] наліпка збережена у {path} (друку немає)", flush=True)
        return saved

    qlr = BrotherQLRaster(config.PRINTER_MODEL)
    qlr.exception_on_warning = True
    instructions = convert(
        qlr=qlr, images=[img], label=config.LABEL_SIZE, rotate="0",
        threshold=70.0, dither=False, compress=False,
        red=False, dpi_600=False, hq=config.PRINT_HQ, cut=True,
    )
    for _ in range(max(1, int(qty))):
        send(instructions=instructions, printer_identifier=config.DEVICE,
             backend_identifier=config.BACKEND, blocking=True)
    return []
