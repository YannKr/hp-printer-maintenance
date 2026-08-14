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

`tomli_w` is a declared dependency and writes the config on every version. Python < 3.11 also needs `tomli` to read it (installed by `setup.sh`); 3.11+ uses stdlib `tomllib`.

## Key design notes

- The launcher `hpmaint.py` re-execs itself under `.venv/python` if not already inside the venv, so users never need to manually activate.
- Rich theme tokens (`moo.ok`, `moo.warn`, `moo.accent`, etc.) are defined in `theme.py`.
- HTTP 403 from the printer has three causes: no password configured, a wrong password, or a refusal despite a valid session. `EWSClient._denied_reason()` picks the right one from what `probe()` observed, so the tool never tells a user to run `hpmaint configure` when they already have.
- `/AuthChk` reports "password is set" in two ways depending on firmware: HTTP 401, or HTTP 200 with `{"hasAuth": true}`. Both must trigger the Digest handshake.
- Redirects are never followed automatically. `ews._https_upgrade()` honours exactly one shape: http → https, same host, same path, same query, default port. Some firmware sends the whole EWS to https and that is the only redirect a printer has a reason to send. TLS on the upgraded connection is not verified (`ews._VERIFY_UPGRADE`) because these certificates are self-signed; the target is pinned to the same host and path, so the redirect cannot move credentials elsewhere.
- `InternalPrintCap.xml` is fetched before each job to validate that the requested job type is supported. The parser matches any element whose local name contains `jobtype`, and permits the attempt when the list comes back empty. An unrecognised naming variant must not refuse every job.
