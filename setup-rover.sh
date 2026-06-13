#!/usr/bin/env bash
# Install the rover control service on lexa.local (Debian 13 / Pi).
# Uses a venv (PEP-668 blocks system pip on Debian 13). Run from ~/lexa.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Creating venv at rover/.venv ..."
python3 -m venv rover/.venv
rover/.venv/bin/pip install --upgrade pip -q
echo "==> Installing deps (viam-sdk, fastapi, uvicorn)..."
rover/.venv/bin/pip install -q -r rover/requirements.txt

if [ ! -f rover/.env ]; then
    cp rover/.env.example rover/.env
    echo "==> Created rover/.env — EDIT IT: set VIAM_API_KEY / VIAM_API_KEY_ID / VIAM_ADDRESS"
fi

echo
echo "==> Verifying imports..."
rover/.venv/bin/python - <<'PY'
import importlib
for m in ("viam", "fastapi", "uvicorn"):
    importlib.import_module(m)
    print(f"  ok: {m}")
PY

echo
echo "Next:"
echo "  1) Edit rover/.env with your Viam connect credentials."
echo "  2) Run:  rover/.venv/bin/uvicorn rover_service:app --app-dir rover --host 0.0.0.0 --port 9000"
echo "     (or install the systemd unit: see docs/ROVER.md)"
echo "  3) Test: curl localhost:9000/health"
