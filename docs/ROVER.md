# Rover control — LLM-driven autonomy

LLM control of the lexa Viam rover. One service on the Pi exposes a **REST API**
(for any client / the future autonomous agent) and an **MCP endpoint** (the tool
surface the Pepebot Live LLM calls). Everything goes through a safety layer.

```
 Pepebot Live LLM (Gemini) ─tool-call─► gateway 192.168.100.242
                                             │ MCP JSON-RPC POST
                                             ▼
                            Rover service @ Pi  (rover/rover_service.py, :9000)
                            ├─ POST /mcp        (initialize/tools.list/tools.call)
                            ├─ REST /drive /move /turn /stop /estop /state /imu /camera
                            └─ safety clamps + e-stop gate + bounded duration
                                             │ Viam Python SDK
                                             ▼
                                      viam-server ─► base / accelerometer / camera
```

The Live LLM already receives the webcam video, so it can *see* while it drives;
the tools provide actuation + non-visual telemetry.

## Components

| File | Role |
|---|---|
| `rover/rover_service.py` | The service: REST + `/mcp`, Viam wrapper, safety |
| `rover/requirements.txt` | `viam-sdk`, `fastapi`, `uvicorn`, `pydantic` |
| `rover/.env.example` | Config: Viam creds, component names, safety limits |
| `rover/rover-control.service` | systemd unit (auto-start on the Pi) |
| `rover/mcp-registry-entry.json` | Fallback manual MCP registration |
| `setup-rover.sh` | venv + deps installer |
| `skills/rover/SKILL.md` | Pepebot skill: auto-registers the MCP server + guides the LLM |

## Deploy

### On the Pi (lexa.local — runs the rover + viam-server)

```bash
cd ~/lexa
bash setup-rover.sh                 # venv + deps
nano rover/.env                     # set VIAM_API_KEY / VIAM_API_KEY_ID / VIAM_ADDRESS
                                    #   (from app.viam.com -> machine -> CONNECT)
                                    # and confirm VIAM_BASE / VIAM_MOVEMENT_SENSOR / VIAM_CAMERA
                                    #   match your Viam config component names

# install + start as a service
sudo cp rover/rover-control.service /etc/systemd/system/
sudo systemctl enable --now rover-control
curl -s localhost:9000/health       # {"ok":true,"connected":true,...}
```

### On the gateway (192.168.100.242 — runs Pepebot)

The skill registers the MCP server automatically. Copy it into the gateway's
skills workspace:

```bash
# from this repo (skills/rover/SKILL.md) to the gateway:
scp -r skills/rover root@192.168.100.242:/root/.pepebot/workspace/skills/
# restart the gateway so it loads the skill + syncs the MCP registry
ssh root@192.168.100.242 'systemctl restart pepebot'
```

Then start the Live client as usual (`python3 client-video.py`) and talk — the
LLM now has the `rover_*` tools and can drive while seeing through the camera.

> Manual alternative (no skill): merge `rover/mcp-registry-entry.json` into
> `/root/.pepebot/workspace/mcp/registry.json` on the gateway and restart it.

## REST API

| Method | Path | Body / result |
|---|---|---|
| POST | `/drive` | `{linear,-1..1; angular,-1..1; seconds}` → bounded power drive, auto-stop |
| POST | `/move` | `{distance_m, speed_mps?}` → drive straight |
| POST | `/turn` | `{angle_deg, speed_dps?}` → spin in place |
| POST | `/stop` | stop now |
| POST | `/estop` · `/estop/clear` | engage / clear emergency stop |
| GET | `/state` | connected / moving / estopped |
| GET | `/imu` | accelerometer x/y/z |
| GET | `/camera` | JPEG snapshot (base64) |
| GET | `/health` · `/capabilities` | service + tool list |

## MCP tools (what the LLM sees)

`rover_drive`, `rover_move`, `rover_turn`, `rover_stop`, `rover_estop`,
`rover_get_state`, `rover_get_imu`. Schemas served from `/mcp` `tools/list`.

## Safety (enforced server-side)

- Power and speed are clamped (`MAX_LINEAR_POWER`, `MAX_ANGULAR_POWER`, `MAX_SPEED_MPS`).
- `rover_drive` always **auto-stops** after a capped duration (`MAX_DRIVE_SECONDS`) —
  no unbounded "drive forever"; continuous motion = repeated calls.
- `/move` and `/turn` distances/angles are capped (`MAX_MOVE_M`, `MAX_TURN_DEG`).
- `rover_estop` halts and blocks all motion until `/estop/clear`.
- The LLM cannot exceed any limit; tune them in `rover/.env`.

## Modes

- **Teleop (now):** user speaks → LLM calls one tool → rover acts → LLM narrates.
- **Autonomous (next):** a goal-driven loop (perceive via `/camera` + `/imu` +
  state → decide → act → repeat) built on the same REST API. The API + safety
  layer is the foundation; the autonomous agent is added on top without changing
  the rover service.

## Status

Built and committed; **pending deploy/verification on the Pi** (rover was offline
during authoring). When online: confirm Viam component names in the config and
that `viam-sdk` installs in the venv on aarch64 / Python 3.13.
