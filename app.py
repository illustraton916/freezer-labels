import io
import json
import os
import re
from datetime import date, datetime, timedelta

from flask import (Flask, flash, redirect, render_template_string, request,
                   send_file, url_for)
from PIL import Image, ImageDraw, ImageFont

from brother_ql.conversion import convert
from brother_ql.backends.helpers import send
from brother_ql.raster import BrotherQLRaster

# --- Налаштування (перевизначаються через env у docker-compose) ---
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

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

WIDTH  = 696   # друкована ширина 62мм стрічки @300dpi, у пікселях
MARGIN = 20

# Масштаб шрифтів наліпки (1.1 = на 10% більші за базовий дизайн)
SCALE = float(os.environ.get("LABEL_SCALE", "1.1"))


def _fs(px):
    return max(1, round(px * SCALE))


# Шрифти
F_NAME      = ImageFont.truetype(FONT_BOLD, _fs(46))
F_MARK      = ImageFont.truetype(FONT_REG, _fs(26))
F_H         = ImageFont.truetype(FONT_BOLD, _fs(30))
F_BODY      = ImageFont.truetype(FONT_REG, _fs(26))
F_BODY_BOLD = ImageFont.truetype(FONT_BOLD, _fs(26))
F_PRICE     = ImageFont.truetype(FONT_BOLD, _fs(30))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret")


# ----------------------------- дані -----------------------------
def load_products():
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_product(pid):
    for p in load_products():
        if p["id"] == pid:
            return p
    return None


def save_products(products):
    # products.json бет-маунтиться як файл, тому пишемо «на місці»: rename поверх
    # bind-mount файлу дав би EBUSY. Файл малий, один користувач — цього досить.
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
        f.write("\n")


# -------- керування продуктами (адмінка) --------
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


# --------------------------- утиліти ----------------------------
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


# --------------------- рушій розкладки --------------------------
def wrap(draw, text, font, maxw):
    """Жадібний перенос по словах під ширину maxw."""
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        if draw.textlength(cur + " " + w, font=font) <= maxw:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


class Canvas:
    """Малює текст зверху вниз на високому полотні, потім обрізає під контент."""

    def __init__(self, width=WIDTH, margin=MARGIN, height=4000):
        self.width = width
        self.margin = margin
        self.img = Image.new("L", (width, height), 255)
        self.d = ImageDraw.Draw(self.img)
        self.y = margin
        self.maxw = width - 2 * margin

    def gap(self, px):
        self.y += px

    def text(self, text, font, fill=0, indent=0):
        x = self.margin + indent
        asc, desc = font.getmetrics()
        for ln in wrap(self.d, text, font, self.maxw - indent):
            self.d.text((x, self.y), ln, font=font, fill=fill)
            self.y += asc + desc + 4

    def text_fit(self, text, font_path, max_size, min_size, fill=0):
        """Один рядок; за потреби зменшує шрифт до min_size, щоб не переносити."""
        size = max_size
        while size > min_size and self.d.textlength(
                text, font=ImageFont.truetype(font_path, size)) > self.maxw:
            size -= 2
        font = ImageFont.truetype(font_path, size)
        self.d.text((self.margin, self.y), text, font=font, fill=fill)
        asc, desc = font.getmetrics()
        self.y += asc + desc + 4

    def finish(self):
        return self.img.crop((0, 0, self.width, self.y + self.margin))


