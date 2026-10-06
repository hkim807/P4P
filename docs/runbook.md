# Running and checking the Navel sensor stream

## Computer

Check out `feature/navel-raw-http-stream` and use Python 3.10 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m app.server --host 0.0.0.0 --port 6060
```

The receiver prints a new timestamped output path under `var/recordings/`.
To choose a name, add `--output var/recordings/pilot-01.jsonl`; the file must not
already exist. Each receiver run is a separate recording for one robot.
`--output -` writes frames to stdout instead; Flask request logs go to stderr.

Confirm liveness locally and from Navel:

```bash
curl http://127.0.0.1:6060/health
# On Navel, substitute the computer's actual LAN address:
curl http://192.168.1.100:6060/health
```

To test the receiver without the SDK:

```bash
curl -X POST http://127.0.0.1:6060/api/v1/observations \
  -H 'Content-Type: application/json' \
  -d '{"timestamp":1000000,"people":[],"robot":{"linear_velocity":null,"angular_velocity":null},"safety":{"lidar":null,"sonar":null}}'
```

The response should contain `accepted: true`, and the file should contain the
submitted frame. This checks HTTP/storage only, not robot sensor access.

## Navel

### Connection through a reverse SSH tunnel

If Navel cannot reach the computer's HTTP port directly, but the computer can
SSH into Navel, start the receiver on the computer's loopback interface:

```bash
python3 -m app.server --host 127.0.0.1 --port 6060
```

In a second terminal on the computer, verify the receiver first:

```bash
curl --noproxy '*' --connect-timeout 5 http://127.0.0.1:6060/health
```

If that fails, fix the receiver before setting up the tunnel. Then, still on the
computer, run the following, replacing NAVEL_USER and NAVEL_IP with the same
login details used for an ordinary SSH connection to the robot:

```bash
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:16060:127.0.0.1:6060 NAVEL_USER@NAVEL_IP
```

Leave this terminal running. In another robot shell, test and start the client:

```bash
curl --noproxy '*' --connect-timeout 5 http://127.0.0.1:16060/health
python3 -m robot.navel_client.main --server http://127.0.0.1:16060
```

The path is `Navel localhost:16060 -> SSH -> computer localhost:6060`.
With this tunnel, the client deliberately uses Navel's localhost address.
An ordinary SSH login alone does not create this forward. The `-R` option
creates a listener on the robot; `-N` keeps the session open without a shell.
See the [OpenSSH forwarding reference](https://man.openbsd.org/ssh#R).

The tunnel depends on SSH connectivity and remote forwarding being allowed
for the robot account. `ExitOnForwardFailure` detects failure to establish the
listener, but does not prove the computer's HTTP server is running. If the SSH
session ends, reopen the tunnel. If port 16060 is already occupied, choose another
unused robot port and change both the `-R` port and robot URL to match.

### Direct LAN connection

Use the same branch in the robot's SDK-enabled Python environment. The server's
Flask/Pydantic packages are unnecessary on the robot.

```bash
python3 -m robot.navel_client.main --help
python3 -m robot.navel_client.main --print-only
```

Print-only mode reads the live SDK but makes no HTTP requests. Each stdout line
is a raw JSON frame. Check visible person IDs, plausible distances and gaze,
measured motion, and the lidar/sonar lists. Compare native range values with the
installed SDK/hardware documentation to establish their units. Head position is
included only if a valid `CAM_HEAD` Cartesian entry is available.

Then stream to the computer:

```bash
python3 -m robot.navel_client.main \
  --server http://192.168.1.100:6060 \
  --minimum-send-interval 0.1 \
  --request-timeout 5 \
  --max-locomotion-age 1
