"""Logging setup for hpmaint.

All modules use:
    from .log import get_logger
    log = get_logger(__name__)

The root logger writes to a rotating log file (always on) and optionally
to stderr when --debug is passed.

Log file location (in order of preference):
  1. $HPMAINT_LOG_FILE env var
  2. ~/.local/share/hpmaint/hpmaint.log  (Linux / macOS XDG-ish)
  3. %LOCALAPPDATA%\\hpmaint\\hpmaint.log  (Windows)
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ROOT = "hpmaint"
_MAX_BYTES = 2 * 1024 * 1024   # 2 MB per file
_BACKUP_COUNT = 3               # keep hpmaint.log + 3 rotated copies
_FILE_FMT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
_CONSOLE_FMT = "%(levelname)-8s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"

_log_path: Path | None = None


def default_log_path() -> Path:
    if env := os.environ.get("HPMAINT_LOG_FILE"):
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "hpmaint" / "hpmaint.log"


def setup(debug: bool = False, log_file: Path | None = None) -> Path:
    """Configure root logger. Call once from main() before any other work."""
    global _log_path

    path = log_file or default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_path = path

    root = logging.getLogger(_ROOT)
    root.setLevel(logging.DEBUG)          # capture everything; handlers filter

    # ── rotating file handler (always on, DEBUG level) ──────────────────────
    fh = RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_FILE_FMT, datefmt=_DATEFMT))
    root.addHandler(fh)

    # ── stderr handler (only when --debug) ──────────────────────────────────
    if debug:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(logging.Formatter(_CONSOLE_FMT))
        root.addHandler(sh)

    return path


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the hpmaint namespace."""
    # Strip leading package prefix so callers can just pass __name__
    short = name.replace("hpmaint.", "", 1)
    return logging.getLogger(f"{_ROOT}.{short}")


def log_path() -> Path | None:
    """Return the active log file path (None if setup() not yet called)."""
    return _log_path
