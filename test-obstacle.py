"""Sanity/tune the camera obstacle heuristic: prints the forward-ROI non-floor
fraction per frame. Clear path -> low; object close ahead -> high (>= 0.6 trips)."""
import time

import cv2
import numpy as np

TOL = 80
FRAC_TRIP = 0.6
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
for i in range(25):
    ok, frame = cap.read()
    if not ok:
        time.sleep(0.3)
        continue
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    fy0 = int(h * 0.92)
    ref = np.median(hsv[fy0:h, int(w * 0.35):int(w * 0.65)].reshape(-1, 3), axis=0)
    roi = hsv[int(h * 0.55):fy0, int(w * 0.30):int(w * 0.70)].astype(np.int16)
    frac = float((np.abs(roi - ref).sum(axis=2) > TOL).mean())
    print(f"frame {i:2d}: non-floor frac={frac:.2f} {'<<< OBSTACLE' if frac >= FRAC_TRIP else ''}")
    time.sleep(0.4)
cap.release()
