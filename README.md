**English** | [Українська](README.uk.md)

# 🏷️ Food Labels for Brother QL-700

Renders and prints Finnish food-compliance labels on a Brother QL-700:
ingredients, allergens, nutrition facts, production and best-before dates, net
weight and price. Dates, shelf life and price are computed, not typed.

The renderer serves two consumers, and that shape is the project:

```
tablet --Wi-Fi--> Node backend --docker net--> :5001 /api/*  --+
                                                              +--> [this app] --USB--> [QL-700]
laptop --ssh -L tunnel-----------------------> :5000 legacy UI +
```

* **`:5001 /api/*`** — the internal print service (render / print / healthz).
  No database, no auth, no clock of its own: the product and the date arrive in
  the payload. This is what the Node backend calls.
* **`:5000`** — the original self-contained web UI (product cards, preview,
  print, admin CRUD over `products.json`), kept as break-glass: if the Node side
  is down, labels still come out.

Both run in **one process** (`app.py`) because the USB printer is held
exclusively by a single process — `/dev/usb/lp0` does not open twice, two
containers would fight over the device, and no serialization on the Node side
would fix that. They are still two separate Flask apps: 5001 has no legacy
routes (404), 5000 has no `/api/*` (404). Reaching the wrong one is impossible
by construction, not by convention.

## Two label types

- **`frozen`** — full label: ingredients, allergens (printed in bold),
  preparation instructions, storage conditions, nutrition per 100 g, dates,
  weight and price (`Hinta: 5,25 € (15 €/kg)`).
- **`deli`** (salads / deli counter) — short label: ingredients as one
  paragraph, weight + price (`Paino: 500 g | Hinta: 7,50 € (15 €/kg)`), dates.

Dates are always printed as `18.07.2026`. Price is derived from
`price_per_kg`: `price = weight(kg) × rate`, formatted as `x,xx €`. With no
weight the label prints a blank line to fill in by hand.

## What's inside

| File / module | Purpose |
|------|---------|
| `app.py` | entry point: **two servers in one process** — `/api/*` on 5001, legacy UI on 5000 |
| `labels/__init__.py` | app factories (`create_api_app` / `create_legacy_app`) and the `ENABLE_LEGACY_UI` flag |
| `labels/config.py` | env-driven settings, fonts, font scale |
| `labels/formatting.py` | price, weight and date formatting |
| `labels/store.py` | `products.json` read/write, slugs, categories |
| `labels/forms.py` | admin form parsing and validation |
| `labels/rendering.py` | label rendering (Pillow, auto-height canvas) + layout trace |
| `labels/printing.py` | printing: mock (to file) or real (`brother_ql`) |
| `labels/routes.py` | legacy web UI routes + the same incomplete-label guard |
| `labels/service.py` | **HTTP service: `/api/render`, `/api/print`, `/api/healthz`** |
| `labels/validation.py` | payload validation (closed field list) + the incomplete-label guard |
| `labels/fonts_check.py` | vendored DejaVu + SHA-256 verification at startup |
| `labels/fonts/*.ttf` | the fonts themselves (not from `apt` — see below) |
| `labels/templates/` | `print.html`, `admin_base/list/form.html` |
| `tools/golden_gate.py` | the "66 labels, byte for byte as in production" gate + print-config assertion |
| `tools/smoke_api.py` | contract smoke test for `/api/*` and the legacy UI |
| `products.json` | the product catalog (see format below) |
| `Dockerfile`, `docker-compose*.yml` | build and run |

## Print service (`/api/*`)

| Method | Endpoint | What it does |
|---|---|---|
| `POST` | `/api/render` | label PNG for the payload. Prints nothing, stores nothing |
| `POST` | `/api/print` | the same plus `qty`, and prints it (mock mode: saves a file) |
| `GET` | `/api/healthz` | version, print mode, device presence, font hashes, canvas geometry, timezone |

