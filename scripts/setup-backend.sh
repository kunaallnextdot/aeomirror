#!/usr/bin/env bash
# First-time backend setup for AEOMirror (macOS / Linux).
#
# - Requires Python 3.11 (the project's confirmed supported version; 3.10+ works
#   because the code uses `X | None` unions). Fails clearly if it is missing.
# - Creates backend/.venv ONLY if it does not already exist (never recreated on
#   a normal run, so `npm run dev` stays fast).
# - Upgrades pip, then installs backend/requirements.txt into the venv.
# - Does not touch application source code and never prints .env contents.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
REQ_FILE="$BACKEND_DIR/requirements.txt"

# 1. Locate a supported Python interpreter (prefer 3.11, then any python3 >= 3.10)
PY=""
if command -v python3.11 >/dev/null 2>&1; then
  PY="$(command -v python3.11)"
elif command -v python3 >/dev/null 2>&1 \
     && python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)'; then
  PY="$(command -v python3)"
fi

if [ -z "$PY" ]; then
  echo "[setup:backend] ERROR: Python 3.11 (or 3.10+) not found on PATH." >&2
  echo "                Install it, e.g.:  brew install python@3.11" >&2
  echo "                Then re-run:       npm run setup:backend" >&2
  exit 1
fi
echo "[setup:backend] Using Python: $PY ($("$PY" --version 2>&1))"

if [ ! -f "$REQ_FILE" ]; then
  echo "[setup:backend] ERROR: $REQ_FILE not found." >&2
  exit 1
fi

# 2. Create the venv only if it is missing
if [ -x "$VENV_DIR/bin/python" ]; then
  echo "[setup:backend] Existing venv found at backend/.venv (leaving it in place)."
else
  echo "[setup:backend] Creating virtual environment at backend/.venv ..."
  "$PY" -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"

# 3. Upgrade pip
echo "[setup:backend] Upgrading pip ..."
"$VENV_PY" -m pip install --quiet --upgrade pip

# 4. Install requirements
echo "[setup:backend] Installing backend requirements ..."
"$VENV_PY" -m pip install --quiet -r "$REQ_FILE"

echo "[setup:backend] Done. Backend venv is ready: backend/.venv"
echo "[setup:backend] Next: npm run dev"