# --------------------- рендери шаблонів -------------------------
def render_frozen(p, weight_g):
    c = Canvas()
    c.text(p["name"], F_NAME)
    if p.get("frozen_mark"):
        c.text(p["frozen_mark"], F_MARK, fill=110)
    c.gap(12)

    c.text("Ainesosat:", F_H)
    for ing in p.get("ingredients", []):
        c.text(ing, F_BODY)
    if p.get("allergens"):
        c.text(p["allergens"], F_BODY_BOLD)
    c.gap(10)

    if p.get("instructions"):
        c.text("Valmistusohje:", F_H)
        c.text(p["instructions"], F_BODY)
        c.gap(10)

    if p.get("storage"):
        c.text("Säilytysolosuhteet:", F_H)
        c.text(p["storage"], F_BODY)
        c.gap(10)

    n = p.get("nutrition")
    if n:
        c.text("Ravintoarvo 100 g:ssa:", F_H)
        c.text(f"Energia-arvo: {n['energy_kj']} kJ / {n['energy_kcal']} kcal", F_BODY)
        c.text(f"Rasvat: {n['fat']}", F_BODY)
        c.text(f"joista tyydyttyneitä: {n['saturated']}", F_BODY, indent=30)
        c.text(f"Hiilihydraatit: {n['carbs']}", F_BODY)
        c.text(f"Proteiinit: {n['protein']}", F_BODY)
        c.text(f"Suola: {n['salt']}", F_BODY)
        c.gap(10)

    made = date.today()
    best = made + timedelta(days=p["shelf_life_days"])
    c.text(f"Valmistuspäivä: {fmt_date(made)}", F_BODY)
    c.text(f"Parasta ennen: {fmt_date(best)}", F_BODY)
    c.gap(8)

    if weight_g:
        c.text(f"Nettopaino: {fmt_weight(weight_g)}", F_PRICE)
        c.text(f"Hinta: {eur(calc_price(weight_g, p['price_per_kg']))} "
               f"({rate(p['price_per_kg'])})", F_PRICE)
    else:
        c.text("Nettopaino: ______________", F_PRICE)
        c.text(f"Hinta: {rate(p['price_per_kg'])}", F_PRICE)
    return c.finish()


def render_deli(p, weight_g):
    c = Canvas()
    c.text(p["name"], F_NAME)
    c.gap(12)

    c.text("Ainekset: " + p.get("ingredients_text", ""), F_BODY)
    c.gap(10)

    if weight_g:
        c.text_fit(f"Paino: {fmt_weight(weight_g)} | "
                   f"Hinta: {eur(calc_price(weight_g, p['price_per_kg']))} "
                   f"({rate(p['price_per_kg'])})", FONT_BOLD, _fs(30), _fs(22))
    else:
        c.text_fit(f"Paino: ______ | Hinta: {rate(p['price_per_kg'])}", FONT_BOLD, _fs(30), _fs(22))

    made = date.today()
    best = made + timedelta(days=p["shelf_life_days"])
    c.text(f"Valmistettu: {fmt_date(made)}", F_BODY)
    c.text(f"Parasta ennen: {fmt_date(best)}", F_BODY)
    return c.finish()


RENDERERS = {"frozen": render_frozen, "deli": render_deli}


def make_label(p, weight_g=None):
    renderer = RENDERERS.get(p["type"])
    if renderer is None:
        raise ValueError(f"Невідомий тип наліпки: {p['type']}")
    return renderer(p, weight_g)


# ------------------------------ друк ----------------------------
def print_label(img, qty=1):
    if PRINT_MODE == "mock":
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        for i in range(max(1, int(qty))):
            path = os.path.join(OUTPUT_DIR, f"{datetime.now():%Y%m%d-%H%M%S}-{i+1}.png")
            img.save(path)
            print(f"[MOCK] наліпка збережена у {path} (друку немає)", flush=True)
        return

    qlr = BrotherQLRaster(PRINTER_MODEL)
    qlr.exception_on_warning = True
    instructions = convert(
        qlr=qlr, images=[img], label=LABEL_SIZE, rotate="0",
        threshold=70.0, dither=False, compress=False,
        red=False, dpi_600=False, hq=PRINT_HQ, cut=True,
    )
    for _ in range(max(1, int(qty))):
        send(instructions=instructions, printer_identifier=DEVICE,
             backend_identifier=BACKEND, blocking=True)


