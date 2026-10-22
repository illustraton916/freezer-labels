"""EAN-13: контрольна цифра, перевірка, модулі й малювання штрихів.

Чисті функції без Pillow: таблиці кодування й геометрія штрихів живуть тут, а
розкладка на наліпці (центрування, тихі зони, цифри під штрихами, траса) — у
rendering.Canvas.barcode. Малювання приймає будь-який об'єкт із методом
`rectangle([x0, y0, x1, y1], fill=…)` (ImageDraw підходить), тож модуль
тестується без полотна й без шрифтів.

Символ EAN-13 — рівно 95 модулів:
    101 | 6 цифр × 7 (L або G — парність за першою цифрою) | 01010 |
    6 цифр × 7 (R) | 101
Перша цифра штрихами не кодується — лише парністю лівої шістки — і друкується
ліворуч від стартового guard'а. Кривий код тут — ValueError, а не «майже
правильний» символ: сканер прочитав би його як інший товар.
"""
import re

# L-коди (непарна кількість одиниць). R — побітове доповнення L, G — R задом
# наперед. Таблиці зі стандарту GS1 General Specifications (§5.1.1.2).
_L = ("0001101", "0011001", "0010011", "0111101", "0100011",
      "0110001", "0101111", "0111011", "0110111", "0001011")
_R = tuple("".join("1" if b == "0" else "0" for b in code) for code in _L)
_G = tuple(code[::-1] for code in _R)
# Парність лівої шістки за першою цифрою коду (L = непарна, G = парна).
_PARITY = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
           "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")
_TABLE = {"L": _L, "G": _G, "R": _R}

START_GUARD, CENTRE_GUARD, END_GUARD = "101", "01010", "101"
MODULES = 95
# Індекси модулів guard-штрихів: вони нижчі за штрихи даних на guard_extra —
# між ними знизу стоять групи цифр.
GUARD_MODULES = (frozenset(range(0, 3)) | frozenset(range(45, 50))
                 | frozenset(range(92, 95)))

# Саме ASCII-цифри: str.isdigit() пропустив би «٣» або «²», а int() їх з'їв би.
_DIGITS12 = re.compile(r"^[0-9]{12}$")
_DIGITS13 = re.compile(r"^[0-9]{13}$")


def ean13_check_digit(digits12):
    """Контрольна цифра для 12 цифр: ваги 1,3,1,3,… зліва, доповнення до 10."""
    if not isinstance(digits12, str) or not _DIGITS12.match(digits12):
        raise ValueError("очікується рядок рівно з 12 цифр")
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits12))
    return str((10 - total % 10) % 10)


def ean13_is_valid(code):
    """True лише для рядка рівно з 13 ASCII-цифр із правильною контрольною."""
    if not isinstance(code, str) or not _DIGITS13.match(code):
        return False
    return ean13_check_digit(code[:12]) == code[12]


def ean13_modules(code):
    """95 модулів символу (0 = пробіл, 1 = штрих) як список int."""
    if not ean13_is_valid(code):
        raise ValueError(f"не EAN-13: {code!r}")
    parity = _PARITY[int(code[0])]
    bits = START_GUARD
    for digit, p in zip(code[1:7], parity):
        bits += _TABLE[p][int(digit)]
    bits += CENTRE_GUARD
    for digit in code[7:13]:
        bits += _R[int(digit)]
    bits += END_GUARD
    return [int(b) for b in bits]


def draw_ean13_bars(draw, modules, x, y, module_px, bar_h, guard_extra, fill=0):
    """Малює штрихи модуль за модулем від (x, y): кожен рівно module_px у
    ширину й bar_h у висоту, guard-штрихи — на guard_extra нижче. Усі
    координати цілі; rectangle у Pillow включає обидва кінці — звідси «-1».
    Повертає (ширина штрихів, висота разом із guard'ами) у пікселях."""
    for i, bit in enumerate(modules):
        if not bit:
            continue
        h = bar_h + (guard_extra if i in GUARD_MODULES else 0)
        x0 = x + i * module_px
        draw.rectangle([x0, y, x0 + module_px - 1, y + h - 1], fill=fill)
    return len(modules) * module_px, bar_h + guard_extra
