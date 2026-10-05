# Sensor-stream extraction

Branch: `feature/navel-raw-http-stream`. Starting point: `main` commit
`af211ba` (recorder/replayer merge). This branch now includes the raw Navel
stream, UID tracking, temporal SocialState, pure rules, and a conservative
logical target lock, a command protocol, and an opt-in physical script executor.

## Components

1. `robot/navel_client/main.py` opens `navel.Robot()`. One async task awaits
   `next_locomotion(timeout=1.0)`; another awaits `next_frame(timeout=1.0)`.
   With `--head-focus`, the perception collector also invokes
   `look_at_person` before adapting or queuing an eligible frame.
2. `adapter.py` copies selected measured fields into plain JSON-compatible
   dictionaries. It uses neither the SDK nor any computer-side package at import.
3. A queue with capacity one holds the newest pending perception frame. Replacing
   it discards older unsent observations.
4. A third task rate-limits sends and calls `ObservationTransport.send` through
   `asyncio.to_thread`. Collection continues while blocking HTTP waits.
5. `transport.py` sends JSON to `POST /api/v1/observations` with a timeout. It
   returns HTTP status and a JSON acknowledgement. Optional dry-run or physical
   executors inspect policy responses; local head focus starts before HTTP.
6. `app/server.py` validates `RawObservationFrame`; `app/recording.py` checks
   timestamp order and writes one JSON line under a thread lock.
   Acknowledgement follows a successful write/close.
   The lock supports threads in one process; use one receiver process per output
   file. Closing a write is not an explicit fsync guarantee against power loss.
7. `app/replay.py` reads/validates JSONL lazily and replays using timestamp gaps.
   It prints frames locally by default; optional `--server` sends them by HTTP.
8. With social processing enabled, `app/social_pipeline.py` serializes tracking,
   SocialState, pure rules, target lock, and command proposal. The response
   carries the pure `policy_decision`, `target_lock.effective_decision`, and an
   eligible `robot_command`. The robot's optional executors validate these.
   Validated lock responses also pin
   provisional head acquisition to the server's selected UID while current.

## Delivery semantics

This is a live stream rather than a lossless recorder of every SDK packet.
Rate limiting, queue replacement, and temporary network failures can drop frames.
A frame already in flight cannot be replaced. At most one pending frame is kept;
the next send uses the newest one available after waiting for the rate limit.

SDK receive timeouts are retried. Observation HTTP failures are reported; the
failed frame is dropped and the next available frame is attempted. Physical
execution feedback is queued in order and retried through transient HTTP
failures while the client remains running. The receiver rejects duplicate/backward timestamps
within a recording. A lost acknowledgement can occur after the server has
already written the frame.

A non-timeout SDK error terminates collection and cancels the sibling tasks,
with the exception visible to the operator. Ctrl-C cancels tasks and closes the
SDK context. An in-progress HTTP worker may finish during shutdown and remains
bounded by its request timeout.

The file has no source identifier because the requested frame contains none.
Use one receiver/output file per robot when records must remain attributable.
The receiver's `/health` endpoint reports service liveness only.

## Current boundary

- Replaced `ObservationFrame` with `RawObservationFrame`.
- Retained person IDs, distances, gaze overlap, optional measured head position,
  and forward/yaw velocities; added lidar/sonar range readings.
- The wire format contains no task/controller assumptions, images, capability
  metadata, or inferred social features. Tracking, SocialState, rules, and the
  target lock are derived on the computer from the raw frames.
- The physical executor runs configured approach/engage and route/stop scripts,
  enforces command expiry and lease, and reports actual outcomes. Robot action
  scripts and hardware verification must still be supplied.
- The robot still needs only its supplied Navel SDK plus the standard library.

The receiver is a simple Flask development service for the computer/robot LAN
workflow. It has no authentication layer. Its output defaults to the ignored
`var/recordings/<UTC-start-time>.jsonl` file and grows while observations arrive.
Existing recordings cannot be reused as output. For this lightweight stage,
one file/receiver run is one recording session for one robot. Explicit robot
session headers and per-frame envelopes are deferred until stateful processing.

## Verification boundary

Offline tests exercise sensor mapping, strict server validation, ordered JSONL,
tracking, temporal states, pure rules, lock transitions, replay/live parity,
head focus with a fake SDK, HTTP failures, and the concurrent collector through
a real local receiver. Dependency-isolation tests block Navel and server
packages while importing the robot client.

These checks cannot establish physical socket access, range units, sensor
accuracy, or availability on a specific Navel. Verify those on hardware with
`--print-only` before the LAN streaming check.
