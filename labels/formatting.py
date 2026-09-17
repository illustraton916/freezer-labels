"""Форматування чисел, ваги, цін і дат (чисті функції, без залежностей)."""


def parse_weight(s):
    """'350' / '350,5' / '0.5' грам -> float, або None."""
    s = (s or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def calc_price(weight_g, per_kg):
    return weight_g / 1000.0 * per_kg


def eur(v):
    """7.5 -> '7,50 €'"""
    return f"{v:.2f}".replace(".", ",") + " €"


def rate(v):
    """15.0 -> '15 €/kg', 15.5 -> '15,5 €/kg'"""
    s = f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{s} €/kg"


def fmt_weight(weight_g):
    return f"{int(round(weight_g))} g"


def fmt_date(d, fmt=None):
    # Завжди числовий формат 18.07.2026 (fmt у сигнатурі — для сумісності
    # зі старими продуктами, де ще збережено date_format)
    return f"{d:%d.%m.%Y}"
