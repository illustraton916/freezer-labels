"""Рушій розкладки (Canvas) і рендери наліпок (frozen / deli).

Малювання НЕ змінювалося: ті самі шрифти, ті самі кроки y, той самий перенос.
Додано рівно дві речі, обидві не впливають на пікселі:
  * `made_on` / `best_before` параметрами (раніше date.today() був зашитий
    усередині — через це рендер був недетермінований і не міг бути сервісом);
  * необов'язкова трасa розкладки (`trace`) — журнал того, ЩО саме намальовано,
    щоб при майбутньому розходженні хешів бачити зсунутий рядок, а не «хеш інший».
"""
import os
from datetime import date, timedelta

from PIL import Image, ImageDraw, ImageFont

from .config import (WIDTH, MARGIN, SCALE, FONT_BOLD, F_NAME, F_MARK, F_H,
                     F_BODY, F_BODY_BOLD, F_PRICE, fs)
from .formatting import calc_price, eur, fmt_date, fmt_weight, rate


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


def _font_name(font):
    """Ім'я файлу шрифту без шляху — щоб траса не залежала від машини."""
    path = getattr(font, "path", None)
    return os.path.basename(path) if path else str(font)


class Canvas:
    """Малює текст зверху вниз на високому полотні, потім обрізає під контент."""

    def __init__(self, width=WIDTH, margin=MARGIN, height=4000, trace=None):
        self.width = width
        self.margin = margin
        self.img = Image.new("L", (width, height), 255)
        self.d = ImageDraw.Draw(self.img)
        self.y = margin
        self.maxw = width - 2 * margin
        # trace = список, у який дописуються draw-ops; None = діагностика вимкнена
        self.trace = trace

    # Траса пишеться тільки коли її просили: без неї жодного зайвого textlength.
    @property
    def tracing(self):
        return self.trace is not None

    def gap(self, px):
        if self.tracing:
            self.trace.append({"op": "gap", "px": px,
                               "y_before": self.y, "y_after": self.y + px})
        self.y += px

    def text(self, text, font, fill=0, indent=0):
        x = self.margin + indent
        asc, desc = font.getmetrics()
        lines = wrap(self.d, text, font, self.maxw - indent)
        if self.tracing:
            self.trace.append({
                "op": "block", "source": text, "lines": lines,
                "wrapped_into": len(lines),                 # >1 = був перенос
                "font": _font_name(font), "size_px": font.size,
                "x": x, "y": self.y, "fill": fill, "indent": indent,
                "maxw": self.maxw - indent, "ascent": asc, "descent": desc,
            })
        for i, ln in enumerate(lines):
            if self.tracing:
                self.trace.append({
                    "op": "text", "text": ln, "font": _font_name(font),
                    "size_px": font.size, "x": x, "y": self.y, "fill": fill,
                    "line": i + 1, "of": len(lines),
                    "width_px": round(self.d.textlength(ln, font=font), 2),
                })
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
        if self.tracing:
            self.trace.append({
                "op": "text_fit", "text": text, "font": os.path.basename(font_path),
                "size_px": size, "size_tried_from": max_size, "size_floor": min_size,
                "shrunk": size != max_size,
                "x": self.margin, "y": self.y, "fill": fill,
                "width_px": round(self.d.textlength(text, font=font), 2),
                "maxw": self.maxw, "ascent": asc, "descent": desc,
            })
        self.y += asc + desc + 4

    def finish(self):
        img = self.img.crop((0, 0, self.width, self.y + self.margin))
        if self.tracing:
            self.trace.append({"op": "finish", "width": self.width,
                               "height": img.height, "y_content_end": self.y,
                               "margin": self.margin})
        return img


def label_dates(p, made_on=None, best_before=None):
    """Дати наліпки. made_on=None → сьогодні (стара поведінка веб-інтерфейсу)."""
    made = made_on or date.today()
    best = best_before or made + timedelta(days=p["shelf_life_days"])
    return made, best


def render_frozen(p, weight_g, made_on=None, best_before=None, trace=None):
    c = Canvas(trace=trace)
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

    made, best = label_dates(p, made_on, best_before)
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


def render_deli(p, weight_g, made_on=None, best_before=None, trace=None):
    c = Canvas(trace=trace)
    c.text(p["name"], F_NAME)
    c.gap(12)

    c.text("Ainekset: " + p.get("ingredients_text", ""), F_BODY)
    c.gap(10)

    if weight_g:
        c.text_fit(f"Paino: {fmt_weight(weight_g)} | "
                   f"Hinta: {eur(calc_price(weight_g, p['price_per_kg']))} "
                   f"({rate(p['price_per_kg'])})", FONT_BOLD, fs(30), fs(22))
    else:
        c.text_fit(f"Paino: ______ | Hinta: {rate(p['price_per_kg'])}", FONT_BOLD, fs(30), fs(22))

    made, best = label_dates(p, made_on, best_before)
    c.text(f"Valmistettu: {fmt_date(made)}", F_BODY)
    c.text(f"Parasta ennen: {fmt_date(best)}", F_BODY)
    return c.finish()


RENDERERS = {"frozen": render_frozen, "deli": render_deli}


def make_label(p, weight_g=None, made_on=None, best_before=None, trace=None):
    """Наліпка продукту p.

    weight_g    — None = наліпка з порожнім місцем під вагу;
    made_on     — дата виготовлення (datetime.date); None = сьогодні за годинником
                  контейнера (стара поведінка; нові /api/* завжди передають дату);
    best_before — None = made_on + shelf_life_days;
    trace       — список, у який пишеться траса розкладки (діагностика).
    """
    renderer = RENDERERS.get(p["type"])
    if renderer is None:
        raise ValueError(f"Невідомий тип наліпки: {p['type']}")
    return renderer(p, weight_g, made_on=made_on, best_before=best_before, trace=trace)


def make_label_traced(p, weight_g=None, made_on=None, best_before=None):
    """(img, trace) — те саме, але з обов'язковою трасою розкладки."""
    ops = []
    # Дати рахуємо ДО рендеру й передаємо явно: інакше опівночі траса могла б
    # показати не ту дату, що намальована.
    made, best = label_dates(p, made_on, best_before)
    img = make_label(p, weight_g, made_on=made, best_before=best, trace=ops)
    meta = {
        "canvas": {"width": WIDTH, "margin": MARGIN, "scale": SCALE},
        "product_id": p.get("id"),
        "type": p.get("type"),
        "weight_g": weight_g,
        "made_on": made.isoformat(),
        "best_before": best.isoformat(),
        "height": img.height,
        "ops": ops,
    }
    return img, meta
