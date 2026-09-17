"""Точка входу рендерера: два сервери в одному процесі (Ф2).

  :5001  /api/* — render / print / healthz для Node-бекенду. ВНУТРІШНІЙ:
         у компоузі порт не публікується взагалі, тож дістатися до нього може
         тільки контейнер `api` по docker-мережі.
  :5000  старий веб-інтерфейс («розбий скло»). У компоузі публікується лише на
         127.0.0.1 → з LAN недоступний, з сервера — через
         `ssh -L 5000:localhost:5000 <user>@<host>`.
         Вимикається повністю через ENABLE_LEGACY_UI=false.

Чому один процес, а не два контейнери: USB-принтер тримає рівно один процес
(`/dev/usb/lp0` відкривається ексклюзивно). Два контейнери билися б за пристрій,
і жодна серіалізація в Node цього б не врятувала.

Змінні середовища (усі зі здоровими типовими значеннями):
  API_PORT=5001      API_BIND=0.0.0.0
  LEGACY_PORT=5000   LEGACY_BIND=0.0.0.0
  ENABLE_LEGACY_UI=true

Чому BIND типово 0.0.0.0, а не 127.0.0.1, якщо старий UI має бути «лише
локальним»: усередині контейнера пакет із опублікованого порту приходить з
docker-мосту, а не з loopback'а. Прив'язка до 127.0.0.1 **в контейнері** зробила
б `127.0.0.1:5000:5000` мертвим портом. Межу «лише локально» ставить компоуз
(адреса публікації), а BIND лишається для запуску без docker.
"""
import os
import sys
import threading

from werkzeug.serving import make_server

from labels import create_api_app, create_legacy_app, legacy_ui_enabled


def _port(name, default):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"{name}={raw!r} — очікується номер порту")


API_PORT    = _port("API_PORT", 5001)
API_BIND    = os.environ.get("API_BIND", "0.0.0.0")
LEGACY_PORT = _port("LEGACY_PORT", 5000)
LEGACY_BIND = os.environ.get("LEGACY_BIND", "0.0.0.0")


def _make(app, host, port, name):
    """Піднімає сокет одразу: зайнятий порт має валити старт, а не спливати
    через годину як «служба є, а не відповідає»."""
    srv = make_server(host, port, app, threaded=True)
    print(f"[{name}] слухає http://{host}:{port}", flush=True)
    return srv


def main():
    servers = []
    api = _make(create_api_app(), API_BIND, API_PORT, "api")
    servers.append(api)

    if legacy_ui_enabled():
        legacy = _make(create_legacy_app(), LEGACY_BIND, LEGACY_PORT, "legacy-ui")
        servers.append(legacy)
        threading.Thread(target=legacy.serve_forever, name="legacy-ui",
                         daemon=True).start()
    else:
        print("[legacy-ui] ENABLE_LEGACY_UI=false — старий інтерфейс вимкнено, "
              "сокет 5000 не відкривається", flush=True)

    try:
        api.serve_forever()          # головний потік: SIGTERM від docker працює
    except KeyboardInterrupt:
        pass
    finally:
        for s in servers:
            s.shutdown()


if __name__ == "__main__":
    main()
