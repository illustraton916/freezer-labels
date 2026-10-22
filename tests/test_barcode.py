# -*- coding: utf-8 -*-
"""Тести EAN-13 (labels/barcode.py), пакунка й штрих-кода на наліпці.

Запуск із кореня репозиторію:
    python -m unittest discover -s tests

Лише stdlib і те, що вже є в requirements.txt (Pillow — для рендер-тестів).
Golden-тест тут допоміжний: він бере 3 продукти baseline, якщо еталон є на
машині (ті самі шляхи/змінні, що й у tools/golden_gate.py), і пропускається,
якщо його немає — повноцінний доказ «66/66 без змін» дає сам гейт.
"""
import hashlib
import io
import json
import os
import sys
import unittest
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from labels.barcode import (GUARD_MODULES, MODULES, draw_ean13_bars,  # noqa: E402
                            ean13_check_digit, ean13_is_valid, ean13_modules)
from labels.config import (BARCODE_BAR_H, BARCODE_GUARD_EXTRA,  # noqa: E402
                           BARCODE_MODULE_PX, BARCODE_QUIET_LEFT,
                           BARCODE_QUIET_RIGHT, WIDTH)
from labels.rendering import make_label, make_label_traced  # noqa: E402
from labels.validation import (_check_product, label_warnings,  # noqa: E402
                               validate_payload)

# Відомі коди: 4006381333931 — класичний приклад EAN-13 у документації,
# 5901234123457 — приклад із Вікіпедії. Обидва з правильною контрольною.
KNOWN_VALID = ("4006381333931", "5901234123457", "6400000000019")

# Вручну складені зі стандартних таблиць (L/G/R + парність за першою цифрою):
#   4006381333931: перша 4 → LGLLGG для 0,0,6,3,8,1; права 3,3,3,9,3,1 (R)
#   5901234123457: перша 5 → LGGLLG для 9,0,1,2,3,4; права 1,2,3,4,5,7 (R)
HAND_VERIFIED = {
    "4006381333931":
        "101" + "0001101" + "0100111" + "0101111" + "0111101" + "0001001"
        + "0110011" + "01010" + "1000010" + "1000010" + "1000010" + "1110100"
        + "1000010" + "1100110" + "101",
    "5901234123457":
        "101" + "0001011" + "0100111" + "0110011" + "0010011" + "0111101"
        + "0011101" + "01010" + "1100110" + "1101100" + "1000010" + "1011100"
        + "1001110" + "1000100" + "101",
}

# Вигаданий продукт — жодних бойових даних.
DEMO_FROZEN = {
    "id": "demo-pakaste", "type": "frozen", "name": "DEMO PAKASTE",
    "price_per_kg": 16.0, "shelf_life_days": 90,
    "frozen_mark": "pakastettu tuote",
    "ingredients": ["Taikina: vehnäjauho, vesi, suola.",
                    "Täyte: peruna, sipuli, auringonkukkaöljy."],
    "allergens": "Sisältää gluteenia.",
    "instructions": "Keitä 5 minuuttia.",
    "storage": "Säilytä -18 °C:ssa.",
    "nutrition": {"energy_kj": 900, "energy_kcal": 215, "fat": "6 g",
                  "saturated": "2 g", "carbs": "30 g", "protein": "8 g",
                  "salt": "1 g"},
}
DEMO_DELI = {
    "id": "demo-salaatti", "type": "deli", "name": "DEMO SALAATTI",
    "price_per_kg": 16.0, "shelf_life_days": 7,
    "ingredients_text": "peruna, porkkana, herneet, majoneesi, suola.",
}
GTIN = "6400000000019"
PACK = {"gtin": GTIN, "pack_weight_g": 400, "pack_price": 6.4}
MADE_ON = date(2026, 9, 16)


