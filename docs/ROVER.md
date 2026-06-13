# Rover control — LLM-driven autonomy

LLM control of the lexa rover. One service on the Pi exposes a **REST API** (for
any client / the future autonomous agent) and an **MCP endpoint** (the tool
surface the Pepebot Live LLM calls). Backend = **direct GPIO via gpiozero**
driving an L298N H-bridge (no Viam). Everything goes through a safety layer.

```
 Pepebot Live LLM (Gemini) ─tool-call─► gateway 192.168.100.242
                                             │ MCP JSON-RPC POST
                                             ▼
                            Rover service @ Pi 192.168.100.212  (:9000)
                            ├─ POST /mcp        (initialize/tools.list/tools.call)
                            ├─ REST /drive /move /turn /stop /estop /state /imu
                            └─ safety clamps + e-stop gate + auto-stop
                                             │ gpiozero
                                             ▼
                                   L298N H-bridge ─► 2 DC motors (+ encoders)
```

The Live LLM also receives the webcam video, so it can *see* while it drives.

## Components

| File | Role |
|---|---|
| `rover/rover_service.py` | The service: REST + `/mcp`, gpiozero motor control, safety |
| `rover/requirements.txt` | `fastapi`, `uvicorn`, `pydantic` (gpiozero/lgpio from system) |
| `rover/.env.example` | Config: pins, safety limits, calibration |
| `rover/rover-control.service` | systemd unit (auto-start on the Pi) |
| `rover/mcp-registry-entry.json` | The MCP registry entry for the gateway |
| `setup-rover.sh` | venv (`--system-site-packages`) + deps installer |
| `skills/rover/SKILL.md` | Pepebot skill: registers the MCP server + guides the LLM |

## Pins (BCM, from docs/WIRING.md)

L298N: left motor `IN1=17 IN2=27 EN=22`, right motor `IN3=23 IN4=24 EN=25`;
encoders left=`5` right=`26`. (No conflict with I2S audio 18/19/21.)

## Deploy

### On the Pi (192.168.100.212)

```bash
cd ~/lexa
bash setup-rover.sh                         # venv + fastapi/uvicorn; gpiozero from apt
sudo cp rover/rover-control.service /etc/systemd/system/
sudo systemctl enable --now rover-control
curl -s localhost:9000/health               # {"ok":true,"connected":true,...}
```

**Motor direction calibration** (prop the rover up, wheels hanging):
drive each wheel and observe; set `INVERT_LEFT` / `INVERT_RIGHT` (= -1 to flip) and
`SWAP_SIDES` (=1 if left/right swapped) in `rover/.env`, then
`sudo systemctl restart rover-control`. (On this build the defaults are already
correct: left=left, right=right, both forward.)

### On the gateway (192.168.100.242)

```bash
# MCP registry entry (already deployed):
#   /root/.pepebot/workspace/mcp/registry.json  ->  rover-control @ http://192.168.100.212:9000/mcp
# skill (LLM guidance):
scp -r skills/rover root@192.168.100.242:/root/.pepebot/workspace/skills/
ssh root@192.168.100.242 'systemctl restart pepebot'
```

Then run the Live client (`python3 client-video.py`) and talk — the LLM has the
`rover_*` tools and drives while seeing the camera.

## REST API

| Method | Path | Body / result |
|---|---|---|
| POST | `/drive` | `{linear -1..1, angular -1..1, seconds}` → bounded power drive, auto-stop |
| POST | `/move` | `{distance_m, power?}` → drive straight (open-loop, time-based) |
| POST | `/turn` | `{angle_deg, power?}` → spin in place (open-loop) |
| POST | `/stop` · `/estop` · `/estop/clear` | stop / engage / clear emergency stop |
| GET | `/state` | connected, estopped, encoder counts |
| GET | `/imu` | accelerometer g x/y/z (needs I2C enabled) |
| GET | `/health` · `/capabilities` | service + tool list |

## MCP tools (what the LLM sees)

`rover_drive`, `rover_move`, `rover_turn`, `rover_stop`, `rover_estop`,
`rover_get_state`, `rover_get_imu`.

## Safety (enforced server-side)

- Per-wheel duty clamped (`MAX_WHEEL_POWER`).
- `rover_drive` **auto-stops** after a capped duration (`MAX_DRIVE_SECONDS`) — no
  unbounded "drive forever"; continuous motion = repeated calls.
- `/move` and `/turn` distance/angle/time capped (`MAX_MOVE_M`, `MAX_TURN_DEG`, `MAX_MOVE_SECONDS`).
- `rover_estop` halts and blocks motion until `/estop/clear`.
- The LLM cannot exceed any limit; tune them in `rover/.env`.

## Notes / next

- `move`/`turn` are **open-loop (time-based)** — approximate. Calibrate `CALIB_MPS`
  / `CALIB_DPS` in `rover/.env` by measuring, or upgrade to encoder-closed-loop
  using the `/state` encoder counts.
- Accelerometer (ADXL345) needs I2C enabled: uncomment `dtparam=i2c_arm=on` in
  `/boot/firmware/config.txt` + reboot.
- **Autonomous (next):** a goal-driven loop (perceive via camera + `/state` →
  decide → act) built on the same REST API; the safety layer is the foundation.
