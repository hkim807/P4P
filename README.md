# LLM/VLM Social Navigation Pipeline

This repository contains a read-only social-navigation proof of concept: canonical
observation contracts, synthetic/replay sources, temporal state estimation,
event-driven decision scheduling, and an HTTP gateway to a locally hosted Ollama
model. It also includes a Navel-side collector that reads SDK data and sends
canonical observations without commanding the robot.

![LLM/VLM social-navigation architecture](docs/architecture.jpg)

## Current scope

Implemented runtime paths:

```text
Text test client
        |
        | HTTP POST /chat
        v
Python gateway (port 6000)
        |
        | OpenAI-compatible API
        v
Ollama (127.0.0.1:11434)
        |
        v
Qwen 2.5 model
```

The canonical observation pipeline is separately available at
`POST /api/v1/observations`. It validates `ObservationFrame`, maintains temporal
state per adapter ID, schedules model calls, requests an Ollama JSON-schema
response, and returns a validated `BehaviorIntent`. VLM input, final validation
for execution, and physical robot control are not implemented.

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
  navel_client/   Read-only Navel adapter, transport, and executable collector
client.py         Minimal client for Navel or another computer
docs/
  architecture.jpg
schemas/v1/       Generated JSON Schemas for public contracts
tests/
  test_server.py  Offline chat API tests
  test_navel_adapter.py
  test_observation_pipeline.py
requirements.txt
```

## Prerequisites

- Python 3.10 or newer
- Ollama installed on the server computer
- Enough CPU/GPU memory for the selected model

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
API_PORT=6000
```

Start the gateway:

```bash
python3 -m app.server
```

## Test input and output

Check both the Ollama connection and selected model:

```bash
curl http://127.0.0.1:6000/health
```

Send an input to the model:

```bash
curl -X POST http://127.0.0.1:6000/chat \
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

Find the LAN IP of the computer running this gateway. If it is `192.168.1.100`, send requests from Navel to:

```text
http://192.168.1.100:6000/chat
```

Port `6000` must be permitted by the server firewall. Ollama can remain bound to `127.0.0.1`; only this gateway needs to be exposed to the robot network.

Do not configure the Navel robot to use `127.0.0.1`, because that address would refer to Navel itself. It must use the gateway computer's actual LAN IP.

The included dependency-free client can be copied to or run on Navel:

```bash
python3 client.py \
  --server http://192.168.1.100:6000 \
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
curl http://127.0.0.1:6000/health
```

Only the Flask port needs to be reachable from the robot. Keep Ollama local when
possible. The Navel command must use the laptop's LAN address, not `127.0.0.1`.

### Navel setup and run command

The Navel SDK is expected to already be installed on the robot. From a directory
where the repository may be stored:

```bash
git clone https://github.com/hkim807/P4P.git
cd P4P
git pull --ff-only
python3 -m robot.navel_client.main \
  --server http://<LAPTOP_IP>:6000 \
  --adapter-id navel-<ROBOT_ID>
```

The client collects locomotion concurrently, keeps only the newest unsent frame,
and performs blocking standard-library HTTP in a worker thread. Temporary SDK
and HTTP timeouts are reported without immediately terminating collection.

Useful diagnostics:

```bash
# Print canonical observations locally without HTTP or Ollama calls.
python3 -m robot.navel_client.main --print-only

# Make this diagnostic run bypass normal scheduling.
python3 -m robot.navel_client.main \
  --server http://<LAPTOP_IP>:6000 \
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
  --server http://<LAPTOP_IP>:6000 \
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

1. Run adapter and server tests locally: `python3 -m unittest discover -s tests -v`.
2. Start the server with a fake test LLM or the intentionally running Ollama backend.
3. Confirm `GET /health`.
4. Run the Navel client with `--print-only`.
5. Point the client at the laptop's reachable LAN endpoint.
6. Confirm `accepted=true` for canonical observations.
7. Confirm a returned `social_state_id`.
8. Enable one short `--force-decision` run to verify the Ollama round trip.
9. Stop it and return to normal scheduler-controlled operation.

This proof of concept remains read-only throughout these stages. A structured
LLM selection is parsed and validated into `BehaviorIntent`, then printed for
inspection; it is never executed as a robot behavior.

## Next milestone

Add deterministic state-freshness, action-precondition, capability, and motion
safety validation plus a conservative fallback policy. Only after those gates
and shadow-mode trials should a robot-specific executor consume an intent.
