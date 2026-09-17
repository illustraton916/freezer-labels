#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden-гейт: 66 наліпок мають збігтися з еталоном байт-у-байт.

ЩО ДОВОДИТЬ. 22 бойові продукти × 3 ваги (200/500/1000 г) при фіксованій даті
дають рівно ті самі PNG, що їх сьогодні друкує сервер. Еталонні SHA-256 зняті з
бойового сервера (migrate/output/golden-hashes.json) — тобто гейт порівнює не
«з учорашнім собою», а з тим, що реально наклеєно на продукцію.

ДВА ШЛЯХИ, обидва обов'язкові:
  --mode direct  прямий виклик make_label()      — ловить зміну верстки/шрифтів;
  --mode http    POST /api/render                — ловить ще й зміну контракту
                 сервісу (валідація, парсинг дати, збирання PNG у відповідь).
  --mode both    обидва (типово).

КРІМ ХЕШІВ — КОНФІГУРАЦІЯ ДРУКУ. Хеш рахується з PNG, а в принтер іде
brother_ql.convert(label=LABEL_SIZE, hq=PRINT_HQ, …). Ці два значення на PNG не
впливають узагалі: з LABEL_SIZE=29 гейт лишався б зеленим 66/66, а з принтера
виходила б наліпка іншої ширини. Тому кожен режим ще й звіряє конфігурацію із
зашитою EXPECTED_CONFIG (direct — свій процес, http — /api/healthz живого
сервісу). Розбіжність = червоний гейт нарівні з розбіжністю хешів.

НЕПОВНІ ПРОДУКТИ БЕЗ ЖОДНОЇ ENV-ДІРИ. У baseline є `kaalikarylet` — frozen без
складу, тобто юридично неповна наліпка, яку сервіс зобов'язаний відмовитися
рендерити. Гейт знає про це заздалегідь (--expect-incomplete) і в HTTP-режимі
чекає для таких id рівно `422 incomplete_label` — це й зараховується як успіх.
Їхні три хеші звіряються в direct-режимі, де guard'а немає за побудовою. Тому
гейт ганяється проти ПРОД-конфігурації: жодного ALLOW_INCOMPLETE_RENDER більше
не існує ні в коді, ні в компоузі. Якщо очікування розійшлося з даними —
це явна помилка конфігурації, а не мовчазний пропуск перевірки.

ЧОМУ ДАТА ФІКСОВАНА. На наліпці друкуються Valmistuspäivä / Parasta ennen, тож
хеш залежить від дня. Еталон знято 2026-09-16 (див. --made-on), і саме тому
рендер мусив навчитися приймати made_on ззовні: інакше гейт «протухав» би
щодоби.

ЗАПУСК (усередині контейнера рендерера, mock-режим):
  docker run --rm -v <mylly-kitchen>/migrate:/golden:ro freezer-labels-labels \
      python tools/golden_gate.py --mode direct
