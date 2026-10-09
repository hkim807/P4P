# Optional live model inference (Step 6)

The receiver can run the existing SocialState LLM and image-only VLM policies
alongside its rule policy. Model results are written to a separate JSONL file.
Outside an opted-in SingleTrial, model results remain audit-only. They do not
enter target locking, command planning, execution feedback, or the robot's
command dispatcher. The existing `policy_decision`, `target_lock`, and
`robot_command` response fields continue to come from the rule pipeline.

## Selected SingleTrial delivery

`--single-trial-policy rules|llm|vlm` selects the sole decision source. Model
trials register at `/api/v1/model-trials`, attach their identity to observation
POSTs, and poll `/api/v1/model-trials/result` every 0.1 seconds. The existing
worker publishes pending/succeeded/failed results in memory before audit flushing.
Only success contains the strict `{action, reason}` decision; correlation fields
(trial ID, server session, policy, request ID, source state and robot timestamp)
and model/image/timing diagnostics stay outside it. At most 16 trials are retained,
each with one outstanding request, cleared on close or the registered deadline.
Outside trials, existing sampling and paired `both` audits remain available.

The robot's `--model-result-max-age` is provisionally **10 seconds**, measured
from the original observation on its monotonic clock. The current single-person
scene and server readiness still require fresh observations under the unchanged
`--max-decision-age` (one second). LLM keeps structured evidence requirements;
VLM instead requires a fresh matched head image and no gaze classification.
Failure or expiry permits a fresh selected-policy request within the existing
30-second trial deadline, with no fallback action. Late/duplicate results cannot
change a decided or terminated trial. Registration rejects incompatible server
configuration before route startup. Route cancellation, settling and zero base
velocity precede polling/close acknowledgements; acceptance ends at DECIDED with
no final-action execution.

For rules, start the receiver with `--social-output var/rules.social.jsonl` and
no model flags. For LLM, use `--model-inference llm --llm-model <installed-model>
--model-output var/llm.models.jsonl`. For VLM, use `--model-inference vlm
--vlm-model <installed-vision-model> --model-output var/vlm.models.jsonl
--sdk-output var/vlm.sdk.jsonl --model-allow-receipt-match`; the last flag opts
into the existing bounded receipt association when exact SDK timestamps differ.
Choose new output paths each run. On the robot use:

```bash
python3 -m robot.navel_client.main --server http://COMPUTER_IP:6060 \
  --decision-dry-run --single-trial --single-trial-policy rules
# Replace rules with llm for LLM. For VLM replace rules with vlm and append:
# --model-provenance --sdk-capture --camera-capture --camera-interval 0.5
```

Append `--route-trial` to use the existing real moving baseline. Mocked software
checks do not establish camera availability, inference latency, or physical stop.

## Receiver commands

Run these from the repository root with the computer dependencies installed and
Ollama listening at `http://127.0.0.1:11434`. Each command uses fresh destinations;
choose another name for each subsequent run. Model inference is disabled by
default. These four modes are explicit:

```bash
# Disabled: existing raw receiver behavior, no Ollama calls.
.venv/bin/python -m app.server --host 127.0.0.1 --port 6060 \
  --output var/recordings/live-disabled-01.jsonl --model-inference disabled

# LLM only: the same complete SocialState used by the rule policy.
.venv/bin/python -m app.server --host 127.0.0.1 --port 6060 \
  --output var/recordings/live-llm-01.jsonl --model-inference llm \
  --llm-model qwen2.5:3b --llm-timeout 120 --llm-temperature 0 \
  --llm-seed 7 --llm-num-predict 256 \
  --model-output var/temporal-validation/live-llm-01.models.jsonl

# VLM only: camera HTTP ingestion works without a camera recording directory.
.venv/bin/python -m app.server --host 127.0.0.1 --port 6060 \
  --output var/recordings/live-vlm-01.jsonl \
  --sdk-output var/recordings/live-vlm-01.sdk.jsonl --model-inference vlm \
  --vlm-model gemma3:4b --vlm-timeout 120 --vlm-temperature 0 \
  --vlm-seed 7 --vlm-num-predict 256 \
  --model-output var/temporal-validation/live-vlm-01.models.jsonl

# Both: independent models, paired state IDs, optional raw SDK/camera recording.
.venv/bin/python -m app.server --host 127.0.0.1 --port 6060 \
  --output var/recordings/live-both-01.jsonl \
  --sdk-output var/recordings/live-both-01.sdk.jsonl \
  --camera-output-dir var/recordings/live-both-01.cameras \
  --social-output var/temporal-validation/live-both-01.social.jsonl \
  --model-inference both --llm-model qwen2.5:3b --vlm-model gemma3:4b \
  --llm-timeout 120 --vlm-timeout 120 --llm-temperature 0 --vlm-temperature 0 \
  --llm-seed 7 --vlm-seed 7 --llm-num-predict 256 --vlm-num-predict 256 \
  --model-sample-interval 1 --model-queue-capacity 4 --model-max-camera-age 1 \
  --model-camera-cache-capacity 8 \
  --model-output var/temporal-validation/live-both-01.models.jsonl
```

