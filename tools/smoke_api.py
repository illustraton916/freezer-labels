#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Димова перевірка контракту /api/* + того, що старий веб-інтерфейс живий.

Це не юніт-тести, а список тверджень, які мають лишатися правдою після будь-якої
зміни рендерера:

  healthz  — сервіс живий, шрифти збігаються з очікуваними хешами;
  render   — валідний продукт дає PNG; неповна frozen-наліпка дає 422;
  print    — неповну наліпку НЕ друкує навіть з allow_incomplete;
  payload  — перейменоване/зайве поле, склад рядком замість масиву, крива дата
             і qty не там — усе 422 з поясненням, а не мовчазна наліпка;
  trace    — режим діагностики віддає розкладку по рядках;
  ports    — Ф2: /api/* є на 5001 і НЕМАЄ на 5000, старий UI — навпаки;
  legacy   — /, /preview і /print працюють як раніше (або їх немає взагалі,
             якщо ENABLE_LEGACY_UI=false — тоді перевіряється саме відсутність);
             і ТУТ ЖЕ: неповна наліпка не друкується й через порт 5000 — картка
             позначена, кнопка вимкнена, POST /print дає відмову у flash.

Друкувальні кроки виконуються ТІЛЬКИ якщо сервіс у PRINT_MODE=mock: на бойовому
сервері цей скрипт свідомо нічого не надсилає на принтер.

  # усередині контейнера рендерера (порт 5001 назовні не публікується):
  python tools/smoke_api.py
  python tools/smoke_api.py --expect-legacy off      # перевірка ENABLE_LEGACY_UI=false
"""
import argparse
import base64
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

FAILURES = []
# Старий інтерфейс показує результат друку через flash у сесійній куці —
# без cookiejar ми б не побачили «[ТЕСТ] Збережено у файл».
OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def check(name, ok, detail=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def request(url, data=None, method=None, form=None):
    body, headers = None, {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method or ("POST" if body else "GET"))
    try:
        with OPENER.open(req, timeout=30) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except urllib.error.URLError as e:
        # Порт закритий (ENABLE_LEGACY_UI=false) — це теж результат перевірки,
        # а не аварія скрипта. HTTPError ловиться вище, бо він підклас URLError.
        return 0, str(e.reason).encode("utf-8", "replace"), {}


def as_json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return {}


GOOD = {
    "id": "smoke-frozen", "type": "frozen", "name": "SMOKE TUOTE",
    "price_per_kg": 15.0, "shelf_life_days": 90,
    "frozen_mark": "pakastettu tuote",
    # Довгий склад навмисно: на 696 px він переноситься, тож траса має показати
    # точки переносу — саме заради них існує layout-trace.
    "ingredients": ["Taikina: vehnäjauho, munat, vesi, suola, auringonkukkaöljy. "
                    "Täyte: sianliha (60 %), naudanliha (40 %), sipuli, suola, "
                    "mustapippuri, persilja."],
    "allergens": "Sisältää gluteenia, munia.",
    "instructions": "Keitä 5 minuuttia.",
    "storage": "Säilytä -18 °C:ssa.",
    "nutrition": {"energy_kj": 1050, "energy_kcal": 250, "fat": "14 g",
                  "saturated": "5 g", "carbs": "20 g", "protein": "12 g",
                  "salt": "1 g"},
}
INCOMPLETE = dict(GOOD, id="smoke-incomplete", ingredients=[], allergens="")


def blocked_ids(page):
    """id продуктів, які сторінка друку старого UI позначила як заборонені.

    Читаємо саме розмітку, а не products.json: сторінка й роут /print мають
    рахувати повноту наліпки одним і тим самим правилом, і розбіжність між
    «кнопка активна» і «сервер друкує» — це теж дефект.
    """
    ids = []
    for chunk in page.split('<div class="card')[1:]:
        card, _, _ = chunk.partition("</form>")
        if card.startswith(' blocked"'):
            m = re.search(r'name="id" value="([^"]+)"', card)
            if m:
                ids.append(m.group(1))
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:5001",
                    help="сервіс /api/* — Ф2: порт 5001, назовні не публікується")
    ap.add_argument("--legacy-url", default="http://127.0.0.1:5000",
                    help="старий веб-інтерфейс — порт 5000, лише 127.0.0.1")
    ap.add_argument("--expect-legacy", choices=("auto", "on", "off"), default="auto",
                    help="чи мусить старий UI бути живим: on = мусить, "
                         "off = мусить бути вимкненим (ENABLE_LEGACY_UI=false), "
                         "auto = як є (типово)")
    ap.add_argument("--legacy-id", default="krepit-kanan",
                    help="id продукту, який є в products.json сервісу")
    ap.add_argument("--legacy-expect-incomplete", default="",
                    help="id продукту, який у products.json сервісу юридично "
                         "неповний: старий UI мусить позначити його й "
                         "відмовитися друкувати (для baseline — kaalikarylet)")
    args = ap.parse_args()
    b = args.base_url.rstrip("/")
    lb = args.legacy_url.rstrip("/")

    print("healthz")
    status, raw, _ = request(f"{b}/api/healthz")
    h = as_json(raw)
    check("200 і status=ok", status == 200 and h.get("status") == "ok", str(status))
    check("шрифти збігаються з очікуваними", (h.get("fonts") or {}).get("ok") is True)
    check("є версія, режим друку й стан пристрою",
          bool(h.get("version")) and "print_mode" in h
          and "device_present" in (h.get("printer") or {}))
    mock = h.get("print_mode") == "mock"
    if not mock:
        print("  УВАГА: сервіс не в mock-режимі — кроки з друком пропускаю")

    print("render: валідний продукт")
    status, raw, hdr = request(f"{b}/api/render",
                               {"product": GOOD, "weight_g": 350,
                                "made_on": "2026-01-15"})
    check("200 image/png", status == 200 and raw[:4] == b"\x89PNG", str(status))
    check("заголовки з хешем і датами",
          bool(hdr.get("X-Label-Sha256")) and hdr.get("X-Label-Made-On") == "2026-01-15"
          and hdr.get("X-Label-Best-Before") == "2026-04-15",
          f"{hdr.get('X-Label-Made-On')} → {hdr.get('X-Label-Best-Before')}")

    print("render: дата керує наліпкою")
    _, raw_a, _ = request(f"{b}/api/render", {"product": GOOD, "weight_g": 350,
                                              "made_on": "2026-01-15"})
    _, raw_b, _ = request(f"{b}/api/render", {"product": GOOD, "weight_g": 350,
                                              "made_on": "2026-02-15"})
    check("інша made_on → інший PNG", raw_a != raw_b)

    print("render: guard неповної frozen-наліпки")
    status, raw, _ = request(f"{b}/api/render",
                             {"product": INCOMPLETE, "weight_g": 350,
                              "made_on": "2026-01-15"})
    e = as_json(raw).get("error") or {}
    check("422 incomplete_label", status == 422 and e.get("code") == "incomplete_label",
          f"{status} {e.get('code')}")
    check("перелічено відсутні поля",
          set(e.get("missing") or []) == {"ingredients", "allergens"},
          str(e.get("missing")))

    print("print: неповну наліпку не друкує")
    status, raw, _ = request(f"{b}/api/print",
                             {"product": INCOMPLETE, "weight_g": 350, "qty": 1,
                              "made_on": "2026-01-15"})
    e = as_json(raw).get("error") or {}
    check("422 incomplete_label", status == 422 and e.get("code") == "incomplete_label",
          f"{status} {e.get('code')}")

    # Обхід guard'а прибрано з коду повністю, тож саме слово має відхилятися як
    # невідоме поле. Якщо колись хтось спробує повернути діру — цей тест впаде.
    status, raw, _ = request(f"{b}/api/print",
                             {"product": INCOMPLETE, "weight_g": 350, "qty": 1,
                              "made_on": "2026-01-15", "allow_incomplete": True})
    e = as_json(raw).get("error") or {}
    check("allow_incomplete більше не існує", status == 422 and e.get("code") == "invalid_payload",
          f"{status} {e.get('code')}")

    print("payload: криві тіла")
    cases = [
        ("невідоме поле продукту", {"product": dict(GOOD, nutrition_facts={}),
                                    "made_on": "2026-01-15"}),
        ("склад рядком замість масиву",
         {"product": dict(GOOD, ingredients="усе одним рядком"),
          "made_on": "2026-01-15"}),
        ("нема shelf_life_days",
         {"product": {k: v for k, v in GOOD.items() if k != "shelf_life_days"}}),
        ("крива дата", {"product": GOOD, "made_on": "16.09.2026"}),
        ("від'ємна вага", {"product": GOOD, "weight_g": -5}),
        ("qty на /render", {"product": GOOD, "qty": 2}),
        ("бракує ключа nutrition",
         {"product": dict(GOOD, nutrition={"energy_kj": 1, "energy_kcal": 1,
                                           "fat": "1", "carbs": "1",
                                           "protein": "1", "salt": "1"})}),
    ]
    for name, payload in cases:
        status, raw, _ = request(f"{b}/api/render", payload)
        e = as_json(raw).get("error") or {}
        check(f"422 invalid_payload: {name}",
              status == 422 and e.get("code") == "invalid_payload",
              f"{status} {e.get('code')} {e.get('issues')}")

    print("trace: діагностика розкладки")
    status, raw, _ = request(f"{b}/api/render?trace=1",
                             {"product": GOOD, "weight_g": 350,
                              "made_on": "2026-01-15"})
    d = as_json(raw)
    ops = ((d.get("trace") or {}).get("ops") or [])
    texts = [o for o in ops if o["op"] == "text"]
    check("200 і є draw-ops", status == 200 and len(texts) > 5, f"{len(texts)} рядків")
    check("у рядка є шрифт, кегль і y",
          bool(texts) and {"font", "size_px", "y"} <= set(texts[0]),
          json.dumps(texts[0], ensure_ascii=False) if texts else "")
    check("PNG у base64 збігається з тілом", bool(d.get("png_base64"))
          and base64.b64decode(d["png_base64"])[:4] == b"\x89PNG")
    check("є точки переносу (block.lines)",
          any(o["op"] == "block" and o["wrapped_into"] > 1 for o in ops))

    if mock:
        print("print: mock")
        status, raw, _ = request(f"{b}/api/print",
                                 {"product": GOOD, "weight_g": 350, "qty": 2,
                                  "made_on": "2026-01-15"})
        d = as_json(raw)
        check("200 sent=true", status == 200 and d.get("sent") is True, str(status))
        check("mock зберіг 2 файли", len(d.get("saved") or []) == 2, str(d.get("saved")))
        check("є render_sha256 і дати", bool(d.get("render_sha256"))
              and d.get("best_before") == "2026-04-15")

    # ── Ф2: два порти, два застосунки ────────────────────────────────────
    # Розділення має бути справжнім, а не «домовленістю про префікси»: на порту
    # сервісу старих роутів немає взагалі, на порту старого UI немає /api/*.
    print("ports: розділення 5001 (api) / 5000 (legacy)")
    status, _, _ = request(f"{b}/")
    check("на порту api немає старого UI (404)", status == 404, f"HTTP {status}")
    status, _, _ = request(f"{b}/admin")
    check("на порту api немає /admin (404)", status == 404, f"HTTP {status}")

    st_root, raw_root, _ = request(f"{lb}/")
    st_health, _, _ = request(f"{lb}/api/healthz")
    legacy_up = st_root != 0
    check("на порту legacy немає /api/*",
          st_health == 404 if legacy_up else st_health == 0,
          "порт закрито" if st_health == 0 else f"HTTP {st_health}")

    want_legacy = {"on": True, "off": False}.get(args.expect_legacy, legacy_up)
    if args.expect_legacy != "auto":
        check(f"ENABLE_LEGACY_UI відповідає очікуванню ({args.expect_legacy})",
              legacy_up == want_legacy,
              "UI живий" if legacy_up else "порт 5000 закрито")

    if want_legacy:
        print("legacy: старий веб-інтерфейс")
        check("GET / 200", st_root == 200 and b"<form" in raw_root.lower(),
              f"HTTP {st_root}")
        status, raw, _ = request(
            f"{lb}/preview?id={urllib.parse.quote(args.legacy_id)}&weight=350")
        check("GET /preview 200 PNG", status == 200 and raw[:4] == b"\x89PNG",
              f"HTTP {status}")
        status, raw, _ = request(f"{lb}/admin")
        check("GET /admin 200", status == 200, f"HTTP {status}")
        if mock:
            status, raw, _ = request(f"{lb}/print",
                                     form={"id": args.legacy_id, "weight": "350",
                                           "qty": "1"}, method="POST")
            # urllib сам іде за 303 на "/", тож бачимо або редірект, або сторінку
            # з flash-повідомленням «[ТЕСТ] Збережено у файл».
            page = raw.decode("utf-8", "replace")
            check("POST /print зберіг наліпку (mock)",
                  status in (302, 303) or (status == 200 and "Збережено" in page),
                  f"HTTP {status}")

        # ── guard неповної наліпки на СТАРОМУ UI ─────────────────────────
        # Донедавна його тут не було: /api/* відмовляв, а порт 5000 (у проді
        # увімкнений, PRINT_MODE=real) друкував наліпку без складу. Перевіряємо
        # обидві половини — позначку на сторінці й саму відмову сервера.
        print("legacy: guard неповної наліпки")
        page_root = raw_root.decode("utf-8", "replace")
        blocked = blocked_ids(page_root)
        want_id = args.legacy_expect_incomplete.strip()
        if want_id:
            check(f"сторінка позначає «{want_id}» як недрукований",
                  want_id in blocked, f"позначені: {blocked or 'жодного'}")
        if blocked:
            check("у кожної позначеної картки кнопка друку вимкнена",
                  page_root.count('<button type="submit" disabled') == len(blocked),
                  f"{len(blocked)} карток")
        pid = want_id or (blocked[0] if blocked else "")
        if not pid:
            # Не «все добре», а «нема на чому перевірити»: на здоровому проді це
            # законно, у гейті — привід передати --legacy-expect-incomplete.
            print("  УВАГА: у products.json сервісу немає неповних наліпок — "
                  "guard старого UI перевіряти нема на чому")
        elif not mock:
            print("  УВАГА: сервіс не в mock-режимі — пробу друку неповної "
                  "наліпки пропускаю (зламаний guard надрукував би її по-справжньому)")
        else:
            status, raw, _ = request(f"{lb}/print",
                                     form={"id": pid, "weight": "350", "qty": "1"},
                                     method="POST")
            page = raw.decode("utf-8", "replace")
            refused = 'class="flash err">Друк заборонено' in page
            printed = "Збережено у файл" in page or "Надруковано:" in page
            check(f"POST /print «{pid}» — відмова, наліпки немає",
                  status in (302, 303) or (status == 200 and refused and not printed),
                  f"HTTP {status}" + ("" if refused else ", у flash немає відмови")
                  + (", але наліпка збереглася!" if printed else ""))
    else:
        print("legacy: ENABLE_LEGACY_UI=false — старих роутів не має бути")
        check("порт 5000 не відкрито або старих роутів немає",
              st_root in (0, 404), f"HTTP {st_root}")
        status, _, _ = request(f"{lb}/admin")
        check("/admin недоступний", status in (0, 404), f"HTTP {status}")
        # Головне твердження вимкнення: сервіс друку від цього не постраждав.
        status, _, _ = request(f"{b}/api/healthz")
        check("/api/* живий попри вимкнений UI", status == 200, f"HTTP {status}")

    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО {len(FAILURES)}: " + "; ".join(FAILURES))
        return 1
    print("OK: усі перевірки пройдені")
    return 0


if __name__ == "__main__":
    sys.exit(main())