# ------------------------------ веб -----------------------------
PAGE = """
<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Наліпки — Друк</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 16px;
         background: #0f1115; color: #e8eaed; }
  h1 { font-size: 24px; margin: 8px 4px 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 16px; align-items: start; }
  .card { background: #1a1d24; border: 1px solid #2a2f3a; border-radius: 14px;
          padding: 14px; }
  .head { display: flex; justify-content: space-between; align-items: baseline;
          gap: 8px; margin-bottom: 10px; }
  .title { font-size: 18px; font-weight: 600; }
  .note { font-size: 13px; color: #8b93a1; }
  .badge { font-size: 12px; padding: 3px 9px; border-radius: 999px; white-space: nowrap; }
  .badge.frozen { background: #1e3a5f; color: #9cd0ff; }
  .badge.deli   { background: #14532d; color: #9be3ad; }
  .card img { width: 100%; background: #fff; border-radius: 8px; display: block; }
  .row { display: flex; gap: 10px; align-items: center; margin-top: 12px; }
  label.f { font-size: 14px; color: #b7bdc7; }
  input { font-size: 20px; padding: 10px; border-radius: 10px;
          border: 1px solid #333; background: #11141a; color: #fff; width: 100%; }
  input.w { width: 100px; } input.q { width: 64px; }
  button { flex: 1; font-size: 20px; padding: 14px; border: 0; border-radius: 10px;
           background: #2563eb; color: #fff; font-weight: 600; cursor: pointer; }
  button:active { background: #1e4fc4; }
  .flash { padding: 14px; border-radius: 10px; margin-bottom: 16px; font-size: 18px; }
  .ok { background: #14532d; } .err { background: #5a1620; }
  .topbar { display: flex; justify-content: space-between; align-items: center;
            flex-wrap: wrap; gap: 10px; }
  .adminlink { color: #9cd0ff; text-decoration: none; font-size: 16px;
               border: 1px solid #2a2f3a; padding: 8px 14px; border-radius: 10px; }
  .toolbar { margin: 4px 0 16px; }
  .search { width: 100%; box-sizing: border-box; font-size: 18px; padding: 12px 14px;
            border-radius: 12px; border: 1px solid #2a2f3a; background: #11141a;
            color: #fff; }
  .tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  .tab { flex: 0 0 auto; font-size: 15px; padding: 9px 16px; border: 1px solid #2a2f3a;
         border-radius: 999px; background: #1a1d24; color: #b7bdc7; cursor: pointer;
         font-weight: 500; transition: background .2s, color .2s, border-color .2s; }
  .tab.active { background: #2563eb; border-color: #2563eb; color: #fff; }
  .cat { font-size: 12px; color: #8b93a1; margin-top: 2px; }
  .empty { color: #8b93a1; font-size: 16px; padding: 24px 4px; display: none; }
  @media (max-width: 520px) {
    body { padding: 10px; }
    .grid { grid-template-columns: 1fr; gap: 12px; }
    h1 { font-size: 21px; }
    button { padding: 16px; font-size: 21px; }
    input.w { width: 110px; } input.q { width: 72px; }
  }
  @media (prefers-reduced-motion: no-preference) {
    body { animation: pagefade .25s ease; }
  }
  @keyframes pagefade { from { opacity: 0; } to { opacity: 1; } }
  @view-transition { navigation: auto; }
</style>
</head>
<body>
  <div class="topbar"><h1>🏷️ Друк наліпок</h1>
    <a class="adminlink" href="{{ url_for('admin') }}">⚙️ Керування</a></div>
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat, m in msgs %}<div class="flash {{ cat }}">{{ m }}</div>{% endfor %}
  {% endwith %}
  <div class="toolbar">
    <input class="search" id="search" type="search" placeholder="🔎 Пошук продукту…"
           oninput="applyFilter()">
    {% if categories %}
    <div class="tabs">
      <button type="button" class="tab active" data-cat="" onclick="setTab(this)">Усі</button>
      {% for c in categories %}
      <button type="button" class="tab" data-cat="{{ c }}" onclick="setTab(this)">{{ c }}</button>
      {% endfor %}
    </div>
    {% endif %}
  </div>
  <div class="grid">
    {% for p in products %}
    <div class="card" data-name="{{ p.name }}" data-note="{{ p.note_uk or '' }}"
         data-cat="{{ p.category or '' }}">
      <div class="head">
        <div>
          <div class="title">{{ p.name }}</div>
          {% if p.note_uk %}<div class="note">{{ p.note_uk }}</div>{% endif %}
          {% if p.category %}<div class="cat">🗂 {{ p.category }}</div>{% endif %}
        </div>
        <span class="badge {{ p.type }}">{{ "заморозка" if p.type == "frozen" else "делі" }}</span>
      </div>
      <img id="img-{{ p.id }}" src="{{ url_for('preview', id=p.id) }}" alt="{{ p.name }}">
      <form method="post" action="{{ url_for('do_print') }}">
        <input type="hidden" name="id" value="{{ p.id }}">
        <div class="row">
          <div><label class="f">Вага, г</label>
            <input class="w" type="text" inputmode="decimal" name="weight"
                   id="w-{{ p.id }}" placeholder="напр. 350"
                   oninput="upd('{{ p.id }}')"></div>
          <div><label class="f">К-сть</label>
            <input class="q" type="number" name="qty" value="1" min="1" max="20"></div>
        </div>
        <div class="row"><button type="submit">Друк</button></div>
      </form>
    </div>
    {% endfor %}
  </div>
  <div class="empty" id="empty">Нічого не знайдено 🤷</div>
  <script>
    var activeCat = "";
    function setTab(btn) {
      activeCat = btn.dataset.cat;
      document.querySelectorAll('.tab').forEach(function (t) {
        t.classList.toggle('active', t === btn);
      });
      applyFilter();
    }
    function applyFilter() {
      var q = (document.getElementById('search').value || '').trim().toLowerCase();
      var shown = 0;
      document.querySelectorAll('.card').forEach(function (card) {
        var hay = (card.dataset.name + ' ' + card.dataset.note).toLowerCase();
        var ok = (!q || hay.indexOf(q) !== -1) &&
                 (!activeCat || card.dataset.cat === activeCat);
        card.style.display = ok ? '' : 'none';
        if (ok) shown++;
      });
      document.getElementById('empty').style.display = shown ? 'none' : 'block';
    }
    function upd(id) {
      var w = document.getElementById('w-' + id).value;
      document.getElementById('img-' + id).src =
        '{{ url_for("preview") }}?id=' + encodeURIComponent(id) +
        '&weight=' + encodeURIComponent(w) + '&t=' + Date.now();
    }
  </script>
</body>
</html>
"""