Port 5001 is **never published** — only the neighbouring `api` container reaches
it over the docker network; port 5000 is published on `127.0.0.1` only, and
`ENABLE_LEGACY_UI=false` removes it completely (routes unregistered, socket
never opened).

So `/api/*` has to be exercised **from inside** the docker network. The image
ships no curl; it ships the tools instead:

```bash
docker compose exec labels python tools/smoke_api.py
docker compose exec labels python tools/golden_gate.py --mode both
```

A request looks like this (from anything that can see port 5001 — the `api`
container, or locally while running `python app.py`):

```bash
curl -sS -X POST localhost:5001/api/render -H 'Content-Type: application/json' \
  -d '{"product": {"id":"x","type":"frozen","name":"KREPIT", "price_per_kg":15.0,
       "shelf_life_days":90, "ingredients":["Taikina: ..."],
       "allergens":"Sisältää gluteenia.", "storage":"Säilytä -18 °C:ssa.",
       "nutrition":{"energy_kj":850,"energy_kcal":205,"fat":"8 g","saturated":"2 g",
                    "carbs":"22 g","protein":"10 g","salt":"1 g"}},
       "weight_g": 350, "made_on": "2026-09-16"}' -o label.png
```

The product may be sent flat (product fields next to `weight_g`) or nested under
`product`. Control fields: `weight_g` (`null` = leave a blank for the weight),
`made_on`, `best_before` (defaults to `made_on + shelf_life_days`), `qty`
(`/api/print` only), `trace`. There is no `allow_incomplete` escape hatch — the
guard has no off switch, and `tools/smoke_api.py` asserts exactly that.

**Three things `/api/*` does differently from the legacy UI, deliberately:**

1. **The date is a parameter.** `made_on` comes from outside; without it the
   container clock is used (the old behaviour), and the response reports
   `made_on_source`.
2. **Closed field list.** A renamed or extra field is a `422`, not a label
   silently printed without its nutrition table.
3. **The date is mandatory for printing.** `/api/print` without `made_on` →
   `422 missing_made_on`. The container clock has no business deciding a legally
   meaningful production date (it runs UTC; the kitchen runs Europe/Helsinki).

**The incomplete-label guard, however, is shared by both print paths.** A
`frozen` product missing `ingredients` / `allergens` / `storage` / `nutrition`
(or a `deli` product missing `ingredients_text`) will not print through
`/api/print` (`422 incomplete_label`) and will not print through the legacy UI
either: `labels/routes.py::do_print` calls the same `require_complete_label`,
and the print page marks such a card and disables its button. Until September
2026 the guard existed only on `/api/*`, so nothing stopped port 5000 — enabled
in production with `PRINT_MODE=real` — from accepting such a product. Break-glass means bypassing the Node backend, not bypassing food labelling.

`/preview` has no guard, on purpose: showing on screen what is missing is not
the same act as sticking it on a product.

Errors are `{"error": {"code": "...", "message": "...", "issues"/"missing":
[...]}}` with codes `invalid_payload`, `incomplete_label`, `missing_made_on`
(422), `render_failed` (500) and `printer_error` (502).

### Layout trace

`POST /api/render?trace=1` (or `"trace": true`) returns JSON instead of a PNG:
`png_base64`, `sha256`, and `trace.ops` — every drawn line with its font, pixel
size, `y` coordinate and wrap points (`op: "block"` shows how many lines a
paragraph wrapped into). This is what turns "the label hash changed" into "this
one line wrapped differently".

### Vendored fonts

`DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` live in `labels/fonts/` instead of
coming from `apt`. Label layout is not "text on a canvas" — it is the metrics of
one specific font build: `wrap()` measures line width with
`ImageDraw.textlength`, and `text_fit()` steps the size down against the same
metric. A different DejaVu build means different break points, which means a
different ingredient list on a legally meaningful label that still looks
entirely plausible. `apt` is not a versioning system; the next image rebuild
could have swapped the file silently. This is also why the renderer is not
rewritten in another language: a different rasterizer produces a label that is
plausible and legally different.

