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

### Connection through a reverse SSH tunnel

If Navel cannot reach the computer's HTTP port directly, but the computer can
SSH into Navel, start the receiver on the computer's loopback interface:

```bash
python3 -m app.server --host 127.0.0.1 --port 6060 --output var/observations.jsonl
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
| `--server` | `NAVEL_SENSOR_SERVER`, otherwise `http://127.0.0.1:6060` | Computer's LAN URL, or `http://127.0.0.1:16060` with the reverse tunnel |
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
| Robot HTTP connection fails but PC-local health works | Use the reverse tunnel above if PC-to-Navel SSH works; shared router membership alone does not establish HTTP reachability. |
| PC-local health fails | Start the receiver and inspect its terminal for startup errors; the tunnel needs a working receiver on computer port 6060. |
| Remote port forwarding failed | Robot port 16060 already occupied, or SSH forwarding restricted; inspect `ssh -v` output and the robot account's forwarding configuration. |
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
