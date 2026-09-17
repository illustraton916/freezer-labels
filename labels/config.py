"""Налаштування (перевизначаються через env у docker-compose) + шрифти."""
import os

from PIL import ImageFont

from .fonts_check import font_path, verify_fonts

PRINTER_MODEL = os.environ.get("PRINTER_MODEL", "QL-700")
LABEL_SIZE    = os.environ.get("LABEL_SIZE", "62")          # 62 = безперервна 62мм стрічка
DEVICE        = os.environ.get("PRINTER_DEVICE", "/dev/usb/lp0")
BACKEND       = os.environ.get("PRINTER_BACKEND", "linux_kernel")
PRODUCTS_FILE = os.environ.get("PRODUCTS_FILE", "products.json")

# real = друкувати на принтер (сервер); mock = зберігати картинку у OUTPUT_DIR (тест)
PRINT_MODE = os.environ.get("PRINT_MODE", "real").lower()
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/app/output")

# false = швидший друк (стандартна якість), true = чіткіше але повільніше
PRINT_HQ = os.environ.get("PRINT_HQ", "false").lower() in ("1", "true", "yes")

# Прапорця ALLOW_INCOMPLETE_RENDER тут більше немає: обхід guard'а неповної
# наліпки прибрано з коду повністю. Гейт (tools/golden_gate.py) знає, які
# продукти baseline неповні, і для них чекає саме 422 incomplete_label — тож
# прод-конфігурація проходить гейт без жодної env-діри.

# Шрифти вендорені в репозиторії; хеші перевіряються ДО створення ImageFont,
# тобто жоден шлях коду не встигне намалювати наліпку чужим шрифтом.
verify_fonts()
FONT_BOLD = font_path("DejaVuSans-Bold.ttf")
FONT_REG  = font_path("DejaVuSans.ttf")

WIDTH  = 696   # друкована ширина 62мм стрічки @300dpi, у пікселях
MARGIN = 20

# Масштаб шрифтів наліпки (1.1 = на 10% більші за базовий дизайн)
SCALE = float(os.environ.get("LABEL_SCALE", "1.1"))


def fs(px):
    """Розмір шрифту з урахуванням масштабу."""
    return max(1, round(px * SCALE))


# Шрифти наліпки
F_NAME      = ImageFont.truetype(FONT_BOLD, fs(46))
F_MARK      = ImageFont.truetype(FONT_REG, fs(26))
F_H         = ImageFont.truetype(FONT_BOLD, fs(30))
F_BODY      = ImageFont.truetype(FONT_REG, fs(26))
F_BODY_BOLD = ImageFont.truetype(FONT_BOLD, fs(26))
F_PRICE     = ImageFont.truetype(FONT_BOLD, fs(30))
