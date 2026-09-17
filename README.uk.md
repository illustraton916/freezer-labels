[English](README.md) | **Українська**

# 🏷️ Наліпки харчових продуктів для Brother QL-700

Веб-каталог: відкрив сторінку → вибрав продукт → вписав вагу → надрукувалась
етикетка (склад, харчова цінність, дати, вага, ціна). Дата, термін придатності
й ціна рахуються автоматично.

```
[QL-700] --USB--> [Docker: цей застосунок] <--Wi-Fi--> [Планшет/ноут: браузер]
```

## Два типи наліпок
- **`frozen`** (заморозка) — повна етикетка: склад, алергени, інструкція
  приготування, умови зберігання, харчова цінність /100 г, дати, вага, ціна
  (`Hinta: 5,25 € (15 €/kg)`).
- **`deli`** (салати/делі) — коротка: склад, вага + ціна
  (`Paino: 500 g | Hinta: 7,50 € (15 €/kg)`).

Дати скрізь у форматі `18.07.2026`. Ціна рахується з `price_per_kg`:
`ціна = вага(кг) × ставка`, формат `x,xx €`.

На сторінці друку є **пошук** (по назві та укр. примітці) і **вкладки
категорій** (поле `category` продукту, вільний текст). Інтерфейс адаптовано
під телефон.

## Що всередині
| Файл / модуль | Призначення |
|------|-------------|
| `products.json` | продукти з усіма полями (див. нижче) |
| `app.py` | точка входу: **два сервери** — `/api/*` на 5001 і старий UI на 5000 |
| `labels/config.py` | налаштування з env, шрифти, масштаб |
| `labels/formatting.py` | форматування цін, ваги, дат |
| `labels/store.py` | читання/запис `products.json`, slug, категорії |
| `labels/forms.py` | парсинг і валідація форми продукту |
| `labels/rendering.py` | рендер наліпки (Pillow, авто-висота) + layout-trace |
| `labels/printing.py` | друк: mock (у файл) або real (`brother_ql`) |
| `labels/routes.py` | Flask-роути старого веб-інтерфейсу + той самий guard неповної наліпки |
| `labels/service.py` | **HTTP-сервіс `/api/render`, `/api/print`, `/api/healthz`** |
| `labels/validation.py` | перевірка payload'а + guard неповної `frozen`-наліпки |
| `labels/fonts_check.py` | вендорені DejaVu + перевірка SHA-256 на старті |
| `labels/fonts/*.ttf` | самі шрифти (не з `apt` — див. нижче) |
| `labels/templates/` | HTML: `print.html`, `admin_base/list/form.html` |
| `tools/golden_gate.py` | гейт «66 наліпок байт-у-байт як на проді» + звірка конфігурації друку |
| `tools/smoke_api.py` | димова перевірка контракту `/api/*` і старого UI |
| `Dockerfile`, `docker-compose*.yml` | збірка й запуск |

### Додати / змінити продукт (`products.json`)
Заморозка:
```json
{
  "id": "krepit-kanan", "type": "frozen",
  "name": "KREPIT KANAN KANSSA", "note_uk": "Млинці з куркою",
  "price_per_kg": 15.0, "shelf_life_days": 184, "date_format": "en",
  "frozen_mark": "pakastettu tuote",
  "ingredients": ["Taikina: ...", "Täyte: ..."],
  "allergens": "Sisältää gluteenia, munia ja maitotuotteita.",
  "instructions": "Lämmitä ...", "storage": "Säilytä -18 °C:ssa ...",
  "nutrition": {"energy_kj": 850, "energy_kcal": 205, "fat": "8 g",
                "saturated": "2 g", "carbs": "22 g", "protein": "10 g", "salt": "1 g"}
}
```
Делі:
```json
{
  "id": "olivier-makkara", "type": "deli",
  "name": "OLIVIER-SALAATTI MAKKARALLA", "note_uk": "Олів'є з ковбасою",
  "price_per_kg": 15.0, "shelf_life_days": 10, "date_format": "numeric",
  "ingredients_text": "keitetty peruna, porkkana, ..."
}
```
- `id` — унікальний, без пробілів. `note_uk` — внутрішня примітка, на наліпку **не** друкується.
- `shelf_life_days` — термін придатності; `Parasta ennen` = «сьогодні + днів».
- `date_format` — `en` (18 Jul 2026) або `numeric` (18.07.2026).

