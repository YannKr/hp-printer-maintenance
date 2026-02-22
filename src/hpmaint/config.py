"""Configuration: file-backed + env-var overrides."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Use stdlib tomllib on 3.11+; fall back to tomli on 3.10
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef,import-not-found]
        except ImportError:
            tomllib = None  # type: ignore[assignment]

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "hpmaint" / "config.toml"

_DEFAULTS: dict[str, Any] = {
    "printer": {
        "ip": "",           # auto-discovered if empty
        "port": 80,
        "username": "admin",
        "password": "",
        "timeout": 10,
    },
    "maintenance": {
        "default_sequence": "standard",
    },
}


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path(
            os.environ.get("HPMAINT_CONFIG", str(DEFAULT_CONFIG_PATH))
        )
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------ load/save

    def _load(self) -> None:
        self._data = _deep_merge({}, _DEFAULTS)
        if self._path.exists():
            raw = self._path.read_bytes()
            if tomllib is not None:
                self._data = _deep_merge(self._data, tomllib.loads(raw.decode()))
        # Env-var overrides
        if ip := os.environ.get("HPMAINT_PRINTER_IP"):
            self._data["printer"]["ip"] = ip
        if pw := os.environ.get("HPMAINT_PRINTER_PASSWORD"):
            self._data["printer"]["password"] = pw

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import tomli_w  # noqa: F401 — optional dep
            self._path.write_bytes(tomli_w.dumps(self._data).encode())
        except ImportError:
            # Write a minimal hand-rolled TOML
            lines: list[str] = []
            for section, values in self._data.items():
                lines.append(f"\n[{section}]")
                for k, v in values.items():
                    lines.append(f'{k} = {_toml_value(v)}')
            self._path.write_text("\n".join(lines) + "\n")

    # ------------------------------------------------------------------ accessors

    @property
    def printer_ip(self) -> str:
        return self._data["printer"]["ip"]

    @printer_ip.setter
    def printer_ip(self, value: str) -> None:
        self._data["printer"]["ip"] = value

    @property
    def printer_port(self) -> int:
        return int(self._data["printer"]["port"])

    @property
    def username(self) -> str:
        return self._data["printer"]["username"]

    @property
    def password(self) -> str:
        return self._data["printer"]["password"]

    @password.setter
    def password(self, value: str) -> None:
        self._data["printer"]["password"] = value

    @property
    def timeout(self) -> int:
        return int(self._data["printer"]["timeout"])

    @property
    def default_sequence(self) -> str:
        return self._data["maintenance"]["default_sequence"]

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


# ------------------------------------------------------------------ helpers

def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    return str(v)
