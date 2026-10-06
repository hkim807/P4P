# Robot action script interface

`--physical-executor` runs validated `APPROACH` and `ENGAGE` commands through
five local scripts. It is opt-in. The robot client still collects sensors and
keeps the HTTP connection; the scripts own the physical action they report.
All five paths are required:

- `--approach-script`: perform one bounded approach and report success only
  after reaching the intended interaction range and stopping.
- `--engage-script`: perform one engagement and report success only after it
  finishes.
- `--pause-route-script`: confirm the fixed route has stopped before an action
  starts. It is called as soon as the server reports a logical target lock.
- `--resume-route-script`: resume the fixed route after the lock is released and
  no action is active.
- `--stop-script`: stop any movement or other ongoing action. It is called after
  every action, on cancellation, and on shutdown while the route is paused.

The executor starts each script as an isolated process group, with no shell.
Python `.py` files run with the client's Python interpreter; other files must
be executable. Each invocation receives **one JSON line on stdin**:

```json
{
  "interface_version": "navel-action-script-v1",
  "command": {
    "command_id": "session:lock:1:APPROACH:17:1",
    "action": "APPROACH",
    "lock_id": "session:lock:1",
    "target_uid": 17,
    "target_track_epoch": 1,
    "execution_lease_us": 2000000
  },
  "observation": {"timestamp": 123456789, "people": [], "robot": {}, "safety": {}},
  "reason": "EXECUTE"
}
```

The actual `command` is the full server command object, and `observation` is
the exact raw frame whose response authorized it. For route hooks, `command`
is `null`; on shutdown, `observation` can also be `null`. Scripts must accept
all valid values of `reason` as context and must not use the example frame as
a fixed schema. Each script must write **one JSON object** to stdout and exit
zero on confirmed success:

```json
{"ok": true}
```

Any nonzero exit, timeout, invalid output, or `{"ok": false}` is a failure.
Diagnostics go to stderr. Scripts must keep stdout free of other text. The
standard-library-only `robot.navel_client.script_contract` module provides
`read_request(expected_action="APPROACH")`, `report_success()`, and
`report_failure(reason)` for use inside action scripts. Use
`expected_action="ENGAGE"` in the engagement script; route and stop hooks can
call `read_request()` without an expected action. The helper validates the
request shape but does not control robot hardware.

The action script should validate its own live distance and obstacle data during
movement; the launch frame is only a snapshot. The public SDK range units are
not established in this codebase, so the executor does not infer a safe travel
distance from the raw range arrays. The stop script must issue a real hardware
stop and confirm it before returning `{"ok": true}`. Process termination by
itself is not a hardware stop.

## How the executor behaves

The robot validates the command against the current frame, policy decision,
logical lock, UID and track epoch, expiry, and session. It pauses the route
before launching a script, requires measured near-zero base velocity, and
requires valid front lidar and sonar readings for `APPROACH`. It compares no
range to an obstacle threshold because the range units are not established in
this codebase. It runs only one action at a time and records each
command ID once. It terminates the script process group and calls the stop
script after completion, failure, target loss, changed policy, HTTP failure,
response timeout, action lease expiry, or client shutdown. A failed stop or
route hook latches a fault and prevents further action or automatic route
resumption. A changed UID cannot carry a running action through tentative
reassociation; that action is cancelled. The server may issue a new action
after a later confirmed lock handoff.
If the route is paused without an active action, loss of valid server responses
also invokes the stop hook once and leaves the route paused.

The robot sends `RECEIVED`, `STARTED`, then `COMPLETED`, `FAILED`, or
`CANCELLED`; a command can be `REJECTED` before starting. The server releases
the logical lock into cooldown after a completed engagement or any failed,
cancelled, or rejected action. A completed approach keeps the lock, so the
policy can progress to `ENGAGE`. The route resumes only after a subsequent
valid server response reports `COOLDOWN` or `UNLOCKED` and the stop hook has
confirmed a stop. If feedback or server responses are unavailable, the route
remains paused.

## Configuration

Start the PC receiver with social processing and an execution trace:

```bash
python3 -m app.server --host 0.0.0.0 --port 6060 \
  --output var/recordings/physical-01.jsonl \
  --command-output var/recordings/physical-01.commands.jsonl \
  --execution-output var/recordings/physical-01.execution.jsonl \
  --command-config config/commands.json
```

Run the robot client with paths to the scripts you provide:

```bash
python3 -m robot.navel_client.main --server http://COMPUTER_LAN_IP:6060 \
  --physical-executor \
  --approach-script /absolute/path/approach.py \
  --engage-script /absolute/path/engage.py \
  --pause-route-script /absolute/path/pause_route.py \
  --resume-route-script /absolute/path/resume_route.py \
  --stop-script /absolute/path/stop.py
```

`--physical-executor` cannot be combined with print-only or either dry-run
mode. It can run with `--head-focus`; while an action is active, the local head
focus controller does not issue new focus commands. The scripts must coordinate
their use of the Navel SDK with the sensor collector's SDK connection and with
the route controller. The executor cannot verify hardware ownership from a
script's JSON acknowledgement.

The default command source age is 1 second and the action lease is 2 seconds.
Set `execution_lease_s` in a copy of `config/commands.json` to a measured,
bounded duration for your actions (maximum 120 seconds). The robot also
cancels an active action after 1 second without a valid server response by
default; `--response-timeout` tunes that interval. The script hook and stop
timeouts can be tuned with `--hook-timeout` and `--stop-timeout`.

The software tests use local stub scripts. Navel trials still need to verify
the actual stop effect, route pause and resume, obstacle handling, SDK access,
and the measured time each script requires.
