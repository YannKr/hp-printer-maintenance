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
        if self._path.exists() and tomllib is not None:
            raw = self._path.read_bytes()
            try:
                parsed = tomllib.loads(raw.decode())
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                # Without this, a malformed config makes every later run die
                # with a bare TOMLDecodeError until the file is deleted by hand.
                raise SystemExit(
                    f"Config file is not valid TOML: {self._path}\n"
                    f"  {exc}\n"
                    f"Edit it, or delete it and run `hpmaint configure` again:\n"
                    f"  rm {self._path}"
                ) from exc
            self._data = _deep_merge(self._data, parsed)
        # Env-var overrides
        if ip := os.environ.get("HPMAINT_PRINTER_IP"):
            self._data["printer"]["ip"] = ip
        if pw := os.environ.get("HPMAINT_PRINTER_PASSWORD"):
            self._data["printer"]["password"] = pw

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # The file holds the EWS password. Create it 0600 before any content
        # is written, and re-apply the mode in case the file already existed.
        self._path.touch(mode=0o600, exist_ok=True)
        os.chmod(self._path, 0o600)
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


_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_str(s: str) -> str:
    """Quote a string as a TOML basic string.

    A password containing " or \\ used to be written raw, producing a file
    that no TOML parser could read back.
    """
    out: list[str] = []
    for ch in s:
        if ch in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return _toml_str(v)
    return str(v)