(ім'я образу — те, яке будує `docker compose build`: <тека>-<сервіс>.)
Ненульовий код виходу = наліпка поїхала; PNG і траса розбіжностей лягають у
--out-dir для порівняння.
"""
import argparse
import base64
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request

# Запускається як `python tools/golden_gate.py` — корінь репо в sys.path вручну.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Дата, з якою знято еталонні хеші (parity_check.py проти бойового сервера).
DEFAULT_MADE_ON = "2026-09-16"
DEFAULT_PRODUCTS = os.environ.get("GOLDEN_PRODUCTS",
                                  "/golden/input/products.baseline.json")
DEFAULT_HASHES = os.environ.get("GOLDEN_HASHES",
                                "/golden/output/golden-hashes.json")
# Ф2: /api/* живе на 5001 (внутрішній порт, назовні не публікується). Гейт
# ганяється або всередині контейнера рендерера, або з сусіднього контейнера в
# тій самій docker-мережі — з хоста цього порту не видно за побудовою.
DEFAULT_BASE_URL = os.environ.get("RENDERER_URL", "http://127.0.0.1:5001")
# Продукти baseline, які guard зобов'язаний відмовитися рендерити (див. вгорі
# і migrate/input/known-issues.md). Список навмисно зашитий, а не «що знайдемо»:
# гейт має падати і коли неповних стало більше, і коли їх раптом не стало.
DEFAULT_EXPECT_INCOMPLETE = "kaalikarylet"

# ── ДРУКАРСЬКИЙ ШЛЯХ ПОВЗ ХЕШІ ────────────────────────────────────────────
# 66 еталонів — це хеші PNG від Pillow. А на принтер іде НЕ PNG: printing.py
# віддає ту саму картинку в brother_ql.convert(label=LABEL_SIZE, hq=PRINT_HQ,
# rotate/threshold/dither/cut) і вже той результат — у пристрій. Тобто
# LABEL_SIZE=29 або PRINT_HQ=true лишають гейт зеленим 66/66, а з принтера
# виходить наліпка іншої ширини або іншої щільності. Те саме з боку полотна:
# WIDTH/MARGIN/SCALE впливають на хеш, але якщо колись зіб'ються ВСІ 66 — з
# самих хешів не видно, що саме зламалося.
#
# Тому конфігурація звіряється окремо і жорстко, зашитими значеннями (не з
# .env: файл, який перевіряють проти самого себе, нічого не доводить):
EXPECTED_CONFIG = {
    "label_size": "62",    # DK-22205, безперервна стрічка 62 мм
    "hq": False,           # бойовий друк — стандартна якість
    "scale": 1.1,          # масштаб шрифтів, з яким знято еталони
    "width_px": 696,       # друкована ширина 62 мм @300dpi
    "margin_px": 20,
}
# Сталі brother_ql.convert(), які не налаштовуються через env і тому не можуть
# «поїхати» непомітно: rotate="0", threshold=70.0, dither=False, cut=True.
# Якщо колись їх винесуть у змінні — місце для перевірки вже є.


def _same_scale(a, b):
    """1.1 із JSON і 1.1 із float('1.10') — те саме число."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def _cmp_config(actual, source, fonts_ok):
    """Звіряє словник {ключ: значення} з EXPECTED_CONFIG. True = збіг."""
    all_ok = True
    for key, want in EXPECTED_CONFIG.items():
        got = actual.get(key)
        ok = _same_scale(got, want) if key == "scale" else got == want
        all_ok &= ok
        print(f"[конфіг {source}] {key}: {got!r}"
              + ("" if ok else f"  ← ОЧІКУВАЛИ {want!r}"))
    if fonts_ok is not None:
        all_ok &= bool(fonts_ok)
        print(f"[конфіг {source}] шрифти: "
              + ("збігаються з очікуваними хешами" if fonts_ok
                 else "НЕ ЗБІГАЮТЬСЯ з очікуваними хешами"))
    if not all_ok:
        print("  → конфігурація друку розійшлася з бойовою: наліпка поїде "
              "фізично, хоча хеші можуть збігатися")
    return all_ok


def check_config_direct():
    """Конфігурація процесу, в якому щойно рендерили 66 наліпок."""
    from labels import config
    from labels.fonts_check import EXPECTED_FONT_SHA256, actual_font_hashes

    actual = actual_font_hashes()
    fonts_ok = all(actual.get(n) == h for n, h in EXPECTED_FONT_SHA256.items())
    return _cmp_config({
        "label_size": config.LABEL_SIZE,
        "hq": config.PRINT_HQ,
        "scale": config.SCALE,
        "width_px": config.WIDTH,
        "margin_px": config.MARGIN,
    }, "напряму", fonts_ok)


def check_config_http(base_url):
    """Те саме, але як його бачить живий сервіс — через /api/healthz.

    Саме ця перевірка ловить випадок «контейнер підняли зі старим env»: гейт
    усередині контейнера бачив би правильні значення лише тому, що docker exec
    успадковує env, а сервіс міг стартувати з іншим.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/api/healthz", timeout=10) as r:
            h = json.loads(r.read())
    except Exception as e:                            # noqa: BLE001
        print(f"[конфіг через HTTP] /api/healthz недоступний: "
              f"{type(e).__name__}: {e}")
        return False
    printer = h.get("printer") or {}
    label = h.get("label") or {}
    missing = [k for k in ("label_size", "hq") if k not in printer] \
        + [k for k in ("scale", "width_px", "margin_px") if k not in label]
    if missing:
        print(f"[конфіг через HTTP] /api/healthz не віддає поля: "
              f"{', '.join(missing)} — перевіряти нема що")
        return False
    return _cmp_config({
        "label_size": printer.get("label_size"),
        "hq": printer.get("hq"),
        "scale": label.get("scale"),
        "width_px": label.get("width_px"),
        "margin_px": label.get("margin_px"),
    }, "через HTTP", (h.get("fonts") or {}).get("ok"))


# ── два шляхи рендеру ─────────────────────────────────────────────────────

def render_direct(product, weight_g, made_on, want_trace=False):
    """Прямий виклик рендерера в цьому ж процесі."""
    from datetime import date

    from labels.rendering import make_label, make_label_traced

    md = date.fromisoformat(made_on)
    trace = None
    if want_trace:
        img, trace = make_label_traced(product, weight_g, made_on=md)
    else:
        img = make_label(product, weight_g, made_on=md)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), trace


def _post(url, payload, timeout=30):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def render_http(base_url, product, weight_g, made_on, want_trace=False):
    """Той самий рендер, але через контракт сервісу. Жодних прапорців-обходів:
    неповні продукти йдуть окремою гілкою (expect_refusal_http)."""
    payload = {"product": product, "weight_g": weight_g, "made_on": made_on}
    if want_trace:
        payload["trace"] = True
    status, body, _ = _post(f"{base_url}/api/render", payload)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:400].decode('utf-8', 'replace')}")
    if want_trace:
        data = json.loads(body)
        return base64.b64decode(data["png_base64"]), data["trace"]
    if body[:4] != b"\x89PNG":
        raise RuntimeError("відповідь не PNG")
    return body, None


# ── неповні продукти baseline ─────────────────────────────────────────────

def _is_mock(base_url):
    """Чи сервіс у PRINT_MODE=mock (за /api/healthz). Невідомо → вважаємо, що ні."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/healthz", timeout=10) as r:
            return json.loads(r.read()).get("print_mode") == "mock"
    except Exception:                                 # noqa: BLE001
        return False