> Редагувати JSON вручну не обов'язково — є веб-адмінка (нижче).

## ⚙️ Керування продуктами (адмінка)

Відкрий **http://localhost:5000/admin** (або кнопка «⚙️ Керування» на сторінці
друку). З Ф2 порт 5000 публікується лише на `127.0.0.1`, тож із ноутбука —
через тунель: `ssh -L 5000:localhost:5000 root@<сервер>`.
- список усіх продуктів → **Редагувати** / **Видалити**;
- **+ Додати продукт** — форма з усіма полями, яка адаптується під тип
  (для делі поля харчової цінності приховані);
- `id` генерується автоматично зі slug назви (унікальний);
- зміни одразу пишуться у `products.json` (write-in-place, бо файл bind-mount).

---

## 🔌 Сервіс для бекенду (`/api/*`, фаза Ф0)

Цей застосунок — ще й **внутрішній сервіс друку** для нового Node-бекенду
(`mylly-kitchen`). Рендерер лишається без БД, без авторизації і **без власного
годинника**: увесь продукт і дата приходять у payload.

| Метод | Ендпоінт | Що робить |
|---|---|---|
| `POST` | `/api/render` | PNG наліпки за payload'ом. Нічого не друкує |
| `POST` | `/api/print` | те саме + `qty`, друкує (у mock — зберігає файл) |
| `GET` | `/api/healthz` | версія, режим друку, наявність пристрою, хеші шрифтів |

**Порти (Ф2).** `/api/*` слухає **5001** і назовні **не публікується ніколи** —
до нього ходить лише контейнер `api` по docker-мережі. Старий веб-інтерфейс
слухає **5000** і публікується лише на `127.0.0.1`. Це два різні Flask-застосунки:
на 5001 старих роутів немає (404), на 5000 немає `/api/*` (404). Прапорець
`ENABLE_LEGACY_UI=false` знімає старий інтерфейс повністю — роути не
реєструються, сокет 5000 не відкривається.

Тому перевіряти `/api/*` треба **зсередини** docker-мережі. У самому образі
curl немає — є готові інструменти:

```bash
docker compose exec labels python tools/smoke_api.py
docker compose exec labels python tools/golden_gate.py --mode both
```

Приклад запиту (з будь-якої машини, що бачить порт 5001 — наприклад, із
сусіднього контейнера `api`, або локально під час запуску `python app.py`):

```bash
curl -sS -X POST localhost:5001/api/render -H 'Content-Type: application/json' \
  -d '{"product": {"id":"x","type":"frozen","name":"KREPIT", "price_per_kg":15.0,
       "shelf_life_days":90, "ingredients":["Taikina: ..."],
       "allergens":"Sisältää gluteenia.", "storage":"Säilytä -18 °C:ssa.",
       "nutrition":{"energy_kj":850,"energy_kcal":205,"fat":"8 g","saturated":"2 g",
                    "carbs":"22 g","protein":"10 g","salt":"1 g"}},
       "weight_g": 350, "made_on": "2026-09-16"}' -o label.png
```

Продукт можна класти і плоско (поля продукту поруч із `weight_g`), і в ключ
`product`. Керівні поля: `weight_g` (null = порожнє місце під вагу), `made_on`,
`best_before` (типово `made_on + shelf_life_days`), `qty` (лише `/api/print`),
`trace`. Обхідного `allow_incomplete` **не існує**: guard неповної наліпки не
має вимикача (`tools/smoke_api.py` перевіряє саме це).

**Що відрізняє `/api/*` від старого веб-інтерфейсу — і це навмисно:**

1. **Дата параметром.** `made_on` приходить ззовні; без нього береться дата
   контейнера (стара поведінка), і у відповіді видно `made_on_source`.
2. **Закритий список полів.** Перейменоване або зайве поле — `422`, а не мовчки
   надрукована наліпка без харчової цінності.