Use `--host 0.0.0.0` for direct LAN access, or use the existing reverse SSH tunnel
in [the runbook](runbook.md). Each policy has independent `--llm-base-url` or
`--vlm-base-url`, model, timeout, temperature, seed, and prediction-token settings.
Selected policies require explicit model names and a new `--model-output`.
Configuration is validated before serving. Existing files, symlinks, duplicate
destinations, and nested receiver destinations are rejected in enabled modes.
Social processing is automatically enabled for any model mode, even without
`--social-output`. Existing tracking, temporal, lock, and command configuration
options remain available.

## Sender and provenance contract

The existing sender command still posts the original raw format:

```bash
python3 -m robot.navel_client.main --server http://127.0.0.1:16060
```

For correlated model input, run the sender in the robot's SDK environment with
the existing SDK and camera capture options plus the new opt-in flag:

```bash
python3 -m robot.navel_client.main --server http://127.0.0.1:16060 \
  --minimum-send-interval 0.1 --request-timeout 5 --max-locomotion-age 1 \
  --sdk-capture --camera-capture --camera-interval 0.5 --model-provenance
```

This command reads the real robot; it is distinct from the local recorded-input
check below. Without `--head-focus` or executor flags, it enables no robot
movement or speech. `--model-provenance` requires both capture flags and excludes
SDK-only capture. Receivers used with this command must enable `--sdk-output`
for the sender's independent SDK stream and either VLM inference or
`--camera-output-dir` for camera delivery. The existing stream senders retain
their delivery semantics; model inference itself has no retries.

The opt-in observation body is:

```json
{"observation": {"timestamp": 1000000, "people": [], "robot": {"linear_velocity": null, "angular_velocity": null}, "safety": {"lidar": null, "sonar": null}}, "model_source": {"version": 1, "clock": "robot-host-monotonic-us", "capture": {"capture_version": 1, "session_id": "shared-capture-UUID", "stream": "perception", "sequence": 1, "received_monotonic_us": 999990, "received_unix_us": 1700000000000000, "packet": {"time": 12345, "persons": []}}}}
```

The raw schema is unchanged. The complete SDK perception capture is paired with
its raw observation before the sender's latest-only queue, so replacement cannot
mix their provenance. Existing dispatchers and logs receive the original raw
observation. The camera and SDK collectors share the same capture UUID. The
version-1 collectors use robot-host `time.monotonic_ns()` for their receipt
timestamps; the adapter uses that clock for collection time. SDK capture occurs
before raw conversion. SDK packets need not arrive at the receiver before their
observations because the envelope carries the source record itself.

Legacy raw observations still work in enabled modes: the LLM can run, while the
VLM records unavailable provenance. Invalid envelopes are rejected before raw
persistence. Unknown clock declarations cannot establish camera association.

## Sampling, queue, and lifecycle

One worker per receiver processes selected jobs. Ollama HTTP requests and PNG
encoding run in that worker, outside request handling and estimation locks.
`--model-sample-interval` defaults to one robot-host second; zero selects every
accepted SocialState. Sampling uses source state timestamps, never inference
completion time. A selected job freezes the full validated SocialState and the
camera association immediately. Camera frames arriving later do not change it.

`--model-queue-capacity` defaults to four pending jobs, in addition to one active
job. If full, the newest selected input is dropped and audited as `queue_dropped`;
older queued jobs remain. A dropped selection consumes its sampling interval.
Both policies always share a job's `source_state_id`. Sampling skips return
`sampled_out` in the HTTP scheduling response and health counters, without a
model JSONL row. Disabled policies in selected rows have `not_run_disabled`.
No request waits for a future camera.
Memory is bounded by pending job count, per-frame capture limits, the camera
cache, and one active job. Jobs can retain selected RGB frames after cache
eviction. The cache defaults to eight head events and at most 64 MiB of RGB,
including explicit camera-unavailable events; chest frames are not model input.

Shutdown stops new work, records cancelled pending jobs, and waits for active
work and model flushing for the sum of selected HTTP timeouts plus five seconds.
An in-flight synchronous HTTP call cannot be cancelled. If that deadline elapses,
shutdown reports incomplete flushing; abrupt process termination can lose the
active result. The receiver
CLI disables the Flask development reloader; worker startup is idempotent and
process-aware. `/health` includes worker counts, activity, and output errors only
when enabled. Output failures are visible there and in receiver logs, while
observation processing continues. Request acceptance acknowledges observation
processing and model scheduling, not successful inference.

## Camera association and clocks

Camera transport remains the existing `/api/v1/camera-frames` RGB8/base64 contract.
No replay manifest or stored image is required. Only validated head frames from
the declared source capture session can be used. A new capture session replaces
the cache. Without recording, the first positive sequence may join an existing
camera stream; subsequent events must be contiguous, with identical last-event
retries accepted. Recording retains its existing sequence-1 start requirement.