def error_of(body):
    """{'code': …, 'missing': […]} з тіла відповіді сервісу."""
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data.get("error") or {}


def expect_refusal_http(base_url, product, weight_g, made_on):
    """Неповна наліпка через HTTP має отримати 422 incomplete_label.
    Повертає (ok, опис) — саме ця відмова й зараховується гейтом як успіх."""
    status, body, _ = _post(f"{base_url}/api/render",
                            {"product": product, "weight_g": weight_g,
                             "made_on": made_on})
    err = error_of(body)
    if status == 422 and err.get("code") == "incomplete_label":
        return True, f"422 incomplete_label, бракує {err.get('missing')}"
    return False, (f"очікували 422 incomplete_label, отримали {status} "
                   f"{err.get('code')}")


def resolve_incomplete(products, expected_ids):
    """Звіряє оголошений список неповних продуктів із самим baseline.

    Правило береться з labels.validation — того самого модуля, яким відмовляє
    сервіс. Тому гейт падає не лише коли змінилися дані, а й коли хтось
    послабив guard: тоді «неповних» стане менше, ніж оголошено.
    Повертає (ids, problems).
    """
    from labels.validation import missing_label_fields

    actual = {p["id"]: missing_label_fields(p) for p in products}
    actual_ids = {pid for pid, miss in actual.items() if miss}
    problems = []
    for pid in sorted(expected_ids - actual_ids):
        problems.append(f"{pid}: оголошений неповним, але в baseline він повний "
                        f"(або guard послаблено)")
    for pid in sorted(actual_ids - expected_ids):
        problems.append(f"{pid}: неповний ({', '.join(actual[pid])}), але не "
                        f"оголошений у --expect-incomplete")
    for pid in sorted(actual_ids & expected_ids):
        print(f"[baseline] {pid}: неповна наліпка (бракує {', '.join(actual[pid])}) "
              f"— через HTTP очікуємо 422")
    return actual_ids & expected_ids, problems


