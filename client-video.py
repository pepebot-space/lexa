import argparse
import asyncio
import base64
import importlib
import json
import math
import os
import signal
import sys
from array import array
from typing import Optional

import pyaudio  # apt: python3-pyaudio  |  pip: pyaudio
import websockets  # apt: python3-websockets  |  pip: websockets

try:
    cv2 = importlib.import_module("cv2")  # apt: python3-opencv | pip: opencv-python
except Exception:
    cv2 = None

# audioop was removed from the stdlib in Python 3.13. Use it if present
# (faster, C-implemented), otherwise fall back to a pure-Python RMS.
try:
    import audioop  # type: ignore

    def _rms(pcm_bytes: bytes, width: int) -> int:
        return audioop.rms(pcm_bytes, width)

except Exception:

    def _rms(pcm_bytes: bytes, width: int) -> int:
        if not pcm_bytes or width != 2:
            return 0
        samples = array("h")
        usable = (len(pcm_bytes) // 2) * 2
        samples.frombytes(pcm_bytes[:usable])
        if sys.byteorder == "big":
            samples.byteswap()  # PCM on the wire is little-endian
        if not samples:
            return 0
        acc = 0
        for s in samples:
            acc += s * s
        return int(math.sqrt(acc / len(samples)))


# Software output attenuation. The MAX98357A (hifiberry-dac) has no hardware
# volume, and the bot's near-full-scale voice clips at the amp's 9dB gain.
# Scaling the PCM down (OUTPUT_GAIN < 1.0) prevents that clipping/distortion.
try:
    import numpy as _np  # bundled with opencv (python3-opencv -> python3-numpy)

    def apply_gain(pcm: bytes, gain: float) -> bytes:
        if gain == 1.0 or not pcm:
            return pcm
        a = _np.frombuffer(pcm, dtype="<i2").astype(_np.float32) * gain
        _np.clip(a, -32768, 32767, out=a)
        return a.astype("<i2").tobytes()

except Exception:

    def apply_gain(pcm: bytes, gain: float) -> bytes:
        if gain == 1.0 or not pcm:
            return pcm
        a = array("h")
        a.frombytes(pcm[: (len(pcm) // 2) * 2])
        if sys.byteorder == "big":
            a.byteswap()
        for i in range(len(a)):
            v = int(a[i] * gain)
            a[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
        if sys.byteorder == "big":
            a.byteswap()
        return a.tobytes()


# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables / CLI flags)
# ---------------------------------------------------------------------------

# Audio configuration (same framing as the reference client.py)
INPUT_RATE = 16000
OUTPUT_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2
FORMAT = pyaudio.paInt16

INPUT_CHUNK = 2048
OUTPUT_CHUNK = 4096
OUTPUT_PREBUFFER_CHUNKS = 3

# Pepebot Live API gateway. lexa.local connects out to the gateway host.
DEFAULT_GATEWAY = "ws://192.168.100.242:18790/v1/live"
URL = os.environ.get("LIVE_API_URL", DEFAULT_GATEWAY)

# Live setup parameters (sent to the gateway on connect)
PROVIDER = os.environ.get("LIVE_PROVIDER", "vertex")
MODEL = os.environ.get("LIVE_MODEL", "gemini-live-2.5-flash-native-audio")
AGENT = os.environ.get("LIVE_AGENT", "default")

# Live system prompt (pepebot >=0.5.14 honors setup.system_prompt as the upstream
# systemInstruction, highest precedence). Override via LIVE_SYSTEM_PROMPT (inline)
# or LIVE_SYSTEM_PROMPT_FILE (path); otherwise this default rover persona is sent.
DEFAULT_SYSTEM_PROMPT = """You are LEXA, a small autonomous differential-drive rover with ONE fixed forward-facing camera. You SEE the live camera feed and move by calling tools:
- rover_drive(linear, angular, seconds): linear forward(+)/back(-) -1..1; angular left(+)/right(-) -1..1; bounded burst, auto-stops. Returns blocked:true if it hit something.
- rover_turn(angle_deg): spin in place (+left / -right). Use ~30-45 to scan, ~90 to change direction.
- rover_move(distance_m): drive straight (+forward / -back).
- rover_stop(); rover_get_state() (blocked?, encoders); rover_get_imu() (tilt).

GOLDEN RULES:
1) LOOK AROUND FIRST. You have only ONE camera, so to find something or understand a space you MUST rotate to see. When searching or exploring: spin in place in ~30-45 degree steps and check the camera after each step — do a full sweep (up to a 360 turn) to locate the target or a clear path BEFORE driving. If you still don't see it, drive to a new spot and scan again. Never drive forward blindly.
2) MOVE DECISIVELY, not in tiny twitches. Take real steps: rover_move ~0.5-1.0 m, or rover_drive ~0.4 power for ~1.5-2 s, then re-check the camera. (Commands are still bounded for safety.)
3) TALK LESS. Do NOT narrate every action. Work mostly in silence. Speak only briefly when you: found/reached the target, are blocked with no clear path, finished the goal, or the user asks. Otherwise just act quietly.

BEHAVIOR:
- Single command ("maju", "belok kiri", "berhenti"): do it once, quietly.
- A GOAL ("cari & dekati botol", "jelajahi ruangan", "ikuti aku", "ke pintu"): pursue it autonomously — SCAN by rotating to locate the target/path, then approach; loop perceive->act on your own WITHOUT waiting for the user, until the goal is reached, you are blocked with no clear path, or told to stop.

SAFETY:
- If a tool returns blocked:true or an obstacle is close ahead: stop, back up a little, rover_turn to a clear direction, then continue. Never push into things.
- On "berhenti"/"stop": call rover_stop immediately.

Reply in Bahasa Indonesia, but keep talking to a minimum."""

SYSTEM_PROMPT = os.environ.get("LIVE_SYSTEM_PROMPT", "")
_spf = os.environ.get("LIVE_SYSTEM_PROMPT_FILE")
if not SYSTEM_PROMPT and _spf and os.path.exists(_spf):
    with open(_spf, encoding="utf-8") as _f:
        SYSTEM_PROMPT = _f.read().strip()
if not SYSTEM_PROMPT:
    SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

# PyAudio device selection. None -> system default. On the Pi the USB webcam
# mic and the bcm2835 speaker are different cards, so these usually need to be
# set explicitly (use --list-devices to find the indices).
def _env_int(name: str) -> Optional[int]:
    val = os.environ.get(name)
    if val is None or val == "":
        return None
    try:
        return int(val)
    except ValueError:
        return None


INPUT_DEVICE_INDEX = _env_int("INPUT_DEVICE_INDEX")
OUTPUT_DEVICE_INDEX = _env_int("OUTPUT_DEVICE_INDEX")

ENABLE_NOISE_GATE = True
NOISE_FLOOR_ALPHA = 0.95
NOISE_GATE_MULTIPLIER = 2.0
NOISE_GATE_MIN_RMS = 180
NOISE_GATE_HANGOVER = 3

ENABLE_BARGE_IN = False
BOT_SPEAKING_HOLD_SEC = 0.8

# When the bot is silent, stop the I2S output stream so the MAX98357A amp
# powers down (its clock stops) — this kills the constant Class-D hiss between
# responses. Set AMP_IDLE_MUTE=0 to keep the stream always running.
AMP_IDLE_MUTE = os.environ.get("AMP_IDLE_MUTE", "1") not in ("0", "false", "False")
AMP_IDLE_MUTE_SEC = 0.4  # silence gap before powering the amp down

# Scale bot audio level before playback (1.0 = unchanged). Lower this if the
# voice distorts/clips on the MAX98357A. Try 0.4-0.6 to start.
OUTPUT_GAIN = float(os.environ.get("OUTPUT_GAIN", "1.0"))

# Debug: if set to a path, write the raw bot audio (as received, 24kHz mono)
# to a WAV file for offline analysis. Does not affect playback.
DUMP_AUDIO = os.environ.get("DUMP_AUDIO")

# Video settings
ENABLE_CAMERA = os.environ.get("ENABLE_CAMERA", "1") not in ("0", "false", "False")
CAMERA_INDEX = _env_int("CAMERA_INDEX") or 0
VIDEO_MIME = "image/jpeg"
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 360
VIDEO_JPEG_QUALITY = 70
VIDEO_INTERVAL_SEC = 0.5  # ~2 FPS

# Camera obstacle-stop (safety assist; runs here in the client, which owns the
# webcam). Floor-appearance heuristic: the bottom-center strip is taken as the
# floor reference when the path is clear; if the forward ROI is mostly "not
# floor" (an object intruding) for several consecutive frames, POST /stop to the
# rover. Coarse (mono camera, no depth) — the LLM's own vision stays primary.
# Default OFF: the mono-camera floor heuristic false-triggers until tuned on the
# actual floor (run test-obstacle.py on the floor, set OBSTACLE_TOL/FRACTION, then
# enable with OBSTACLE_STOP=1). IMU + encoder-stall safety remain active regardless.
OBSTACLE_STOP = os.environ.get("OBSTACLE_STOP", "0") not in ("0", "false", "False")
ROVER_API = os.environ.get("ROVER_API", "http://localhost:9000")
OBSTACLE_FRACTION = float(os.environ.get("OBSTACLE_FRACTION", "0.6"))  # non-floor frac
OBSTACLE_FRAMES = int(os.environ.get("OBSTACLE_FRAMES", "3"))          # consecutive frames
OBSTACLE_TOL = int(os.environ.get("OBSTACLE_TOL", "80"))               # HSV summed dist
OBSTACLE_COOLDOWN_SEC = float(os.environ.get("OBSTACLE_COOLDOWN_SEC", "2.5"))


def detect_obstacle(frame, state: dict) -> bool:
    """Return True when a near obstacle fills the forward view for OBSTACLE_FRAMES
    consecutive frames (floor-appearance heuristic). `state` holds the counter."""
    if cv2 is None:
        return False
    try:
        import numpy as np

        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        fy0 = int(h * 0.92)
        floor = hsv[fy0:h, int(w * 0.35):int(w * 0.65)].reshape(-1, 3)
        if floor.size == 0:
            return False
        ref = np.median(floor, axis=0)
        roi = hsv[int(h * 0.55):fy0, int(w * 0.30):int(w * 0.70)].astype(np.int16)
        if roi.size == 0:
            return False
        nonfloor = np.abs(roi - ref).sum(axis=2) > OBSTACLE_TOL
        frac = float(nonfloor.mean())
        state["count"] = state.get("count", 0) + 1 if frac >= OBSTACLE_FRACTION else 0
        return state["count"] >= OBSTACLE_FRAMES
    except Exception:
        return False


def _post_rover(path: str):
    import urllib.request

    try:
        req = urllib.request.Request(
            ROVER_API + path, data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


class NoiseGate:
    def __init__(self):
        self.noise_floor = float(NOISE_GATE_MIN_RMS)
        self.hangover_left = 0

    def process(self, pcm_bytes: bytes) -> bytes:
        if not pcm_bytes:
            return pcm_bytes

        rms = _rms(pcm_bytes, SAMPLE_WIDTH)

        if rms < self.noise_floor * 1.5:
            self.noise_floor = (
                NOISE_FLOOR_ALPHA * self.noise_floor + (1 - NOISE_FLOOR_ALPHA) * rms
            )

        threshold = max(NOISE_GATE_MIN_RMS, self.noise_floor * NOISE_GATE_MULTIPLIER)
        is_speech = rms >= threshold

        if is_speech:
            self.hangover_left = NOISE_GATE_HANGOVER
            return pcm_bytes

        if self.hangover_left > 0:
            self.hangover_left -= 1
            return pcm_bytes

        return b"\x00" * len(pcm_bytes)


def try_parse_json(data):
    try:
        if isinstance(data, bytes):
            return json.loads(data.decode("utf-8", errors="ignore"))
        return json.loads(data)
    except Exception:
        return None


def extract_inline_audio(parsed: dict) -> Optional[bytes]:
    server_content = parsed.get("serverContent")
    if not isinstance(server_content, dict):
        return None

    model_turn = server_content.get("modelTurn")
    if not isinstance(model_turn, dict):
        return None

    parts = model_turn.get("parts")
    if not isinstance(parts, list):
        return None

    chunks = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        inline_data = part.get("inlineData")
        if not isinstance(inline_data, dict):
            continue
        b64_audio = inline_data.get("data")
        if not isinstance(b64_audio, str) or not b64_audio:
            continue

        normalized = b64_audio.replace("-", "+").replace("_", "/")
        while len(normalized) % 4 != 0:
            normalized += "="
        try:
            chunks.append(base64.b64decode(normalized))
        except Exception:
            continue

    if not chunks:
        return None
    return b"".join(chunks)


def list_devices():
    p = pyaudio.PyAudio()
    try:
        print("PyAudio devices:")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            print(
                f"  [{i}] {info['name']!r} "
                f"in={info['maxInputChannels']} out={info['maxOutputChannels']} "
                f"defaultRate={int(info['defaultSampleRate'])}"
            )
        try:
            di = p.get_default_input_device_info()
            do = p.get_default_output_device_info()
            print(f"default input  -> [{di['index']}] {di['name']!r}")
            print(f"default output -> [{do['index']}] {do['name']!r}")
        except Exception as e:
            print(f"(could not query defaults: {e})")
    finally:
        p.terminate()


async def main():
    print(f"Connecting to Pepebot Live API at {URL} ...")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_stop(*_):
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_stop)
        except NotImplementedError:
            pass
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())

    p = pyaudio.PyAudio()
    output_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
    bot_speaking_until = 0.0
    video_enabled = False

    dump_wav = None
    if DUMP_AUDIO:
        import wave

        dump_wav = wave.open(DUMP_AUDIO, "wb")
        dump_wav.setnchannels(CHANNELS)
        dump_wav.setsampwidth(SAMPLE_WIDTH)
        dump_wav.setframerate(OUTPUT_RATE)
        print(f"Recording raw bot audio -> {DUMP_AUDIO}")

    stream_out = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=OUTPUT_RATE,
        output=True,
        output_device_index=OUTPUT_DEVICE_INDEX,
        frames_per_buffer=OUTPUT_CHUNK,
    )

    stream_in = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=INPUT_RATE,
        input=True,
        input_device_index=INPUT_DEVICE_INDEX,
        frames_per_buffer=INPUT_CHUNK,
    )

    noise_gate = NoiseGate()

    async def enqueue_audio(pcm: bytes):
        nonlocal bot_speaking_until
        if not pcm:
            return
        if len(pcm) % 2 != 0:
            pcm = pcm[:-1]
        if not pcm:
            return

        if dump_wav is not None:
            try:
                dump_wav.writeframes(pcm)
            except Exception:
                pass

        pcm = apply_gain(pcm, OUTPUT_GAIN)

        try:
            await asyncio.wait_for(output_queue.put(pcm), timeout=0.5)
            bot_speaking_until = max(
                bot_speaking_until, loop.time() + BOT_SPEAKING_HOLD_SEC
            )
        except asyncio.TimeoutError:
            pass

    async def playback_worker():
        bytes_per_out_chunk = OUTPUT_CHUNK * SAMPLE_WIDTH
        prebuffer_target = OUTPUT_PREBUFFER_CHUNKS * bytes_per_out_chunk
        idle_mute_loops = max(1, int(AMP_IDLE_MUTE_SEC / 0.02))
        pending = bytearray()
        started = False        # first-burst prebuffer completed
        stream_running = True  # I2S clock on / amp powered
        idle_loops = 0

        while not stop_event.is_set():
            try:
                pcm = await asyncio.wait_for(output_queue.get(), timeout=0.02)
                pending.extend(pcm)
            except asyncio.TimeoutError:
                pass

            if not pending:
                # Nothing queued. After a short gap, stop the stream so the amp
                # powers down and the idle hiss goes away between responses.
                if AMP_IDLE_MUTE and stream_running:
                    idle_loops += 1
                    if idle_loops >= idle_mute_loops:
                        try:
                            await asyncio.to_thread(stream_out.stop_stream)
                        except Exception:
                            pass
                        stream_running = False
                continue

            idle_loops = 0

            if not started:
                if len(pending) < prebuffer_target:
                    continue
                started = True

            # Audio to play: make sure the stream (and amp) is running again.
            if AMP_IDLE_MUTE and not stream_running:
                try:
                    await asyncio.to_thread(stream_out.start_stream)
                except Exception:
                    pass
                stream_running = True

            if len(pending) >= bytes_per_out_chunk:
                frame = bytes(pending[:bytes_per_out_chunk])
                del pending[:bytes_per_out_chunk]
            else:
                frame = bytes(pending) + (
                    b"\x00" * (bytes_per_out_chunk - len(pending))
                )
                pending.clear()

            try:
                await asyncio.to_thread(stream_out.write, frame)
            except Exception as e:
                if not stop_event.is_set():
                    print(f"Playback error: {e}")
                return

    try:
        async with websockets.connect(
            URL,
            max_size=20 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as ws:
            print("Connected! Sending setup...")

            await ws.send(
                json.dumps(
                    {
                        "setup": {
                            "provider": PROVIDER,
                            "model": MODEL,
                            "agent": AGENT,
                            "enable_tools": True,
                            "system_prompt": SYSTEM_PROMPT,
                        }
                    }
                )
            )

            setup_ok = False
            while not setup_ok and not stop_event.is_set():
                msg = await asyncio.wait_for(ws.recv(), timeout=15)
                parsed = try_parse_json(msg)
                if parsed is None:
                    continue

                if parsed.get("error"):
                    print(f"Error: {parsed['error']}")
                    return

                if parsed.get("status") == "connected":
                    video_meta = parsed.get("video", {})
                    video_enabled = bool(video_meta.get("enabled"))
                    print(
                        f"Proxy connected: {parsed.get('provider')} -> {parsed.get('model')}"
                    )
                    print(
                        f"Video requested={video_meta.get('requested')} "
                        f"supported={video_meta.get('supported')} "
                        f"enabled={video_meta.get('enabled')}"
                    )
                    continue

                if "setupComplete" in parsed:
                    setup_ok = True
                    print("Live session ready")

            if not setup_ok:
                return

            print(
                f"Mic live (input={INPUT_RATE}Hz), speaker live (output={OUTPUT_RATE}Hz)"
            )
            if ENABLE_CAMERA:
                if cv2 is None:
                    print("opencv (cv2) not found; camera sender disabled")
                elif not video_enabled:
                    print(
                        "Server did not enable video. Set live.video=true + provider vertex/gemini."
                    )
                else:
                    print(f"Camera sender active (JPEG frames, index={CAMERA_INDEX})")
            print("Speak now... Press Ctrl+C to stop.")

            async def sender_audio():
                while not stop_event.is_set():
                    try:
                        if (not ENABLE_BARGE_IN) and (loop.time() < bot_speaking_until):
                            await asyncio.sleep(0.02)
                            continue

                        data = await asyncio.to_thread(
                            stream_in.read,
                            INPUT_CHUNK,
                            exception_on_overflow=False,
                        )
                        if ENABLE_NOISE_GATE:
                            data = noise_gate.process(data)

                        b64_data = base64.b64encode(data).decode("utf-8")
                        await ws.send(
                            json.dumps(
                                {
                                    "realtimeInput": {
                                        "mediaChunks": [
                                            {
                                                "mimeType": "audio/pcm;rate=16000",
                                                "data": b64_data,
                                            }
                                        ]
                                    }
                                }
                            )
                        )
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        if not stop_event.is_set():
                            print(f"Sender(audio) error: {e}")
                        stop_event.set()
                        return

            async def sender_video():
                if not ENABLE_CAMERA or cv2 is None or not video_enabled:
                    return

                cap = await asyncio.to_thread(cv2.VideoCapture, CAMERA_INDEX)
                if not cap or not cap.isOpened():
                    print(f"Cannot open webcam (index={CAMERA_INDEX}); video disabled")
                    return

                await asyncio.to_thread(cap.set, cv2.CAP_PROP_FRAME_WIDTH, VIDEO_WIDTH)
                await asyncio.to_thread(
                    cap.set, cv2.CAP_PROP_FRAME_HEIGHT, VIDEO_HEIGHT
                )

                obstacle_state = {"count": 0}
                last_stop = 0.0
                try:
                    while not stop_event.is_set():
                        ok, frame = await asyncio.to_thread(cap.read)
                        if not ok:
                            await asyncio.sleep(VIDEO_INTERVAL_SEC)
                            continue

                        if OBSTACLE_STOP:
                            hit = await asyncio.to_thread(
                                detect_obstacle, frame, obstacle_state
                            )
                            if hit and (loop.time() - last_stop) > OBSTACLE_COOLDOWN_SEC:
                                last_stop = loop.time()
                                obstacle_state["count"] = 0
                                print("Obstacle ahead -> rover /stop (camera assist)")
                                await asyncio.to_thread(_post_rover, "/stop")

                        ok_jpg, encoded = await asyncio.to_thread(
                            cv2.imencode,
                            ".jpg",
                            frame,
                            [int(cv2.IMWRITE_JPEG_QUALITY), VIDEO_JPEG_QUALITY],
                        )
                        if not ok_jpg:
                            await asyncio.sleep(VIDEO_INTERVAL_SEC)
                            continue

                        b64 = base64.b64encode(encoded.tobytes()).decode("utf-8")
                        await ws.send(
                            json.dumps(
                                {
                                    "realtimeInput": {
                                        "mediaChunks": [
                                            {
                                                "mimeType": VIDEO_MIME,
                                                "data": b64,
                                            }
                                        ]
                                    }
                                }
                            )
                        )
                        await asyncio.sleep(VIDEO_INTERVAL_SEC)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    if not stop_event.is_set():
                        print(f"Sender(video) error: {e}")
                    stop_event.set()
                finally:
                    try:
                        await asyncio.to_thread(cap.release)
                    except Exception:
                        pass

            async def receiver():
                while not stop_event.is_set():
                    try:
                        message = await ws.recv()
                    except asyncio.CancelledError:
                        return
                    except websockets.exceptions.ConnectionClosed as e:
                        if not stop_event.is_set():
                            print(f"Connection closed: {e}")
                        stop_event.set()
                        return
                    except Exception as e:
                        if not stop_event.is_set():
                            print(f"Receiver error: {e}")
                        stop_event.set()
                        return

                    if isinstance(message, bytes):
                        parsed_bin = try_parse_json(message)
                        if isinstance(parsed_bin, dict):
                            audio_inline = extract_inline_audio(parsed_bin)
                            if (
                                audio_inline
                                and len(audio_inline) >= 2
                                and len(audio_inline) % 2 == 0
                            ):
                                await enqueue_audio(audio_inline)
                        continue

                    parsed = try_parse_json(message)
                    if parsed is None:
                        continue

                    if parsed.get("error"):
                        print(f"Error: {parsed['error']}")
                        continue

                    audio_inline = extract_inline_audio(parsed)
                    if (
                        audio_inline
                        and len(audio_inline) >= 2
                        and len(audio_inline) % 2 == 0
                    ):
                        await enqueue_audio(audio_inline)

                    model_turn = parsed.get("serverContent", {}).get("modelTurn", {})
                    parts = (
                        model_turn.get("parts", [])
                        if isinstance(model_turn, dict)
                        else []
                    )
                    for part in parts:
                        if isinstance(part, dict) and part.get("text"):
                            print(f"Bot: {part['text']}")

            tasks = [
                asyncio.create_task(playback_worker()),
                asyncio.create_task(sender_audio()),
                asyncio.create_task(sender_video()),
                asyncio.create_task(receiver()),
            ]

            try:
                await stop_event.wait()
            except KeyboardInterrupt:
                stop_event.set()

            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    except ConnectionRefusedError:
        print(
            f"Cannot connect to {URL}. Ensure the gateway is running and live.enabled=true"
        )
    except OSError as e:
        print(f"Network error connecting to {URL}: {e}")
    except asyncio.TimeoutError:
        print("Timeout waiting for setupComplete")
    finally:
        try:
            stream_in.stop_stream()
            stream_in.close()
        except Exception:
            pass
        try:
            stream_out.stop_stream()
            stream_out.close()
        except Exception:
            pass
        if dump_wav is not None:
            try:
                dump_wav.close()
            except Exception:
                pass
        p.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pepebot Live API client (mic + webcam -> gateway, speaker out)"
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List PyAudio input/output devices and exit",
    )
    parser.add_argument("--url", help="Override gateway WebSocket URL")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        sys.exit(0)

    if args.url:
        URL = args.url

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
