"""Resample /tmp/bot.wav to 48k stereo (numpy) and play it STRAIGHT to the DAC
hardware (hw:1,0), bypassing the ALSA 'plug' resampler entirely.

If this is clear -> the ALSA plug resampler was the culprit.
If still garbled -> it's the amp/speaker reproducing speech, not software.
"""
import subprocess
import sys
import wave

import numpy as np

src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/bot.wav"
dst = "/tmp/bot48.wav"
TARGET = 48000

w = wave.open(src, "rb")
rate, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
raw = w.readframes(n)
w.close()

x = np.frombuffer(raw, dtype="<i2").astype(np.float32)
if ch == 2:
    x = x.reshape(-1, 2).mean(axis=1)  # downmix to mono first

# linear-interp resample rate -> TARGET
out_len = int(len(x) * TARGET / rate)
xi = np.linspace(0, len(x) - 1, out_len)
y = np.interp(xi, np.arange(len(x)), x)

# mono -> stereo, clip to int16
st = np.repeat(y[:, None], 2, axis=1)
np.clip(st, -32768, 32767, out=st)
st = st.astype("<i2")

ww = wave.open(dst, "wb")
ww.setnchannels(2)
ww.setsampwidth(2)
ww.setframerate(TARGET)
ww.writeframes(st.tobytes())
ww.close()
print(f"resampled {rate}Hz/{ch}ch -> {TARGET}Hz/2ch, wrote {dst} ({out_len/TARGET:.1f}s)")

print(">>> playing DIRECT to hw:1,0 (no ALSA plug, no resampler)...")
subprocess.run(["aplay", "-D", "hw:1,0", dst])
print("done")
