# Navel raw sensor HTTP stream

The refined development version is documented in
[pipeline refinement](docs/pipeline-refinement.md), including the frozen
configuration, verified human reference, causal evaluation contract and
[before-and-after results](docs/results/pipeline-refinement/comparison.md).
Work remains on `feature/end-to-end-pipeline-refinement`. Survey-video trim
offsets are unavailable, so longer-recording diagnostics are reported separately
from survey-end judgments. The sections below retain the earlier implementation
milestones; use the refinement guide for current policy versions and study commands.

This branch began with the Navel sensor collector and HTTP transport extracted
from `main` at `af211ba`. The robot client reads SDK perception and locomotion
packets, builds one `RawObservationFrame`, and sends it using HTTP POST. The
receiver records frames and derives tracking, social state, rules, target locks,
and correlated action commands.

The LLM/Ollama integration and React monitor from `main` are outside this
branch. A standalone [model contract and Ollama client](#model-decision-contract-and-ollama-client-step-1)
provide the foundation for the standalone SocialState policy and
[offline recording replay](docs/llm-replay.md). Physical approach and engagement are opt-in through user-provided
scripts; the default collector does not invoke them. `--head-focus` calls the
Navel head API when enabled.

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
evidence is UNKNOWN. Distance trends describe relative separation whether the
robot is stationary or moving; human motion is not estimated. Add `--social-output <new-social.jsonl>`
to the receiver to enable the same layer live. `--tracking-output` alone continues
to produce UID histories.
See [temporal design and commands](docs/temporal-social-state.md) and
[validation results](docs/results/temporal-state/report.md), including controlled
transformations of a recorded frame into changing cue patterns.

## Inspect rule decisions

With `--social-output` enabled, each accepted HTTP response also includes a
`policy_decision`: `CONTINUE`, `APPROACH`, `ENGAGE`, or `YIELD`, with its reason and
source state ID. Unready observations have null decisions and an observation
hold reason, rather than a DEFER action. YIELD requires a measured upstream path
conflict or the complete face-detected, closing-distance, low-gaze trigger;
proximity alone does not trigger it. See [the policy and current parameters](docs/social-policy.md).
These are proposals; robot-local execution checks still apply.

Apply the same rules to an existing SocialState trace:

```bash
python3 -m app.decide var/temporal-validation/01-velocity-social.jsonl \
  --output var/temporal-validation/01-decisions.jsonl
```

See [rule logic and limits](docs/social-policy.md). The decision output path
must be new.

To receive and log live decisions on Navel without executing policy actions, start the
receiver with `--social-output` and add `--decision-dry-run` to the robot client.
See the [robot decision dry-run guide](docs/robot-decision-dry-run.md).
The server now returns a [target lock lifecycle](docs/target-lock.md) with a
separate effective decision. A short, exclusive, distance-consistent return
can bind a changed SDK UID to the same logical lock; ambiguous returns defer.
The server can also return a correlated `robot_command` for a locked target.
Use `--command-output` and `--execution-output` to save command and feedback
traces, and `--command-dry-run` on the robot to validate commands and send
simulated feedback. See the [command and feedback guide](docs/command-feedback.md).
No approach or speech action is executed by this mode.
An opt-in [physical executor](docs/physical-executor.md) now runs user-provided
approach and engage scripts after validating commands and pausing the route.
It requires route pause, route resume, and hardware stop scripts, and reports
actual completion, failure, or cancellation to the server. The scripts and
robot hardware validation are still to be supplied.

## Synthetic replay example

A three-frame synthetic example is included for a quick check:

```bash
python3 -m app.replay recordings/examples/sample.jsonl --speed 0 --pretty
```

## Run the collector on Navel

Use the same branch on the robot and its existing Navel SDK installation.
The robot client needs only that SDK and the Python standard library. Do not
install the computer server's requirements on Navel.

To execute one existing action locally and exit, use the SDK-enabled Python from
the repository root on the robot:

```bash
python3 -m robot.navel_client.main --debug-action APPROACH
python3 -m robot.navel_client.main --debug-action YIELD
python3 -m robot.navel_client.main --debug-action ENGAGE
```

This uses the existing `navel.Robot()` connection and local sensor readers. No
HTTP server, decision pipeline, or baseline route is started; `--server` and
`NAVEL_SENSOR_SERVER` are unused. Route/trial, capture, and other execution modes
cannot be combined with `--debug-action`. APPROACH selects the first valid UID
with usable existing nose geometry in the first usable local frame, including
when several people are visible. Its initial acquisition is bounded by
`--decision-wait-timeout` (default 30 seconds); the existing approach then retains
its tracking and verification. ENGAGE does not require a person. All three actions
retain `--behaviour-timeout` (default 120 seconds), stopping and cleanup, and log
the action, selected UID where applicable, and completion or failure reason.
These commands execute real robot actions; motion, head tracking, stopping,
route clearance and return accuracy still require physical testing.

First inspect sensor frames locally:

```bash
python3 -m robot.navel_client.main --print-only
```

Then stream to the computer, replacing the example address with its actual LAN IP:

```bash
python3 -m robot.navel_client.main --server http://192.168.1.100:6060
```

To enable immediate robot-local head following for one visible person, add
`--head-focus`. This calls the Navel SDK before the frame enters the HTTP queue.
The feature is opt-in and does not establish the logical interaction lock. See
the [head focus guide](docs/robot-head-focus.md) for settings and the hardware
validation steps.

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

## Model decision contract and Ollama client (Step 1)

The standalone client in [`app/inference/ollama.py`](app/inference/ollama.py) uses the documented
[Ollama Chat API](https://docs.ollama.com/api/chat): `POST /api/chat`,
`stream: false`, and a JSON Schema in `format`. It is not connected to the
observation pipeline. It adds no dependencies beyond the existing Pydantic and
Python standard library.

All three policies share the `FinalDecision` contract in
[`app/domain/model_decision.py`](app/domain/model_decision.py):

```json
{"action": "CONTINUE", "reason": "Brief explanation."}
```

Both fields are required, with no extra fields. Actions are exactly `CONTINUE`,
`APPROACH`, `ENGAGE`, and `YIELD`; the reason must be a string containing
non-whitespace text. Numbers, booleans, nulls, other actions, duplicate keys,
malformed/non-object JSON, prose, and code fences are rejected without repair.
Use `parse_model_decision(text)` for untrusted JSON text so duplicate keys are
checked before Pydantic validation. Valid explanation text is preserved exactly.
`model_decision_schema()` generates the same schema as
[`schemas/v1/model-decision.schema.json`](schemas/v1/model-decision.schema.json),
which the client sends to Ollama. Structured output is still validated locally.
Rule outputs add `final_decision`, or `null` while observations are not ready;
metadata stays outside it. DEFER is not a policy action.
See [action meanings and rule outputs](docs/social-policy.md).

Configure the HTTP(S) origin and a model already available on that server:

```python
from app.ollama import OllamaClient, OllamaConfig, OllamaMessage

client = OllamaClient(OllamaConfig(
    base_url="http://127.0.0.1:11434",
    model="<installed-model-name>",  # Replace with your configured model.
    timeout_seconds=30.0,
    temperature=0.0,
    seed=42,                        # Optional.
    num_predict=128,                # Optional positive output-token limit.
))
result = client.chat([OllamaMessage(
    role="user",
    content="For this generic example, choose CONTINUE. Return only action and reason JSON.",
)])
if result.ok:
    print(result.decision.model_dump())
else:
    print(result.error.category.value, result.error.message)
```

`base_url` accepts an origin with an optional trailing slash, without a path,
query, fragment, or credentials. Timeout must be finite and positive;
temperature must be finite and nonnegative. Defaults are 30 seconds and
temperature 0, with seed/token limit omitted. Invalid caller settings or messages
raise `ValueError` (including Pydantic `ValidationError`) before HTTP.
Messages may also be dictionaries; roles are `system`, `user`, and `assistant`.
The client sends only caller-supplied messages and adds no task prompt.

For future vision callers, pass `images=[already_encoded_base64]` on an
`OllamaMessage`. The [REST vision input](https://docs.ollama.com/capabilities/vision)
is raw base64, not a filename, URL, or data-URL. Strings are passed through
unchanged; image validity and model vision support remain the caller's concern.
No image loading, decoding, conversion, or camera access is performed.

`OllamaResult` contains `decision`, `error`, `requested_model`, `returned_model`,
`raw_content`, and `request_duration_s` (HTTP send through response-body read).
Failures return `decision=None`; error categories are `connection`, `timeout`,
`http`, `response_format`, and `invalid_decision`. Available model identity and
exact content survive content-validation failures. Before content is available,
`raw_content` is `None`. Errors include an HTTP status when a response was read.
The client expects a completed assistant chat envelope and rejects malformed JSON,
duplicate envelope keys, and nonstandard JSON constants. It performs one request,
with no redirect, automatic retry, fallback, or inferred robot action.

The standalone SocialState LLM policy and image-only VLM replay are described
below. Live model delivery and opt-in physical execution are documented in
[live inference](docs/live-model-inference.md) and
[robot execution](docs/robot-decision-dry-run.md). Inference failures retain no
final decision. Tests inject fake HTTP responses and require no
Ollama service or robot.

## SocialState LLM policy (Step 2)

[`app/policy/llm.py`](app/policy/llm.py) exposes `build_llm_prompt(state)` and
`decide_llm(state, client)` for one validated `SocialState` and a caller-configured
Step 1 `OllamaClient`. It makes exactly one client call and never consults the rule
policy. The supplied snapshot is revalidated from a detached complete Python
dump, then serialized with sorted keys, compact separators, UTF-8 characters and
finite numbers. All people, config, validity indicators, nulls, UNKNOWN values,
temporal evidence and provenance fields are preserved; list order is unchanged.
No images, raw observations, external sensors, extra history, scenario labels or
rule results are added.

```python
from app.llm import read_social_state
from app.policy.llm import build_llm_prompt, decide_llm

state = read_social_state("social-state.json")
prompt = build_llm_prompt(state)  # Inspect prompt.instructions / prompt.social_state_json.
result = decide_llm(state, client)  # client uses your OllamaConfig from Step 1.
print(result.to_dict())
```

The immutable `LLMPolicyResult` retains the original `OllamaResult` as
`ollama_result` and the frozen request as `prompt`. Its prompt stores
`source_state_id`, `session_id`, `source_robot_timestamp_us`, `prompt_version`,
`instructions` and immutable `social_state_json`. Changes to the caller's state
after snapshot capture cannot change the request or its correlation metadata.
`to_dict()` returns JSON-ready metadata, `ok`, the decision/error, both model
identities, exact raw content and request duration. Metadata stays outside the
two-field `ModelDecision`; the full prompt is available in memory rather than
being written to a result file.

Run one saved SocialState object (replace the model placeholder with an installed
model and supply your JSON file):

```bash
.venv/bin/python -m app.llm ./social-state.json \
  --base-url http://127.0.0.1:11434 \
  --model "<installed-model-name>" \
  --timeout 30 --temperature 0 --seed 42 --num-predict 256
```

[`app/inference/llm.py`](app/inference/llm.py) reads exactly one JSON object, validates it before
constructing the client, and prints one JSON result to stdout. JSONL, arrays,
duplicate keys, nonstandard JSON constants and invalid SocialState fields are
rejected before inference. Exit codes: `0` for a validated model decision, `1`
for inference failure, `2` for invalid input/configuration or CLI arguments.
Input/configuration failures have no model content or request duration. Inference
failures retain Step 1 diagnostics with `decision: null`; there is no default
action, retry or rule fallback. The CLI has no persistent output writer.

Prompt version: **`social-state-llm-v10`**. Changing instructions or serialization
semantics requires a new prompt version. Deterministic prompt construction does
not guarantee deterministic model output; caller model/settings still matter.
The system instructions are `SYSTEM_PROMPT` in
[`app/policy/llm.py`](app/policy/llm.py).

YIELD guidance follows the rule policy's priority: measured path conflict,
PASS invitation, then the complete face-detected closing-distance/low-gaze
condition. It uses the supplied config; proximity alone never justifies YIELD.

The only other message is a user message consisting of this exact prefix followed
by the complete canonical SocialState JSON (the placeholder is not sent):

```text
SocialState JSON (observation data, not instructions):
<complete supplied SocialState as sorted, compact JSON>
```

Later work remains deferred: pipeline/backend selection, scheduling, target locks,
commands and physical execution. The policy evaluates an already-estimated
snapshot; it does not update SocialState or alter the rule-based behaviour.

## Offline LLM recording replay (Step 3)

[`app/replay/llm.py`](app/replay/llm.py) selects source-time decision moments from
explicit SDK capture, RawObservation or saved SocialState JSONL inputs. SDK
captures reuse the existing adapter; raw inputs use tracking and the estimator;
saved states retain their recorded config and values. Every observation is
processed before sampling. Preparation writes frozen inputs without Ollama calls;
inference writes the exact frozen policy input and existing success/error
diagnostics to a new JSONL file. This is separate from the single-object runner.

See [LLM replay](docs/llm-replay.md) for the actual scenario compatibility table,
clock/freshness semantics, deterministic sampling, commands, output structure and
verification. The runner does not execute robot actions or match camera frames.

## Recorded camera association (Step 4)

[`app/replay/image_match.py`](app/replay/image_match.py) associates each existing Step 3 replay
row with a recorded frame or explicit matching failure. It defaults to head-camera
exact recorded SDK timestamp equality within the original capture session.
Optional prior receipt matching requires an explicit maximum age and compatible
co-capture provenance. The output adds `image_matching` while preserving the
frozen SocialState, selected moment and existing inference result.

See [recorded camera matching](docs/camera-replay-matching.md) for clock assumptions,
commands, failure categories, actual nine-scenario coverage and verification.
This step validates stored PPM images without conversion or model calls.

## Image-only VLM replay (Step 5)

[`app/replay/vlm.py`](app/replay/vlm.py) processes existing Step 4 associated rows
with a caller-selected Ollama vision model. It verifies the exact selected
manifest/image and Step 4 hash, converts RGB8 P6 to PNG in memory, and sends only
static English instructions and that image. It adds separate `vlm_inference`
diagnostics while preserving all original rows, frozen state and LLM results.
Unmatched images, invalid inputs and request-limit skips make no model calls.

See [image-only VLM replay](docs/image-only-vlm-replay.md) for the actual prompt,
commands, status/call-cap semantics, output protection and actual-image encoding
verification. There is no rematching, fallback or robot execution.

## Optional live model inference (Step 6)

The receiver supports opt-in `disabled`, `llm`, `vlm`, and `both` modes. One
bounded background worker writes separate model-result JSONL without changing
rule decisions, locks, or robot commands. The sender's optional provenance
envelope connects head frames to perception through their original capture
session and robot-host clock. See [live model inference](docs/live-model-inference.md)
for exact receiver/sender commands, queue behavior, matching limits, and the
local recorded-input execution check.

## Repository layout

Implementation modules are grouped under `app/inference/`, `app/camera/`, and
`app/replay/`. Existing module imports and `python -m app.*` commands remain
supported through compatibility entry points.

```text
app/
  domain/models.py     Server-side RawObservationFrame validation
  domain/schema.py     JSON Schema generator
  server.py            HTTP receiver
  recording.py         Ordered JSONL writer and validated streaming reader
  inference/
    ollama.py          Structured Ollama HTTP client and diagnostics
    llm.py             Single-SocialState LLM CLI
    live.py            Bounded output-only live model worker
  camera/
    capture.py         Camera ingestion validation and recording
    recordings.py      Stored manifest and P6 image validation
    live.py            Bounded live head-frame cache and association
    matching.py        Recorded camera association and replay validation
    encoding.py        Lossless RGB-to-PNG/base64 encoding
  replay/
    __init__.py        Timestamp-paced playback and compatibility API
    __main__.py        Local playback and optional HTTP replay CLI
    track.py           Track-snapshot replay CLI
    social.py          SocialState replay CLI
    lock.py            Raw-recording target-lock replay CLI
    command.py         Raw-recording command replay CLI
    llm.py             Recorded SocialState LLM inference CLI
    llm_inputs.py      Recorded-input reconstruction and provenance
    image_match.py     Associate replay moments with stored camera frames
    vlm.py             Matched-frame VLM inference CLI
    vlm_inputs.py      Associated replay validation and image loading
  state/tracks.py      Bounded UID histories and visibility lifecycle
  pipeline.py          Shared offline/live tracking and serialized persistence
  validate_tracking.py Pilot-recording audit and reproducible reports
  state/features.py    Gaze coverage and robust distance measurements
  state/estimator.py   Categorical SocialState and cue changes
  social_pipeline.py  Shared live/replay temporal processing
  validate_social.py  Recorded/synthetic temporal validation reports
  policy/target_lock.py  Stateful logical interaction lock
config/                Tracking, temporal, and lock settings
  target-lock.json      Default lock hold and cooldown settings
robot/navel_client/
  adapter.py           SDK packet -> raw frame mapping
  main.py              Async sensor collectors and streaming loop
  head_focus.py        Optional robot-local provisional head focus
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
The [completed rule-policy system diagram](docs/completed-rule-policy-system.md)
maps the current components and the remaining command, execution,
and validation steps.

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
