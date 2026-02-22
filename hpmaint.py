#!/usr/bin/env python3
"""HP Printer Maintenance — launcher.

Works whether installed via the `hpmaint` entry point or run directly as
./hpmaint.py from the project directory.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"

# If a venv exists and we're not already inside it, re-exec with venv python.
if VENV.exists() and sys.prefix != str(VENV):
    venv_python = VENV / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    if venv_python.exists():
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)

# Remove the current directory and "" from sys.path to prevent this file
# from shadowing the `hpmaint` package when importing.
for _p in (str(HERE), ""):
    if _p in sys.path:
        sys.path.remove(_p)

# Ensure src/ is on the path when running without install.
src = HERE / "src"
if src.exists() and str(src) not in sys.path:
    sys.path.insert(0, str(src))

from hpmaint.main import main  # noqa: E402

if __name__ == "__main__":
    main(standalone_mode=True)
