"""Валідація payload'а /api/* + guard неповної frozen-наліпки.

Два різні рівні, які легко сплутати:

1. СТРУКТУРА (`invalid_payload`). Шов Node↔Python — слабо типізований JSON, і
   найнебезпечніший сценарій не «прийшло сміття», а «прийшло майже те саме»:
   `nutrition` перейменовано, `ingredients` склеєно в рядок, `frozen_mark`
   загублено. Pillow намалював би таку наліпку без зауважень. Тому список полів
   — ЗАКРИТИЙ: невідомий ключ це помилка, а не «додаткові дані».

2. ПОВНОТА (`incomplete_label`). frozen без складу / алергенів / умов зберігання
   / харчової цінності — юридично неповна наліпка. Сьогодні на сервері такий
   продукт є (`kaalikarylet`) і він спокійно друкується. Нові /api/* відмовляють.
   Для `deli` перевірка теж є: наліпка delі друкує рядок «Ainekset: …», і
   порожній `ingredients_text` дає наліпку зі словом «Ainekset:» і нічим після
   нього — тобто склад відсутній, а виглядає як присутній.

3. ДАТА (`missing_made_on`). Окремий рівень, і тільки для /api/print: рендер без
   дати ще можна показати на екрані, а надруковану наліпку вже не переробиш.
   Дата контейнера — це UTC, кухня живе за Europe/Helsinki, тож «сьогодні» за
   годинником рендерера вночі й уранці може бути вчорашнім днем.

Усі три рівні діють ТІЛЬКИ на /api/*. Старий веб-інтерфейс і /preview не чіпаємо:
вони — «розбий скло» і мають працювати рівно як учора.
"""
import math
import re
from datetime import date

# Поля продукту, які існують у products.json. Закритий список: див. коментар вище.
PRODUCT_FIELDS = {
    "id", "type", "name", "note_uk", "category", "price_per_kg",
    "shelf_life_days", "frozen_mark", "ingredients", "ingredients_text",
    "allergens", "instructions", "storage", "nutrition",
}
# Керівні поля запиту (усе, що не належить самому продукту).
# `allow_incomplete` тут НЕМА навмисно: обхід guard'а прибрано з коду повністю,
# тож старий запит із цим полем отримає 422 invalid_payload, а не тихо пройде.
CONTROL_FIELDS = {"product", "weight_g", "made_on", "best_before", "qty",
                  "trace"}

# Поля, без яких наліпка юридично неповна — окремо для кожного типу.
# Ціна (`price_per_kg`) і термін (`shelf_life_days`) тут не повторюються: вони
# обов'язкові для обох типів уже на рівні структури (_check_product), а дату
# друку вимагає require_made_on.
REQUIRED_FIELDS_BY_TYPE = {
    "frozen": ("ingredients", "allergens", "storage", "nutrition"),
    "deli": ("ingredients_text",),
}
# Сумісність зі старими імпортами (гейт і тести посилалися на цю назву).
REQUIRED_FROZEN_FIELDS = REQUIRED_FIELDS_BY_TYPE["frozen"]

# Ключі харчової цінності — рівно ці сім, саме їх читає rendering.render_frozen.
NUTRITION_KEYS = ("energy_kj", "energy_kcal", "fat", "saturated", "carbs",
                  "protein", "salt")
# Енергія друкується з одиницями в самому шаблоні («{energy_kj} kJ / {…} kcal»),
# тож число тут безпечне: на наліпці все одно буде «850 kJ / 205 kcal».
NUTRITION_NUMERIC_OK = ("energy_kj", "energy_kcal")
# А ці п'ять друкуються дослівно: «Rasvat: {fat}». Число 18 дало б «Rasvat: 18»
# без грамів — юридично значущий рядок без одиниці виміру. Тільки рядок.
NUTRITION_TEXT_ONLY = tuple(k for k in NUTRITION_KEYS
                            if k not in NUTRITION_NUMERIC_OK)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_QTY = 50


