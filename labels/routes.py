"""Flask-роути: сторінка друку, прев'ю, друк, адмінка (CRUD).

Guard неповної наліпки діє і тут. Раніше він стояв лише на `/api/*`, а старий
UI кликав `print_label(make_label(...))` напряму — тож через порт 5000 (у проді
він увімкнений, PRINT_MODE=real) з принтера виходила юридично неповна наліпка,
напр. бойовий `kaalikarylet` без складу. Правило одне для обох шляхів друку:
рішення «що є повною наліпкою» живе в labels/validation.py, а не в тому, який
порт набрав користувач.

`/preview` guard'а НЕ має навмисно: це перегляд на екрані, а не друк. Щоб
перегляд не читався як дозвіл, картка такого продукту на сторінці друку
показує, чого бракує, і кнопка друку в ній вимкнена.
"""
import io

from flask import (flash, redirect, render_template, request, send_file,
                   url_for)

from . import config
from .formatting import calc_price, eur, fmt_weight, parse_weight
from .forms import build_product_from_form, product_to_fields
from .printing import print_label
from .rendering import make_label
from .store import (all_categories, get_product, load_products, save_products)
from .validation import PayloadError, missing_label_fields, require_complete_label

# Назви полів людською мовою: машинне «ingredients» у flash-повідомленні нічого
# не каже тому, хто стоїть біля принтера й хоче зрозуміти, що дозаповнити.
# Родовий відмінок — щоб вставлялося в «бракує …» і «немає …» без переробки.
FIELD_UK = {
    "ingredients": "складу (Ainekset)",
    "ingredients_text": "складу (Ainekset)",
    "allergens": "алергенів (Allergeenit)",
    "storage": "умов зберігання (Säilytys)",
    "nutrition": "харчової цінності (Ravintosisältö)",
}


def missing_uk(product):
    """Перелік відсутніх полів наліпки українською (порожній = друк дозволено)."""
    return [FIELD_UK.get(f, f) for f in missing_label_fields(product)]


def register_routes(app):

    @app.route("/")
    def index():
        products = sorted(load_products(), key=lambda p: p["name"].lower())
        # Рахуємо тим самим правилом, яким відмовить /print: картка не має права
        # обіцяти друк, якого не буде.
        missing_by_id = {p["id"]: m for p in products
                         if (m := missing_uk(p))}
        return render_template("print.html", products=products,
                               categories=all_categories(products),
                               missing_by_id=missing_by_id)

    @app.route("/preview")
    def preview():
        p = get_product(request.args.get("id", ""))
        if not p:
            return "not found", 404
        img = make_label(p, parse_weight(request.args.get("weight")))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")

    @app.route("/print", methods=["POST"])
    def do_print():
        pid = request.form["id"]
        qty = request.form.get("qty", "1")
        weight = parse_weight(request.form.get("weight"))
        p = get_product(pid)
        if not p:
            flash(f"Продукт «{pid}» не знайдено", "err")
            return redirect(url_for("index"), code=303)
        # Той самий guard, що й на /api/print. Стоїть ДО make_label: неповну
        # наліпку не можна ані надрукувати, ані «про всяк випадок» зрендерити
        # у файл — саме такий файл потім і приклеюють.
        try:
            require_complete_label(p)
        except PayloadError:
            flash(f"Друк заборонено: «{p['name']}» — неповна наліпка, бракує "
                  + ", ".join(missing_uk(p))
                  + ". Це обов'язкові дані харчового маркування — допиши їх у "
                    "«Керування» і друкуй знову.", "err")
            return redirect(url_for("index"), code=303)
        try:
            print_label(make_label(p, weight), qty=qty)
            info = p["name"]
            if weight:
                info += f" · {fmt_weight(weight)} · {eur(calc_price(weight, p['price_per_kg']))}"
            prefix = ("[ТЕСТ] Збережено у файл (без друку): "
                      if config.PRINT_MODE == "mock" else "Надруковано: ")
            flash(f"{prefix}{info} ×{qty}", "ok")
        except Exception as e:
            flash(f"Помилка друку: {e}", "err")
        return redirect(url_for("index"), code=303)

    @app.route("/admin")
    def admin():
        return render_template("admin_list.html", products=load_products())

    @app.route("/admin/new")
    def admin_new():
        fv = {"type": "frozen", "frozen_mark": "pakastettu tuote"}
        return render_template("admin_form.html", fv=fv, title="Новий продукт",
                               categories=all_categories())

    @app.route("/admin/edit/<pid>")
    def admin_edit(pid):
        p = get_product(pid)
        if not p:
            flash("Продукт не знайдено", "err")
            return redirect(url_for("admin"), code=303)
        return render_template("admin_form.html", fv=product_to_fields(p),
                               title="Редагувати продукт",
                               categories=all_categories())

    @app.route("/admin/save", methods=["POST"])
    def admin_save():
        existing_id = (request.form.get("id") or "").strip() or None
        try:
            products = load_products()
            p = build_product_from_form(request.form, existing_id)
            if existing_id and any(x["id"] == existing_id for x in products):
                products = [p if x["id"] == existing_id else x for x in products]
            else:
                products.append(p)
            save_products(products)
            flash(f"Збережено: {p['name']}", "ok")
            return redirect(url_for("admin"), code=303)
        except ValueError as e:
            flash(f"Помилка: {e}", "err")
            title = "Редагувати продукт" if existing_id else "Новий продукт"
            return render_template("admin_form.html", fv=request.form, title=title,
                                   categories=all_categories()), 400

    @app.route("/admin/delete/<pid>", methods=["POST"])
    def admin_delete(pid):
        products = load_products()
        remaining = [p for p in products if p["id"] != pid]
        if len(remaining) != len(products):
            save_products(remaining)
            flash("Видалено", "ok")
        else:
            flash("Продукт не знайдено", "err")
        return redirect(url_for("admin"), code=303)