def _png_sha(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()


def _texts(ops):
    """Усі намальовані рядки траси (text і text_fit) у порядку малювання."""
    return [o["text"] for o in ops if o["op"] in ("text", "text_fit")]


# ── чисті функції ─────────────────────────────────────────────────────────

class CheckDigitTests(unittest.TestCase):
    def test_known_codes(self):
        self.assertEqual(ean13_check_digit("400638133393"), "1")
        self.assertEqual(ean13_check_digit("590123412345"), "7")
        self.assertEqual(ean13_check_digit("640000000001"), "9")
        self.assertEqual(ean13_check_digit("000000000000"), "0")

    def test_rejects_bad_input(self):
        for bad in ("40063813339", "4006381333931", "40063813339x",
                    "٤٠٠٦٣٨١٣٣٣٩٣", 400638133393, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ean13_check_digit(bad)


class ValidityTests(unittest.TestCase):
    def test_valid(self):
        for code in KNOWN_VALID:
            self.assertTrue(ean13_is_valid(code), code)

    def test_invalid(self):
        bad = (
            "400638133393",      # 12 цифр
            "40063813339312",    # 14 цифр
            "400638133393x",     # літера
            "4006381333932",     # крива контрольна
            "4006381333930",
            "٤006381333931",     # не-ASCII цифра
            " 4006381333931",    # пробіл
            4006381333931,       # число, а не рядок
            None, "",
        )
        for code in bad:
            self.assertFalse(ean13_is_valid(code), repr(code))


class ModulesTests(unittest.TestCase):
    def test_95_modules_and_guards(self):
        for code in KNOWN_VALID:
            m = ean13_modules(code)
            self.assertEqual(len(m), MODULES)
            self.assertEqual(MODULES, 95)
            self.assertEqual(m[0:3], [1, 0, 1], code)
            self.assertEqual(m[45:50], [0, 1, 0, 1, 0], code)
            self.assertEqual(m[92:95], [1, 0, 1], code)
            self.assertTrue(all(b in (0, 1) for b in m))

    def test_guard_module_set(self):
        self.assertEqual(sorted(GUARD_MODULES),
                         [0, 1, 2, 45, 46, 47, 48, 49, 92, 93, 94])

    def test_digit_blocks_shape(self):
        """L/G-блоки починаються з 0 і закінчуються 1, R — навпаки; у L непарна
        кількість одиниць, у G і R — парна. Так вимагає стандарт, і саме ці
        властивості дають сканеру напрямок читання."""
        for code in KNOWN_VALID:
            m = ean13_modules(code)
            for i in range(6):
                left = m[3 + 7 * i: 10 + 7 * i]
                right = m[50 + 7 * i: 57 + 7 * i]
                self.assertEqual((left[0], left[-1]), (0, 1), (code, i))
                self.assertEqual((right[0], right[-1]), (1, 0), (code, i))
                self.assertEqual(sum(right) % 2, 0, (code, i))
            # у коді з першою цифрою 0 вся ліва шістка — L (непарна парність)
        m0 = ean13_modules("0" + "00000000000" + ean13_check_digit("000000000000"))
        for i in range(6):
            self.assertEqual(sum(m0[3 + 7 * i: 10 + 7 * i]) % 2, 1)

    def test_hand_verified_bit_strings(self):
        for code, bits in HAND_VERIFIED.items():
            self.assertEqual(len(bits), 95, code)
            self.assertEqual("".join(map(str, ean13_modules(code))), bits, code)

    def test_rejects_invalid(self):
        for bad in ("4006381333932", "400638133393", "abc", 4006381333931):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ean13_modules(bad)


class _FakeDraw:
    """Записує прямокутники — геометрію штрихів перевіряємо без Pillow."""

    def __init__(self):
        self.rects = []

    def rectangle(self, box, fill=None):
        self.rects.append((tuple(box), fill))


class DrawBarsTests(unittest.TestCase):
    def test_bar_geometry(self):
        m = ean13_modules("4006381333931")
        d = _FakeDraw()
        w, h = draw_ean13_bars(d, m, x=100, y=50, module_px=4, bar_h=150,
                               guard_extra=10)
        self.assertEqual((w, h), (95 * 4, 160))
        self.assertEqual(len(d.rects), sum(m))        # 1 прямокутник на модуль-штрих
        for (x0, y0, x1, y1), fill in d.rects:
            self.assertEqual(fill, 0)
            self.assertEqual(x1 - x0 + 1, 4)          # рівно 4 px у ширину
            self.assertEqual(y0, 50)
            idx = (x0 - 100) // 4
            self.assertEqual((x0 - 100) % 4, 0)
            self.assertEqual(m[idx], 1)
            want_h = 160 if idx in GUARD_MODULES else 150
            self.assertEqual(y1 - y0 + 1, want_h, idx)


# ── рендер ────────────────────────────────────────────────────────────────

class RenderBarcodeTests(unittest.TestCase):
    def test_frozen_with_gtin_renders_taller_and_traced(self):
        plain, plain_meta = make_label_traced(DEMO_FROZEN, None, made_on=MADE_ON)
        packed, meta = make_label_traced(dict(DEMO_FROZEN, **PACK), None,
                                         made_on=MADE_ON)
        self.assertEqual(plain.width, packed.width)
        self.assertGreater(packed.height, plain.height)
        self.assertFalse([o for o in plain_meta["ops"] if o["op"] == "barcode"])
        ops = [o for o in meta["ops"] if o["op"] == "barcode"]
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual(op["code"], GTIN)
        self.assertEqual(op["module_px"], BARCODE_MODULE_PX)
        self.assertEqual(op["bar_h"], BARCODE_BAR_H)
        self.assertEqual(op["guard_h"], BARCODE_BAR_H + BARCODE_GUARD_EXTRA)
        self.assertGreaterEqual(op["height"], op["guard_h"])
        for key in ("y", "height", "module_px", "code"):
            self.assertIn(key, op)
        # op barcode — останній перед finish, після gap
        self.assertEqual(meta["ops"][-1]["op"], "finish")
        self.assertEqual(meta["ops"][-2]["op"], "barcode")
        self.assertEqual(meta["ops"][-3]["op"], "gap")
        # meta показує нові поля — і лише в продукту, який їх має
        self.assertEqual((meta["gtin"], meta["pack_weight_g"], meta["pack_price"]),
                         (GTIN, 400, 6.4))
        self.assertTrue(meta["pack_applied"])
        for key in ("gtin", "pack_weight_g", "pack_price", "pack_applied"):
            self.assertNotIn(key, plain_meta)

    def test_symbol_centred_with_quiet_zones(self):
        _, meta = make_label_traced(dict(DEMO_FROZEN, gtin=GTIN), 500,
                                    made_on=MADE_ON)
        op = [o for o in meta["ops"] if o["op"] == "barcode"][0]
        total_modules = BARCODE_QUIET_LEFT + 95 + BARCODE_QUIET_RIGHT
        self.assertEqual(op["width_px"], total_modules * BARCODE_MODULE_PX)
        self.assertEqual(op["x"], (WIDTH - op["width_px"]) // 2)
        self.assertEqual(op["bars_x"], op["x"] + BARCODE_QUIET_LEFT * BARCODE_MODULE_PX)
        # ліве поле і праве поле символу відрізняються щонайбільше на 1 px
        left = op["x"]
        right = WIDTH - (op["x"] + op["width_px"])
        self.assertLessEqual(abs(left - right), 1)

    def test_bars_decode_back_to_modules(self):
        """Читаємо рядок пікселів посередині штрихів і відновлюємо модулі —
        найкращий доказ правильності символу без сканера."""
        for product in (dict(DEMO_FROZEN, gtin=GTIN),
                        dict(DEMO_DELI, gtin="4006381333931")):
            img, meta = make_label_traced(product, 500, made_on=MADE_ON)
            op = [o for o in meta["ops"] if o["op"] == "barcode"][0]
            mod, bx, y0 = op["module_px"], op["bars_x"], op["y"]
            row = y0 + op["bar_h"] // 2
            got = []
            for i in range(95):
                px = {img.getpixel((bx + i * mod + k, row)) for k in range(mod)}
                self.assertEqual(len(px), 1, f"модуль {i} неоднорідний: {px}")
                value = px.pop()
                self.assertIn(value, (0, 255), f"модуль {i} не чисто чорний/білий")
                got.append(1 if value == 0 else 0)
            self.assertEqual(got, ean13_modules(product["gtin"]), product["id"])
            # тихі зони на рядку сканування — білі
            for x in range(op["x"], bx):
                self.assertEqual(img.getpixel((x, row)), 255, ("ліва тиха зона", x))
            for x in range(bx + 95 * mod, op["x"] + op["width_px"]):
                self.assertEqual(img.getpixel((x, row)), 255, ("права тиха зона", x))
            # guard-штрихи нижчі за штрихи даних: перший рядок під даними
            below = y0 + op["bar_h"]
            for i in GUARD_MODULES:
                if ean13_modules(product["gtin"])[i]:
                    self.assertEqual(img.getpixel((bx + i * mod, below)), 0, i)
            for i in (3 + 6, 50 + 1):                 # дані: тут уже біло
                if ean13_modules(product["gtin"])[i]:
                    self.assertEqual(img.getpixel((bx + i * mod, below)), 255, i)
            # а нижче за guard'и — теж біло (цифри стоять між групами, не під guard'ами)
            self.assertEqual(img.getpixel((bx, y0 + op["guard_h"])), 255)

    def test_deterministic(self):
        p = dict(DEMO_FROZEN, **PACK)
        self.assertEqual(_png_sha(make_label(p, None, made_on=MADE_ON)),
                         _png_sha(make_label(p, None, made_on=MADE_ON)))


class RenderPackTests(unittest.TestCase):
    def test_frozen_pack_lines(self):
        p = dict(DEMO_FROZEN, **PACK)
        for weight in (None, 400, 400.0):
            _, meta = make_label_traced(p, weight, made_on=MADE_ON)
            texts = _texts(meta["ops"])
            self.assertIn("Nettopaino: 400 g", texts, weight)
            self.assertIn("Hinta: 6,40 € (16 €/kg)", texts, weight)
            self.assertTrue(meta["pack_applied"])

    def test_frozen_other_weight_is_weighed_as_before(self):
        p = dict(DEMO_FROZEN, **PACK)
        _, meta = make_label_traced(p, 350, made_on=MADE_ON)
        texts = _texts(meta["ops"])
        self.assertIn("Nettopaino: 350 g", texts)
        self.assertIn("Hinta: 5,60 € (16 €/kg)", texts)
        self.assertFalse(meta["pack_applied"])
        # з тією ж вагою без пакунка — ті самі рядки й (без gtin) ті самі байти
        q = {k: v for k, v in p.items() if k not in ("pack_weight_g", "pack_price", "gtin")}
        p_no_gtin = {k: v for k, v in p.items() if k != "gtin"}
        self.assertEqual(_png_sha(make_label(p_no_gtin, 350, made_on=MADE_ON)),
                         _png_sha(make_label(q, 350, made_on=MADE_ON)))

    def test_deli_pack_line_and_barcode_last(self):
        p = dict(DEMO_DELI, **PACK)
        _, meta = make_label_traced(p, None, made_on=MADE_ON)
        fit = [o for o in meta["ops"] if o["op"] == "text_fit"]
        self.assertEqual(len(fit), 1)
        self.assertEqual(fit[0]["text"], "Paino: 400 g | Hinta: 6,40 € (16 €/kg)")
        self.assertEqual([o["op"] for o in meta["ops"][-3:]], ["gap", "barcode", "finish"])
        # штрих-код стоїть ПІСЛЯ дат
        ops = meta["ops"]
        y_dates = max(o["y"] for o in ops if o["op"] == "text"
                      and o["text"].startswith("Parasta ennen"))
        self.assertGreater(ops[-2]["y"], y_dates)

    def test_deli_without_pack_blank_line_unchanged(self):
        _, meta = make_label_traced(DEMO_DELI, None, made_on=MADE_ON)
        fit = [o for o in meta["ops"] if o["op"] == "text_fit"][0]
        self.assertEqual(fit["text"], "Paino: ______ | Hinta: 16 €/kg")


def _golden_paths():
    """(products, hashes) еталону, якщо він є на цій машині; інакше None.
    Ті самі джерела, що й у tools/golden_gate.py, плюс сусідній репозиторій."""
    sibling = os.path.join(REPO_ROOT, "..", "mylly-kitchen", "migrate")
    candidates = (
        (os.environ.get("GOLDEN_PRODUCTS"), os.environ.get("GOLDEN_HASHES")),
        ("/golden/input/products.baseline.json", "/golden/output/golden-hashes.json"),
        (os.path.join(sibling, "input", "products.baseline.json"),
         os.path.join(sibling, "output", "golden-hashes.json")),
    )
    for products, hashes in candidates:
        if products and hashes and os.path.exists(products) and os.path.exists(hashes):
            return products, hashes
    return None


class GoldenSampleTests(unittest.TestCase):
    """3 продукти baseline без нових полів мають давати ті самі байти, що й
    еталон із прода. Пропускається без еталону — повний доказ дає гейт."""

    @unittest.skipUnless(_golden_paths(), "еталон golden-hashes недоступний")
    def test_three_baseline_products_unchanged(self):
        products_path, hashes_path = _golden_paths()
        with open(products_path, encoding="utf-8") as f:
            products = json.load(f)
        with open(hashes_path, encoding="utf-8") as f:
            golden = json.load(f)["hashes"]
        frozen = [p for p in products if p["type"] == "frozen"]
        deli = [p for p in products if p["type"] == "deli"]
        sample = [frozen[0], frozen[-1], deli[0]]
        for p in sample:
            for key in ("gtin", "pack_weight_g", "pack_price"):
                self.assertNotIn(key, p)
            for w in (200, 1000):
                sha = _png_sha(make_label(p, w, made_on=MADE_ON))
                self.assertEqual(sha, golden[f"{p['id']}@{w}"]["sha256"],
                                 f"{p['id']}@{w}")


# ── валідація ─────────────────────────────────────────────────────────────

class ValidationTests(unittest.TestCase):
    def issues(self, product):
        out = []
        _check_product(product, out)
        return out

    def test_good_pack_and_gtin_pass(self):
        self.assertEqual(self.issues(dict(DEMO_FROZEN, **PACK)), [])
        self.assertEqual(self.issues(dict(DEMO_DELI, **PACK)), [])
        self.assertEqual(self.issues(dict(DEMO_FROZEN, gtin=None)), [])

    def test_bad_gtin(self):
        for bad in ("4006381333932", "400638133393", "abc", 4006381333931, ""):
            issues = self.issues(dict(DEMO_FROZEN, gtin=bad))
            self.assertEqual(len(issues), 1, (bad, issues))
            self.assertTrue(issues[0].startswith("gtin:"), issues[0])
        # текст кривої контрольної називає правильну цифру
        [issue] = self.issues(dict(DEMO_FROZEN, gtin="4006381333932"))
        self.assertIn("вона 1", issue)

    def test_pack_must_come_together(self):
        [issue] = self.issues(dict(DEMO_FROZEN, pack_weight_g=400))
        self.assertIn("pack_price", issue)
        [issue] = self.issues(dict(DEMO_FROZEN, pack_price=6.4))
        self.assertIn("pack_weight_g", issue)

    def test_pack_types(self):
        for bad_w in (0, -1, 400.5, "400", True):
            issues = self.issues(dict(DEMO_FROZEN, pack_weight_g=bad_w, pack_price=6.4))
            self.assertTrue(any(i.startswith("pack_weight_g:") for i in issues), bad_w)
        for bad_p in (-0.01, "6.4", float("nan")):
            issues = self.issues(dict(DEMO_FROZEN, pack_weight_g=400, pack_price=bad_p))
            self.assertTrue(any(i.startswith("pack_price:") for i in issues), bad_p)
        self.assertEqual(self.issues(dict(DEMO_FROZEN, pack_weight_g=400, pack_price=0)), [])

    def test_payload_flat_and_nested_accept_new_fields(self):
        nested = validate_payload({"product": dict(DEMO_FROZEN, **PACK),
                                   "made_on": "2026-09-16"})
        self.assertEqual(nested["product"]["gtin"], GTIN)
        flat = validate_payload(dict(DEMO_FROZEN, **PACK, made_on="2026-09-16"))
        self.assertEqual(flat["product"]["pack_weight_g"], 400)

    def test_warning_on_inconsistent_pack_price(self):
        self.assertEqual(label_warnings(dict(DEMO_FROZEN, **PACK)), [])
        warnings = label_warnings(dict(DEMO_FROZEN, **dict(PACK, pack_price=6.0)))
        self.assertEqual(len(warnings), 1)
        self.assertIn("15.00 €/kg", warnings[0])
        # різниця рівно 1 цент/кг ще не попередження (поріг — понад 1 цент)
        self.assertEqual(label_warnings(dict(DEMO_FROZEN, pack_weight_g=1000,
                                             pack_price=16.01)), [])


if __name__ == "__main__":
    unittest.main()
