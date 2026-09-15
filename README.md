# LLM/VLM Social Navigation Pipeline

This repository contains a read-only social-navigation proof of concept: canonical
observation contracts, synthetic/replay sources, temporal state estimation,
event-driven decision scheduling, and an HTTP gateway to a locally hosted Ollama
model. It also includes a Navel-side collector that reads SDK data and sends
canonical observations without commanding the robot.

![LLM/VLM social-navigation architecture](docs/architecture.jpg)

## Current scope

Implemented Navel-to-LLM runtime path:

```text
Navel next_frame + next_locomotion
  -> NavelObservationAdapter
  -> ObservationFrame
  -> HTTP POST /api/v1/observations
  -> TemporalSocialStateEstimator
  -> SocialState
  -> DecisionScheduler
  -> LLMPolicyBridge
  -> Ollama structured response
  -> validated BehaviorIntent in the HTTP response
  -> typed Navel behavior command and dry-run handler
```

The simpler `POST /chat` route remains available only as an Ollama connectivity
diagnostic. The robot pipeline uses `POST /api/v1/observations`.

This path is covered offline with Navel SDK-shaped perception and locomotion
objects, a real local HTTP request, and a fake structured LLM. A live test still
requires the Navel SDK and sockets on the robot, a network route to the gateway,
and the configured Ollama model on the lab computer. VLM input, final validation
for execution, and physical robot control are not implemented. See
`docs/behavior-intent-output-mapping.md` for the dry-run output architecture.

Ollama is the local model runtime. It performs inference on the computer where it is installed; requests are not sent to an Ollama cloud model. The Python gateway and Ollama are expected to run on the same server computer by default. Navel calls the gateway using that computer's LAN IP address.

## Repository layout

```text
app/
  adapters/       Synthetic and future robot/replay observation sources
  config.py       Environment configuration
  decision/       Event scheduler and schema-constrained LLM policy bridge
  domain/         Versioned pipeline contracts and schema generator
  llm.py          Ollama client
  server.py       Flask API
  state/          Temporal social-state estimation
robot/
  navel_client/   Read-only collection, transport, and dry-run behavior mapping
client.py         Minimal client for Navel or another computer
docs/
  architecture.jpg
schemas/v1/       Generated JSON Schemas for public contracts
tests/
  test_server.py  Offline chat API tests
  test_navel_adapter.py
  test_navel_server_e2e.py
  test_observation_pipeline.py
requirements.txt
```

## Prerequisites

- Python 3.10 or newer
- Ollama installed on the server computer
- Enough CPU/GPU memory for the selected model

These are server prerequisites. The server dependencies, including Pydantic,
are listed in `requirements.txt`. The robot-side dependency boundary is
documented separately in `requirements-navel.txt`.

Install and start Ollama according to the installation instructions for the server's operating system, then download the default model:

```bash
ollama pull qwen2.5:7b
ollama serve
```

Confirm that Ollama is available:

```bash
curl http://127.0.0.1:11434/api/tags
```

## Install the gateway

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

The application defaults use a local Ollama arrangement. Override them through
the environment or a local `.env` file when needed:

```env
OLLAMA_HOST=127.0.0.1
OLLAMA_PORT=11434
OLLAMA_MODEL=qwen2.5:7b
API_HOST=0.0.0.0
API_PORT=6060
DECISION_MODE=NORMAL
```

Start the gateway:

```bash
python3 -m app.server
```

## Pipeline Lens monitor

Pipeline Lens is the local shadow-mode recorder and replayer included with the
gateway. It visualizes each observation as it moves through validation, social
state estimation, scheduling, policy selection, and intent validation. Replays
use fresh estimator/scheduler state and never call a robot actuation API.

Build the web client once:

```bash
cd web
npm install
npm run build
cd ..
```

Then start the gateway and open `http://127.0.0.1:6060`. The monitor discovers
the checked-in synthetic JSONL recordings automatically. Select a recording to
create an isolated replay, then use restart, play/pause, single-step, speed, the
timeline, stage cards, transition diff, payload inspector, and latency view.