3. **Дата обов'язкова для друку.** `/api/print` без `made_on` → `422
   missing_made_on`: годинник контейнера не має права визначати юридично
   значущу дату виготовлення.

**А от guard неповної наліпки спільний для обох шляхів друку.** `frozen` без
`ingredients` / `allergens` / `storage` / `nutrition` (і `deli` без
`ingredients_text`) не друкується ні через `/api/print` (`422
incomplete_label`), ні через старий UI (`labels/routes.py::do_print` кличе той
самий `require_complete_label`, а сторінка друку позначає таку картку й гасить
кнопку). До вересня 2026 guard стояв лише на `/api/*` — і порт 5000, увімкнений
у проді з `PRINT_MODE=real`, спокійно видавав стрічку без складу (бойовий
`kaalikarylet`). «Розбий скло» — це обхід Node-бекенду, а не обхід харчового
маркування.

`/preview` guard'а не має навмисно: показати на екрані, чого бракує, — не те
саме, що наклеїти це на продукт.

Помилки: `{"error": {"code": "invalid_payload|incomplete_label|printer_error",
"message": "...", "issues"/"missing": [...]}}`.

### Layout-trace (діагностика)

`POST /api/render?trace=1` (або `"trace": true`) повертає JSON: `png_base64`,
`sha256` і `trace.ops` — кожен намальований рядок із шрифтом, кеглем,
координатою `y` і точками переносу (`op: "block"` показує, на скільки рядків
розбився абзац). Це те, що перетворює «хеш наліпки змінився» на «ось цей рядок
перенісся інакше».

### Вендорені шрифти

`DejaVuSans.ttf` і `DejaVuSans-Bold.ttf` лежать у `labels/fonts/`, а не ставляться
з `apt`: верстка наліпки — це метрики конкретного білда шрифту, і `apt` не є
системою версіонування. Хеші зашиті в `labels/fonts_check.py` і перевіряються
**на старті** — при розбіжності застосунок не стартує (вихід 1, явна помилка).
`/api/healthz` показує очікувані й фактичні хеші.
Походження, версія й ліцензія шрифтів (DejaVu Fonts License — вільна,
дозвільна) — у [labels/fonts/NOTICE.md](labels/fonts/NOTICE.md).

### Golden-гейт (66 наліпок)

22 бойові продукти × 3 ваги мають давати байт-у-байт ті самі PNG, що їх друкує
сервер. Еталони зняті з прода (`mylly-kitchen/migrate/output/golden-hashes.json`),
дата зафіксована (`--made-on`, типово `2026-09-16` — день зняття еталону).

```bash
docker run --rm -e PRINT_MODE=mock \
  -v <шлях>/mylly-kitchen/migrate:/golden:ro freezer-labels-labels:latest \
  python tools/golden_gate.py --mode direct          # прямий виклик make_label
docker exec <контейнер> python tools/golden_gate.py  # direct + http + guard
docker exec <контейнер> python tools/smoke_api.py    # контракт /api/* і старий UI
```

Ненульовий код виходу = наліпка поїхала; PNG і траси розбіжностей лягають у
`output/golden-mismatch/`. Через HTTP гейт іде тим самим `/api/render`, тож
перевіряє ще й контракт сервісу.

> `kaalikarylet` у бойових даних не має складу, тому guard законно відмовляє
> йому в рендері — і гейт цього **чекає**: у режимі `http` для оголошених
> `--expect-incomplete` успіхом зараховується саме `422 incomplete_label`, а їхні
> хеші звіряє режим `direct`, де guard'а немає за побудовою. Жодного
> `ALLOW_INCOMPLETE_RENDER` більше не існує ні в коді, ні в компоузі: гейт
> ганяється проти прод-конфігурації, без env-дір.

**Крім хешів гейт звіряє конфігурацію друку.** Хеш рахується з PNG, а в принтер
іде `brother_ql.convert(label=LABEL_SIZE, hq=PRINT_HQ, …)`: `LABEL_SIZE=29` або
`PRINT_HQ=true` не змінюють жодного байта PNG — гейт лишався б зеленим 66/66, а
зі столу брали б наліпку іншої ширини. Тому кожен режим ще й порівнює
`LABEL_SIZE`, `PRINT_HQ`, `LABEL_SCALE`, ширину полотна, поле й хеші шрифтів із
зашитим `EXPECTED_CONFIG` (у `direct` — свій процес, у `http` — `/api/healthz`
живого сервісу). Розбіжність = червоний гейт нарівні з розбіжністю хешів.

---

## 🧪 Тест на ноуті (Docker Desktop, без принтера)

USB-принтер під Windows у Docker не прокидається, тому на ноуті ганяємо все,
крім фізичного друку. Замість друку наліпка зберігається у папку `./output` —
подивишся, що саме друкувалось би.

```bash
docker compose up --build
```
Відкрий **http://localhost:5000** → тицяй продукти. Кожен «друк» кладе PNG
наліпки у `./output/`. Так перевіряєш список, вигляд наліпок, дати, терміни.

Це працює бо базовий `docker-compose.yml` стоїть у режимі `PRINT_MODE=mock`.
Нічого міняти в коді при переносі на сервер не треба — див. нижче.

---

## 🖨️ Бойовий запуск на сервері (реальний друк)

Той самий код, просто додаєш серверний оверлей `docker-compose.prod.yml`
(вмикає реальний друк і проброс USB):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Перед цим прокинь принтер у VM/LXC (крок 1 нижче).

## Крок 1. Прокинути USB-принтер на Proxmox

Найнадійніший варіант — **VM з пробросом USB по ID** (переживає перетики кабелю
й перезавантаження). USB-ID QL-700: `04f9:2042`.

```bash
# на хості Proxmox, <vmid> — id твоєї VM з Linux + Docker
qm set <vmid> -usb0 host=04f9:2042
```

Усередині VM перевір, що принтер видно як символьний пристрій:
```bash
ls -l /dev/usb/lp0
```
Якщо шляху немає — переконайся, що завантажений модуль ядра `usblp`
(`modprobe usblp`).

> Альтернатива — привілейований **LXC** замість VM. Тоді в
> `/etc/pve/lxc/<id>.conf` додай:
> ```
> lxc.cgroup2.devices.allow: c 180:* rwm
> lxc.mount.entry: /dev/usb/lp0 dev/usb/lp0 none bind,optional,create=file
> ```
> VM простіший і стабільніший — раджу починати з нього.

## Крок 2. Запустити застосунок

Скопіюй цю папку на VM/LXC (де видно `/dev/usb/lp0`) і запусти з серверним оверлеєм:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## Крок 3. Друкувати

З Ф2 старий інтерфейс більше **не відкритий у LAN** — він слухає лише
`127.0.0.1`. З ноутбука:
```bash
ssh -L 5000:localhost:5000 root@<ip-сервера>   # далі http://localhost:5000
```
Штатний шлях для кухні — планшет і телефон через новий бекенд
(`http://<ip-сервера>:8080`), а це — «розбий скло».
Бачиш картки продуктів з прев'ю наліпки → кількість → **Друк**.

---

## Налаштування

Через `environment` у `docker-compose.yml`:

| Змінна | За замовч. | Опис |
|--------|-----------|------|
| `PRINTER_MODEL` | `QL-700` | модель принтера |
| `LABEL_SIZE` | `62` | тип стрічки: `62` = безперервна 62мм (DK-22205). Для висічених — свій код, напр. `29x90`. **Бойове значення звіряє golden-гейт** |
| `PRINTER_DEVICE` | `/dev/usb/lp0` | шлях до принтера |
| `PRINT_HQ` | `false` | `false` = швидший друк (стандартна якість), `true` = чіткіше але повільніше. **Звіряє golden-гейт** |
| `LABEL_SCALE` | `1.1` | масштаб шрифтів наліпки (1.0 = базовий, 1.1 = +10%). **Звіряє golden-гейт** |
| `SECRET_KEY` | `dev-only-secret` | секрет Flask для flash-повідомлень старого UI. У публічному репозиторії ключа немає навмисно — задавай своє значення в оточенні |
| `PRINT_MODE` | `real` | `mock` = не друкувати, зберігати PNG у `OUTPUT_DIR` |
| `BUILD_SHA` | `dev` | версія збірки, видно в `/api/healthz` |

Три позначені змінні впливають на фізичну наліпку, тож у гейті вони прибиті
константами (`EXPECTED_CONFIG` у `tools/golden_gate.py`): міняти їх можна лише
разом із перезняттям еталонів і підписом власника.

Список підтримуваних розмірів стрічки:
```bash
docker compose exec labels brother_ql info labels
```

## Якщо не друкує
- `ls -l /dev/usb/lp0` є на хості? Якщо ні — принтер не прокинувся (крок 1).
- Права: пристрій зазвичай `root:lp 660` — контейнер працює від root, доступ є.
- Логи: `docker compose logs -f labels`.
- Перевір друк напряму:
  `docker compose exec labels brother_ql -b linux_kernel -p /dev/usb/lp0 -m QL-700 print -l 62 <файл.png>`
