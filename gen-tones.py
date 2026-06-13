"""Generate test WAVs for diagnosing MAX98357A noise."""
import math
import struct
import wave


def gen(path, rate, ch, freq, secs=3, amp=10000):
    w = wave.open(path, "wb")
    w.setnchannels(ch)
    w.setsampwidth(2)
    w.setframerate(rate)
    frames = bytearray()
    for i in range(int(rate * secs)):
        s = int(amp * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", s) * ch
    w.writeframes(bytes(frames))
    w.close()
    print(f"wrote {path} ({rate}Hz {ch}ch {freq}Hz)")


# Test 1: native 48k stereo -> played raw to hw (no resampling) = purest path
gen("/tmp/t1_raw_440.wav", 48000, 2, 440)
# Test 2: 24k mono = exactly the Pepebot output format (plug resamples + upmix)
gen("/tmp/t2_client_880.wav", 24000, 1, 880)
# Loud + long tone for the 3.5mm jack (44.1k stereo native, ~85% amplitude, 5s)
gen("/tmp/loud_jack.wav", 44100, 2, 440, secs=5, amp=28000)