Exact matching compares the SDK perception `packet.time` numerically with camera
`timestamp_us`, without unit conversion. Equality does **not** establish exposure
synchronization or a mapping from the SDK clock to UTC. The signed difference of
robot-host camera receipt minus perception receipt must also fall within
`--model-max-camera-age` (default one second). Exact matches can have positive
receipt differences and record that limitation explicitly.

`--model-allow-receipt-match` opts into prior receipt matching when no exact
timestamp match exists. It requires the supported common robot-host monotonic
clock and original capture UUID, selects only the latest camera received at or
before perception, and applies the same age limit. Ambiguous, stale, missing,
cross-session, and incompatible-clock inputs yield unavailable/error results.
Receiver-host receipt clocks are recorded separately and never compared with
robot-host clocks. Arrival order, capture UUID declarations, and transport delays
are not proof of simultaneous exposure.

The selected RGB raster is encoded losslessly to one PNG in memory. VLM messages
remain exactly the existing static English system/user instructions plus one
PNG image. No SocialState, model/rule decision, ID, filename, timestamp, or matching
metadata enters those messages.

## Results and local execution check

The model JSONL contains source state/session/ingest identifiers, the frozen
complete SocialState, source capture and separate receiver receipt timestamps,
effective model configuration, camera association and limitations, RGB/PNG hashes,
and separate `llm_inference` and `vlm_inference` diagnostics. Diagnostics retain
requested/returned models, prompt version, raw content, strict two-field decision
or error, and request duration. Inference-host UTC completion is separate from
source timestamps. Statuses distinguish success, model failure, unavailable or
invalid images, queue drops, and work not run. Rows are emitted on completion or
skip/drop, so file order can differ from source ingest order.

The top-level keys include `source_state_id`, `session_id`, `ingest_sequence`,
`source_robot_timestamp_us`, `social_state`, canonical `social_state_json`,
`source`, `scheduling`, `completed_at`, and the two policy diagnostics.
`source.model_source.capture` preserves the SDK perception record;
`source.receiver_received_monotonic_us` and `receiver_received_unix_us` describe
HTTP receipt. Each policy has its own `ollama_configuration`, `completed_at`,
`raw_content`, `decision`, `error`, and `request_duration_s`. VLM diagnostics add
`image_matching`, `verified_image_sha256`, and `image_encoding`. Live source
image hashes cover raw RGB bytes; replay source hashes continue to cover PPM
files. PNG hashes cover exactly the encoded image supplied to the model.

| Policy status | Meaning |
| --- | --- |
| `succeeded` | Real model response passed strict decision validation; `ok=true` |
| `failed` | Model/transport/validation failure; no decision or fallback |
| `input_unavailable` | No usable source-camera association; no VLM request |
| `input_invalid` | Image encoding failed; no VLM request |
| `queue_dropped` | Newest selected job exceeded pending capacity; no requests |
| `not_run_disabled` | This policy was not enabled for the selected row |
| `not_run_shutdown` | Pending work was cancelled during shutdown |
| `not_run_audit_failure` | Audit output was unavailable; subsequent work was stopped |

`ok=null` marks unavailable or skipped work; model and image-encoding failures
have `ok=false`. Input-validation failure before a job can be frozen is returned in
the scheduling response and health diagnostics. Failed social processing retains
the existing receiver error response and does not submit incomplete model input.

Focused checks require no robot or Ollama:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The real local execution check feeds existing SDK/camera recordings through the
live HTTP endpoints using the sender's transport contracts and the adapter. It
preserves original capture UUIDs, SDK values, RGB pixels, and robot-host receipt
times. Reconstructed raw collection timestamps use original perception receipt
times; the original adapter's exact collection instant cannot be recovered.
This checks receiver ingestion, worker isolation, real Ollama inference, and
strict results. It does not validate physical robot networking, fresh SDK camera
availability, concurrent arrival ordering on Navel, or exposure synchronization.
The execution report and exact local commands are stored with each run under
`var/temporal-validation/live-models-step6-*`; these generated artifacts are
ignored by Git. This check invokes no replay inference runner and contacts no
physical robot.

The completed Step 6 local run used scenario-04 inputs and produced two strict
LLM plus two strict VLM results with no model failures. Both paired state IDs
ended in `:1` and `:11`; each produced LLM `CONTINUE` and VLM `STOP`. Eleven
observations were accepted, ten during active inference, with a maximum
overlapping HTTP acknowledgement of 1.52 ms. The full suite passed 510 tests.
See the generated [execution report](../var/temporal-validation/live-models-step6-20261006T173259Z/execution-report.md)
and [paired model results](../var/temporal-validation/live-models-step6-20261006T173259Z/model-results.jsonl)
on the machine where this run was performed. Generated artifacts are not
committed. These results establish execution and isolation, not decision quality.
