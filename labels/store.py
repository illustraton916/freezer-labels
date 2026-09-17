"""Доступ до products.json + допоміжне для продуктів (slug, категорії)."""
import json
import re

from . import config

# Читаємо config.PRODUCTS_FILE щоразу (а не копію), щоб тести могли підмінити шлях.


def load_products():
    with open(config.PRODUCTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_product(pid):
    for p in load_products():
        if p["id"] == pid:
            return p
    return None


def save_products(products):
    # products.json бет-маунтиться як файл, тому пишемо «на місці»: rename поверх
    # bind-mount файлу дав би EBUSY. Файл малий, один користувач — цього досить.
    with open(config.PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
        f.write("\n")


_SLUG_MAP = str.maketrans({"ä": "a", "ö": "o", "å": "a", "ü": "u",
                           "ß": "ss", "é": "e", "è": "e"})


def slugify(name):
    s = name.lower().translate(_SLUG_MAP)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "tuote"


def unique_slug(name, products):
    ids = {p["id"] for p in products}
    base = slugify(name)
    slug, i = base, 2
    while slug in ids:
        slug = f"{base}-{i}"
        i += 1
    return slug


def all_categories(products=None):
    if products is None:
        products = load_products()
    return sorted({p["category"] for p in products if p.get("category")},
                  key=str.lower)
