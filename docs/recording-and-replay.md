# Lightweight recording and replay

This implements Layer 1, the raw recording/replay foundation. It uses the
existing raw sensor frame, Flask, Pydantic, and the standard library. There is no
database, UI, social-state estimator, or policy. One file is one recording
session from one robot; its lines contain only the raw frames.

Layer 2 person tracking is now available separately through `python3 -m app.track`
or receiver `--tracking-output`. See [person tracking](person-tracking.md) for
its design rationale, commands, and validation. The raw-only workflow below is
unchanged.

Layer 3 adds `python3 -m app.social` for temporal SocialState replay and receiver
`--social-output` for live estimates. See [temporal social state](temporal-social-state.md)
for commands, threshold assumptions, and recorded/synthetic validation.

## Probe complete Navel SDK packets

The ordinary observation recording discards SDK fields before HTTP and only
sends the latest observation at up to 10 Hz. To inspect what the installed SDK
actually populates, start the receiver with a **new** diagnostic output path:

```bash
# Computer; use --host 127.0.0.1 with the existing reverse SSH tunnel.
python3 -m app.server --host 127.0.0.1 --port 6060 \
  --output var/recordings/probe-01.observations.jsonl \
  --sdk-output var/recordings/probe-01.sdk.jsonl \
  --camera-output-dir var/recordings/probe-01.cameras
```