def _all_categories(products=None):
    if products is None:
        products = load_products()
    return sorted({p["category"] for p in products if p.get("category")},
                  key=str.lower)


@app.route("/")
def index():
    products = sorted(load_products(), key=lambda p: p["name"].lower())
    return render_template_string(PAGE, products=products,
                                  categories=_all_categories(products))


@app.route("/preview")
def preview():
    pid = request.args.get("id", "")
    p = get_product(pid)
    if not p:
        return "not found", 404
    weight = parse_weight(request.args.get("weight"))
    img = make_label(p, weight)
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
    try:
        print_label(make_label(p, weight), qty=qty)
        info = p["name"]
        if weight:
            info += f" · {fmt_weight(weight)} · {eur(calc_price(weight, p['price_per_kg']))}"
        prefix = "[ТЕСТ] Збережено у файл (без друку): " if PRINT_MODE == "mock" else "Надруковано: "
        flash(f"{prefix}{info} ×{qty}", "ok")
    except Exception as e:
        flash(f"Помилка друку: {e}", "err")
    return redirect(url_for("index"), code=303)


ADMIN_STYLE = """
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 16px;
         background: #0f1115; color: #e8eaed; }
  h1 { font-size: 24px; margin: 8px 4px 20px; }
  .topbar { display: flex; justify-content: space-between; align-items: center;
            flex-wrap: wrap; gap: 12px; }
  .note { font-size: 13px; color: #8b93a1; }
  .badge { font-size: 12px; padding: 3px 9px; border-radius: 999px; white-space: nowrap; }
  .badge.frozen { background: #1e3a5f; color: #9cd0ff; }
  .badge.deli { background: #14532d; color: #9be3ad; }
  .flash { padding: 14px; border-radius: 10px; margin-bottom: 16px; font-size: 18px; }
  .ok { background: #14532d; } .err { background: #5a1620; }
  .btn { display: inline-block; font-size: 15px; padding: 9px 14px; border: 0;
         border-radius: 9px; background: #2a2f3a; color: #e8eaed; text-decoration: none;
         cursor: pointer; }
  .btn.add, .btn.save { background: #2563eb; color: #fff; font-weight: 600; }
  .btn.del { background: #5a1620; color: #ffd7dd; }
  .lnk { color: #9cd0ff; text-decoration: none; margin-right: 14px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th, td { padding: 11px 10px; border-bottom: 1px solid #2a2f3a; text-align: left;
           vertical-align: top; }
  th { color: #8b93a1; font-weight: 600; font-size: 14px; }
  td.actions { white-space: nowrap; }
  td.actions form { display: inline; }
  @media (prefers-reduced-motion: no-preference) {
    body { animation: pagefade .25s ease; }
  }
  @keyframes pagefade { from { opacity: 0; } to { opacity: 1; } }
  @view-transition { navigation: auto; }
"""

