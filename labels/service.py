"""HTTP-сервіс рендерера: /api/render, /api/print, /api/healthz.

Це шар, яким користуватиметься новий Node-бекенд (план Ф2). Рендерер лишається
без БД, без авторизації та без власних даних: увесь продукт приходить у payload.

Чим /api/* відрізняється від старого веб-інтерфейсу (і це навмисно):
  * дата приходить ззовні (`made_on`), а не береться з годинника контейнера;
    для /api/print вона ОБОВ'ЯЗКОВА — надруковану наліпку вже не переробиш;
  * payload валідується за закритим списком полів;
  * frozen без складу/алергенів/зберігання/харчової цінності → 422, а не друк;
  * deli без складу (`ingredients_text`) → так само 422.
Старі роути (`/`, `/preview`, `/print`, `/admin/*`) не змінені жодним рядком —
вони лишаються аварійним «розбий скло» і мають працювати рівно як учора.

Ф2: цей блупринт живе на власному порту 5001 (окремий Flask-застосунок, див.
app.py і labels/__init__.py). Порт назовні не публікується — до нього ходить
лише контейнер `api` по docker-мережі.

Приклад (усередині контейнера рендерера):
  curl -sS -X POST localhost:5001/api/render -H 'Content-Type: application/json' \
       -d '{"product": {...}, "weight_g": 350, "made_on": "2026-09-16"}' -o label.png
"""
import base64
import hashlib
import importlib.metadata as metadata
import io
import os
import platform
import time
from datetime import datetime

from flask import Blueprint, jsonify, request, Response

from . import config
from .fonts_check import EXPECTED_FONT_SHA256, actual_font_hashes
from .printing import print_label
from .rendering import label_dates, make_label, make_label_traced
from .validation import (PayloadError, label_warnings, require_complete_label,
                         require_made_on, validate_payload)
from .version import BUILD_SHA, SERVICE_VERSION

api = Blueprint("api", __name__, url_prefix="/api")


# ── службове ──────────────────────────────────────────────────────────────

def _err(code, message, status=422, **details):
    body = {"code": code, "message": message}
    body.update(details)
    return jsonify({"error": body}), status


def _payload_error(e):
    return _err(e.code, e.message, 422, **e.details)


def _body():
    """Тіло запиту як JSON. Не-JSON → PayloadError (422, а не 400 від Flask)."""
    data = request.get_json(silent=True)
    if data is None:
        raise PayloadError("invalid_payload",
                           "очікується тіло JSON із Content-Type: application/json")
    return data


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")      # той самий виклик, що й у /preview
    return buf.getvalue()


def _pkg(name):
    try:
        return metadata.version(name)
    except Exception:
        return None


def _made_on_source(req):
    return "request" if req["made_on"] else "container_clock"


# ── /api/render ───────────────────────────────────────────────────────────

@api.post("/render")
def api_render():
    """PNG наліпки за payload'ом. Нічого не друкує й нічого не зберігає."""
    try:
        req = validate_payload(_body(), require_qty=False)
        product = req["product"]
        # Обходу тут немає й не буде: golden-гейт знає, які продукти baseline
        # неповні, і чекає для них саме 422 — замість того, щоб просити діру.
        require_complete_label(product)
    except PayloadError as e:
        return _payload_error(e)

    want_trace = req["trace"] or request.args.get("trace") in ("1", "true", "yes")
    try:
        if want_trace:
            img, trace = make_label_traced(product, req["weight_g"],
                                           made_on=req["made_on"],
                                           best_before=req["best_before"])
        else:
            img = make_label(product, req["weight_g"], made_on=req["made_on"],
                             best_before=req["best_before"])
    except Exception as e:                       # noqa: BLE001 — назовні як 500
        return _err("render_failed", f"{type(e).__name__}: {e}", 500)

    png = _png(img)
    sha = hashlib.sha256(png).hexdigest()
    warnings = label_warnings(product)

    if want_trace:
        trace["made_on_source"] = _made_on_source(req)
        return jsonify({
            "sha256": sha, "bytes": len(png), "width": img.width,
            "height": img.height, "warnings": warnings,
            "png_base64": base64.b64encode(png).decode("ascii"),
            "trace": trace,
        })

    made, best = label_dates(product, req["made_on"], req["best_before"])
    # У заголовках тільки ASCII: werkzeug кодує їх latin-1, український текст
    # попереджень туди не влізе — він є у /api/print і в режимі trace.
    return Response(png, mimetype="image/png", headers={
        "X-Label-Sha256": sha,
        "X-Label-Height": str(img.height),
        "X-Label-Width": str(img.width),
        "X-Label-Made-On": made.isoformat(),
        "X-Label-Best-Before": best.isoformat(),
        "X-Label-Made-On-Source": _made_on_source(req),
        "X-Label-Warning-Count": str(len(warnings)),
        "Cache-Control": "no-store",
    })


