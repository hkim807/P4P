# Navel raw sensor HTTP stream

This branch extracts the read-only Navel sensor collector and HTTP transport
from `main` at `af211ba`. It contains a robot client and a small computer-side
receiver. The client reads SDK perception and locomotion packets, builds one
`RawObservationFrame`, and sends it using HTTP POST. The receiver validates each
frame and appends it to a JSONL file.

The LLM/Ollama integration, social-state estimation, decision scheduling,
behavior handlers, replay recordings, and React monitor belong to the original
pipeline on `main`; they are removed from this extraction. There are no robot
motion, speech, configuration, or actuator calls in the client.

## Data flow

```text
Navel robot                             Computer on the same network
  next_frame() -> people           ┐
                                  ├ -> RawObservationFrame
  next_locomotion() -> velocities  │       -> HTTP POST /api/v1/observations
                      ranges      ┘           -> validation -> JSONL
```

Perception and locomotion are collected concurrently. Each perception packet
produces a frame using the latest locomotion packet. Only the newest pending
frame is retained. HTTP runs in a worker thread and defaults to at most 10 POST
starts per second. Missing, invalid, or stale measurements are represented as
`null`; they are never replaced with an assumed zero.

## RawObservationFrame

```json
{
  "timestamp": 1234567890,
  "people": [
    {
      "uid": 17,
      "distance_m": 1.85,
      "gaze_overlap": 0.75,
      "optional_relative_head_position": {
        "coordinate_frame": "CAM_HEAD",
        "x": 1.8,
        "y": 0.3,
        "z": 0.1
      }
    }
  ],
  "robot": {
    "linear_velocity": 0.2,
    "angular_velocity": -0.1
  },
  "safety": {
    "lidar": [2.0, 1.0],
    "sonar": [0.5, 0.7, 1.2]
  }
}
```

- `timestamp`: robot-host monotonic time in microseconds when perception is
  collected. This is not UTC and cannot be compared across robot reboots or hosts.
- `uid`: nonnegative integer SDK person ID. Duplicate or invalid IDs are skipped.
- `distance_m`: SDK `dist_mm` divided by 1000, or `null`.
- `gaze_overlap`: measured SDK score between 0 and 1, or `null`.
- `optional_relative_head_position`: omitted when unavailable; otherwise the
  Cartesian `g_head_position` entry in `CAM_HEAD`, in metres. It is relative to
  the head camera, with the coordinate label retained.
- `linear_velocity`: signed forward `odometry.velocity.linear_x` in m/s, or `null`.
- `angular_velocity`: `odometry.velocity.angular_z` in rad/s, or `null`.
- `lidar`: SDK range list, ordered front, back.
- `sonar`: SDK range list, ordered front-right, front-left, back.

Range values retain SDK native units. The public SDK data reference does not
specify their units; this extraction applies no conversion and does not label
these fields as metres. An unavailable sensor list is `null`; an invalid reading
inside a list is `null` in its original slot. See the
[contract and mapping document](docs/raw-observation-frame.md) for details and
SDK references. The machine-readable contract is
[schemas/v1/raw-observation-frame.schema.json](schemas/v1/raw-observation-frame.schema.json).

## Run the receiver on the computer

Use Python 3.10 or newer. From this branch's repository root:

```bash
git switch feature/navel-raw-http-stream
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m app.server --host 0.0.0.0 --port 6060
```

In another terminal:

```bash
curl http://127.0.0.1:6060/health
```

The receiver returns `{"status":"ok","service":"navel-raw-sensor-receiver"}`.
The receiver prints the recording path at startup and creates a timestamped
JSONL file under `var/recordings/` when the first frame arrives. To choose a
name, add `--output var/recordings/pilot-01.jsonl`. That path must be new;
existing files are never overwritten or appended across receiver runs.
To print frames instead, use `--output -`. The receiver requires Flask and
Pydantic, and no model runtime or robot SDK.

## View and replay recordings

Each JSONL line contains one complete raw frame. Use `tail -f` while recording
or `cat` after stopping the receiver:

