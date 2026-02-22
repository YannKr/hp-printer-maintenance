# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup and running

```sh
./setup.sh          # create .venv, install deps (requests, zeroconf, rich, click)
./hpmaint.py        # run (auto-activates .venv)
hpmaint             # alternative if installed via pip install -e .
```

Debug logging:
```sh
./hpmaint.py --debug run standard    # also prints to stderr
# Log always written to ~/.local/share/hpmaint/hpmaint.log
```

There is no test suite. Manual testing requires a real printer on the LAN (or a fixed IP via `--ip`).

## Architecture

The codebase is a layered CLI tool. Data flows top-down:

```
main.py (Click CLI + Rich UI)
  └─ maintenance.py  (Sequence/Step dataclasses + runner)
       └─ ews.py     (EWSClient — HTTP Digest Auth + LEDM XML)
  └─ discover.py     (mDNS/Bonjour → LAN /24 port scan fallback)
  └─ config.py       (TOML file + env var overrides)
```

### `ews.py` — the core

`EWSClient` talks to HP's LEDM (Lightweight Embedded Device Management) XML REST API:

- `probe()` — auth flow: GET `/` (session cookie) → GET `/AuthChk` → Digest Auth with `X-Auth-Client-Counter` nonce
- `_internal_print(job_type)` — POSTs to `/DevMgmt/InternalPrintDyn.xml`. All POST requests must include `X-Requested-With`, `Origin`, `Referer` headers (nginx CSRF check on the printer)
- `align_printhead()` — POSTs to `/Calibration/Session`
- `get_ink_levels()` — GETs `/DevMgmt/ConsumableConfigDyn.xml`

Every operation returns `MaintenanceResult(success, message, manual_instructions)`. If automation fails, `manual_instructions` contains touchscreen fallback steps shown to the user.

### `maintenance.py` — sequences

`SEQUENCES` dict maps keys (`refresh`, `standard`, `deep`, `nuclear`) to `Sequence` objects containing `Step` lists. `_dispatch_step()` maps steps to EWSClient calls by **string matching on `step.label`** — this is intentional (keeps sequence definitions declarative).

### `discover.py`

Tries mDNS first (zeroconf, 4 s timeout across 5 service types). Falls back to scanning the local /24 with 64 concurrent workers, then confirms candidates by fetching `/` and checking for HP keywords.

### `config.py`

Config file: `~/.config/hpmaint/config.toml`. Env vars override file values:

| Env var | Purpose |
|---|---|
| `HPMAINT_PRINTER_IP` | Skip discovery |
| `HPMAINT_PRINTER_PASSWORD` | EWS password |
| `HPMAINT_CONFIG` | Override config file path |
| `HPMAINT_DEBUG` | Enable debug logging |
| `HPMAINT_LOG_FILE` | Override log file path |

Python < 3.11 requires `tomli`/`tomli_w` (installed by `setup.sh`); 3.11+ uses stdlib `tomllib`.

## Key design notes

- The launcher `hpmaint.py` re-execs itself under `.venv/python` if not already inside the venv, so users never need to manually activate.
- Rich theme tokens (`moo.ok`, `moo.warn`, `moo.accent`, etc.) are defined in `theme.py`.
- HTTP 403 from the printer means the EWS password is set but not configured — the tool surfaces this with instructions to run `hpmaint configure`.
- `InternalPrintCap.xml` is fetched before each job to validate that the requested job type is supported, making the tool adaptive across firmware versions.