# ── сам гейт ──────────────────────────────────────────────────────────────

def run_mode(mode, products, golden, weights, made_on, base_url, out_dir,
             incomplete_ids=frozenset()):
    render = (lambda p, w, t=False: render_direct(p, w, made_on, t)) if mode == "direct" \
        else (lambda p, w, t=False: render_http(base_url, p, w, made_on, t))

    ok, refused, mismatches, errors = 0, 0, [], []
    for p in products:
        # Через HTTP неповний продукт не має рендеритися взагалі: його хеш
        # звіряє direct-режим, а тут перевіряється сама відмова.
        guarded = mode == "http" and p["id"] in incomplete_ids
        for w in weights:
            key = f"{p['id']}@{w}"
            want = golden.get(key)
            if want is None:
                errors.append(f"{key}: нема в еталоні")
                continue
            if guarded:
                good, detail = expect_refusal_http(base_url, p, w, made_on)
                if good:
                    ok += 1
                    refused += 1
                else:
                    errors.append(f"{key}: {detail}")
                continue
            try:
                png, _ = render(p, w)
            except Exception as e:                    # noqa: BLE001
                errors.append(f"{key}: рендер впав — {type(e).__name__}: {e}")
                continue
            got = hashlib.sha256(png).hexdigest()
            if got == want["sha256"]:
                ok += 1
                continue
            mismatches.append((key, want, got, len(png)))
            # Артефакти для розбору: PNG + траса розкладки саме цієї наліпки.
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, f"{mode}-{p['id']}-{w}.png"), "wb") as f:
                f.write(png)
            try:
                _, trace = render(p, w, True)
                if trace is not None:
                    with open(os.path.join(out_dir, f"{mode}-{p['id']}-{w}.trace.json"),
                              "w", encoding="utf-8") as f:
                        json.dump(trace, f, ensure_ascii=False, indent=2)
            except Exception:                         # noqa: BLE001
                pass

    total = len(products) * len(weights)
    label = "напряму" if mode == "direct" else "через HTTP"
    suffix = (f" (з них {refused} — відмова 422 incomplete_label, як і має бути; "
              f"їхні хеші звіряє режим direct)") if refused else ""
    print(f"[{label}] збіг {ok}/{total}{suffix}")
    for key, want, got, nbytes in mismatches:
        print(f"  РОЗБІЖНІСТЬ {key}: очікували {want['sha256'][:12]}… ({want['bytes']} б), "
              f"отримали {got[:12]}… ({nbytes} б)")
    for e in errors:
        print(f"  ПОМИЛКА {e}")
    if mismatches:
        print(f"  → PNG і траси розбіжностей: {out_dir}")
    return ok == total and not errors