```bash
tail -f var/recordings/pilot-01.jsonl
cat var/recordings/pilot-01.jsonl
```

Replay on the PC without Navel or a tunnel:

```bash
python3 -m app.replay var/recordings/pilot-01.jsonl
python3 -m app.replay var/recordings/pilot-01.jsonl --speed 2
python3 -m app.replay var/recordings/pilot-01.jsonl --speed 0 --pretty
```

Default playback preserves recorded timestamp gaps and prints JSONL. `--speed 2`
plays twice as fast; `--speed 0` prints immediately; `--pretty` uses indented JSON.
Original robot timestamps are preserved. No network calls occur unless `--server`
is supplied. See [the recording/replay guide](docs/recording-and-replay.md) for
HTTP replay and pilot recording instructions.

## Track people over time

The next PC layer maintains bounded raw histories per session/UID/track epoch.
Replay a recording into track snapshots without Navel:

```bash
python3 -m app.track var/recordings/03_stationary_gaze.jsonl --config config/person-tracking.json
```

Each stdout line is a snapshot with retained samples, visibility, and lifecycle
events; stderr prints a completion summary. Add `--speed 1` for original pacing
or `--output /tmp/navel-tracks.jsonl` to create a new trace. For live tracking,
add `--tracking-output <new-trace.jsonl>` to the receiver. Raw recording and
derived tracking stay in separate files; there are no social decisions or actions.

See [person tracking: design, research rationale, and usage](docs/person-tracking.md)
and [validation on recordings 01-07](docs/results/person-tracking/report.md).

## Estimate temporal social cues

Transform recordings into SocialState with gaze patterns, distance zones,
relative-distance trends, and measured evidence:

```bash
python3 -m app.social var/recordings/03_stationary_gaze.jsonl --config config/temporal-state.json
python3 -m app.validate_social var/recordings/0[1-7]_*.jsonl --output-dir var/temporal-validation/run-01
```

Outputs are separate from raw recordings. Thresholds are provisional; missing
evidence is UNKNOWN, and human motion requires valid near-zero robot velocities
throughout the fitted distance interval. Add `--social-output <new-social.jsonl>`
to the receiver to enable the same layer live. `--tracking-output` alone continues
to produce UID histories.
See [temporal design and commands](docs/temporal-social-state.md) and
[validation results](docs/results/temporal-state/report.md), including controlled
transformations of a recorded frame into changing cue patterns.

## Inspect rule decisions

With `--social-output` enabled, each accepted HTTP response also includes a
`policy_decision`: `CONTINUE`, `APPROACH`, `ENGAGE`, or `DEFER`, with its reason and
source state ID. The current cues cannot establish a route conflict, so the
policy does not emit `YIELD`. These are proposals, not robot commands.

Apply the same rules to an existing SocialState trace:

```bash
python3 -m app.decide var/temporal-validation/01-velocity-social.jsonl \
  --output var/temporal-validation/01-decisions.jsonl
```

See [rule logic and limits](docs/social-policy.md). The decision output path
must be new.

To receive and log live decisions on Navel without robot actions, start the
receiver with `--social-output` and add `--decision-dry-run` to the robot client.
See the [robot decision dry-run guide](docs/robot-decision-dry-run.md).

## Synthetic replay example

A three-frame synthetic example is included for a quick check:

```bash
python3 -m app.replay recordings/examples/sample.jsonl --speed 0 --pretty
```

## Run the collector on Navel

Use the same branch on the robot and its existing Navel SDK installation.
The robot client needs only that SDK and the Python standard library. Do not
install the computer server's requirements on Navel.

First inspect sensor frames locally:

```bash
python3 -m robot.navel_client.main --print-only
```

Then stream to the computer, replacing the example address with its actual LAN IP:

```bash
python3 -m robot.navel_client.main --server http://192.168.1.100:6060
```

After each accepted POST, the client prints the full `RawObservationFrame` as
indented JSON to stdout. Request acknowledgements and errors go to stderr.
`--print-only` continues to print one compact JSON frame per line without HTTP.

