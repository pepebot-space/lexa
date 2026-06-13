"""Analyze a dumped bot-audio WAV to locate the 'unclear voice' cause."""
import sys
import wave

import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/bot.wav"
w = wave.open(path, "rb")
n, rate, ch, sw = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
raw = w.readframes(n)
w.close()

a = np.frombuffer(raw, dtype="<i2").astype(np.float32)
print(f"file={path} rate={rate}Hz ch={ch} width={sw}B frames={n} dur={n/rate:.2f}s bytes={len(raw)}")
if a.size == 0:
    print(">>> KOSONG: tidak ada audio bot terekam (bot tidak merespon / mic tak menangkap?)")
    sys.exit()

dur = n / rate
peak = float(np.max(np.abs(a)))
rms = float(np.sqrt(np.mean(a**2)))
clip = int(np.sum(np.abs(a) >= 32700))
zeros = int(np.sum(a == 0))
zc = float(np.sum(np.abs(np.diff(np.sign(a)))) / 2 / dur)
print(f"peak={peak:.0f}/32767 ({peak/32767*100:.1f}%)  rms={rms:.0f}")
print(f"clipping_samples={clip} ({clip/a.size*100:.3f}%)   exact_zeros={zeros} ({zeros/a.size*100:.1f}%)")
print(f"zero_crossings/sec={zc:.0f}  (speech~1k-4k; >8k = noise/garbled/byteswap)")

spec = np.abs(np.fft.rfft(a))
freqs = np.fft.rfftfreq(len(a), 1.0 / rate)
tot = float(np.sum(spec**2)) + 1e-9


def band(lo, hi):
    m = (freqs >= lo) & (freqs < hi)
    return float(np.sum(spec[m] ** 2)) / tot * 100


print(
    f"energy: <300Hz={band(0,300):.0f}%  300-3400Hz(speech)={band(300,3400):.0f}%  "
    f">3400Hz={band(3400,rate/2):.0f}%   dominant={freqs[int(np.argmax(spec))]:.0f}Hz"
)

# Byte-swap sanity: if data were byte-swapped, swapping should LOWER zero-crossings
sw_a = a.astype(np.int16).byteswap().astype(np.float32)
sw_zc = float(np.sum(np.abs(np.diff(np.sign(sw_a)))) / 2 / dur)
print(f"if byteswapped -> zero_crossings/sec={sw_zc:.0f} (jika ini JAUH lebih rendah, datanya ketukar endian)")
