# lexa — Pepebot Live API client

Runs on **lexa.local** (Raspberry Pi, Debian 13, Python 3.13) with a USB webcam
(+ built-in mic) and a speaker. Streams mic audio and webcam video to the
Pepebot Live API gateway and plays the model's audio response back.

```
  lexa.local (mic + webcam + speaker)  ───ws──►  gateway 192.168.100.242:18790
```

## Install (on lexa.local)

```bash
bash setup-lexa.sh
```

Installs `python3-pyaudio`, `python3-opencv`, `python3-websockets` via apt
(prebuilt for aarch64), then lists the audio devices.

## Run

ALSA defaults are configured so that **input = USB webcam mic** and
**output = MAX98357A amp** (see "Audio hardware" below), so no env vars are
needed:

```bash
python3 client-video.py
```

To inspect/override devices:

```bash
python3 client-video.py --list-devices
INPUT_DEVICE_INDEX=2 python3 client-video.py   # mic explicit (index can shift)
```

## Audio hardware (MAX98357A I2S amp)

> Full hardware wiring (Viam Rover 1 + MAX98357A, incl. the GPIO19/encoder conflict
> and its fix) is documented in [docs/WIRING.md](docs/WIRING.md).

A MAX98357A I2S DAC/amplifier is wired to the Pi 3 (BCLK=GPIO18, LRCLK=GPIO19,
DIN=GPIO21). Configured via:

- `/boot/firmware/config.txt`: `dtoverlay=hifiberry-dac` (backup at
  `config.txt.bak-pepebot`). The MAX98357A speaks plain I2S, so the
  `hifiberry-dac` overlay drives it — no SD-pin GPIO setup required.
- `/etc/asound.conf`: default PCM → `plug` → `hw:sndrpihifiberry`. `plug`
  auto-resamples the 24 kHz mono stream up to the DAC's 48 kHz stereo.

Card layout after setup: `0` bcm2835 Headphones (onboard, still available),
`1` snd_rpi_hifiberry_dac (the MAX98357A), `2` USB webcam mic, `3` HDMI.

Quick check: `speaker-test -D default -t sine -f 440 -c 2 -l 1`

**Wiring is critical — LRCLK MUST be on GPIO19.** The Pi's PCM peripheral
hardwires LRCLK (frame-sync) to GPIO19, BCLK to GPIO18, data to GPIO21; these
cannot be moved (GPIO9 is SPI MISO, not an I2S pin). Symptom of a mis-wired
LRCLK: **test tones sound roughly OK but speech is garbled/unintelligible even
though the received audio data is clean** (a jittery frame-sync survives a
single tone but destroys complex speech). Fix = put LRCLK back on GPIO19.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `LIVE_API_URL` | `ws://192.168.100.242:18790/v1/live` | Gateway WebSocket URL |
| `INPUT_DEVICE_INDEX` | system default | PyAudio mic device index |
| `OUTPUT_DEVICE_INDEX` | system default | PyAudio speaker device index |
| `CAMERA_INDEX` | `0` | OpenCV camera index (`/dev/video0`) |
| `ENABLE_CAMERA` | `1` | Set `0` to disable video |
| `LIVE_PROVIDER` | `vertex` | Live provider |
| `LIVE_MODEL` | `gemini-live-2.5-flash-native-audio` | Live model |
| `LIVE_AGENT` | `default` | Agent name |
| `OUTPUT_GAIN` | `1.0` | Scale bot audio level before playback (lower if it clips/distorts on the amp) |
| `AMP_IDLE_MUTE` | `1` | Stop the I2S stream when the bot is silent so the MAX98357A powers down (kills idle hiss between responses); set `0` to disable |
| `DUMP_AUDIO` | _(off)_ | Path to write raw received bot audio as a WAV for debugging |

You can also override the URL with `--url ws://host:port/v1/live`.

## Debug / tuning helpers

- `analyze-audio.py <wav>` — analyze a `DUMP_AUDIO` recording (rate, clipping, speech-band energy, byte-order) to tell whether garbled audio is a data problem vs a playback/wiring problem.
- `play-test.py <wav> [--fpb N] [--device IDX]` — replay a WAV through PyAudio (the client's playback path) in isolation.
- `resample-play.py <wav>` — resample to 48 kHz stereo and play straight to `hw:1,0` (bypasses ALSA `plug`) to rule the resampler in/out.
- `test-devices.py`, `gen-tones.py`, `test-idlemute.py` — device/tone/idle-mute checks.

## Notes

- Audio framing: mic 16 kHz mono PCM in, speaker 24 kHz mono PCM out, video JPEG ~2 fps.
- `audioop` was removed from Python 3.13; the noise gate uses a pure-Python RMS
  fallback, so no extra package is needed.
- Video only activates if the gateway reports `video.enabled=true` in its
  `connected` message (needs `live.video=true` + a vertex/gemini provider).