Then run this on Navel, using the tunnel URL (or the computer's LAN URL):

```bash
python3 -m robot.navel_client.main --server http://127.0.0.1:16060 \
  --sdk-capture-only --camera-capture --camera-interval 1.0
```

`--sdk-capture-only` reads `next_frame()` and `next_locomotion()` concurrently,
without sending policy observations or making robot commands. For simultaneous
v1 observations and SDK diagnostics, use `--sdk-capture` instead. The SDK file
is created on the first packet; the observations file remains absent during a
capture-only run. Each SDK JSONL line has `stream` (`perception` or
`locomotion`), per-stream `sequence`, `session_id`, robot-host
`received_monotonic_us`, robot-host `received_unix_us`, and the complete
JSON-compatible SDK `packet`. SDK source times such as `packet.time` and
`packet.odometry.time` remain in their original units. Enum keys and values
are stored by name; non-finite float values are stored as `"NaN"`, `"Infinity"`,
or `"-Infinity"`. All fields present on an SDK data struct are retained,
including null/invalid person IDs, sound metadata, odometry, and range arrays.

With `--camera-capture`, the robot also tries `HeadCamera.get_frame()` and
`ChestCamera.get_frame()` independently. Each successful RGB frame is sent to
the computer and saved as a PPM file under `probe-01.cameras/head/` or
`probe-01.cameras/chest/`. The `probe-01.cameras/frames.jsonl` manifest contains
the relative image path, camera name, frame dimensions, SDK `timestamp_us`,
robot receipt times, and sequence. Images are separate files because putting
their pixel bytes in JSONL would make the recording difficult to inspect. A
camera class missing from the installed SDK, failed context setup, missing or
failing `get_frame()`, invalid frame format, or ten consecutive timeouts writes
an `event: "unavailable"` manifest row with a reason and logs the same warning
on the robot. The other camera and the two sensor packet streams continue.
`--camera-interval` controls the attempt interval per camera; the default is
one second. A new capture needs a new camera directory.

Inspect a short pilot before the eight scenarios:

```bash
tail -f var/recordings/probe-01.sdk.jsonl
tail -f var/recordings/probe-01.cameras/frames.jsonl
```

The robot queues packets separately from the rate-limited observation sender.
Transport errors retry the same packet, and the receiver accepts identical
retries without duplicate lines. If the 1024-packet queue fills, capture stops
with an error rather than silently losing records. The SDK itself returns the
newest available sample, so its API may skip intermediate generated samples
if collection cannot keep up. A fresh run requires a fresh output path.

## Record through the working reverse tunnel

On the PC, activate the existing environment and start a named recording:

```bash
source .venv/bin/activate
python3 -m app.server --host 127.0.0.1 --port 6060 --output var/recordings/pilot-01.jsonl
```

Keep the working reverse SSH tunnel running. On Navel, run the existing client:

```bash
python3 -m robot.navel_client.main --server http://127.0.0.1:16060
```

The robot collector and wire format do not need changes. The receiver creates
the file on the first accepted frame, then writes one complete JSON object per
line. Null measurements and omitted optional head positions are preserved.

On the PC, view arrivals:

```bash
tail -f var/recordings/pilot-01.jsonl
```

Stop the receiver with Ctrl-C to finish the recording. Choose `pilot-02.jsonl`
for the next run. Existing output files are refused instead of being overwritten
or mixed with a new run. Omitting `--output` chooses a timestamped path under
`var/recordings/` and prints it at startup.

Use one receiver per robot. The lightweight session boundary is the output file;
source IDs, request session headers, and per-frame metadata envelopes are deferred.
Within one receiver run, timestamps must increase. Duplicate/backward frames
receive HTTP 409 and are not recorded. If the robot reboots and its monotonic
clock resets, restart the receiver with a fresh recording path.

## View or replay on the PC

For unchanged JSONL text:

```bash
cat var/recordings/pilot-01.jsonl
```

For playback using the original time gaps:

```bash
python3 -m app.replay var/recordings/pilot-01.jsonl
```

Other playback modes:

```bash
# Twice the recorded playback speed:
python3 -m app.replay var/recordings/pilot-01.jsonl --speed 2

# Immediately inspect all frames as indented JSON:
python3 -m app.replay var/recordings/pilot-01.jsonl --speed 0 --pretty
```

The default output is JSONL on stdout; the completion count/errors go to stderr.
Redirect stdout to save replay output. `--pretty` uses multiline JSON for
inspection and is not a JSONL export. Ctrl-C stops playback.

Replay validates the raw schema and increasing timestamps while reading one
line at a time. Errors identify the source file/line. Blank lines are skipped;
empty recordings fail. Original sensor timestamps are never rewritten. Pacing
uses relative gaps, not the PC's wall clock, and does not accumulate output time
into every subsequent frame's delay. Slow output can make playback fall behind;
it does not drop frames. Reading and replay use bounded memory.

## Optional HTTP replay

To exercise the receiver without Navel, start a separate receiver on the PC:

```bash
python3 -m app.server --host 127.0.0.1 --port 6061 --output var/recordings/replay-01.jsonl
```

In another PC terminal:

```bash
python3 -m app.replay var/recordings/pilot-01.jsonl --server http://127.0.0.1:6061
```

Use a fresh receiver/output for every HTTP replay. Replaying into an existing
live session may collide with its timestamp order; do not mix live and replay
inputs. The CLI validates the entire source recording before its first POST,
then streams it without loading the whole file. Stop recording before replaying
to ensure the input file is no longer changing.

Each successful POST is also printed on stdout. A timeout, invalid response, or
rejection stops playback with an error and a nonzero exit status; frames are not
silently skipped. No HTTP happens without `--server`.

## Try it immediately

A three-frame synthetic demonstration is checked in:

```bash
python3 -m app.replay recordings/examples/sample.jsonl
```

This is demonstration data, not robot calibration data.

## Additional pilot recordings for calibration

Record separate files for a stationary person, a person walking toward/away from
a stationary base, a brief gaze, sustained gaze, and a short disappearance.
Use descriptive filenames and note what actually happened beside each file in
your experiment notes. Record robot movement separately so relative distance
changes are not mistaken for human movement. Check the existing angular-velocity
diagnostic and native range units on hardware before interpreting those channels.

The PC still stores the exact available cues, including unknown measurements;
this software does not establish physical sensor accuracy or unit calibration.

## Checks

```bash
python3 -m unittest discover -s tests -v
```

Tests cover timestamp rejection, concurrent writes, recording isolation, invalid
files, preserved nulls, deterministic local replay, playback timing, and a real
HTTP recording replayed into a fresh local receiver.