```

After each accepted POST, the client prints the full `RawObservationFrame` as
indented JSON to stdout, including `timestamp`, `people`, `robot`, and `safety`.
Unavailable measurements remain `null`, and optional head position is included
when available. Acknowledgements such as `timestamp=... people=... accepted=true`
and request errors go to stderr. Failed requests do not print an accepted frame.
This output also applies when streaming through the reverse SSH tunnel.
To inspect policy decisions on Navel without executing policy actions, start the
receiver with `--social-output` and add `--decision-dry-run` to the robot client.
See the [decision dry-run procedure](robot-decision-dry-run.md).
With social processing enabled, responses also include the
[target lock lifecycle](target-lock.md). Add `--lock-output` on the server to
write its frame-by-frame trace. The robot dry-run uses the lock's effective
decision.
Add `--head-focus` to move the head toward the first unambiguous visible UID.
This operates locally before HTTP delivery, including when `--decision-dry-run`
is enabled. See the [head focus procedure](robot-head-focus.md) before enabling it.
On the computer, inspect the output with:

```bash
# Substitute the recording path printed by the receiver:
tail -f var/recordings/pilot-01.jsonl
```

Confirm that people change as the live scene changes and new lines continue to
arrive. Stop both processes with Ctrl-C when finished.

For named recordings, viewing, and playback, see
[recording-and-replay.md](recording-and-replay.md). Playback to stdout needs no
robot or tunnel. The receiver and replay both validate the existing raw format.

## Client controls

| Control | Default | Purpose |
| --- | --- | --- |
| `--server` | `NAVEL_SENSOR_SERVER`, otherwise `http://127.0.0.1:6060` | Computer's LAN URL, or `http://127.0.0.1:16060` with the reverse tunnel |
| `--minimum-send-interval` | 0.1 seconds | Minimum interval between POST starts; zero removes rate limiting |
| `--request-timeout` | 5 seconds | HTTP timeout |
| `--max-locomotion-age` | 1 second | Maximum cache age before velocities/ranges become null |
| `--print-only` | Off | Print collected frames instead of sending them |
| `--decision-dry-run` | Off | Validate live policy responses and log one placeholder call per decision/target transition |
| `--command-dry-run` | Off | Validate locked commands and POST simulated feedback; no approach or speech calls |
| `--physical-executor` | Off | Run configured action and route scripts with a stop hook, response watchdog, and physical outcome feedback |
| `--head-focus` | Off | Send `look_at_person` for one unambiguous visible UID before HTTP delivery; this moves the head even with `--decision-dry-run` |
| `--head-focus-magnitude` | 0.5 | SDK head movement magnitude, from 0 to 1 |
| `--head-focus-grace` | 0.75 seconds | Hold the selected UID across a short perception gap before allowing another |
| `--max-decision-age` | 1 second | Reject a response if its source frame is older than this on Navel's monotonic clock |
| `--decision-timeout` | 2 seconds | Cancel the active logging placeholder after this long without a valid response |

Server-side `--lock-output PATH` writes a new lock trace and enables social
processing. `--lock-config PATH` loads hold/cooldown settings. Both are described
in the [target lock guide](target-lock.md).
Server-side `--command-output PATH` and `--execution-output PATH` enable and
trace correlated command proposals and simulated robot feedback. Set
`--command-config PATH` to tune source age, lease, and per-session capacity.
`--command-dry-run` requires HTTP and cannot be combined with
`--decision-dry-run`. See the [command and feedback guide](command-feedback.md).
Physical mode requires all five script paths and excludes both dry-run modes.
See the [physical executor script contract](physical-executor.md).

Timeout and age values must be finite and positive. The interval must be finite
and nonnegative. Head magnitude must be finite and between 0 and 1. `--server`
takes a base URL, without an API path or query.

## Troubleshooting

For angular velocity that stays zero, inspect the SDK directly on Navel:

```bash
python3 robot/tests/stream_angular_velocity.py --duration 30
```

Watch the output while the base turns using your existing controls. The script
prints the raw velocity object, components, and orientation without the adapter
or HTTP. See [the diagnostic instructions](../robot/tests/README.md).

| Symptom | Check |
| --- | --- |
| Navel SDK missing | Run the collector in the robot's SDK-enabled environment. `--help` and offline tests do not need the SDK. |
| Connection refused / request timeout | Receiver running, correct computer LAN IP, network route, and TCP 6060 allowed on computer. |
| Robot HTTP connection fails but PC-local health works | Use the reverse tunnel above if PC-to-Navel SSH works; shared router membership alone does not establish HTTP reachability. |
| PC-local health fails | Start the receiver and inspect its terminal for startup errors; the tunnel needs a working receiver on computer port 6060. |
| Remote port forwarding failed | Robot port 16060 already occupied, or SSH forwarding restricted; inspect `ssh -v` output and the robot account's forwarding configuration. |
| Velocity and safety stay null | Locomotion receive packets, their odometry/distances fields, and cache age; do not interpret null as stationary or clear space. |
| Position absent | A valid `g_head_position` entry labelled `CAM_HEAD`; head angles do not satisfy this field. |
| HTTP 400 | New raw contract, required null keys, finite numeric values, unique integer UIDs, and gaze range. Old pipeline payloads are incompatible. |
| HTTP 409 | Duplicate/backward robot timestamp; if the robot clock restarted, start a fresh receiver recording. |
| Recording already exists | Choose a new `--output` path or omit the option for an automatic timestamped name. |
| HTTP 503 | Output directory permissions or storage errors; the frame was not acknowledged. |
| Missing intermediate frames | Expected for newest-frame streaming, rate limits, slow requests, or transient failures. |
| Non-timeout SDK exception | Collector stops and closes its SDK context; inspect the exception and robot/socket connectivity, then restart. |

## Offline checks

For optional output-only LLM/VLM inference in the live receiver, see
[live model inference](live-model-inference.md). It includes disabled, LLM-only,
VLM-only, and paired commands plus the sender's opt-in capture provenance.
Model outputs are recorded separately and do not control the robot.

```bash
source .venv/bin/activate
python3 -m unittest discover -s tests -v
python3 -m app.domain.schema
```

The tests simulate SDK data and bind a loopback HTTP server; no hardware or
Ollama runtime is required. The generated schema is checked against the committed
artifact during tests.