class PayloadError(ValueError):
    """422: payload не приймається. code — машинна причина, details — деталі."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_text(v):
    return isinstance(v, str)


def _parse_date(value, field, issues):
    if value is None:
        return None
    if not _is_text(value) or not _ISO_DATE.match(value):
        issues.append(f"{field}: очікується дата у форматі YYYY-MM-DD")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        issues.append(f"{field}: неіснуюча дата {value!r}")
        return None


def _check_product(p, issues):
    if not isinstance(p, dict):
        issues.append("product: очікується об'єкт")
        return
    unknown = sorted(set(p) - PRODUCT_FIELDS)
    if unknown:
        issues.append("невідомі поля продукту: " + ", ".join(unknown)
                      + " (перейменоване поле = мовчки загублений текст наліпки)")

    ptype = p.get("type")
    if ptype not in ("frozen", "deli"):
        issues.append("type: має бути 'frozen' або 'deli'")

    if not _is_text(p.get("name")) or not p.get("name", "").strip():
        issues.append("name: обов'язковий непорожній рядок")

    price = p.get("price_per_kg")
    if not _is_num(price) or price < 0:
        issues.append("price_per_kg: обов'язкове невід'ємне число")

    days = p.get("shelf_life_days")
    if not _is_int(days) or days < 0:
        issues.append("shelf_life_days: обов'язкове ціле невід'ємне число")

    for field in ("id", "note_uk", "category", "frozen_mark", "allergens",
                  "instructions", "storage", "ingredients_text"):
        if field in p and p[field] is not None and not _is_text(p[field]):
            issues.append(f"{field}: очікується рядок")

    ing = p.get("ingredients")
    if ing is not None:
        if not isinstance(ing, list):
            issues.append("ingredients: очікується масив рядків "
                          "(1 елемент = 1 друкований абзац), а не рядок")
        elif not all(_is_text(x) for x in ing):
            issues.append("ingredients: усі елементи мають бути рядками")

    n = p.get("nutrition")
    if n is not None:
        if not isinstance(n, dict):
            issues.append("nutrition: очікується об'єкт")
        else:
            missing = [k for k in NUTRITION_KEYS if k not in n]
            if missing:
                issues.append("nutrition: бракує ключів " + ", ".join(missing))
            extra = sorted(set(n) - set(NUTRITION_KEYS))
            if extra:
                issues.append("nutrition: невідомі ключі " + ", ".join(extra))
            for k in NUTRITION_NUMERIC_OK:
                if k in n and not (_is_int(n[k]) or _is_text(n[k])):
                    issues.append(f"nutrition.{k}: очікується ціле число або рядок")
            # Числа тут заборонені: 18 надрукувалося б як «Rasvat: 18» без «g»,
            # а це юридично значущий рядок. А от ПОРОЖНІЙ рядок — не помилка
            # payload'а, а дірка в даних: такі продукти (напр. suolatut-syrnykyt
            # із порожньою сіллю) друкуються на проді сьогодні, і блокувати їх
            # тут означало б зламати те, що працює. Для них є warning нижче.
            for k in NUTRITION_TEXT_ONLY:
                if k in n and not _is_text(n[k]):
                    issues.append(
                        f"nutrition.{k}: очікується рядок з одиницею виміру, напр. "
                        f"\"18 g\" — число надрукувалось би без «g»")


def validate_payload(body, require_qty=False):
    """body (розібраний JSON) -> нормалізований запит. Кидає PayloadError."""
    if not isinstance(body, dict):
        raise PayloadError("invalid_payload",
                           "очікується JSON-об'єкт у тілі запиту")

    issues = []
    if "product" in body:
        product = body["product"]
        unknown = sorted(set(body) - CONTROL_FIELDS)
        if unknown:
            issues.append("невідомі поля запиту: " + ", ".join(unknown))
    else:
        # Плоска форма: сам продукт + керівні поля поруч.
        product = {k: v for k, v in body.items() if k not in CONTROL_FIELDS}
        unknown = sorted(set(body) - CONTROL_FIELDS - PRODUCT_FIELDS)
        if unknown:
            issues.append("невідомі поля запиту: " + ", ".join(unknown))

    _check_product(product, issues)

    weight = body.get("weight_g")
    if weight is not None and (not _is_num(weight) or weight <= 0):
        issues.append("weight_g: число > 0 або null (null = порожнє місце під вагу)")

    made_on = _parse_date(body.get("made_on"), "made_on", issues)
    best_before = _parse_date(body.get("best_before"), "best_before", issues)
    if made_on and best_before and best_before < made_on:
        issues.append("best_before: раніше за made_on")

    qty = body.get("qty", 1)
    if require_qty:
        if not _is_int(qty) or not (1 <= qty <= MAX_QTY):
            issues.append(f"qty: ціле від 1 до {MAX_QTY}")
    elif "qty" in body:
        issues.append("qty: не приймається цим ендпоінтом")

    if "trace" in body and not isinstance(body["trace"], bool):
        issues.append("trace: очікується true/false")

    if issues:
        raise PayloadError("invalid_payload",
                           "payload не пройшов перевірку", issues=issues)

    return {
        "product": product,
        "weight_g": weight,
        "made_on": made_on,
        "best_before": best_before,
        "qty": qty if require_qty else 1,
        "trace": bool(body.get("trace", False)),
    }


def missing_label_fields(product):
    """Поля, без яких наліпку цього типу друкувати не можна.

    Порожній рядок і порожній масив рахуються відсутніми: наліпка з «Ainekset:»
    і нічим після нього гірша за помилку — вона виглядає повною.
    """
    required = REQUIRED_FIELDS_BY_TYPE.get(product.get("type"))
    if not required:
        return []          # невідомий тип уже впіймано структурною перевіркою

    def is_blank(v):
        if v is None or v == "" or v == [] or v == {}:
            return True
        if isinstance(v, str):
            return not v.strip()
        # Список із порожніх рядків — теж «нічого»: ["", "  "] намалював би
        # «Ainesosat:» і порожнє місце під ним. Саме це обіцяє docstring вище,
        # і саме цього код раніше не робив.
        if isinstance(v, (list, tuple)):
            return all(is_blank(x) for x in v)
        return False

    return [f for f in required if is_blank(product.get(f))]


def require_complete_label(product):
    missing = missing_label_fields(product)
    if missing:
        ptype = product.get("type")
        raise PayloadError(
            "incomplete_label",
            f"{ptype}-наліпка неповна: " + ", ".join(missing)
            + " — друк заборонено (обов'язкові дані харчового маркування)",
            missing=missing,
        )


def require_made_on(req):
    """Дата виготовлення обов'язкова там, де наслідок фізичний (друк).

    Без неї рендер узяв би `date.today()` годинника контейнера. Контейнер живе
    в UTC, а кухня — в Europe/Helsinki: до 3-ї ночі влітку це вчорашній день,
    і він поїде на паперову наліпку разом із неправильним «Parasta ennen».
    Прев'ю (/api/render) і старий /preview цю поблажку зберігають — там видно
    `made_on_source`, і нічого не друкується.
    """
    if req.get("made_on") is None:
        raise PayloadError(
            "missing_made_on",
            "made_on обов'язковий для друку: дату визначає той, хто знає "
            "часовий пояс кухні, а не годинник контейнера",
        )


def label_warnings(product):
    """Не блокує друк, але видно у відповіді: порожні значення всередині
    харчової цінності («Rasvat: » без числа) і відсутня інструкція."""
    warnings = []
    n = product.get("nutrition") or {}
    empty = [k for k in NUTRITION_KEYS
             if k in n and (n[k] is None or str(n[k]).strip() == "")]
    if empty:
        warnings.append("порожні значення харчової цінності: " + ", ".join(empty))
    if product.get("type") == "frozen" and not product.get("instructions"):
        warnings.append("немає instructions (Valmistusohje) — блок не друкується")
    return warnings