The expected SHA-256 sums are hardcoded in `labels/fonts_check.py` (in code, not
in a manifest next to the fonts — otherwise swapping both would go unnoticed)
and verified **at startup**, before the first `ImageFont` exists. A mismatch
means the service does not start: exit 1, explicit error. `/api/healthz` reports
expected vs. actual. The fonts are DejaVu Sans 2.37 (Debian bookworm build);
provenance and licence — DejaVu Fonts License, free and permissive — are in
[labels/fonts/NOTICE.md](labels/fonts/NOTICE.md).

### Golden gate (66 labels)

22 production products × 3 weights (200 / 500 / 1000 g) = 66 labels that must
come out byte-identical to what the server prints today. The reference SHA-256
sums were captured from the production server, so the gate compares the renderer
against what is physically stuck on product, not against yesterday's copy of
itself. The date is pinned (`--made-on`, default `2026-09-16`, the day the
baseline was taken) — which is exactly why the renderer had to learn to accept
`made_on` from outside; otherwise the gate would rot daily.

The baseline and the 66 reference hashes live in a separate, private
repository together with the production data they were captured from, so a
fresh clone of *this* repo cannot run the gate as-is. Both paths are plain
CLI parameters (`--products`, `--golden`, `--expect-incomplete`), so pointing
the gate at your own catalogue and your own references is the intended way to
use it.

```bash
# baseline + hashes are mounted from the private data repo
docker run --rm -e PRINT_MODE=mock \
  -v <path>/<data-repo>/migrate:/golden:ro freezer-labels-labels:latest \
  python tools/golden_gate.py --mode direct     # direct make_label() call
docker exec <container> python tools/golden_gate.py   # direct + http + guard
docker exec <container> python tools/smoke_api.py     # /api/* and legacy UI contract
```

Both modes are required and catch different things: `direct` calls `make_label()`
in-process and catches layout and font drift; `http` goes through
`POST /api/render` and catches contract drift too (validation, date parsing, PNG
assembly). A non-zero exit means the label moved; mismatching PNGs and their
layout traces land in `output/golden-mismatch/`.

**Beyond the hashes, the gate asserts the print configuration**, because that
never enters the PNG hash. What reaches the printer is
`brother_ql.convert(label=LABEL_SIZE, hq=PRINT_HQ, …)`, and `LABEL_SIZE=29` or
`PRINT_HQ=true` change not one byte of the PNG: the gate would stay green 66/66
while the tape coming off the printer is a different width or density. So each
mode also compares `LABEL_SIZE`, `PRINT_HQ`, `LABEL_SCALE`, canvas width, margin
and the font hashes against a hardcoded `EXPECTED_CONFIG` — `direct` reads its
own process, `http` reads `/api/healthz` of the live service, which is what
catches "the container was started with a stale env". A mismatch fails the gate
exactly like a hash mismatch.

**The negative controls are expected to turn it red.** One baseline product
(`kaalikarylet`) has no ingredient list, so the guard is obliged to refuse it —
and the gate expects that: in `http` mode, for ids declared via
`--expect-incomplete`, exactly `422 incomplete_label` counts as a pass, while
their three hashes are verified in `direct` mode, where the guard does not exist
by construction. The declared list is cross-checked against the baseline using
the same `labels/validation.py` the service refuses with, so the gate goes red
both when the data changes and when someone weakens the guard (fewer incomplete
products than declared). Declaring none at all is a configuration error, not a
pass: a green gate that verified nothing is worse than a red one. `/api/print`
probes run only in `PRINT_MODE=mock` — the gate must never put a label on real
tape. And no `ALLOW_INCOMPLETE_RENDER` exists any more, in the code or in
compose: the gate runs against the production configuration, with no env holes.

## Quick start

### Dev mode — no printer (mock)