# ── /api/print ────────────────────────────────────────────────────────────

@api.post("/print")
def api_print():
    """Рендерить і друкує (real) або зберігає у файл (mock).

    Два блокери, яких немає в /api/render, бо тут наслідок фізичний:
      * `made_on` обов'язковий (422 missing_made_on) — дату визначає той, хто
        знає часовий пояс кухні, а не годинник контейнера;
      * неповна наліпка (frozen без складу/алергенів/зберігання/харчової
        цінності, deli без складу) — 422 incomplete_label, без винятків.
    """
    try:
        req = validate_payload(_body(), require_qty=True)
        product = req["product"]
        require_made_on(req)
        require_complete_label(product)
    except PayloadError as e:
        return _payload_error(e)

    try:
        img = make_label(product, req["weight_g"], made_on=req["made_on"],
                         best_before=req["best_before"])
    except Exception as e:                       # noqa: BLE001
        return _err("render_failed", f"{type(e).__name__}: {e}", 500)

    png = _png(img)
    sha = hashlib.sha256(png).hexdigest()
    made, best = label_dates(product, req["made_on"], req["best_before"])

    try:
        saved = print_label(img, qty=req["qty"])
    except Exception as e:                       # noqa: BLE001 — реальний текст
        return _err("printer_error", f"{type(e).__name__}: {e}", 502,
                    printer={"model": config.PRINTER_MODEL,
                             "device": config.DEVICE,
                             "mode": config.PRINT_MODE},
                    render_sha256=sha)

    return jsonify({
        "sent": True,
        "mode": config.PRINT_MODE,
        "qty": req["qty"],
        "render_sha256": sha,
        "bytes": len(png),
        "height": img.height,
        "made_on": made.isoformat(),
        "best_before": best.isoformat(),
        "made_on_source": _made_on_source(req),
        "warnings": label_warnings(product),
        "saved": saved or [],
        "printer": {"model": config.PRINTER_MODEL, "device": config.DEVICE,
                    "label_size": config.LABEL_SIZE, "hq": config.PRINT_HQ},
    })


# ── /api/healthz ──────────────────────────────────────────────────────────

@api.get("/healthz")
def api_healthz():
    """Стан сервісу. Пристрій принтера тільки перевіряється на існування —
    ніяких читань статусу з USB на цьому шляху (зависле читання не має права
    покласти /healthz)."""
    fonts_actual = actual_font_hashes()
    fonts_ok = all(fonts_actual.get(n) == h for n, h in EXPECTED_FONT_SHA256.items())
    device_present = os.path.exists(config.DEVICE)
    return jsonify({
        "status": "ok" if fonts_ok else "degraded",
        "service": "freezer-labels-renderer",
        "version": SERVICE_VERSION,
        "build_sha": BUILD_SHA,
        "print_mode": config.PRINT_MODE,
        "printer": {
            "model": config.PRINTER_MODEL,
            "device": config.DEVICE,
            "device_present": device_present,
            "backend": config.BACKEND,
            "label_size": config.LABEL_SIZE,
            "hq": config.PRINT_HQ,
        },
        "fonts": {
            "ok": fonts_ok,
            "dir": os.path.dirname(config.FONT_REG),
            "expected": EXPECTED_FONT_SHA256,
            "actual": fonts_actual,
        },
        "label": {"width_px": config.WIDTH, "margin_px": config.MARGIN,
                  "scale": config.SCALE},
        "versions": {"python": platform.python_version(),
                     "pillow": _pkg("pillow"), "brother_ql": _pkg("brother_ql"),
                     "flask": _pkg("flask")},
        "output_dir": config.OUTPUT_DIR,
        # Годинник видно навмисно: на /api/print дата обов'язкова, але /api/render
        # і старий /preview ще мають fallback «сьогодні», і тут одразу читається,
        # чи справді контейнер у Europe/Helsinki, а не в UTC.
        "timezone": {"tz_env": os.environ.get("TZ"), "names": list(time.tzname),
                     "utc_offset": datetime.now().astimezone().strftime("%z")},
        "server_time": datetime.now().isoformat(timespec="seconds"),
    })


def register_api(app):
    # Український текст помилок читабельний у curl/логах, а не др...
    app.json.ensure_ascii = False
    app.register_blueprint(api)
    # Будь-який PayloadError, що просочився повз явні try, все одно 422.
    app.register_error_handler(PayloadError, lambda e: _payload_error(e))
    return app