For frontend development, leave the gateway running and use `npm run dev` from
`web/`; Vite serves the UI at `http://127.0.0.1:5173` and proxies monitor API
requests to port 6060.

Live Navel observations continue to use `POST /api/v1/observations`. Connected
sources appear automatically, and the record control stores canonical
observations plus `trace.jsonl` under the ignored `var/recordings` directory.
The monitor is deliberately read-only with respect to connected robots.

## LLM Debug mode

Debug mode keeps the normal Navel ingestion, `SocialState` estimator,
scheduler, Ollama client, action set and `BehaviorIntent` validation. It changes
the prompt and structured response for each scheduled decision, retains the
latest request-local snapshot for the live page, and saves every completed or
failed Debug decision to a local SQLite history:

```text
Navel sensors
  -> NavelObservationAdapter
  -> ObservationFrame
  -> SocialState
  -> Debug prompt builder
  -> exact ordered messages captured at the Ollama request boundary
  -> Ollama
  -> validated debug response and BehaviorIntent
  -> atomic DebugDecisionSnapshot
  -> latest snapshot plus durable SQLite history
  -> monitor API and SSE events
  -> /debug
```

Build the web client as described above, then start a Debug gateway:

```bash
source .venv/bin/activate
DECISION_MODE=DEBUG API_HOST=0.0.0.0 python3 -m app.server
```

Open `http://127.0.0.1:6060/debug` on the gateway computer. From another
computer, replace `127.0.0.1` with the gateway computer's LAN address. Select a
connected source in the left sidebar. The page updates after each completed
Debug inference and shows:

- the social summary and important available `SocialState` inputs;
- cited observations and interpretations as separate evidence types;
- the recommended action, rationale and every action's model-reported score;
- uncertainty and missing information;
- the exact ordered system and rendered user messages;
- the raw `SocialState`, original Ollama completion and validated output;
- request, model, timing, source-freshness and snapshot-revision metadata.

The page remains empty until that source completes a scheduled decision. A
newly detected person normally supplies the first trigger. For a brief
diagnostic without waiting for a scheduler event, start the Navel client with
`--force-decision`, confirm one snapshot, then stop that diagnostic because it
requests inference for every transmitted frame.

A failed Ollama request or invalid structured response returns HTTP 502 for that
observation but remains visible on `/debug`. Expand the prompt, raw state and raw
response panels to identify whether the problem occurred before or after model
generation. Read the latest snapshot directly:

```bash
curl http://127.0.0.1:6060/api/v1/monitor/sources/navel-robot-1/debug-snapshot
```

The gateway stores every Debug decision automatically from the first inference
observed for a connected source. This does not require the Observatory
**Record** button. The database is `var/debug-decisions.sqlite3`, persists
across gateway restarts and is excluded from Git. Existing Observatory
recordings remain manually started JSONL files.

List saved decisions newest first, then fetch one complete snapshot by its
request ID:

```bash
curl 'http://127.0.0.1:6060/api/v1/monitor/sources/navel-robot-1/debug-decisions?limit=50'
curl http://127.0.0.1:6060/api/v1/monitor/debug-decisions/REQUEST_ID
```

Use the returned `next_cursor` as the list endpoint's `before` value to load an
older page. The current web page still opens the latest snapshot; the next UI
stage will add the selectable history list. The history has no automatic
deletion limit.

Restart with `DECISION_MODE=NORMAL`, or omit the setting, to use the original
compact policy prompt and response. Normal mode remains the default and does not
create Debug snapshots.

### Available evidence and limits

The Debug page reports only information preserved in the request's
`SocialState`. The original Navel SDK packets are not retained in a Debug
snapshot; use the Observatory/live recording path to inspect the canonical
`ObservationFrame` before state estimation.

