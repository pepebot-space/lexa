# lexa — Design & Build Document

`lexa` is an **autonomous voice + video rover**: a Raspberry Pi 3 on a Viam Rover 1
chassis, driven by the **Pepebot Live API** (Gemini Live, vertex) as its brain. You
talk to it; it sees through a webcam, replies through a speaker, and drives itself
toward goals — plus a web dashboard for teleop and monitoring.

This document captures the whole system and the build journey (decisions + root
causes). Companion docs: [WIRING.md](WIRING.md), [ROVER.md](ROVER.md),
[pepebot-live-system-prompt-request.md](pepebot-live-system-prompt-request.md).

---

## 1. Topology

```
  You ⇄ (voice + video) ⇄  client-video.py  ──ws──►  Pepebot gateway
        on the Pi (lexa.local)                        192.168.100.242:18790/v1/live
        │  owns mic + webcam + speaker                 (Gemini Live, vertex)
        │                                                   │ tool calls (MCP)
        │  MJPEG :8081 ─────────────────────────┐          ▼
        ▼                                        │   ┌──────────────────┐
   rover_service.py  ◄──REST /drive,/state...────┘   │ MCP JSON-RPC POST│
   on the Pi :9000   ◄──MCP /mcp──────────────────────  http://...212:9000/mcp
        │  gpiozero                                   └──────────────────┘
        ▼
   L298N H-bridge ─► 2 DC motors (+ encoders), ADXL345, MAX98357A speaker
```

| Host | Address | Role |
|---|---|---|
| **lexa.local** | **192.168.100.212** (wlan0; eth0 = .124 when plugged) | The rover: Pi 3, Debian 13, Python 3.13, user `vexa` (passwordless sudo). Hostname renamed `vexa-002`→`lexa`. |
| **gateway** (`pepebot`) | **192.168.100.242:18790** | Pepebot gateway (v0.5.16), `pepebot.service`, root SSH, config `/root/.pepebot/config.json`. Live: provider `vertex`, model `gemini-live-2.5-flash-native-audio`, lang `id-ID`, `live.video=true`. |

- Repo: `git@github.com:pepebot-space/lexa.git` (branch `main`).
- WiFi: SSID `irawan.science`, autoconnect on, **powersave disabled** (`nmcli 802-11-wireless.powersave 2`) — it dropped intermittently otherwise.
- mDNS via avahi (`avahi-daemon` + `libnss-mdns`), so the Pi is reachable as `lexa.local`.

---

## 2. Hardware

- **Raspberry Pi 3 Model B** (aarch64).
- **Viam Rover 1** chassis: L298N dual H-bridge, 2 encoded DC motors, **ADXL345** accelerometer (I2C). *Note: the Viam docs are only used for the chassis/wiring — `viam-server` is NOT installed; motors are driven directly via GPIO.*
- **USB webcam** (`GENERAL WEBCAM`) with a built-in mic (`/dev/video0`, ALSA capture card).
- **MAX98357A** I2S DAC/amplifier → passive speaker.

Full pin map: see [WIRING.md](WIRING.md). Key pins (BCM):
`I2S` BCLK=18, **LRCLK=19**, DIN=21 · `L298N` left 17/27/22, right 23/24/25 ·
`encoders` left=5, right=26 · `I2C` SDA=2, SCL=3.

---

## 3. Software components

| File | Role |
|---|---|
| `client-video.py` | **Live client** on the Pi: mic+webcam→gateway, speaker out; camera annotation + obstacle reflex; MJPEG stream `:8081`; sends the autonomous system prompt. Runs as `lexa-live.service`. |
| `rover/rover_service.py` | **Rover control** (FastAPI `:9000`): REST + `/mcp` (LLM tools) + `GET /` dashboard + `/api/*` compat. gpiozero/L298N backend; safety. Runs as `rover-control.service`. |
| `static/index.html` | **Web dashboard** (rebranded from `pepebot-space/vexa`): camera/power/logs/motion panels. |
| `skills/rover/SKILL.md` | Pepebot skill: registers the rover MCP server + gives the LLM driving guidance. |
| `rover/`, `setup-*.sh`, `*.service` | deps, installers, systemd units. |
| `test-*.py`, `gen-tones.py`, `analyze-audio.py`, `play-test.py`, `resample-play.py` | diagnostics built during bring-up. |