The base `docker-compose.yml` runs with `PRINT_MODE=mock`: instead of printing,
every "print" saves the label as a PNG into `./output/`, so you can check the
catalog, label layout, dates and prices on any machine (USB printers are not
passed through to Docker Desktop on Windows/macOS anyway).

```bash
docker compose up --build
```

Then open **http://localhost:5000** and click around.

### Production mode — real USB printing

Same code; add the production overlay, which switches to real printing and maps
the USB device into the container:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The host must expose the printer (kernel module `usblp`). The overlay maps
`/dev/brother-ql` — a udev symlink, since re-plugging the printer renumbers
`lp0` → `lp1` and silently breaks printing — onto `/dev/usb/lp0` in the
container; without that symlink on the host, map `/dev/usb/lp0` directly.

The legacy UI is no longer reachable from the LAN, so from a laptop:

```bash
ssh -L 5000:localhost:5000 <user>@<host>   # then http://localhost:5000
```

<details>
<summary>Running the host as a Proxmox VM / LXC</summary>

The most reliable setup is a **VM with USB passthrough by device ID** (it
survives cable re-plugs and reboots). The QL-700 USB ID is `04f9:2042`:

```bash
# on the Proxmox host, <vmid> = your Linux VM with Docker
qm set <vmid> -usb0 host=04f9:2042
```

Inside the VM, verify the printer shows up as a character device:

```bash
ls -l /dev/usb/lp0
```

If the path is missing, make sure the `usblp` kernel module is loaded
(`modprobe usblp`).

Alternatively, use a privileged **LXC** and add to `/etc/pve/lxc/<id>.conf`:

```
lxc.cgroup2.devices.allow: c 180:* rwm
lxc.mount.entry: /dev/usb/lp0 dev/usb/lp0 none bind,optional,create=file
```

The VM route is simpler and more stable.

</details>

## ⚙️ Managing products (admin UI)

Open **http://localhost:5000/admin** (or the "Manage" button on the print page)
over the tunnel above:

- list of all products with **Edit** / **Delete**;
- **Add product** — a form with all fields that adapts to the label type
  (nutrition fields are hidden for deli products);
- the `id` is generated automatically as a unique slug of the name;
- changes are written straight to `products.json` (write-in-place, since the
  file is bind-mounted into the container).

The print page has **search** (by name and internal note) and **category tabs**
(free-text `category` field on a product). The UI is optimized for phones and
tablets.

### Editing `products.json` by hand

Frozen product:

```json
{
  "id": "krepit-kanan", "type": "frozen",
  "name": "KREPIT KANAN KANSSA", "note_uk": "internal note",
  "category": "Pancakes",
  "price_per_kg": 15.0, "shelf_life_days": 184,
  "frozen_mark": "pakastettu tuote",
  "ingredients": ["Taikina: ...", "Täyte: ..."],
  "allergens": "Sisältää gluteenia, munia ja maitotuotteita.",
  "instructions": "Lämmitä ...", "storage": "Säilytä -18 °C:ssa ...",
  "nutrition": {"energy_kj": 850, "energy_kcal": 205, "fat": "8 g",
                "saturated": "2 g", "carbs": "22 g", "protein": "10 g", "salt": "1 g"}
}
```

Deli product:

```json
{
  "id": "olivier-makkara", "type": "deli",
  "name": "OLIVIER-SALAATTI MAKKARALLA", "note_uk": "internal note",
  "category": "Salads",
  "price_per_kg": 15.0, "shelf_life_days": 10,
  "ingredients_text": "keitetty peruna, porkkana, ..."
}
```

- `id` — unique, no spaces. `note_uk` is an internal note and is **not**
  printed on the label.
- `shelf_life_days` — shelf life; `Parasta ennen` (best before) = production
  date + that many days.
- `category` (optional) — free text used for the category tabs on the print
  page.