Human `state_age_ms` provides track-level age. The current state contract does
not contain a timestamp for each sensor field, so the page does not claim that a
distance, gaze or expression value has its own measured age. It also does not
contain the estimator's individual historical samples. Temporal claims can cite
derived fields such as `distance_trend`, `closing_speed_mps` and gaze-window
summaries, but cannot display or reconstruct the private sample sequence.

With distance-only Navel input, decreasing separation supports a closing trend;
it does not establish whether the human, robot or both moved. Human position and
trajectory remain unknown unless a verified robot-base coordinate mapping is
configured. Images, body orientation, speech activity and group observations
are also absent from the current Navel adapter. The response validator checks
types, action contracts, scores, exact source paths and copied scalar values. A
reviewer must still assess whether the model's concise natural-language
interpretations are warranted by those cited values.

## Test input and output

Check both the Ollama connection and selected model:

```bash
curl http://127.0.0.1:6060/health
```

Send an input to the model:

```bash
curl -X POST http://127.0.0.1:6060/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Reply with exactly: LLM connection successful"}'
```

Example response:

```json
{
  "provider": "ollama",
  "model": "qwen2.5:7b",
  "input": "Reply with exactly: LLM connection successful",
  "output": "LLM connection successful"
}
```

You can perform the same test with the included Python client:

```bash
python3 client.py "Reply with exactly: LLM connection successful"
```

An optional system prompt and temperature can also be supplied:

```json
{
  "message": "A person is crossing the robot's path. What should it do?",
  "system_prompt": "You are a cautious social-navigation assistant.",
  "temperature": 0.2
}
```

## Connect from Navel or another network computer

Find the LAN IP of the computer running this gateway. If it is `192.168.1.100`,
the Navel observation client should use this gateway base URL:

```text
http://192.168.1.100:6060
```

`ObservationTransport` appends `/api/v1/observations`. Do not point the Navel
pipeline at `/chat`; that endpoint accepts free-form text, not an
`ObservationFrame`.

Port `6060` must be permitted by the server firewall. Ollama can remain bound to `127.0.0.1`; only this gateway needs to be exposed to the robot network.

Do not configure the Navel robot to use `127.0.0.1`, because that address would refer to Navel itself. It must use the gateway computer's actual LAN IP.

The separate dependency-free `client.py` checks only the optional `/chat`
diagnostic and does not exercise the observation pipeline:

```bash
python3 client.py \
  --server http://192.168.1.100:6060 \
  "Reply with a short connection confirmation"
```

## API

### `GET /health`

Checks whether Ollama is reachable and whether `OLLAMA_MODEL` is installed. It returns HTTP `503` if the runtime or selected model is unavailable.

### `POST /chat`

Request body:

```json
{
  "message": "Required input string",
  "system_prompt": "Optional instruction",
  "temperature": 0.2
}
```

Successful responses contain `provider`, `model`, `input`, and `output`. Upstream Ollama failures return HTTP `502`.

### `POST /api/v1/observations`

Accepts one canonical `ObservationFrame`. It always performs contract validation
and temporal estimation. The scheduler calls the LLM only for a social event or
active-scene refresh, unless `?force_decision=1` is supplied for a short
diagnostic.

When a decision is triggered successfully, the response contains a complete
`BehaviorIntent`:

```json
{
  "accepted": true,
  "observation_id": "navel-5010005:123456789:000001",
  "social_state_id": "state-...",
  "decision_triggered": true,
  "forced_decision": false,
  "triggers": ["HUMAN_DETECTED"],
  "behavior_intent": {
    "schema_version": "1.0",
    "decision_id": "decision-...",
    "observation_id": "navel-5010005:123456789:000001",
    "social_state_id": "state-...",
    "created_at_us": 123456789,
    "action": "ORIENT",
    "target_human_id": "17",
    "preferences": {
      "target_speed_mps": null,
      "preferred_social_distance_m": null,
      "passing_side": null,
      "orientation_target_rad": 0.0,
      "hold_duration_s": null
    },
    "valid_for_ms": 1000,
    "reason_codes": ["HUMAN_DETECTED"],
    "decision_confidence": 0.75
  }
}
```