def check_guard(base_url, products, made_on, incomplete_ids):
    """Доводить, що guard живий і що друк неповної наліпки заборонено.

    Якщо перевіряти нема на чому — це НЕ «все добре», а помилка конфігурації:
    мовчазно зелений гейт без жодної перевірки гірший за червоний.
    """
    if not incomplete_ids:
        print("[guard] ПОМИЛКА КОНФІГУРАЦІЇ: у baseline не оголошено жодного "
              "неповного продукту, тож guard нема на чому перевірити. "
              "Вкажіть --expect-incomplete або візьміть повний baseline.")
        return False
    by_id = {p["id"]: p for p in products}
    all_ok = True
    for pid in sorted(incomplete_ids):
        good, detail = expect_refusal_http(base_url, by_id[pid], 500, made_on)
        print(f"[guard] render {pid}: {detail}"
              + (" — очікувано" if good else ""))
        all_ok &= good

    # Проби на /api/print робимо ТІЛЬКИ в mock-режимі: якщо guard колись
    # зламається, така проба на бойовому сервері виплюне справжню стрічку з
    # юридично неповною наліпкою. Гейт не має права нічого надрукувати.
    if not _is_mock(base_url):
        print("[guard] сервіс не в PRINT_MODE=mock — проби /api/print пропущено "
              "(гейт не друкує на бойовому принтері)")
        return all_ok

    for pid in sorted(incomplete_ids):
        status, body, _ = _post(f"{base_url}/api/print",
                                {"product": by_id[pid], "weight_g": 500,
                                 "qty": 1, "made_on": made_on})
        err = error_of(body)
        ok_print = status == 422 and err.get("code") == "incomplete_label"
        print(f"[guard] print  {pid}: {status} {err.get('code')}"
              + (" — очікувано" if ok_print else
                 " — ОЧІКУВАЛИ 422 incomplete_label"))
        all_ok &= ok_print

    # Дата друку: без made_on /api/print не має права взяти годинник контейнера.
    sample = next((p for p in products if p["id"] not in incomplete_ids), None)
    if sample is not None:
        status, body, _ = _post(f"{base_url}/api/print",
                                {"product": sample, "weight_g": 500, "qty": 1})
        err = error_of(body)
        ok_date = status == 422 and err.get("code") == "missing_made_on"
        print(f"[guard] print без made_on: {status} {err.get('code')}"
              + (" — очікувано" if ok_date else
                 " — ОЧІКУВАЛИ 422 missing_made_on"))
        all_ok &= ok_date
    return all_ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("direct", "http", "both"), default="both")
    ap.add_argument("--products", default=DEFAULT_PRODUCTS)
    ap.add_argument("--golden", default=DEFAULT_HASHES)
    ap.add_argument("--made-on", default=DEFAULT_MADE_ON,
                    help=f"дата еталону, типово {DEFAULT_MADE_ON}")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--out-dir", default="output/golden-mismatch")
    ap.add_argument("--expect-incomplete", default=DEFAULT_EXPECT_INCOMPLETE,
                    help="id через кому, які в baseline юридично неповні й через "
                         f"HTTP мають давати 422 (типово {DEFAULT_EXPECT_INCOMPLETE})")
    ap.add_argument("--no-guard-check", action="store_true",
                    help="не перевіряти 422 на неповній наліпці (режим http)")
    args = ap.parse_args()

    with open(args.products, encoding="utf-8") as f:
        products = json.load(f)
    with open(args.golden, encoding="utf-8") as f:
        golden_file = json.load(f)
    golden = golden_file["hashes"]
    weights = golden_file["weights"]

    expected = len(products) * len(weights)
    print(f"продуктів: {len(products)}, ваги: {weights}, наліпок: {expected}, "
          f"еталонів: {len(golden)}, дата: {args.made_on}")
    if expected != len(golden):
        print(f"  ПОМИЛКА: у файлі еталонів {len(golden)} записів, а очікується {expected}")
        return 1

    expect_ids = {s.strip() for s in args.expect_incomplete.split(",") if s.strip()}
    incomplete_ids, problems = resolve_incomplete(products, expect_ids)
    if problems:
        print("  ПОМИЛКА КОНФІГУРАЦІЇ (baseline і --expect-incomplete розійшлися):")
        for p in problems:
            print(f"    - {p}")
        return 1

    modes = ["direct", "http"] if args.mode == "both" else [args.mode]
    if modes == ["http"] and incomplete_ids:
        print(f"  УВАГА: хеші {sorted(incomplete_ids)} у режимі http не звіряються "
              f"(сервіс їх законно не рендерить) — потрібен ще --mode direct")
    all_ok = True
    for mode in modes:
        all_ok &= run_mode(mode, products, golden, weights, args.made_on,
                           args.base_url, args.out_dir, incomplete_ids)
        # Конфігурація друку — окремо від хешів і в тому ж режимі, що й рендер:
        # direct перевіряє процес, http — живий сервіс (див. EXPECTED_CONFIG).
        all_ok &= check_config_direct() if mode == "direct" \
            else check_config_http(args.base_url)
    if "http" in modes and not args.no_guard_check:
        all_ok &= check_guard(args.base_url, products, args.made_on, incomplete_ids)

    print("\nOK: усі наліпки збігаються з еталоном" if all_ok
          else "\nГЕЙТ НЕ ПРОЙДЕНО")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
