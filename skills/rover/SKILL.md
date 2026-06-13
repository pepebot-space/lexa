---
name: rover-control
description: Drive and sense the lexa Viam rover, and run autonomous missions. Use when the user asks the robot to move/turn/stop, OR gives a goal like "explore the room", "find and approach X", "follow me", "go to the door". Exposes rover_drive / rover_move / rover_turn / rover_stop / rover_estop / rover_get_state / rover_get_imu.
always: false
mcp:
  - name: rover-control
    transport: http
    url: http://192.168.100.212:9000/mcp
---

# Rover control

You can drive a small differential-drive rover and read its sensors. You also
see the rover's webcam as live video — **use what you see to decide how to move.**

## Tools

- `rover_drive(linear, angular, seconds)` — power-based, bounded. `linear`
  forward(+)/back(-) -1..1; `angular` left(+)/right(-) -1..1; `seconds` until
  auto-stop. Returns `blocked:true` if the wheels stalled against an obstacle.
  **For continuous motion, call repeatedly.**
- `rover_move(distance_m, power?)` — drive straight an (approximate) distance.
- `rover_turn(angle_deg, power?)` — spin in place (+left/CCW, -right/CW; ~90 = quarter turn).
- `rover_stop()` — stop now. `rover_estop()` — emergency stop (blocks until cleared).
- `rover_get_state()` — connected / e-stop / encoder counts.
- `rover_get_imu()` — accelerometer g (x/y/z); detect tilt/bumps/whether moving.

## Driving well (single commands)

- Move in **short steps and look**: small `rover_drive` bursts (~0.3–0.4 power,
  ~1 s) or modest `rover_move` (≤0.5 m), then check the camera before the next.
- Use `rover_turn` for precise turns; `rover_drive` angular for gentle steering.
- If a command returns `blocked:true`, you hit something — **don't push**; stop,
  back up a little, turn, and find a clear path.
- Drive conservatively (power ≤ ~0.4). The server bounds every command anyway.

## Autonomous missions (goals)

When the user gives a **goal** instead of one command (e.g. "jelajahi ruangan",
"cari botol lalu dekati", "ikuti aku", "ke pintu"), pursue it yourself with a
**perceive → act → perceive loop**:

1. Acknowledge the goal briefly, then act in small steps:
   - Look at the current camera view.
   - Choose ONE small next action (a short `rover_drive` burst, or a `rover_turn`).
   - **Look again** at the new view and check the result (`blocked`?).
   - Repeat toward the goal. Keep steps small so you can react to what you see —
     never one long drive.
2. **Obstacles:** if `blocked:true` or something is close ahead → stop, back up a
   little, `rover_turn` to a clear direction, then continue. Favor turning to
   inspect over charging forward.
3. **Mission patterns:**
   - *Explore/wander* — go forward in clear space; turn away from obstacles; cover the area.
   - *Find & approach X* — turn to scan; when X is in view, center on it, drive toward it, stop ~0.5 m away.
   - *Follow* — keep the target centered and at a steady distance; turn to track, drive to maintain distance.
   - *Go to <place>* — head toward it using visual landmarks.
4. **Stop when:** goal reached, blocked with no clear path (say so / ask), or the
   user says stop/"berhenti" — always obey immediately with `rover_stop`.
5. **Narrate** concisely each step: what you see + what you're doing
   ("ada meja di depan, aku belok kiri lalu maju").

## Examples

- "maju pelan" → `rover_drive(linear=0.35, angular=0, seconds=1)`, then look.
- "belok kanan" → `rover_turn(angle_deg=-90)`. "berhenti" → `rover_stop()`.
- "jelajahi ruangan" → loop: look → `rover_drive(0.35,0,1)` in clear space →
  look → on obstacle `rover_turn` to clear side → continue; narrate; stop on request.
- "cari & dekati botol" → `rover_turn` to scan → spot botol → center → drive toward → stop near it.