An accepted frame can legitimately return `"behavior_intent": null` when the
scheduler finds no reason to call the model.

## Run tests

The tests use a fake model, so they do not need Ollama:

```bash
python3 -m unittest discover -s tests -v
```

## Domain contracts

The robot-independent pipeline contracts are strict Pydantic models in
`app/domain/models.py`:

- `ObservationFrame` contains synchronized raw observations and image references.
- `SocialState` contains temporal, derived, and uncertainty-aware social state.
- `BehaviorIntent` contains a high-level policy proposal that must be validated
  before execution.

Their versioned JSON Schema files are committed under `schemas/v1/`. Regenerate
them after changing a model:

```bash
python3 -m app.domain.schema
```

The test suite fails if the committed schemas do not match the models.

## Synthetic museum and laboratory scenarios

The synthetic adapter produces deterministic, contract-valid `ObservationFrame`
sequences for newcomer encounters without requiring Navel or Ollama. Exact map
trajectories and scenario labels are kept in separate ground truth so they do not
leak into perception inputs.

List the available guide-robot scenarios:

```bash
python3 -m app.adapters.synthetic --list
```

Export one scenario as replayable newline-delimited JSON:

```bash
python3 -m app.adapters.synthetic \
  --scenario newcomer_requests_guidance \
  --output recordings/synthetic/newcomer_requests_guidance.jsonl
```

Optional position noise, gaze noise, and observation dropout are deterministic
for a supplied seed:

```bash
python3 -m app.adapters.synthetic \
  --scenario newcomer_occluded_by_exhibit \
  --output recordings/synthetic/noisy_occlusion.jsonl \
  --seed 42 \
  --position-noise-std-m 0.05 \
  --gaze-noise-std 0.03 \
  --dropout-probability 0.05
```

The initial catalogue covers requests for guidance, non-engaging passersby,
path crossing, normal following, falling behind, exhibit occlusion, and a pair
of newcomers requesting guidance.

## Reading observation JSONL

`JsonlObservationIterator` streams recordings without loading the complete file
into memory. Each line is validated as an `ObservationFrame`, and timestamps
must be strictly increasing by default:

```python
from app.adapters.jsonl import JsonlObservationIterator

source = JsonlObservationIterator(
    "recordings/synthetic/newcomer_requests_guidance.jsonl"
)
for observation in source:
    # Pass only the observation to temporal state estimation.
    print(observation.observation_id, observation.timestamp_us)
```

Ground truth is deliberately available only through the separate record API:

```python
for record in source.iter_records():
    observation = record.observation
    ground_truth = record.ground_truth  # Evaluation only; never model input.
```

Set `require_ground_truth=True` for evaluation jobs that should fail if labels
are absent. Malformed JSON, contract violations, empty files, and non-monotonic
timestamps raise `JsonlObservationError` with the source path and line number.

## Temporal social-state estimation

The deterministic MVP estimator consumes every observation and emits one
contract-valid `SocialState`. It maintains five seconds of per-person history
and derives basic motion, distance trend, gaze history, attention, engagement,
short-occlusion prediction, proxemics, and crowd counts:

```python
from app.adapters.jsonl import JsonlObservationIterator
from app.state import TemporalSocialStateEstimator

observations = JsonlObservationIterator(
    "recordings/synthetic/newcomer_requests_guidance.jsonl"
)
estimator = TemporalSocialStateEstimator()

for social_state in estimator.process(observations):
    print(social_state.state_id, social_state.humans)
```

Use a fresh estimator per experiment or call `reset()` before starting another
recording. The MVP requires monotonic `ROBOT_BASE` observations. Missing cues
remain unknown, and a missing track is retained for up to two seconds as
`predicted_only` with increasing uncertainty. Ground truth never enters the
estimator.

