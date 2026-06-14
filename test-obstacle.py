"""Tune the camera floor/obstacle heuristic on a given floor.

Mirrors client-video.py `_floor_analysis` (glare-aware: a pixel is an obstacle if
it is DARKER or MORE SATURATED than the floor reference; bright reflections are
ignored). Prints per-frame total + per-zone (LEFT/CENTER/RIGHT) non-floor
fractions, their CLEAR/BLOCKED status, and the confidence gate — so you can dial
in the FLOOR_* / ZONE_* env vars, then set the same values in lexa-live.

NOTE: the webcam is single-owner. Stop the Live client first:
  sudo systemctl stop lexa-live
  FLOOR_DARK_TOL=60 python3 test-obstacle.py     # example tuning run
  sudo systemctl start lexa-live
"""
import os
import time

import cv2
import numpy as np

# Same knobs/defaults as client-video.py
FLOOR_ROI_TOP = float(os.environ.get("FLOOR_ROI_TOP", "0.55"))
FLOOR_DARK_TOL = int(os.environ.get("FLOOR_DARK_TOL", "45"))
FLOOR_SAT_TOL = int(os.environ.get("FLOOR_SAT_TOL", "45"))
FLOOR_CONF_GATE = float(os.environ.get("FLOOR_CONF_GATE", "0.80"))
ZONE_CLEAR = float(os.environ.get("ZONE_CLEAR", "0.30"))
ZONE_BLOCK = float(os.environ.get("ZONE_BLOCK", "0.55"))
CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "0"))


def status(fr):
    return "CLEAR" if fr < ZONE_CLEAR else ("BLOCKED" if fr > ZONE_BLOCK else "WARN")


def analyse(frame):
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.int16)
    fy0 = int(h * 0.92)
    ref = np.median(hsv[fy0:h, int(w * 0.30):int(w * 0.70)].reshape(-1, 3), axis=0)
    region = hsv[int(h * FLOOR_ROI_TOP):fy0]
    sat, val = region[:, :, 1], region[:, :, 2]
    mask = ((ref[2] - val) > FLOOR_DARK_TOL) | ((sat - ref[1]) > FLOOR_SAT_TOL)
    total = float(mask.mean()) if mask.size else 0.0
    zw = max(1, mask.shape[1] // 3)
    zones = [float(mask[:, i * zw:(i + 1) * zw].mean()) for i in range(3)]
    return total, zones, total <= FLOOR_CONF_GATE


print(f"params: ROI_TOP={FLOOR_ROI_TOP} DARK_TOL={FLOOR_DARK_TOL} SAT_TOL={FLOOR_SAT_TOL} "
      f"CONF_GATE={FLOOR_CONF_GATE} ZONE_CLEAR={ZONE_CLEAR} ZONE_BLOCK={ZONE_BLOCK}")
cap = cv2.VideoCapture(CAMERA_INDEX)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
for i in range(25):
    ok, frame = cap.read()
    if not ok:
        time.sleep(0.3)
        continue
    total, z, conf = analyse(frame)
    if conf:
        labels = " ".join(f"{n}:{status(f)}({f:.2f})" for n, f in zip(("L", "C", "R"), z))
    else:
        labels = "UNCERTAIN (reflective?) -> overlay suppressed, LLM uses own view"
    print(f"frame {i:2d}: total={total:.2f}  {labels}")
    time.sleep(0.4)
cap.release()
