#!/usr/bin/env sh
# Setup script for HP Printer Maintenance (hpmaint)
# Creates a local .venv, installs dependencies, and makes hpmaint.py executable.
# Usage: ./setup.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Python detection ────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null)
        if [ "$ver" = "True" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.10+ is required but not found." >&2
    exit 1
fi

echo "Using: $($PYTHON --version)"

# ── Virtual environment ─────────────────────────────────────────────────────
VENV=".venv"
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment…"
    "$PYTHON" -m venv "$VENV"
fi

# Activate
if [ -f "$VENV/bin/activate" ]; then
    # shellcheck disable=SC1090
    . "$VENV/bin/activate"
elif [ -f "$VENV/Scripts/activate" ]; then
    # Windows (Git Bash / MSYS2)
    # shellcheck disable=SC1090
    . "$VENV/Scripts/activate"
fi

# ── Dependencies ────────────────────────────────────────────────────────────
echo "Installing dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -e .

# tomli_w is needed only for saving config on Python < 3.11
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" \
    || pip install --quiet tomli tomli_w

# ── Make launcher executable ────────────────────────────────────────────────
chmod +x hpmaint.py
echo ""
echo "  Done. Run the tool with:"
echo ""
echo "    ./hpmaint.py                    # interactive menu"
echo "    ./hpmaint.py run standard       # run standard maintenance sequence"
echo "    ./hpmaint.py run --list         # list all sequences"
echo "    ./hpmaint.py op clean1          # single light clean"
echo "    ./hpmaint.py op clean2 -r 2     # deep clean × 2"
echo "    ./hpmaint.py status             # ink levels + printer status"
echo "    ./hpmaint.py configure          # set printer IP / credentials"
echo ""
echo "  Or, with the venv activated:"
echo "    source .venv/bin/activate"
echo "    hpmaint"
echo ""
echo "  Environment variable overrides:"
echo "    HPMAINT_PRINTER_IP=192.168.1.42 ./hpmaint.py run deep"
echo "    HPMAINT_PRINTER_PASSWORD=mypass ./hpmaint.py status"
echo ""
