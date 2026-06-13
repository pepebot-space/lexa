"""Validate that stopping the PyAudio stream powers down the MAX98357A
(stops the hiss). Listen during the 'SILENCE' window."""
import math
import struct
import time

import pyaudio

RATE = 24000
p = pyaudio.PyAudio()
s = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, output=True, frames_per_buffer=4096)
tone = b"".join(
    struct.pack("<h", int(10000 * math.sin(2 * math.pi * 440 * i / RATE)))
    for i in range(RATE * 2)
)

print(">>> TONE 2 detik (stream ON)...")
s.write(tone)
print(">>> SILENCE 5 detik (stream di-STOP -> amp harusnya mati, desis hilang)...")
s.stop_stream()
time.sleep(5)
print(">>> TONE lagi 1 detik (stream di-START kembali)...")
s.start_stream()
s.write(tone[: RATE * 2])
s.stop_stream()
s.close()
p.terminate()
print("done")
