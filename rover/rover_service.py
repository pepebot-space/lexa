"""
Rover control service for lexa — one FastAPI app exposing TWO interfaces over
the same safety-checked core:

  1. REST API   (POST /drive,/move,/turn,/stop,/estop; GET /state,/imu,/health,
     /capabilities) — for any client / the future autonomous agent.
  2. MCP endpoint (POST /mcp, JSON-RPC 2.0) — the tool surface the Pepebot Live
     LLM calls. Implements initialize / tools.list / tools.call exactly as the
     pepebot MCP HTTP client expects (plain JSON response, not SSE).

Backend: direct GPIO via gpiozero driving an L298N H-bridge (no Viam). Pins per
docs/WIRING.md. All motion goes through clamps + an e-stop gate + bounded
duration, so an LLM can never command unbounded movement.

Run:  uvicorn rover_service:app --app-dir rover --host 0.0.0.0 --port 9000
Config: see rover/.env.example (pins, safety limits, calibration).
"""
import asyncio
import os
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")
from gpiozero import Motor  # noqa: E402  (after pin-factory env)
try:
    from gpiozero import DigitalInputDevice
except Exception:  # pragma: no cover
    DigitalInputDevice = None

# --------------------------------------------------------------------------- #
# Configuration (env — see rover/.env.example). Pins are BCM GPIO numbers.
# --------------------------------------------------------------------------- #
def _int(name, default):
    return int(os.environ.get(name, default))


def _float(name, default):
    return float(os.environ.get(name, default))


# L298N: Motor A = IN1/IN2/ENA, Motor B = IN3/IN4/ENB  (defaults = docs/WIRING.md)
LEFT_IN1 = _int("LEFT_IN1", 17)
LEFT_IN2 = _int("LEFT_IN2", 27)
LEFT_EN = _int("LEFT_EN", 22)
RIGHT_IN1 = _int("RIGHT_IN1", 23)
RIGHT_IN2 = _int("RIGHT_IN2", 24)
RIGHT_EN = _int("RIGHT_EN", 25)
ENC_LEFT = _int("ENC_LEFT", 5)
ENC_RIGHT = _int("ENC_RIGHT", 26)

# Orientation fixes (set after the calibration test). 1 or -1.
INVERT_LEFT = _int("INVERT_LEFT", 1)
INVERT_RIGHT = _int("INVERT_RIGHT", 1)
SWAP_SIDES = os.environ.get("SWAP_SIDES", "0") in ("1", "true", "True")

# Safety limits (server clamps every command — the LLM cannot exceed these)
MAX_WHEEL_POWER = _float("MAX_WHEEL_POWER", 0.7)      # per-wheel duty cap 0..1
DEFAULT_DRIVE_SECONDS = _float("DEFAULT_DRIVE_SECONDS", 1.0)
MAX_DRIVE_SECONDS = _float("MAX_DRIVE_SECONDS", 3.0)
MAX_MOVE_SECONDS = _float("MAX_MOVE_SECONDS", 8.0)
MAX_MOVE_M = _float("MAX_MOVE_M", 3.0)
MAX_TURN_DEG = _float("MAX_TURN_DEG", 360.0)

# Open-loop calibration for discrete move/turn (refine after measuring).
MOVE_POWER = _float("MOVE_POWER", 0.45)
CALIB_MPS = _float("CALIB_MPS", 0.25)     # m/s at MOVE_POWER (forward)
TURN_POWER = _float("TURN_POWER", 0.45)
CALIB_DPS = _float("CALIB_DPS", 90.0)     # deg/s at TURN_POWER (spin in place)

# Obstacle/stall detection: auto-stop if a wheel is powered but not turning
# (rover pushing against a wall / stuck). Uses the encoders — no extra sensor.
STALL_CHECK = os.environ.get("STALL_CHECK", "1") not in ("0", "false", "False")
STALL_GRACE_S = _float("STALL_GRACE_S", 0.3)      # spin-up grace before checking
STALL_WINDOW_S = _float("STALL_WINDOW_S", 0.2)    # window to measure pulses over
STALL_MIN_PULSES = _int("STALL_MIN_PULSES", 3)    # < this in a window = stalled
STALL_MIN_POWER = _float("STALL_MIN_POWER", 0.25)  # only check when truly powered

