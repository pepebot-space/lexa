"""
Rover control service for lexa — one FastAPI app exposing TWO interfaces over
the same safety-checked core:

  1. A plain REST API   (POST /drive, /move, /turn, /stop, /estop; GET /state,
     /imu, /camera, /health, /capabilities) — for any client / the future
     autonomous agent.
  2. An MCP endpoint     (POST /mcp, JSON-RPC 2.0) — the tool surface the Pepebot
     Live LLM calls. Implements `initialize`, `tools/list`, `tools/call` exactly
     as the pepebot MCP HTTP client expects (plain JSON response, not SSE).

All motion goes through clamps + an e-stop gate + bounded duration, so an LLM
can never command unbounded movement.

Run:  uvicorn rover_service:app --host 0.0.0.0 --port 9000
Config: see rover/.env.example (Viam address/keys, component names, limits).
"""
import asyncio
import base64
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Viam SDK (installed in the venv; see setup-rover.sh)
from viam.robot.client import RobotClient
from viam.components.base import Base, Vector3
from viam.components.movement_sensor import MovementSensor
from viam.components.camera import Camera

# --------------------------------------------------------------------------- #
# Configuration (all via environment — see rover/.env.example)
# --------------------------------------------------------------------------- #
VIAM_ADDRESS = os.environ.get("VIAM_ADDRESS", "localhost:8080")
VIAM_API_KEY = os.environ.get("VIAM_API_KEY", "")
VIAM_API_KEY_ID = os.environ.get("VIAM_API_KEY_ID", "")

BASE_NAME = os.environ.get("VIAM_BASE", "viam_base")
IMU_NAME = os.environ.get("VIAM_MOVEMENT_SENSOR", "accelerometer")
CAMERA_NAME = os.environ.get("VIAM_CAMERA", "cam")

# Safety limits (clamped server-side; the LLM cannot exceed these)
MAX_LINEAR_POWER = float(os.environ.get("MAX_LINEAR_POWER", "0.6"))   # 0..1
MAX_ANGULAR_POWER = float(os.environ.get("MAX_ANGULAR_POWER", "0.6"))  # 0..1
DEFAULT_DRIVE_SECONDS = float(os.environ.get("DEFAULT_DRIVE_SECONDS", "1.0"))
MAX_DRIVE_SECONDS = float(os.environ.get("MAX_DRIVE_SECONDS", "3.0"))
MAX_MOVE_M = float(os.environ.get("MAX_MOVE_M", "3.0"))
MAX_SPEED_MPS = float(os.environ.get("MAX_SPEED_MPS", "0.5"))
DEFAULT_SPEED_MPS = float(os.environ.get("DEFAULT_SPEED_MPS", "0.3"))
MAX_TURN_DEG = float(os.environ.get("MAX_TURN_DEG", "360"))
DEFAULT_TURN_DPS = float(os.environ.get("DEFAULT_TURN_DPS", "45"))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# Shared rover state
# --------------------------------------------------------------------------- #
class Rover:
    def __init__(self):
        self.machine: Optional[RobotClient] = None
        self.base: Optional[Base] = None
        self.imu: Optional[MovementSensor] = None
        self.camera: Optional[Camera] = None
        self.estopped = False
        self._motion_lock = asyncio.Lock()
        self.last_error: Optional[str] = None

    async def connect(self):
        if not VIAM_API_KEY or not VIAM_API_KEY_ID:
            self.last_error = "VIAM_API_KEY / VIAM_API_KEY_ID not set"
            return
        try:
            opts = RobotClient.Options.with_api_key(
                api_key=VIAM_API_KEY, api_key_id=VIAM_API_KEY_ID
            )
            self.machine = await RobotClient.at_address(VIAM_ADDRESS, opts)
            self.base = Base.from_robot(self.machine, BASE_NAME)
            try:
                self.imu = MovementSensor.from_robot(self.machine, IMU_NAME)
            except Exception:
                self.imu = None
            try:
                self.camera = Camera.from_robot(self.machine, CAMERA_NAME)
            except Exception:
                self.camera = None
            self.last_error = None
        except Exception as e:  # keep the service up; report via /health
            self.last_error = f"connect failed: {e}"

    async def close(self):
        try:
            if self.base:
                await self.base.stop()
        except Exception:
            pass
        if self.machine:
            await self.machine.close()

    def require_base(self):
        if self.base is None:
            raise RuntimeError(f"not connected to Viam base ({self.last_error or 'unknown'})")
        if self.estopped:
            raise RuntimeError("E-STOP engaged — call /estop/clear before moving")


