#!/usr/bin/env bash
# Install the Pepebot Live API client dependencies on lexa.local (Debian 13 / RPi).
# Uses apt packages (Python 3.13 + PEP-668 make system pip awkward; apt provides
# prebuilt pyaudio/opencv for aarch64 with no compilation).
set -euo pipefail

echo "==> Installing system packages (pyaudio, opencv, websockets, alsa utils)..."
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3-pyaudio \
    python3-opencv \
    python3-websockets \
    libportaudio2 \
    alsa-utils

echo
echo "==> Done. Verifying imports..."
python3 - <<'PY'
import importlib
for m in ("pyaudio", "cv2", "websockets"):
    try:
        importlib.import_module(m)
        print(f"  ok: {m}")
    except Exception as e:
        print(f"  FAIL: {m} -> {e}")
PY

echo
echo "==> Audio devices:"
python3 client-video.py --list-devices || true

echo
echo "Next: pick the mic (input) and speaker (output) indices above, then run:"
echo "  INPUT_DEVICE_INDEX=<mic> OUTPUT_DEVICE_INDEX=<spk> python3 client-video.py"