This version uses transparent thresholds and finite differences. Kalman
filtering, rotation-aware ego-motion compensation, advanced trajectory and path
conflict prediction, group inference, learned engagement, and crowd-flow
estimation remain later improvements.

## Event-driven decision scheduling

`DecisionScheduler` examines every `SocialState` but requests a model decision
only when the social situation changes or an active-scene refresh is due:

```python
from app.decision import DecisionScheduler

scheduler = DecisionScheduler()
for social_state in estimator.process(observations):
    request = scheduler.evaluate(social_state)
    if request is None:
        # Maintain the controller's existing behaviour: initially, keep roaming.
        continue

    print(request.trigger_codes)
    # intent = policy.decide(request.state)
```

The first detected human triggers a decision so the future policy can choose
between continuing the fixed roaming route and actions such as approaching or
greeting. Further triggers include human departure, motion, distance trend,
attention, engagement, speaking, proxemic-zone, robot-context, occlusion, and
reacquisition changes. Events within the default 0.5-second minimum interval
are coalesced, and an active human scene is refreshed every two seconds even if
no discrete event occurs. Empty unchanged scenes do not invoke the model.

Use a fresh scheduler per experiment or call `reset()`. Timing and departure
behaviour are configurable through `SchedulerConfig`.

## Navel observation-to-policy pipeline

The Navel integration reads perception and locomotion only. It does not call any
motion, navigation, head, gaze, speech, or actuator API. The server validates the
LLM selection as `BehaviorIntent`, but the robot client only prints it.

The implemented flow is:

```text
Navel next_frame/next_locomotion
  -> robot-side canonical ObservationFrame dictionary
  -> POST /api/v1/observations
  -> server-side Pydantic validation
  -> TemporalSocialStateEstimator
  -> DecisionScheduler
  -> Ollama only when scheduled
  -> schema validation and canonical BehaviorIntent
  -> intent returned for inspection, never execution
```

The prompt and its research rationale are documented in
[`docs/llm-policy-prompt-design.md`](docs/llm-policy-prompt-design.md).

Server state is isolated by `capabilities.adapter_id`. Give each robot or replay
source a stable unique adapter ID. A non-increasing timestamp for the same ID is
rejected with HTTP 409 instead of resetting temporal history silently.

### Laptop/server setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
ollama pull qwen2.5:7b
```

Start Ollama in one terminal:

```bash
ollama serve
```

Start the gateway in another:

```bash
source .venv/bin/activate
API_HOST=0.0.0.0 python3 -m app.server
```

Confirm the selected model is reachable:

```bash
curl http://127.0.0.1:6060/health
```

Only the Flask port needs to be reachable from the robot. Keep Ollama local when
possible. The Navel command must use the laptop's LAN address, not `127.0.0.1`.

### Navel setup and run command

The Navel SDK is expected to already be installed on the robot. From a directory
where the repository may be stored:

```bash
git clone https://github.com/hkim807/P4P.git
cd P4P
git fetch origin
git switch feature/behavior-intent-output-mapper
git pull --ff-only
python3 --version
python3 -m robot.navel_client.main \
  --server http://<LAPTOP_IP>:6060 \
  --adapter-id navel-<ROBOT_ID>
```

Until this feature is merged into `main`, both computers must check out
`feature/behavior-intent-output-mapper`. The robot must use Python 3.10 or newer.
The Navel SDK is provided on the robot. The client uses only that SDK and the
Python standard library: no virtual environment, internet connection, or
installation from the server's `requirements.txt` is required. The
`requirements-navel.txt` file records that there are currently no additional
PyPI dependencies.

The client collects locomotion concurrently, keeps only the newest unsent frame,
and performs blocking standard-library HTTP in a worker thread. Temporary SDK
and HTTP timeouts are reported without immediately terminating collection.

Before running the full pipeline on the robot, verify the dependency boundary:

```bash
python3 -c "import robot.navel_client.main; print('Navel client imports OK')"
```

Useful diagnostics:

```bash
# Print canonical observations locally without HTTP or Ollama calls.
python3 -m robot.navel_client.main --print-only