PORT = _int("ROVER_PORT", 9000)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# Hardware
# --------------------------------------------------------------------------- #
class Rover:
    def __init__(self):
        self.left: Optional[Motor] = None
        self.right: Optional[Motor] = None
        self.enc_left = None
        self.enc_right = None
        self.enc_left_count = 0
        self.enc_right_count = 0
        self.estopped = False
        self.last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    def connect(self):
        try:
            la, lb = (LEFT_IN1, LEFT_IN2) if INVERT_LEFT == 1 else (LEFT_IN2, LEFT_IN1)
            ra, rb = (RIGHT_IN1, RIGHT_IN2) if INVERT_RIGHT == 1 else (RIGHT_IN2, RIGHT_IN1)
            self.left = Motor(forward=la, backward=lb, enable=LEFT_EN, pwm=True)
            self.right = Motor(forward=ra, backward=rb, enable=RIGHT_EN, pwm=True)
            if DigitalInputDevice is not None:
                self.enc_left = DigitalInputDevice(ENC_LEFT)
                self.enc_right = DigitalInputDevice(ENC_RIGHT)
                self.enc_left.when_activated = self._tick_left
                self.enc_left.when_deactivated = self._tick_left
                self.enc_right.when_activated = self._tick_right
                self.enc_right.when_deactivated = self._tick_right
            self.last_error = None
        except Exception as e:
            self.last_error = f"gpio init failed: {e}"

    def _tick_left(self):
        self.enc_left_count += 1

    def _tick_right(self):
        self.enc_right_count += 1

    def _set_wheels(self, left_v: float, right_v: float):
        if SWAP_SIDES:
            left_v, right_v = right_v, left_v
        self.left.value = _clamp(left_v, -MAX_WHEEL_POWER, MAX_WHEEL_POWER)
        self.right.value = _clamp(right_v, -MAX_WHEEL_POWER, MAX_WHEEL_POWER)

    def _stop(self):
        if self.left:
            self.left.stop()
        if self.right:
            self.right.stop()

    def require(self):
        if self.left is None or self.right is None:
            raise RuntimeError(f"motors not initialized ({self.last_error or 'unknown'})")
        if self.estopped:
            raise RuntimeError("E-STOP engaged — call /estop/clear before moving")


rover = Rover()

# --- Dashboard teleop: non-blocking with a deadman expiry (no command backlog) ---
_teleop = {"expire": 0.0, "active": False}


def _now() -> float:
    return asyncio.get_event_loop().time()


async def teleop_set(left_v: float, right_v: float, seconds: float):
    """Set wheels now and auto-stop after `seconds` via the ticker (deadman).
    Re-sending refreshes the expiry, so held joystick/d-pad stays smooth."""
    rover.require()
    seconds = _clamp(seconds, 0.0, MAX_DRIVE_SECONDS)
    rover._set_wheels(left_v, right_v)
    _teleop["expire"] = _now() + seconds
    _teleop["active"] = True


async def teleop_stop():
    _teleop["active"] = False
    rover._stop()


async def teleop_ticker():
    while True:
        await asyncio.sleep(0.05)
        try:
            if _teleop["active"] and _now() >= _teleop["expire"]:
                _teleop["active"] = False
                rover._stop()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    rover.connect()
    ticker = asyncio.create_task(teleop_ticker())
    yield
    ticker.cancel()
    rover._stop()


