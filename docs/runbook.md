# Running and checking the Navel sensor stream

## Computer

Check out `feature/navel-raw-http-stream` and use Python 3.10 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m app.server --host 0.0.0.0 --port 6060 --output var/observations.jsonl
```

The receiver appends to an existing file. Use a different `--output` path for a
new recording or a different robot. `--output -` writes frames to stdout instead;
Flask request logs go to stderr.

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

The client logs `timestamp=... people=... accepted=true` for successful requests.
On the computer, inspect the output with:

```bash
tail -f var/observations.jsonl
```

Confirm that people change as the live scene changes and new lines continue to
arrive. Stop both processes with Ctrl-C when finished.

## Client controls

| Control | Default | Purpose |
| --- | --- | --- |
| `--server` | `NAVEL_SENSOR_SERVER`, otherwise `http://127.0.0.1:6060` | Computer's HTTP(S) base URL; set its LAN IP on Navel |
| `--minimum-send-interval` | 0.1 seconds | Minimum interval between POST starts; zero removes rate limiting |
| `--request-timeout` | 5 seconds | HTTP timeout |
| `--max-locomotion-age` | 1 second | Maximum cache age before velocities/ranges become null |
| `--print-only` | Off | Print collected frames instead of sending them |

Timeout and age values must be finite and positive. The interval must be finite
and nonnegative. `--server` takes a base URL, without an API path or query.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Navel SDK missing | Run the collector in the robot's SDK-enabled environment. `--help` and offline tests do not need the SDK. |
| Connection refused / request timeout | Receiver running, correct computer LAN IP, network route, and TCP 6060 allowed on computer. |
| Velocity and safety stay null | Locomotion receive packets, their odometry/distances fields, and cache age; do not interpret null as stationary or clear space. |
| Position absent | A valid `g_head_position` entry labelled `CAM_HEAD`; head angles do not satisfy this field. |
| HTTP 400 | New raw contract, required null keys, finite numeric values, unique integer UIDs, and gaze range. Old pipeline payloads are incompatible. |
| HTTP 503 | Output directory permissions or storage errors; the frame was not acknowledged. |
| Missing intermediate frames | Expected for newest-frame streaming, rate limits, slow requests, or transient failures. |
| Non-timeout SDK exception | Collector stops and closes its SDK context; inspect the exception and robot/socket connectivity, then restart. |

## Offline checks

```bash
source .venv/bin/activate
python3 -m unittest discover -s tests -v
python3 -m app.domain.schema
```

The tests simulate SDK data and bind a loopback HTTP server; no hardware or
Ollama runtime is required. The generated schema is checked against the committed
artifact during tests.
