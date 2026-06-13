---
name: rover-control
description: Drive and sense the lexa Viam rover. Use when the user asks the robot to move, turn, stop, explore, approach something, or report what it senses (motion, tilt, acceleration). Exposes rover_drive / rover_move / rover_turn / rover_stop / rover_estop / rover_get_state / rover_get_imu tools.
always: false
mcp:
  - name: rover-control
    transport: http
    url: http://192.168.100.212:9000/mcp
---

# Rover control

You can drive a small differential-drive rover (Viam Rover) and read its
sensors. You also see the rover's webcam as live video, so use what you see to
decide how to move.

## Tools

- `rover_drive(linear, angular, seconds)` — power-based, bounded. `linear`
  forward(+)/back(-) in -1..1; `angular` left(+)/right(-) in -1..1; `seconds`
  is how long before it auto-stops. **For continuous motion, call repeatedly** —
  each call moves for at most a few seconds then stops on its own.
- `rover_move(distance_m, speed_mps?)` — drive straight a precise distance
  (positive forward, negative backward).
- `rover_turn(angle_deg, speed_dps?)` — spin in place (positive left/CCW,
  negative right/CW). Use ~90 for a quarter turn.
- `rover_stop()` — stop now.
- `rover_estop()` — emergency stop; blocks motion until the user asks to resume.
- `rover_get_state()` — connected / moving / e-stop status.
- `rover_get_imu()` — accelerometer (detect tilt, bumps, whether actually moving).

## How to drive well

- **Move in short steps and look.** Prefer small `rover_drive` bursts (0.3–0.4
  power, ~1 s) or modest `rover_move` (≤0.5 m), then check the camera before
  the next step. Don't issue one long command.
- **Confirm before big or risky moves.** If the path looks blocked or unclear,
  stop and ask, or turn to look around first.
- **Turning:** use `rover_turn` for precise angles; use `rover_drive` with
  angular for gentle steering while moving.
- **If anything seems wrong** (about to hit something, tilting, user says stop),
  call `rover_stop` immediately (or `rover_estop`).
- The server clamps speed/duration for safety, so commands are always bounded —
  but still drive conservatively and narrate what you're doing.

## Examples

- "maju pelan" → `rover_drive(linear=0.35, angular=0, seconds=1)`, then look.
- "belok kanan" → `rover_turn(angle_deg=-90)`.
- "maju setengah meter" → `rover_move(distance_m=0.5)`.
- "berhenti" → `rover_stop()`.
- "kamu lagi gerak nggak?" → `rover_get_state()`.