app = FastAPI(title="lexa rover control (gpiozero)", version="0.2.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Core actions (shared by REST routes and MCP tools)
# --------------------------------------------------------------------------- #
async def _run_motion(left_v: float, right_v: float, seconds: float) -> bool:
    """Drive wheels for `seconds`, auto-stopping early if stalled (an obstacle).
    Returns True if a stall was detected. Holds the motion lock."""
    blocked = False
    powered = abs(left_v) >= STALL_MIN_POWER or abs(right_v) >= STALL_MIN_POWER
    async with rover._lock:
        rover._set_wheels(left_v, right_v)
        elapsed = 0.0
        win = 0.0
        last = rover.enc_left_count + rover.enc_right_count
        while elapsed < seconds:
            await asyncio.sleep(0.1)
            elapsed += 0.1
            if not (STALL_CHECK and powered) or elapsed < STALL_GRACE_S:
                continue
            win += 0.1
            if win + 1e-9 >= STALL_WINDOW_S:
                now = rover.enc_left_count + rover.enc_right_count
                if now - last < STALL_MIN_PULSES:
                    blocked = True
                    break
                last, win = now, 0.0
        rover._stop()
    return blocked


async def act_drive(linear: float, angular: float, seconds: float) -> dict:
    """Differential power drive for a bounded duration, then auto-stop.
    linear forward(+)/back(-), angular left(+)/right(-), both -1..1."""
    rover.require()
    linear = _clamp(linear, -1.0, 1.0)
    angular = _clamp(angular, -1.0, 1.0)
    seconds = _clamp(seconds, 0.0, MAX_DRIVE_SECONDS)
    left_v = linear - angular
    right_v = linear + angular
    blocked = await _run_motion(left_v, right_v, seconds)
    return {"ok": True, "linear": linear, "angular": angular, "seconds": seconds,
            "blocked": blocked, "wheels": {"left": round(left_v, 3), "right": round(right_v, 3)}}


async def act_move(distance_m: float, power: Optional[float]) -> dict:
    """Drive straight an approximate distance (open-loop, time-based)."""
    rover.require()
    distance_m = _clamp(distance_m, -MAX_MOVE_M, MAX_MOVE_M)
    p = _clamp(power if power else MOVE_POWER, 0.1, MAX_WHEEL_POWER)
    direction = 1.0 if distance_m >= 0 else -1.0
    seconds = _clamp(abs(distance_m) / max(CALIB_MPS, 1e-3), 0.0, MAX_MOVE_SECONDS)
    blocked = await _run_motion(direction * p, direction * p, seconds)
    return {"ok": True, "distance_m": distance_m, "power": p, "seconds": round(seconds, 2),
            "blocked": blocked, "note": "open-loop (time-based); calibrate CALIB_MPS"}


async def act_turn(angle_deg: float, power: Optional[float]) -> dict:
    """Spin in place an approximate angle (open-loop). +left/CCW, -right/CW."""
    rover.require()
    angle_deg = _clamp(angle_deg, -MAX_TURN_DEG, MAX_TURN_DEG)
    p = _clamp(power if power else TURN_POWER, 0.1, MAX_WHEEL_POWER)
    direction = 1.0 if angle_deg >= 0 else -1.0  # +left => left wheel back, right fwd
    seconds = _clamp(abs(angle_deg) / max(CALIB_DPS, 1e-3), 0.0, MAX_MOVE_SECONDS)
    blocked = await _run_motion(-direction * p, direction * p, seconds)
    return {"ok": True, "angle_deg": angle_deg, "power": p, "seconds": round(seconds, 2),
            "blocked": blocked, "note": "open-loop (time-based); calibrate CALIB_DPS"}


async def act_stop() -> dict:
    rover._stop()
    return {"ok": True, "stopped": True}


async def act_estop(clear: bool = False) -> dict:
    rover.estopped = not clear
    if not clear:
        rover._stop()
    return {"ok": True, "estopped": rover.estopped}


async def read_state() -> dict:
    return {
        "ok": True,
        "connected": rover.left is not None,
        "estopped": rover.estopped,
        "encoders": {"left": rover.enc_left_count, "right": rover.enc_right_count},
        "error": rover.last_error,
    }


async def read_imu() -> dict:
    # ADXL345 over I2C (rover accel). Best-effort; needs I2C enabled + smbus.
    try:
        try:
            from smbus2 import SMBus
        except Exception:
            from smbus import SMBus  # type: ignore
        addr = int(os.environ.get("ADXL345_ADDR", "0x53"), 16)
        bus = SMBus(int(os.environ.get("I2C_BUS", "1")))
        bus.write_byte_data(addr, 0x2D, 0x08)  # POWER_CTL: measure
        await asyncio.sleep(0.05)  # let the first sample settle (else reads 0)

        def _ax(lo, hi):
            v = bus.read_byte_data(addr, lo) | (bus.read_byte_data(addr, hi) << 8)
            return v - 65536 if v > 32767 else v

        x, y, z = _ax(0x32, 0x33), _ax(0x34, 0x35), _ax(0x36, 0x37)
        bus.close()
        g = 0.0039  # ~ +/-2g, 10-bit
        return {"available": True, "g": {"x": round(x * g, 3), "y": round(y * g, 3), "z": round(z * g, 3)}}
    except Exception as e:
        return {"available": False, "reason": str(e)}


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #
class DriveReq(BaseModel):
    linear: float = Field(0.0, description="forward(+)/back(-) -1..1")
    angular: float = Field(0.0, description="left(+)/right(-) -1..1")
    seconds: float = Field(DEFAULT_DRIVE_SECONDS)


class MoveReq(BaseModel):
    distance_m: float
    power: Optional[float] = None


class TurnReq(BaseModel):
    angle_deg: float
    power: Optional[float] = None


def _err(e: Exception):
    code = 423 if "E-STOP" in str(e) else 503
    return JSONResponse(status_code=code, content={"ok": False, "error": str(e)})


@app.post("/drive")
async def rest_drive(r: DriveReq):
    try:
        return await act_drive(r.linear, r.angular, r.seconds)
    except Exception as e:
        return _err(e)


@app.post("/move")
async def rest_move(r: MoveReq):
    try:
        return await act_move(r.distance_m, r.power)
    except Exception as e:
        return _err(e)


@app.post("/turn")
async def rest_turn(r: TurnReq):
    try:
        return await act_turn(r.angle_deg, r.power)
    except Exception as e:
        return _err(e)


@app.post("/stop")
async def rest_stop():
    return await act_stop()


@app.post("/estop")
async def rest_estop():
    return await act_estop(clear=False)


@app.post("/estop/clear")
async def rest_estop_clear():
    return await act_estop(clear=True)


@app.get("/state")
async def rest_state():
    return await read_state()


@app.get("/imu")
async def rest_imu():
    return await read_imu()


@app.get("/health")
async def rest_health():
    return {"ok": rover.left is not None, "connected": rover.left is not None,
            "estopped": rover.estopped, "error": rover.last_error,
            "pins": {"left": [LEFT_IN1, LEFT_IN2, LEFT_EN], "right": [RIGHT_IN1, RIGHT_IN2, RIGHT_EN]}}


@app.get("/capabilities")
async def rest_capabilities():
    return {"tools": [t["name"] for t in TOOLS]}


# --------------------------------------------------------------------------- #
# MCP endpoint (JSON-RPC 2.0 over HTTP POST)
# --------------------------------------------------------------------------- #
TOOLS = [
    {"name": "rover_drive",
     "description": "Drive the rover with power for a short bounded time, then auto-stop. "
     "linear forward(+)/back(-) -1..1; angular left(+)/right(-) -1..1; seconds (auto-capped). "
     "Call repeatedly for continuous motion. If wheels stall against an obstacle it stops "
     "early and returns blocked=true — back off and turn instead of pushing.",
     "inputSchema": {"type": "object", "properties": {
         "linear": {"type": "number", "minimum": -1, "maximum": 1},
         "angular": {"type": "number", "minimum": -1, "maximum": 1},
         "seconds": {"type": "number", "minimum": 0, "maximum": MAX_DRIVE_SECONDS}}}},
    {"name": "rover_move",
     "description": "Drive straight an approximate distance in meters (positive=forward, negative=backward).",
     "inputSchema": {"type": "object", "properties": {
         "distance_m": {"type": "number"}, "power": {"type": "number"}}, "required": ["distance_m"]}},
    {"name": "rover_turn",
     "description": "Spin in place by approx degrees (positive=left/CCW, negative=right/CW).",
     "inputSchema": {"type": "object", "properties": {
         "angle_deg": {"type": "number"}, "power": {"type": "number"}}, "required": ["angle_deg"]}},
    {"name": "rover_stop", "description": "Stop all motion now.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "rover_estop", "description": "Emergency stop: halt and block motion until cleared.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "rover_get_state", "description": "Status: connected, e-stop, encoder counts.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "rover_get_imu", "description": "Read accelerometer (g x/y/z), if available.", "inputSchema": {"type": "object", "properties": {}}},
]

TOOL_INFO = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
             "serverInfo": {"name": "lexa-rover-control", "version": "0.2.0"}}


async def dispatch_tool(name: str, args: dict) -> Any:
    if name == "rover_drive":
        return await act_drive(float(args.get("linear", 0)), float(args.get("angular", 0)),
                               float(args.get("seconds", DEFAULT_DRIVE_SECONDS)))
    if name == "rover_move":
        return await act_move(float(args["distance_m"]), args.get("power"))
    if name == "rover_turn":
        return await act_turn(float(args["angle_deg"]), args.get("power"))
    if name == "rover_stop":
        return await act_stop()
    if name == "rover_estop":
        return await act_estop(clear=False)
    if name == "rover_get_state":
        return await read_state()
    if name == "rover_get_imu":
        return await read_imu()
    raise ValueError(f"unknown tool: {name}")


def _rpc(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return JSONResponse(content=msg)


@app.post("/mcp")
async def mcp_endpoint(body: dict):
    import json
    method = body.get("method")
    id_ = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        return _rpc(id_, TOOL_INFO)
    if method and method.startswith("notifications/"):
        return _rpc(id_, {})
    if method == "tools/list":
        return _rpc(id_, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = await dispatch_tool(name, args)
            is_error = isinstance(result, dict) and result.get("ok") is False
            return _rpc(id_, {"content": [{"type": "text", "text": json.dumps(result)}], "isError": is_error})
        except Exception as e:
            return _rpc(id_, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    return _rpc(id_, error={"code": -32601, "message": f"method not found: {method}"})


# --------------------------------------------------------------------------- #
# Web dashboard (rebranded vexa UI) + /api/* compatibility layer
# --------------------------------------------------------------------------- #
STATIC_INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"
MOTION_CFG = {"y_max": 12000, "x_max": 8000, "dpad_duration": 500}
LOGS: deque = deque(maxlen=80)


def add_log(level: str, component: str, message: str):
    LOGS.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level, "component": component, "message": message,
    })


@app.get("/")
async def dashboard():
    return FileResponse(str(STATIC_INDEX))


@app.get("/api/status")
async def api_status():
    # report connected so the dashboard is usable immediately (rover is local GPIO)
    return {"connected": "true", "device_name": "LEXA Rover", "device_address": "gpio-l298n"}


@app.get("/api/scan")
async def api_scan(timeout: float = 5):
    return {"devices": [{"address": "gpio-l298n", "name": "LEXA Rover"}]}


@app.post("/api/connect")
async def api_connect(body: dict):
    add_log("SUCCESS", "LINK", "Connected to LEXA Rover (GPIO)")
    return {"name": "LEXA Rover", "address": body.get("address", "gpio-l298n")}


@app.post("/api/disconnect")
async def api_disconnect():
    await teleop_stop()
    return {"ok": True}


# Voice agent lives in the separate Live client (client-video.py) — stub here.
@app.get("/api/agent/status")
async def api_agent_status():
    return {"status": "idle"}


@app.post("/api/agent/start")
async def api_agent_start(body: dict | None = None):
    return {"ok": True}


@app.post("/api/agent/stop")
async def api_agent_stop():
    return {"ok": True}


# No battery sensor on this build — return numeric placeholders so the UI renders.
@app.get("/api/battery")
async def api_battery():
    return {"battery_percent": 100.0, "voltage": 0.0, "raw_deci_volt": 0,
            "rx_decode_mode": "n/a", "battery_percent_source": "no-sensor"}


@app.get("/api/logs")
async def api_logs(limit: int = 15):
    return {"logs": list(LOGS)[-limit:]}


@app.get("/api/config/motion")
async def api_get_motion():
    return MOTION_CFG


@app.post("/api/config/motion")
async def api_set_motion(body: dict):
    for k in ("y_max", "x_max", "dpad_duration"):
        if k in body:
            MOTION_CFG[k] = body[k]
    return {"ok": True, **MOTION_CFG}


@app.post("/api/joystick")
async def api_joystick(body: dict):
    x = float(body.get("x", 0))
    y = float(body.get("y", 0))
    dur = float(body.get("duration", 0.5))
    linear = _clamp(y / 32767.0, -1.0, 1.0)
    angular = _clamp(-x / 32767.0, -1.0, 1.0)  # joystick right -> turn right
    try:
        await teleop_set(linear - angular, linear + angular, dur)
        return {"ok": True, "linear": round(linear, 3), "angular": round(angular, 3)}
    except Exception as e:
        return _err(e)


@app.post("/api/move")
async def api_move(body: dict):
    action = (body.get("action") or "stop").lower()
    dur = float(body.get("duration", MOTION_CFG["dpad_duration"] / 1000.0))
    fwd = _clamp(MOTION_CFG["y_max"] / 32767.0, 0.0, MAX_WHEEL_POWER)
    turn = _clamp(MOTION_CFG["x_max"] / 32767.0, 0.0, MAX_WHEEL_POWER)
    try:
        if action == "forward":
            await teleop_set(fwd, fwd, dur)
        elif action == "backward":
            await teleop_set(-fwd, -fwd, dur)
        elif action == "left":
            await teleop_set(-turn, turn, dur)
        elif action == "right":
            await teleop_set(turn, -turn, dur)
        else:
            await teleop_stop()
            return {"ok": True, "stopped": True}
        add_log("CMD", "MOVE", action)
        return {"ok": True, "action": action}
    except Exception as e:
        return _err(e)


@app.get("/video_feed")
async def video_feed():
    # The webcam is owned by the Live client (client-video.py); not served here.
    return JSONResponse(status_code=503, content={"detail": "camera served by the Live client"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
