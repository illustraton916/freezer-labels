"""Фабрики Flask-застосунків рендерера.

Ф2 розводить два інтерфейси по різних портах і, головне, по різних застосунках:

  * `create_api_app()`   → тільки `/api/*` (render / print / healthz), порт 5001.
    Внутрішній сервіс для Node-бекенду. У компоузі цей порт не публікується
    ніколи — до нього ходить лише контейнер `api` по docker-мережі.
  * `create_legacy_app()` → тільки старий веб-інтерфейс (`/`, `/preview`,
    `/print`, `/admin/*`), порт 5000, опублікований лише на 127.0.0.1.
    Це «розбий скло»: якщо Node-бік ляже, наліпки друкуються далі.

Чому два ЗАСТОСУНКИ, а не один на двох сокетах: розділення має бути справжнім і
перевірюваним. На 5001 старих роутів немає взагалі (404), на 5000 немає `/api/*`
(404) — «достукатися не туди» неможливо за побудовою, а не за домовленістю.

`ENABLE_LEGACY_UI=false` вимикає старий інтерфейс повністю: роути не
реєструються, сервер на 5000 не піднімається (див. app.py). Це стан після Ф7.
Жоден рядок рендеру (`rendering.py`, `printing.py`, `config.py`) тут не змінено.
"""
import os

from flask import Flask

from .routes import register_routes
from .service import register_api

# Секрет підписує лише cookie з flash-повідомленнями старого інтерфейсу — там
# немає ні сесій, ні авторизації. Але читається він з оточення, а не зашитий:
# публічний репозиторій не має роздавати ключ, яким підписані cookie на проді.
# Дефолт навмисно названий так, щоб його не сплутали з бойовим значенням.
def _legacy_secret():
    return os.environ.get("SECRET_KEY", "dev-only-secret")

_TRUE = ("1", "true", "yes", "on")


def legacy_ui_enabled():
    """Чи вмикати старий веб-інтерфейс. Типово так (ENABLE_LEGACY_UI=true).

    Значення читається щоразу з env, а не кешується у змінній модуля: тести
    ганяють обидва стани прапорця в одному процесі.
    """
    return os.environ.get("ENABLE_LEGACY_UI", "true").strip().lower() in _TRUE


def create_api_app():
    """Сервіс для Node-бекенду: лише /api/render, /api/print, /api/healthz."""
    app = Flask(__name__)
    register_api(app)
    return app


def create_legacy_app():
    """Старий веб-інтерфейс: /, /preview, /print, /admin/* — і нічого більше."""
    app = Flask(__name__)          # шаблони — labels/templates
    app.secret_key = _legacy_secret()
    register_routes(app)
    return app


def create_app():
    """Обидва набори роутів в одному застосунку — ЛИШЕ для налагодження в один
    порт (`python -m flask --app app`). Бойовий запуск — app.py, два сервери.
    Прапорець ENABLE_LEGACY_UI поважається й тут.
    """
    app = Flask(__name__)
    app.secret_key = _legacy_secret()
    if legacy_ui_enabled():
        register_routes(app)
    register_api(app)
    return app
