# HP Printer Maintenance

CLI tool for automating HP inkjet printhead cleaning, alignment, and diagnostics
via the printer's Embedded Web Server (EWS).

---

## Why this exists

HP inkjet printers clog fast. Leave one idle for a few weeks and the nozzles dry
out — you get banding, missing colours, or nothing at all. The fix is to run
cleaning and alignment cycles, but doing that through the printer's touchscreen
or web UI is slow and repetitive, especially when a badly clogged head needs
several rounds.

HP's official software (HP Smart) offers no maintenance scheduling or
command-line access. HPLIP covers Linux only and has no support for this
printer's generation. No existing tool spoke the right protocol for the HP Envy
Photo 7800 series, which uses a JavaScript SPA over HP's LEDM XML API — not the
traditional HTML-form EWS that older tools rely on.

This tool reverse-engineers that API to automate the full maintenance workflow:
discover the printer, authenticate, trigger cleaning/alignment jobs directly, and
read ink levels back — all from the terminal, scriptable and unattended.

## Tested on

**HP Envy Photo 7855** (firmware 2019–2025)

---

## Compatible printers

The tool uses HP's EWS — the built-in web interface that all networked HP
inkjet printers expose on port 80. Maintenance forms are discovered dynamically
by parsing the EWS pages rather than hardcoding paths, so it adapts to firmware
differences across models.

### Likely compatible (same EWS platform)

These share the same EWS structure as the 7855 and should work out of the box:

| Series | Models |
|--------|--------|
| HP Envy Photo 7800 | 7800, 7802, 7855, 7858 |
| HP Envy Photo 7100 | 7130, 7132, 7155, 7158, 7164 |
| HP Envy Photo 6200 | 6220, 6230, 6232, 6252, 6255, 6258 |
| HP Envy 6000       | 6052, 6055, 6058, 6075 |
| HP Envy 6400       | 6452, 6455, 6458, 6475 |

### Probably compatible (similar EWS, minor differences expected)

These use a closely related EWS. Automation may work; if it doesn't, the tool
prints clear manual touchscreen instructions as a fallback.

| Series | Models |
|--------|--------|
| HP Envy 5000       | 5020, 5030, 5032, 5052, 5055, 5058 |
| HP Envy Inspire 7200 | 7200, 7201, 7220, 7221, 7230, 7255, 7258 |
| HP OfficeJet 5200  | 5200, 5212, 5220, 5230, 5252, 5255, 5258 |
| HP OfficeJet 4650  | 4650, 4652, 4654, 4655, 4658 |
| HP OfficeJet 3830  | 3830, 3831, 3832, 3833, 3834 |

### May work (EWS varies more by firmware)

| Series | Notes |
|--------|-------|
| HP OfficeJet Pro 8020 | 8022, 8025, 8028 — newer JS-heavy EWS |
| HP OfficeJet Pro 9010 | 9010, 9012, 9015, 9018, 9019 |
| HP OfficeJet Pro 9020 | 9020, 9022, 9025 |
| HP DeskJet 2700       | 2700, 2720, 2722, 2723, 2724, 2752, 2755 |
| HP DeskJet 4100       | 4120, 4122, 4130, 4132, 4134, 4152, 4155 |
| HP DeskJet 3630       | 3630, 3632, 3634, 3635, 3636 |

> **Note:** HP OfficeJet Pro and newer DeskJet models use a more JavaScript-heavy
> EWS. If form-based automation fails, the tool will display equivalent manual
> touchscreen instructions for every operation.

### Not compatible

- HP LaserJet series (different maintenance model — laser, no printhead)
- HP PageWide series (different EWS and maintenance interface)
- Printers without a network connection (USB-only models have no EWS)

---

## Requirements

- Python 3.10+
- Printer connected to the same LAN (Wi-Fi or Ethernet)
- EWS accessible on port 80 (default; no firewall blocking it)

---

## Setup

```sh
git clone https://github.com/YannKr/hp-printer-maintenance
cd hp-printer-maintenance
./setup.sh
```

---

## Usage

```sh
# Interactive menu (auto-discovers printer on the LAN)
./hpmaint.py

# Run a maintenance sequence unattended
./hpmaint.py run refresh     # 1× light clean + test print        (1–7 days idle)
./hpmaint.py run standard    # 2× light clean + align + test      (1–4 weeks idle)
./hpmaint.py run deep        # 1× deep + 1× light + align         (1–3 months idle)
./hpmaint.py run nuclear     # 5× deep + soak + align             (3+ months / severe clogs)

# Repeat a sequence
./hpmaint.py run deep --repeat 2

# Individual operations
./hpmaint.py op clean1       # light clean (~2 min)
./hpmaint.py op clean2       # deep clean (~5 min, uses more ink)
./hpmaint.py op clean2 -r 3  # deep clean × 3
./hpmaint.py op align        # printhead alignment
./hpmaint.py op quality      # print nozzle test pattern
./hpmaint.py op test         # print demo / status page
./hpmaint.py op ink          # read ink levels from EWS

# Status
./hpmaint.py status          # printer info + ink levels

# Persist settings (saves to ~/.config/hpmaint/config.toml)
./hpmaint.py configure

# Override printer IP without configuring (also via env var)
./hpmaint.py --ip 192.168.1.42 run standard
HPMAINT_PRINTER_IP=192.168.1.42 ./hpmaint.py status

# EWS password (if set on the printer)
./hpmaint.py --password secret run standard
HPMAINT_PRINTER_PASSWORD=secret ./hpmaint.py run deep
```

---

## Maintenance sequence guide

| Idle time | Recommended sequence | Rationale |
|-----------|---------------------|-----------|
| 1–7 days  | `refresh`  | Minor nozzle drying — one light flush is enough |
| 1–4 weeks | `standard` | Moderate drying — two flushes + re-align for quality |
| 1–3 months | `deep`    | Dried ink in nozzles — needs a heavy purge cycle |
| 3+ months / visible banding | `nuclear` | Severe clogs — multiple heavy purges with soak time |

Each sequence ends with a test print so you can judge the result immediately.
If quality is still poor after `nuclear`, run it once more after leaving the
printer powered on for 30 minutes (residual solvent softens dried ink).

---

## How it works

1. **Discovery** — finds the printer via mDNS/Bonjour (`_printer._tcp`, `_ipp._tcp`),
   with a fallback LAN port scan if mDNS yields nothing.
2. **Session setup** — visits the EWS root to obtain a session cookie, then
   authenticates using HTTP Digest Auth against `/AuthChk` (mirroring the browser
   SPA flow, including the `X-Auth-Client-Counter` nonce handshake).
3. **LEDM API calls** — POSTs XML jobs to HP's LEDM (Lightweight Embedded Device
   Management) endpoints: `POST /DevMgmt/InternalPrintDyn.xml` for cleaning/print
   jobs, `POST /Calibration/Session` for alignment, `GET /DevMgmt/ConsumableConfigDyn.xml`
   for ink levels. Requests include the jQuery AJAX headers (`X-Requested-With`,
   `Origin`, `Referer`) that nginx enforces as CSRF protection.
4. **Fallback** — if the printer rejects a command, prints equivalent step-by-step
   touchscreen instructions for the same operation.