- The set of fields above is the whole set: `labels/validation.py` treats the
  product field list as closed, so an extra or renamed key is a `422` rather
  than text quietly missing from the label.

## Configuration

Set via `environment` in `docker-compose.yml` / the prod overlay:

| Variable | Default | Description |
|----------|---------|-------------|
| `PRINT_MODE` | `real` | `real` = send to the printer; `mock` = save PNGs to `OUTPUT_DIR` instead (the base compose file sets `mock`) |
| `PRINTER_MODEL` | `QL-700` | printer model |
| `LABEL_SIZE` | `62` | tape type: `62` = continuous 62 mm (DK-22205); die-cut tapes use their own code, e.g. `29x90`. **Asserted by the golden gate** |
| `PRINTER_DEVICE` | `/dev/usb/lp0` | printer device path inside the container |
| `PRINTER_BACKEND` | `linux_kernel` | `brother_ql` backend |
| `PRODUCTS_FILE` | `products.json` | path to the product catalog |
| `OUTPUT_DIR` | `/app/output` | where mock-mode PNGs are saved |
| `PRINT_HQ` | `false` | `false` = faster print (standard quality), `true` = sharper but slower. **Asserted by the golden gate** |
| `LABEL_SCALE` | `1.1` | label font scale (1.0 = base design, 1.1 = +10%). **Asserted by the golden gate** |
| `ENABLE_LEGACY_UI` | `true` | `false` = `/`, `/preview`, `/print`, `/admin/*` are not registered at all and the socket on 5000 is never opened |
| `SECRET_KEY` | `dev-only-secret` | Flask secret signing the legacy UI's flash cookie (there are no sessions and no auth); the public repo ships no key by design — set your own value in the environment |
| `TZ` | unset (UTC) | compose sets `Europe/Helsinki`. `/api/print` demands an explicit date, but `/api/render` and `/preview` still fall back to "today" — with the right zone that fallback cannot hand back yesterday |
| `BUILD_SHA` | `dev` | build stamp, visible in `/api/healthz` |

The three marked variables change the physical label, so they are nailed down as
constants in `EXPECTED_CONFIG` (`tools/golden_gate.py`): changing them means
re-capturing the golden baseline.

Ports and bind addresses are overridable too (`API_PORT` / `API_BIND`,
`LEGACY_PORT` / `LEGACY_BIND`). Inside the container both bind `0.0.0.0` on
purpose: a packet from a published docker port arrives from the bridge, not from
loopback, so binding `127.0.0.1` in the container would make
`127.0.0.1:5000:5000` a dead port. The "local only" boundary is the publish
address in compose; the bind variables are for running without docker.

List of supported tape sizes:

```bash
docker compose exec labels brother_ql info labels
```

## Troubleshooting

- The container exits immediately with a font error: a vendored font is missing
  or was modified. Restore the font — do not "fix the hash". Label layout
  depends on the metrics of those exact files.
- Does `ls -l /dev/usb/lp0` exist on the host? If not, the printer isn't passed
  through (see the passthrough section above).
- Permissions: the device is usually `root:lp 660` — the container runs as root,
  so access is fine.
- Logs: `docker compose logs -f labels`.
- Health and configuration as the running service sees them — run the smoke
  test from inside the container: `docker compose exec labels python tools/smoke_api.py`.
- Test printing directly, bypassing the app:
  `docker compose exec labels brother_ql -b linux_kernel -p /dev/usb/lp0 -m QL-700 print -l 62 <file.png>`

## Security note

Neither interface authenticates; the boundary is the deployment. `/api/*` is
internal to the docker network, and the legacy UI — print page and admin CRUD
alike — is published on loopback only, reached through an SSH tunnel. Anyone who
reaches either can print labels and edit the catalog, so don't expose them; for
remote access put them behind a VPN or an authenticating reverse proxy.
`ENABLE_LEGACY_UI=false` drops the legacy surface entirely once the backend path
is the only one in use.