The robot must use the computer's network address; `127.0.0.1` would point back to
Navel. Both machines need a network route to each other, and the computer must
allow incoming TCP connections on port 6060. Stop the client with Ctrl-C.
Detailed controls and troubleshooting are in [docs/runbook.md](docs/runbook.md).

If direct HTTP access fails but the computer can SSH into Navel, use the
[reverse SSH tunnel setup](docs/runbook.md#connection-through-a-reverse-ssh-tunnel).
That setup forwards Navel's `127.0.0.1:16060` to the computer's
`127.0.0.1:6060`; pass `--server http://127.0.0.1:16060` on the robot.

## HTTP API

`GET /health` returns HTTP 200 when the receiver is running. It does not probe
robot connectivity or disk availability.

`POST /api/v1/observations` accepts one JSON `RawObservationFrame`, validates it,
and writes it before returning:

```json
{"accepted": true, "timestamp": 1234567890, "people_count": 1}
```

Invalid frames or malformed JSON return HTTP 400. A non-JSON content type returns
415, a body larger than 1 MiB returns 413, and a storage failure returns 503.
Duplicate or backward timestamps within a recording return HTTP 409 and are not
written. Start a new receiver/recording if the robot's monotonic clock restarts.
Errors contain `accepted: false`. This endpoint accepts only the new raw format;
it is incompatible with the full pipeline's previous `ObservationFrame` contract.
It returns acknowledgements, without behavior commands.

## Repository layout

```text
app/
  domain/models.py     Server-side RawObservationFrame validation
  domain/schema.py     JSON Schema generator
  server.py            HTTP receiver
  recording.py         Ordered JSONL writer and validated streaming reader
  replay.py            Local playback and optional HTTP replay CLI
  state/tracks.py      Bounded UID histories and visibility lifecycle
  pipeline.py          Shared offline/live tracking and serialized persistence
  track.py             Track-snapshot replay CLI
  validate_tracking.py Pilot-recording audit and reproducible reports
  state/features.py    Gaze coverage and robust distance measurements
  state/estimator.py   Categorical SocialState and cue changes
  social_pipeline.py  Shared live/replay temporal processing
  social.py           SocialState replay CLI
  validate_social.py  Recorded/synthetic temporal validation reports
config/                Person tracking development settings
robot/navel_client/
  adapter.py           SDK packet -> raw frame mapping
  main.py              Async sensor collectors and streaming loop
  transport.py         Standard-library HTTP POST transport
robot/tests/           Standalone live SDK diagnostics to run on Navel
recordings/examples/   Small synthetic recording for trying replay
schemas/v1/            Public raw-frame JSON Schema
docs/                  Sensor mapping, architecture, and operating instructions
tests/                 Offline mapping, validation, streaming, and real HTTP tests
requirements.txt       Computer-side dependencies
requirements-navel.txt Robot-side dependency boundary
var/recordings/         Received JSONL files, including supplied pilot recordings
var/tracking-validation/ Generated tracking traces (ignored by Git)
var/temporal-validation/ Generated social states and synthetic validation inputs
```

## Verification

The next-stage build is described in the
[PC social-state and execution implementation plan](docs/pc-social-state-implementation-plan.md).
It stages UID histories, temporal features, SocialState, a rule policy, command
delivery, and robot execution as separate milestones.

To inspect angular velocity directly on Navel, run:

```bash
python3 robot/tests/stream_angular_velocity.py --duration 30
```

This prints raw SDK velocity fields and a sample summary without HTTP or the
frame adapter. See [robot/tests/README.md](robot/tests/README.md) for interpreting
results and capturing a log.

With the computer dependencies installed:

```bash
python3 -m unittest discover -s tests -v
python3 -m app.domain.schema
```

Tests use SDK-shaped fixtures and a local HTTP server. They cover the full
collector-to-receiver path without Navel hardware. Actual socket access, sensor
availability, and native lidar/sonar units still need checking on the robot.
[docs/architecture.md](docs/architecture.md) records extraction scope and delivery
semantics.