ADMIN_LIST = """
<!doctype html><html lang="uk"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Керування продуктами</title><style>""" + ADMIN_STYLE + """</style></head><body>
  <div class="topbar">
    <h1>⚙️ Керування продуктами</h1>
    <div>
      <a class="lnk" href="{{ url_for('index') }}">← До друку</a>
      <a class="btn add" href="{{ url_for('admin_new') }}">+ Додати продукт</a>
    </div>
  </div>
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat, m in msgs %}<div class="flash {{ cat }}">{{ m }}</div>{% endfor %}
  {% endwith %}
  {% if products %}
  <table>
    <thead><tr><th>Назва</th><th>Тип</th><th>€/кг</th><th>Термін</th><th></th></tr></thead>
    <tbody>
    {% for p in products %}
      <tr>
        <td>{{ p.name }}{% if p.note_uk %}<div class="note">{{ p.note_uk }}</div>{% endif %}{% if p.category %}<div class="note">🗂 {{ p.category }}</div>{% endif %}</td>
        <td><span class="badge {{ p.type }}">{{ 'заморозка' if p.type == 'frozen' else 'делі' }}</span></td>
        <td>{{ '%g'|format(p.price_per_kg|float) }}</td>
        <td>{{ p.shelf_life_days }} дн</td>
        <td class="actions">
          <a class="btn" href="{{ url_for('admin_edit', pid=p.id) }}">Редагувати</a>
          <form method="post" action="{{ url_for('admin_delete', pid=p.id) }}"
                onsubmit="return confirm('Видалити «{{ p.name }}»?');">
            <button class="btn del" type="submit">Видалити</button>
          </form>
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
    <p class="note">Ще немає продуктів. Натисни «Додати продукт».</p>
  {% endif %}
</body></html>
"""

