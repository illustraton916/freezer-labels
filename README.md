**English** | [Українська](README.uk.md)

# 🏷️ Food Labels for Brother QL-700

A small web app for printing food labels in a home/small-business kitchen:
open the page from any device on the network, pick a product, type in the
weight — and a Brother QL-700 prints a Finnish food-compliance label with
ingredients, allergens, nutrition facts, production and best-before dates,
net weight and price. Dates, shelf life and price are all calculated
automatically. Products are managed through a built-in web admin UI backed by
a single `products.json` file.

```
[QL-700] --USB--> [Docker: this app] <--Wi-Fi--> [tablet / laptop: browser]
```

## Two label types

- **`frozen`** — full label: ingredients, allergens (printed in bold),
  preparation instructions, storage conditions, nutrition per 100 g, dates,
  weight and price (`Hinta: 5,25 € (15 €/kg)`).
- **`deli`** (salads / deli counter) — short label: ingredients as one
  paragraph, weight + price (`Paino: 500 g | Hinta: 7,50 € (15 €/kg)`).

Dates are always printed as `18.07.2026`. Price is derived from
`price_per_kg`: `price = weight(kg) × rate`, formatted as `x,xx €`.

The print page has **search** (by name and internal note) and **category
tabs** (free-text `category` field on a product). The UI is optimized for
phones and tablets.

## What's inside

| File | Purpose |
|------|---------|
| `app.py` | renders the label (Pillow, auto-height canvas) and sends it to the printer (`brother_ql`); also serves the print page and the admin UI |
| `products.json` | the product catalog (see format below) |
| `Dockerfile`, `docker-compose*.yml` | build and run |

## Quick start

### Dev mode — no printer (mock)

The base `docker-compose.yml` runs with `PRINT_MODE=mock`: instead of
printing, every "print" saves the label as a PNG into `./output/`, so you can
check the catalog, label layout, dates and prices on any machine (USB
printers are not passed through to Docker Desktop on Windows/macOS anyway).

```bash
docker compose up --build
```

Open **http://localhost:5000** and click around. Each print drops a PNG into
`./output/`.

### Production mode — real USB printing

Same code; add the production overlay, which switches to real printing and
maps the USB device into the container:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The host must expose the printer as `/dev/usb/lp0` (kernel module `usblp`).
Then open `http://<server>:5000` from any device on the network.

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

Open **http://\<server\>:5000/admin** (or the "Manage" button on the print
page):

- list of all products with **Edit** / **Delete**;
- **Add product** — a form with all fields that adapts to the label type
  (nutrition fields are hidden for deli products);
- the `id` is generated automatically as a unique slug of the name;
- changes are written straight to `products.json` (write-in-place, since the
  file is bind-mounted into the container).

### Editing `products.json` by hand

Frozen product:

```json
{
  "id": "krepit-kanan", "type": "frozen",
  "name": "KREPIT KANAN KANSSA", "note_uk": "internal note",
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
  "price_per_kg": 15.0, "shelf_life_days": 10,
  "ingredients_text": "keitetty peruna, porkkana, ..."
}
```

- `id` — unique, no spaces. `note_uk` is an internal note and is **not**
  printed on the label.
- `shelf_life_days` — shelf life; `Parasta ennen` (best before) = today +
  that many days.
- `category` (optional) — free text used for the category tabs on the print
  page.

## Configuration

Set via `environment` in `docker-compose.yml` / the prod overlay:

| Variable | Default | Description |
|----------|---------|-------------|
| `PRINT_MODE` | `real` | `real` = send to the printer; `mock` = save PNGs to `OUTPUT_DIR` instead (the base compose file sets `mock`) |
| `PRINTER_MODEL` | `QL-700` | printer model |
| `LABEL_SIZE` | `62` | tape type: `62` = continuous 62 mm (DK-22205); die-cut tapes use their own code, e.g. `29x90` |
| `PRINTER_DEVICE` | `/dev/usb/lp0` | printer device path |
| `PRINTER_BACKEND` | `linux_kernel` | `brother_ql` backend |
| `PRODUCTS_FILE` | `products.json` | path to the product catalog |
| `OUTPUT_DIR` | `/app/output` | where mock-mode PNGs are saved |
| `PRINT_HQ` | `false` | `false` = faster print (standard quality), `true` = sharper but slower |
| `LABEL_SCALE` | `1.1` | label font scale (1.0 = base design, 1.1 = +10%) |
| `SECRET_KEY` | `dev-only-secret` | Flask session secret (used only for flash messages); set your own value in production |

List of supported tape sizes:

```bash
docker compose exec labels brother_ql info labels
```

## Troubleshooting

- Does `ls -l /dev/usb/lp0` exist on the host? If not, the printer isn't
  passed through (see the passthrough section above).
- Permissions: the device is usually `root:lp 660` — the container runs as
  root, so access is fine.
- Logs: `docker compose logs -f labels`.
- Test printing directly, bypassing the app:
  `docker compose exec labels brother_ql -b linux_kernel -p /dev/usb/lp0 -m QL-700 print -l 62 <file.png>`

## Security note

The app — including the admin UI — is designed for a **trusted LAN** and has
**no authentication**: anyone who can reach port 5000 can print labels and
edit the product catalog. Don't expose it to the internet; if you need remote
access, put it behind a VPN or a reverse proxy with auth.