# Make this diagnostic run bypass normal scheduling.
python3 -m robot.navel_client.main \
  --server http://<LAPTOP_IP>:6060 \
  --force-decision
```

`--force-decision` defaults off; leaving it enabled can request an LLM decision
for every transmitted frame. Stop that diagnostic immediately after confirming
one round trip.

If locomotion velocity is temporarily unavailable, the client skips perception
frames by default. `--stationary-velocity-fallback` explicitly substitutes zero
velocity, but it is valid only when the physical robot is confirmed stationary.

The SDK documentation does not identify any `g_head_position` coordinate as a
robot-base origin. Consequently, 3-D human position is omitted by default. After
physically verifying a coordinate label and transform as equivalent to the
contract's `ROBOT_BASE`, opt in explicitly:

```bash
python3 -m robot.navel_client.main \
  --server http://<LAPTOP_IP>:6060 \
  --robot-base-coordinate-system <VERIFIED_SDK_LABEL>
```

Images are not transported. `id_score` is not mapped because its meaning is not
documented, and SST activity is not associated with people. Body orientation,
speech activity, and groups remain absent.

### Observation API

`POST /api/v1/observations` accepts exactly one canonical `ObservationFrame`.
Invalid contracts return HTTP 400. Out-of-order frames return HTTP 409. An LLM
provider failure or invalid structured selection returns HTTP 502 while retaining
`"accepted": true`, because estimation and scheduling have already succeeded
and their temporal state is preserved.

For a single manual end-to-end check, use:

```text
POST /api/v1/observations?force_decision=1
```

The `triggers` array contains only actual `DecisionTrigger` enum values. A forced
decision with no scheduler event therefore returns an empty trigger array and
`"forced_decision": true`.

### Safe staged verification

1. On the gateway computer, run `python3 -m unittest discover -s tests -v`.
2. Start Ollama and the gateway, then confirm `GET /health` returns HTTP 200 and
   `model_available=true`.
3. On Navel, run the client with `--print-only`. Confirm frames contain plausible
   person IDs, distances, gaze values, and measured locomotion velocity.
4. Start the gateway with `DECISION_MODE=DEBUG`, open
   `http://<LAPTOP_IP>:6060/debug`, then run Navel normally against the same
   gateway. A visible person should cause
   `accepted=true`, a `social_state_id`, `HUMAN_DETECTED`, and eventually a
   structured `[NAVEL BEHAVIOR] ... dry_run=true` log. Confirm the selected
   source advances to a complete snapshot on the Debug page.
5. If no person is present, use one brief `--force-decision` run to exercise the
   structured Ollama response, then stop it immediately. Confirm the page shows
   zero observed humans and preserves uncertainty instead of inventing a person.
6. For one snapshot, compare the raw `SocialState`, exact system/user prompts,
   original response, validated output and recommended action. Confirm their
   request ID and state ID remain together when newer observations arrive.
7. Confirm malformed Debug output is retained as
   `invalid_llm_debug_response`, rather than being returned as an intent, and
   that the next valid inference can replace the failed snapshot.
8. Restart with `DECISION_MODE=NORMAL` and confirm the response contains a valid
   `behavior_intent` without a `debug_snapshot` field.

The live test is successful only after a response contains a non-null
`behavior_intent` that matches `schemas/v1/behavior-intent.schema.json`.

This proof of concept remains read-only throughout these stages. A structured
LLM selection is parsed and validated into `BehaviorIntent`, mapped to an
action-specific command, and dispatched to a dry-run handler; it is never
executed as a robot behavior.

## Next milestone

Add deterministic state-freshness, action-precondition, capability, and motion
safety validation plus a conservative fallback policy. Only after those gates
and shadow-mode trials should a robot-specific executor consume an intent.
