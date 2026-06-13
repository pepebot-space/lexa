"""Quick local check that the mic and speaker work at the rates the client uses.

  python3 test-devices.py            # mic dev=1, speaker via default
  INPUT_DEVICE_INDEX=1 python3 test-devices.py
"""
import array
import math
import os
import struct

import pyaudio

INPUT_RATE = 16000
OUTPUT_RATE = 24000
MIC_INDEX = int(os.environ.get("INPUT_DEVICE_INDEX", "1"))
SPK_INDEX = os.environ.get("OUTPUT_DEVICE_INDEX")  # None -> default (resampling)
SPK_INDEX = int(SPK_INDEX) if SPK_INDEX not in (None, "") else None

p = pyaudio.PyAudio()

# --- speaker: 1s 440Hz tone ---
tone = b"".join(
    struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / OUTPUT_RATE)))
    for i in range(OUTPUT_RATE)
)
try:
    so = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=OUTPUT_RATE,
        output=True,
        output_device_index=SPK_INDEX,
        frames_per_buffer=4096,
    )
    so.write(tone)
    so.stop_stream()
    so.close()
    print(f"SPEAKER: ok (1s 440Hz tone @{OUTPUT_RATE} dev={SPK_INDEX or 'default'})")
except Exception as e:
    print(f"SPEAKER: FAIL -> {e}")

# --- mic: capture ~1s, report peak RMS ---
try:
    si = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=INPUT_RATE,
        input=True,
        input_device_index=MIC_INDEX,
        frames_per_buffer=2048,
    )
    peak = 0
    total = 0
    for _ in range(8):
        d = si.read(2048, exception_on_overflow=False)
        total += len(d)
        a = array.array("h")
        a.frombytes(d)
        if a:
            r = int(math.sqrt(sum(s * s for s in a) / len(a)))
            peak = max(peak, r)
    si.stop_stream()
    si.close()
    print(f"MIC: ok (captured {total} bytes @{INPUT_RATE} dev={MIC_INDEX}, peak RMS={peak})")
except Exception as e:
    print(f"MIC: FAIL -> {e}")

p.terminate()
