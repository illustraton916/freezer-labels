"""Парсинг і валідація форми продукту (адмінка) <-> словник продукту."""
from .store import load_products, unique_slug


def parse_num(s, field):
    s = (s or "").strip().replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"{field}: потрібне число")
    if v < 0:
        raise ValueError(f"{field}: не може бути від'ємним")
    return v


def parse_int(s, field, default=None):
    s = (s or "").strip()
    if s == "":
        if default is not None:
            return default
        raise ValueError(f"{field}: обов'язкове поле")
    try:
        v = int(round(float(s.replace(",", "."))))
    except ValueError:
        raise ValueError(f"{field}: потрібне ціле число")
    if v < 0:
        raise ValueError(f"{field}: не може бути від'ємним")
    return v


def build_product_from_form(form, existing_id=None):
    ptype = (form.get("type") or "").strip()
    if ptype not in ("frozen", "deli"):
        raise ValueError("тип має бути frozen або deli")
    name = (form.get("name") or "").strip()
    if not name:
        raise ValueError("назва обов'язкова")
    price = parse_num(form.get("price_per_kg"), "ціна за кг")
    days = parse_int(form.get("shelf_life_days"), "термін (днів)")

    pid = existing_id or unique_slug(name, load_products())
    p = {"id": pid, "type": ptype, "name": name}
    note = (form.get("note_uk") or "").strip()
    if note:
        p["note_uk"] = note
    cat = (form.get("category") or "").strip()
    if cat:
        p["category"] = cat
    p["price_per_kg"] = price
    p["shelf_life_days"] = days

    if ptype == "frozen":
        fm = (form.get("frozen_mark") or "").strip()
        if fm:
            p["frozen_mark"] = fm
        p["ingredients"] = [l.strip() for l in (form.get("ingredients") or "").splitlines() if l.strip()]
        for field in ("allergens", "instructions", "storage"):
            val = (form.get(field) or "").strip()
            if val:
                p[field] = val
        kcal = (form.get("n_energy_kcal") or "").strip()
        kj = (form.get("n_energy_kj") or "").strip()
        if kcal or kj:
            p["nutrition"] = {
                "energy_kj": parse_int(kj, "kJ", default=0),
                "energy_kcal": parse_int(kcal, "kcal", default=0),
                "fat": (form.get("n_fat") or "").strip(),
                "saturated": (form.get("n_saturated") or "").strip(),
                "carbs": (form.get("n_carbs") or "").strip(),
                "protein": (form.get("n_protein") or "").strip(),
                "salt": (form.get("n_salt") or "").strip(),
            }
    else:
        p["ingredients_text"] = (form.get("ingredients_text") or "").strip()
    return p


def product_to_fields(p):
    fv = {
        "id": p.get("id", ""),
        "type": p.get("type", "frozen"),
        "name": p.get("name", ""),
        "note_uk": p.get("note_uk", ""),
        "category": p.get("category", ""),
        "price_per_kg": p.get("price_per_kg", ""),
        "shelf_life_days": p.get("shelf_life_days", ""),
        "frozen_mark": p.get("frozen_mark", ""),
        "ingredients": "\n".join(p.get("ingredients", [])),
        "allergens": p.get("allergens", ""),
        "instructions": p.get("instructions", ""),
        "storage": p.get("storage", ""),
        "ingredients_text": p.get("ingredients_text", ""),
    }
    n = p.get("nutrition") or {}
    for k in ("energy_kj", "energy_kcal", "fat", "saturated", "carbs", "protein", "salt"):
        fv["n_" + k] = n.get(k, "")
    return fv