ADMIN_FORM = """
<!doctype html><html lang="uk"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title><style>""" + ADMIN_STYLE + """
  form { max-width: 720px; }
  .field { margin-bottom: 14px; }
  label { display: block; font-size: 14px; color: #b7bdc7; margin-bottom: 5px; }
  input, textarea, select { width: 100%; font-size: 16px; padding: 10px;
      border-radius: 8px; border: 1px solid #333; background: #11141a; color: #fff;
      box-sizing: border-box; }
  textarea { min-height: 70px; font-family: inherit; resize: vertical; }
  fieldset { border: 1px solid #2a2f3a; border-radius: 10px; margin: 14px 0; padding: 12px; }
  legend { color: #8b93a1; padding: 0 6px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .actions { display: flex; gap: 12px; margin-top: 18px; }
</style></head><body>
  <h1>{{ title }}</h1>
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat, m in msgs %}<div class="flash {{ cat }}">{{ m }}</div>{% endfor %}
  {% endwith %}
  <form method="post" action="{{ url_for('admin_save') }}">
    <input type="hidden" name="id" value="{{ fv.get('id','') }}">
    <div class="field"><label>Тип наліпки</label>
      <select id="type" name="type" onchange="toggleType()">
        <option value="frozen" {{ 'selected' if fv.get('type')=='frozen' else '' }}>заморозка (frozen)</option>
        <option value="deli" {{ 'selected' if fv.get('type')=='deli' else '' }}>делі (deli)</option>
      </select></div>
    <div class="field"><label>Назва (фінською)</label>
      <input name="name" value="{{ fv.get('name','') }}" required></div>
    <div class="field"><label>Примітка (укр., на наліпку не друкується)</label>
      <input name="note_uk" value="{{ fv.get('note_uk','') }}"></div>
    <div class="field"><label>Категорія (для групування, напр. «Салати»)</label>
      <input name="category" value="{{ fv.get('category','') }}" list="cats">
      <datalist id="cats">
        {% for c in categories %}<option value="{{ c }}">{% endfor %}
      </datalist></div>
    <div class="grid2">
      <div class="field"><label>Ціна, €/кг</label>
        <input name="price_per_kg" value="{{ fv.get('price_per_kg','') }}" inputmode="decimal" required></div>
      <div class="field"><label>Термін, днів</label>
        <input name="shelf_life_days" value="{{ fv.get('shelf_life_days','') }}" inputmode="numeric" required></div>
    </div>
    <fieldset class="frozen-only"><legend>Заморозка</legend>
      <div class="field"><label>Позначка типу</label>
        <input name="frozen_mark" value="{{ fv.get('frozen_mark','') }}" placeholder="pakastettu tuote"></div>
      <div class="field"><label>Склад (Ainesosat) — по рядку на пункт</label>
        <textarea name="ingredients" placeholder="Taikina: ...&#10;Täyte: ...">{{ fv.get('ingredients','') }}</textarea></div>
      <div class="field"><label>Алергени (друкуються жирним)</label>
        <input name="allergens" value="{{ fv.get('allergens','') }}"></div>
      <div class="field"><label>Інструкція (Valmistusohje)</label>
        <textarea name="instructions">{{ fv.get('instructions','') }}</textarea></div>
      <div class="field"><label>Зберігання (Säilytysolosuhteet)</label>
        <textarea name="storage">{{ fv.get('storage','') }}</textarea></div>
      <fieldset><legend>Ravintoarvo /100 g (необов'язково)</legend>
        <div class="grid2">
          <div class="field"><label>Energia, kJ</label><input name="n_energy_kj" value="{{ fv.get('n_energy_kj','') }}"></div>
          <div class="field"><label>Energia, kcal</label><input name="n_energy_kcal" value="{{ fv.get('n_energy_kcal','') }}"></div>
          <div class="field"><label>Rasvat</label><input name="n_fat" value="{{ fv.get('n_fat','') }}" placeholder="8 g"></div>
          <div class="field"><label>joista tyydyttyneitä</label><input name="n_saturated" value="{{ fv.get('n_saturated','') }}" placeholder="2 g"></div>
          <div class="field"><label>Hiilihydraatit</label><input name="n_carbs" value="{{ fv.get('n_carbs','') }}" placeholder="22 g"></div>
          <div class="field"><label>Proteiinit</label><input name="n_protein" value="{{ fv.get('n_protein','') }}" placeholder="10 g"></div>
          <div class="field"><label>Suola</label><input name="n_salt" value="{{ fv.get('n_salt','') }}" placeholder="1 g"></div>
        </div>
      </fieldset>
    </fieldset>

    <fieldset class="deli-only"><legend>Делі</legend>
      <div class="field"><label>Склад (Ainekset) — одним абзацом</label>
        <textarea name="ingredients_text">{{ fv.get('ingredients_text','') }}</textarea></div>
    </fieldset>

    <div class="actions">
      <button class="btn save" type="submit">Зберегти</button>
      <a class="btn" href="{{ url_for('admin') }}">Скасувати</a>
    </div>
  </form>
  <script>
    function toggleType() {
      var t = document.getElementById('type').value;
      document.querySelectorAll('.frozen-only').forEach(function (e) { e.style.display = (t === 'frozen') ? '' : 'none'; });
      document.querySelectorAll('.deli-only').forEach(function (e) { e.style.display = (t === 'deli') ? '' : 'none'; });
    }
    toggleType();
  </script>
</body></html>
"""


@app.route("/admin")
def admin():
    return render_template_string(ADMIN_LIST, products=load_products())


@app.route("/admin/new")
def admin_new():
    fv = {"type": "frozen", "frozen_mark": "pakastettu tuote"}
    return render_template_string(ADMIN_FORM, fv=fv, title="Новий продукт",
                                  categories=_all_categories())


@app.route("/admin/edit/<pid>")
def admin_edit(pid):
    p = get_product(pid)
    if not p:
        flash("Продукт не знайдено", "err")
        return redirect(url_for("admin"), code=303)
    return render_template_string(ADMIN_FORM, fv=product_to_fields(p),
                                  title="Редагувати продукт",
                                  categories=_all_categories())


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
        return render_template_string(ADMIN_FORM, fv=request.form, title=title,
                                      categories=_all_categories()), 400


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