---

## 4. Subsystems

### 4.1 Audio (MAX98357A, I2S)
- `dtoverlay=hifiberry-dac` in `/boot/firmware/config.txt` (the MAX98357A speaks plain
  I2S; the hifiberry-dac overlay drives it, no SD-pin GPIO needed).
- `/etc/asound.conf`: default PCM → `plug` → `hw:sndrpihifiberry` (auto-resamples the
  24 kHz mono bot audio up to the DAC's rate).
- The mic is the USB webcam's capture device; ALSA defaults resolve to **mic-in +
  MAX98357A-out**, so the client needs no audio env vars.
- Knobs in `client-video.py`: `OUTPUT_GAIN` (anti-clip), `AMP_IDLE_MUTE` (stop the I2S
  stream when idle so the Class-D amp doesn't hiss between responses), `DUMP_AUDIO`.
- `audioop` was removed from Python 3.13 → the noise gate uses a **pure-Python RMS** fallback.

### 4.2 Live client (`client-video.py`)
- Connects to `ws://192.168.100.242:18790/v1/live`, sends a `setup` (provider/model/
  agent/`enable_tools`/**`system_prompt`**), streams mic (16 kHz PCM) + webcam (JPEG ~2 fps),
  plays the model's 24 kHz audio.
- Also: annotates frames, runs the obstacle reflex, and serves the MJPEG stream.

### 4.3 Rover control (`rover/rover_service.py`)
- **Backend = direct GPIO via gpiozero** driving the L298N (no Viam).
- **REST**: `POST /drive` (linear/angular power + bounded seconds), `/move` (distance),
  `/turn` (angle), `/stop`, `/estop` (+`/estop/clear`); `GET /state` (encoders, e-stop),
  `/imu` (ADXL345), `/health`, `/capabilities`.
- **MCP** `POST /mcp` (JSON-RPC 2.0): `initialize` / `tools/list` / `tools/call` — the
  tool surface the Live LLM calls (`rover_drive/move/turn/stop/estop/get_state/get_imu`).
- **Safety (server-side, the LLM can't exceed):** power/speed clamps; `rover_drive`
  auto-stops after a capped duration (no "drive forever"); distance/angle/time caps;
  e-stop gate; **encoder stall detection** → if powered but wheels aren't turning, it
  stops early and returns `blocked:true`.
- **Calibration (measured on this rover):** `CALIB_MPS=0.115` (23 cm in 2 s @0.45 power),
  `CALIB_DPS=48` (145° in 3 s @0.45), `MAX_MOVE_SECONDS=10`. `move`/`turn` are open-loop
  time-based — accurate enough for the scan loop (the model re-checks the camera each step).

### 4.4 Pepebot MCP integration
- The Live LLM gets tools from MCP servers listed in the gateway's
  `/root/.pepebot/workspace/mcp/registry.json`. Pepebot **skills are context only**, not
  tools — but a skill's `mcp:` frontmatter can register a server.
- pepebot's MCP HTTP transport is **plain JSON-RPC over POST** (not SSE/streamable), so the
  rover service serves `/mcp` from the same FastAPI app with a tiny handler.
- Registered: `rover-control` → `http://192.168.100.212:9000/mcp` (the `.212` WiFi IP, so it
  works when the rover roams). Skill copied to `/root/.pepebot/workspace/skills/rover/`.

### 4.5 Live system prompt
- The Live API originally had **no system-prompt hook** (only tools were injected). We filed
  a feature request (see the companion doc); pepebot added it (≥0.5.14).
- Precedence: **client `setup.system_prompt` > `live.system_prompt`(/_file) > agent persona**.
- `client-video.py` sends `setup.system_prompt = DEFAULT_SYSTEM_PROMPT` (the autonomous rover
  persona), overridable via `LIVE_SYSTEM_PROMPT` / `LIVE_SYSTEM_PROMPT_FILE`. Gateway logs
  `Applied system instruction {source=client}`.

### 4.6 Autonomous mode
- The brain is the existing Gemini **Live** session (turn-based). The system prompt makes it
  pursue a **goal** in a perceive→act→perceive loop with the rover tools, using the camera.
- Prompt emphasises: **scan first** (single camera → rotate ~30–45° to look around before
  driving), **move decisively** (not tiny twitches), **talk less** (act, narrate only when
  it matters), and obstacle handling.
- Limitation: Live is turn-based, so it acts in bursts per turn rather than a truly
  continuous loop. A standalone perceive-decide-act agent on the same REST API is the future
  upgrade for non-stop autonomy.

### 4.7 Perception / navigation (visual prompting + reflex)
Mono camera = no depth, so the LLM bumped things and lost direction. Approach (software-only):
- **Frame annotation** (`annotate_frame`): the frame the LLM sees is overlaid with a
  direction grid **LEFT/CENTER/RIGHT** (each `CLEAR/BLOCKED`), **NEAR/MID/FAR** distance
  lines, and a **red tint on obstacle pixels**. The system prompt tells the model how to read it.
- **Glare-aware floor detection** (`_floor_analysis`): a pixel is an obstacle if it is
  **darker OR more saturated** than the floor reference; **brighter pixels (glare/reflections
  on shiny floors) are ignored** — this fixed a reflective floor reading as all-obstacle.
- **Confidence gate**: if `>FLOOR_CONF_GATE` (~80%) of the ROI flags (heuristic failing on a
  reflective floor), the red/BLOCKED overlay is **suppressed** and zones show `?` +
  "floor uncertain" — so the annotation never misleads; the LLM uses its own scene judgment.
- **Reflex auto-stop** (`OBSTACLE_STOP`, on in `lexa-live`): independent of the LLM — if the
  CENTER zone is blocked for N frames (and the read is confident), the client POSTs `/stop`.
  Protects both autonomous and dashboard-teleop driving.
- Encoder **stall detection** (in the rover service) is the last-resort collision catch.

### 4.8 Web dashboard (`static/index.html` + `/api/*`)
- Reused the `pepebot-space/vexa` control panel, rebranded vexa→lexa. Served by the rover
  service at `GET /`.
- A `/api/*` **compatibility layer** in the rover service maps the dashboard's calls to lexa:
  `/api/joystick` + `/api/move` → **non-blocking teleop with a deadman expiry** (smooth, no
  backlog, auto-stop on release); `/api/status` reports connected (local GPIO); battery/logs/
  agent are stubbed (no battery sensor; the voice agent is the separate Live client).
- **Live camera**: the Live client (which owns `/dev/video0`) re-serves its frames as **MJPEG
  on `:8081/video`**; the dashboard's camera panel points there, so camera + Live + dashboard
  all run together off one camera.

### 4.9 Services (systemd, on the Pi)
| Unit | Runs | Notes |
|---|---|---|
| `rover-control.service` | `rover/.venv/bin/uvicorn rover_service:app` (`:9000`) | REST + MCP + dashboard. venv is `--system-site-packages` (sees apt gpiozero/lgpio) + pip fastapi/uvicorn. |
| `lexa-live.service` | `python3 client-video.py` | System python (apt pyaudio/opencv/websockets). `OBSTACLE_STOP=1`. Auto-start → rover boots ready. **Implication: mic is always-on/streaming** while up. Stop it before running the client manually (camera single-owner). |

---

## 5. Build journey — problems solved (root causes)

1. **mDNS** — renamed host to `lexa`, installed avahi → reachable as `lexa.local`.
2. **Video disabled** on the gateway → set `live.video=true` (config-side, not client).
3. **Garbled bot speech (the big one)** — speech was unintelligible while test tones were
   fine and the *received* audio was provably clean (proved via `DUMP_AUDIO` +
   `analyze-audio.py`). Root cause: the **I2S LRCLK was on the wrong pin**; a jittery
   frame-sync survives a single tone but destroys complex speech. **Fix: LRCLK → GPIO19.**
4. **GPIO19 conflict** — the Viam Rover's left encoder uses pin 35 (GPIO19), which is the
   *only* I2S LRCLK pin on the Pi header. Encoders can be on any GPIO; **moved the left
   encoder to pin 29 (GPIO5)** and kept LRCLK on GPIO19.
5. **Amp hiss** — MAX98357A analog noise floor (not software). Mitigate via the GAIN pin
   (tie to Vin) + power decoupling. `AMP_IDLE_MUTE` removes the between-response hiss.
6. **WiFi** — no WiFi profile existed (only ethernet); added `irawan.science` + autoconnect;
   later disabled powersave to stop intermittent drops.
7. **No Viam** — `viam-server` was never installed; `gpiozero`/`lgpio` were present →
   chose **direct GPIO** control (simpler, no cloud).
8. **Live had no system prompt** — found there was no hook in pepebot; filed a feature
   request; it was implemented; the client now injects the autonomous persona.
9. **Reflective floor** read as all-obstacle → **glare-aware detection + confidence gate**.

---

## 6. Configuration reference (env)

**Live client (`client-video.py` / `lexa-live.service`):**
`LIVE_API_URL`, `LIVE_PROVIDER`, `LIVE_MODEL`, `LIVE_AGENT`, `LIVE_SYSTEM_PROMPT(_FILE)`,
`INPUT_DEVICE_INDEX`/`OUTPUT_DEVICE_INDEX`, `CAMERA_INDEX`, `ENABLE_CAMERA`,
`OUTPUT_GAIN`, `AMP_IDLE_MUTE`, `DUMP_AUDIO`,
`MJPEG_ENABLE`/`MJPEG_PORT`, `ANNOTATE_FRAME`,
`OBSTACLE_STOP`, `ROVER_API`, `OBSTACLE_FRAMES`, `OBSTACLE_COOLDOWN_SEC`,
`FLOOR_ROI_TOP`, `FLOOR_DARK_TOL`, `FLOOR_SAT_TOL`, `FLOOR_CONF_GATE`, `ZONE_CLEAR`, `ZONE_BLOCK`.

**Rover service (`rover/.env`):** `ROVER_PORT`, L298N pins (`LEFT_*`/`RIGHT_*`/`ENC_*`),
`INVERT_LEFT/RIGHT`, `SWAP_SIDES`, `MAX_WHEEL_POWER`, drive/move/turn caps,
`MOVE_POWER`/`CALIB_MPS`/`TURN_POWER`/`CALIB_DPS`, `STALL_*`, `I2C_BUS`/`ADXL345_ADDR`.

---

## 7. Deploy (summary)

**Pi:** `bash setup-lexa.sh` (audio/cv/ws apt deps) · `bash setup-rover.sh` (venv) ·
install `rover-control.service` + `lexa-live.service` · audio overlay + `asound.conf` (see
WIRING.md) · enable I2C (`dtparam=i2c_arm=on` + `i2c-dev` module) for the IMU.

**Gateway (.242):** write the rover entry into `mcp/registry.json`, copy `skills/rover/`,
`systemctl restart pepebot`.

Open the dashboard at `http://192.168.100.212:9000/`.

---

## 8. Known limitations & future work

- **Mono camera, no depth** — obstacle sense is heuristic. A cheap **distance sensor**
  (ultrasonic HC-SR04 / ToF VL53L0X) is the most robust anti-collision upgrade.
- **Floor heuristic is fragile** on patterned/very reflective floors (mitigated by the
  confidence gate). Tune `FLOOR_*` per environment.
- **Live is turn-based** → autonomy runs in bursts. A standalone perceive-decide-act loop
  would give continuous autonomy.
- **No gyro** (ADXL345 is accel-only) → no true heading; turn is open-loop. Encoder
  dead-reckoning or a magnetometer would add heading awareness.
- **`move`/`turn` open-loop** → re-measure `CALIB_*` if wheels/battery/surface change.
- **Always-on mic** when `lexa-live` runs (privacy/cost consideration).
- WiFi reliability for mobile use depends on signal + clean power separate from motors.
