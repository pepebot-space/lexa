#!/usr/bin/env bash
# Install the rover control service on lexa.local (Debian 13 / Pi).
# Backend = gpiozero (system pkg) + FastAPI (pip in a system-site-packages venv,
# so the venv can see the apt-installed gpiozero/lgpio). Run from ~/lexa.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Ensuring GPIO system packages..."
sudo apt-get install -y -qq python3-gpiozero python3-lgpio python3-smbus2 2>/dev/null || \
    sudo apt-get install -y -qq python3-gpiozero python3-lgpio || true

echo "==> Creating venv (--system-site-packages so gpiozero/lgpio are visible)..."
python3 -m venv --system-site-packages rover/.venv
rover/.venv/bin/pip install --upgrade pip -q
echo "==> Installing FastAPI + uvicorn..."
rover/.venv/bin/pip install -q -r rover/requirements.txt

if [ ! -f rover/.env ]; then
    cp rover/.env.example rover/.env
    echo "==> Created rover/.env (defaults from docs/WIRING.md)."
fi

echo
echo "==> Verifying imports..."
rover/.venv/bin/python - <<'PY'
import importlib
for m in ("gpiozero", "lgpio", "fastapi", "uvicorn"):
    try:
        importlib.import_module(m)
        print(f"  ok: {m}")
    except Exception as e:
        print(f"  FAIL: {m} -> {e}")
PY

echo
echo "Next:"
echo "  Run:  set -a; . rover/.env; set +a; rover/.venv/bin/uvicorn rover_service:app --app-dir rover --host 0.0.0.0 --port 9000"
echo "  Test: curl localhost:9000/health"
echo "  Or install the systemd unit (see docs/ROVER.md), then calibrate motor direction."
