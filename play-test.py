"""Standalone playback tester — play a WAV through PyAudio exactly like the
client does, so we can tune the playback path in isolation (no live load).

Usage:
  python3 play-test.py [file] [--fpb N] [--device IDX] [--chunk N]

  --fpb     frames_per_buffer for the PyAudio stream (bigger = fewer underruns)
  --device  output device index (default = system default = MAX98357A)
  --chunk   how many frames to read+write per loop
"""
import argparse
import wave

import pyaudio

ap = argparse.ArgumentParser()
ap.add_argument("file", nargs="?", default="/tmp/bot.wav")
ap.add_argument("--fpb", type=int, default=4096, help="frames_per_buffer")
ap.add_argument("--device", type=int, default=None, help="output device index")
ap.add_argument("--chunk", type=int, default=2048, help="write chunk frames")
args = ap.parse_args()

w = wave.open(args.file, "rb")
rate, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
print(
    f"play {args.file}: {rate}Hz ch={ch} width={sw}B dur={n/rate:.1f}s | "
    f"device={args.device} fpb={args.fpb} chunk={args.chunk}"
)

p = pyaudio.PyAudio()
s = p.open(
    format=p.get_format_from_width(sw),
    channels=ch,
    rate=rate,
    output=True,
    output_device_index=args.device,
    frames_per_buffer=args.fpb,
)

data = w.readframes(args.chunk)
while data:
    s.write(data)
    data = w.readframes(args.chunk)

s.stop_stream()
s.close()
p.terminate()
w.close()
print("done")