rover = Rover()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await rover.connect()
    yield
    await rover.close()


app = FastAPI(title="lexa rover control", version="0.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Core actions (used by BOTH the REST routes and the MCP tools)
# --------------------------------------------------------------------------- #
async def act_drive(linear: float, angular: float, seconds: float) -> dict:
    """Power-based drive for a bounded duration, then auto-stop. linear/angular
    are -1..1 (forward+/back-, left+/right-). Blocks for `seconds` then stops."""
    rover.require_base()
    linear = _clamp(linear, -MAX_LINEAR_POWER, MAX_LINEAR_POWER)
    angular = _clamp(angular, -MAX_ANGULAR_POWER, MAX_ANGULAR_POWER)
    seconds = _clamp(seconds, 0.0, MAX_DRIVE_SECONDS)
    async with rover._motion_lock:
        await rover.base.set_power(Vector3(x=0, y=linear, z=0), Vector3(x=0, y=0, z=angular))
        await asyncio.sleep(seconds)
        await rover.base.stop()
    return {"ok": True, "linear": linear, "angular": angular, "seconds": seconds}


async def act_move(distance_m: float, speed_mps: Optional[float]) -> dict:
    """Drive straight a bounded distance (+forward / -backward)."""
    rover.require_base()
    distance_m = _clamp(distance_m, -MAX_MOVE_M, MAX_MOVE_M)
    speed = _clamp(speed_mps if speed_mps else DEFAULT_SPEED_MPS, 0.05, MAX_SPEED_MPS)
    async with rover._motion_lock:
        await rover.base.move_straight(
            distance=int(distance_m * 1000), velocity=int(speed * 1000)
        )
    return {"ok": True, "distance_m": distance_m, "speed_mps": speed}


async def act_turn(angle_deg: float, speed_dps: Optional[float]) -> dict:
    """Spin in place a bounded angle (+left / -right)."""
    rover.require_base()
    angle_deg = _clamp(angle_deg, -MAX_TURN_DEG, MAX_TURN_DEG)
    speed = _clamp(speed_dps if speed_dps else DEFAULT_TURN_DPS, 5.0, 180.0)
    async with rover._motion_lock:
        await rover.base.spin(angle=angle_deg, velocity=speed)
    return {"ok": True, "angle_deg": angle_deg, "speed_dps": speed}


async def act_stop() -> dict:
    if rover.base:
        await rover.base.stop()
    return {"ok": True, "stopped": True}


async def act_estop(clear: bool = False) -> dict:
    rover.estopped = not clear
    if rover.base and not clear:
        await rover.base.stop()
    return {"ok": True, "estopped": rover.estopped}


async def read_state() -> dict:
    state: dict[str, Any] = {"estopped": rover.estopped, "connected": rover.base is not None}
    if rover.base:
        try:
            state["is_moving"] = await rover.base.is_moving()
        except Exception as e:
            state["is_moving_error"] = str(e)
    return state


async def read_imu() -> dict:
    if rover.imu is None:
        return {"available": False, "reason": "no movement_sensor configured"}
    out: dict[str, Any] = {"available": True}
    try:
        acc = await rover.imu.get_linear_acceleration()
        out["linear_acceleration"] = {"x": acc.x, "y": acc.y, "z": acc.z}
    except Exception as e:
        out["error"] = str(e)
    return out


async def read_camera() -> dict:
    if rover.camera is None:
        return {"available": False, "reason": "no camera configured"}
    try:
        img = await rover.camera.get_image(mime_type="image/jpeg")
        raw = img.data if hasattr(img, "data") else bytes(img)
        return {"available": True, "mime": "image/jpeg", "base64": base64.b64encode(raw).decode()}
    except Exception as e:
        return {"available": True, "error": str(e)}


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #
class DriveReq(BaseModel):
    linear: float = Field(0.0, description="forward(+)/back(-) power -1..1")
    angular: float = Field(0.0, description="left(+)/right(-) power -1..1")
    seconds: float = Field(DEFAULT_DRIVE_SECONDS, description="duration; auto-stop after")


class MoveReq(BaseModel):
    distance_m: float
    speed_mps: Optional[float] = None


class TurnReq(BaseModel):
    angle_deg: float
    speed_dps: Optional[float] = None


def _err(e: Exception, code: int = 503):
    return JSONResponse(status_code=code, content={"ok": False, "error": str(e)})


@app.post("/drive")
async def rest_drive(r: DriveReq):
    try:
        return await act_drive(r.linear, r.angular, r.seconds)
    except Exception as e:
        return _err(e, 423 if "E-STOP" in str(e) else 503)


@app.post("/move")
async def rest_move(r: MoveReq):
    try:
        return await act_move(r.distance_m, r.speed_mps)
    except Exception as e:
        return _err(e, 423 if "E-STOP" in str(e) else 503)


@app.post("/turn")
async def rest_turn(r: TurnReq):
    try:
        return await act_turn(r.angle_deg, r.speed_dps)
    except Exception as e:
        return _err(e, 423 if "E-STOP" in str(e) else 503)


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


@app.get("/camera")
async def rest_camera():
    return await read_camera()


@app.get("/health")
async def rest_health():
    return {
        "ok": rover.base is not None,
        "connected": rover.base is not None,
        "estopped": rover.estopped,
        "error": rover.last_error,
        "components": {"base": BASE_NAME, "imu": IMU_NAME, "camera": CAMERA_NAME},
    }


@app.get("/capabilities")
async def rest_capabilities():
    return {"tools": [t["name"] for t in TOOLS]}


# --------------------------------------------------------------------------- #
# MCP endpoint (JSON-RPC 2.0 over HTTP POST) — pepebot tool surface
# --------------------------------------------------------------------------- #
TOOLS = [
    {
        "name": "rover_drive",
        "description": "Drive the rover using power for a short bounded time, then auto-stop. "
        "linear: forward(+)/back(-) -1..1; angular: turn left(+)/right(-) -1..1; "
        "seconds: how long (auto-capped). Call repeatedly for continuous motion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "linear": {"type": "number", "minimum": -1, "maximum": 1},
                "angular": {"type": "number", "minimum": -1, "maximum": 1},
                "seconds": {"type": "number", "minimum": 0, "maximum": MAX_DRIVE_SECONDS},
            },
        },
    },
    {
        "name": "rover_move",
        "description": "Drive straight a precise distance in meters (positive=forward, negative=backward).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "distance_m": {"type": "number"},
                "speed_mps": {"type": "number"},
            },
            "required": ["distance_m"],
        },
    },
    {
        "name": "rover_turn",
        "description": "Spin in place by an angle in degrees (positive=left/CCW, negative=right/CW).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "angle_deg": {"type": "number"},
                "speed_dps": {"type": "number"},
            },
            "required": ["angle_deg"],
        },
    },
    {"name": "rover_stop", "description": "Stop all motion immediately.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "rover_estop", "description": "Emergency stop: halt and block further motion until cleared.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "rover_get_state", "description": "Get rover status (connected, moving, e-stop).", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "rover_get_imu", "description": "Read the accelerometer (linear acceleration x/y/z).", "inputSchema": {"type": "object", "properties": {}}},
]

TOOL_INFO = {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "lexa-rover-control", "version": "0.1.0"},
}


async def dispatch_tool(name: str, args: dict) -> Any:
    if name == "rover_drive":
        return await act_drive(
            float(args.get("linear", 0)), float(args.get("angular", 0)),
            float(args.get("seconds", DEFAULT_DRIVE_SECONDS)),
        )
    if name == "rover_move":
        return await act_move(float(args["distance_m"]), args.get("speed_mps"))
    if name == "rover_turn":
        return await act_turn(float(args["angle_deg"]), args.get("speed_dps"))
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
            text = json.dumps(result)
            is_error = isinstance(result, dict) and result.get("ok") is False
            return _rpc(id_, {"content": [{"type": "text", "text": text}], "isError": is_error})
        except Exception as e:
            return _rpc(id_, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})

    return _rpc(id_, error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("ROVER_PORT", "9000")))
