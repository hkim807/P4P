# Offline LLM recording replay (Step 3)

`python -m app.llm_replay` accepts explicit `sdk`, `raw`, or `social` JSONL
inputs. It runs synchronously and builds SocialState plus the current
`social-state-llm-v10` prompt. The four-action output contract remains unchanged.
YIELD guidance uses measured conflict or the complete face/closing/low-gaze
condition described in [the social policy](social-policy.md). Use closed
recordings and a new output path for every run.

## Inputs inspected on this branch

| File under `var/recordings/` | Format | Perception / locomotion | Frames with people | Replay |
|---|---|---:|---:|---|
| `scenario-01.sdk.jsonl` | SDK capture v1 | 91 / 90 | 0 | Reconstruct |
| `scenario-02.sdk.jsonl` | SDK capture v1 | 84 / 82 | 27 | Reconstruct |
| `scenario-03.sdk.jsonl` | SDK capture v1 | 115 / 114 | 110 | Reconstruct |
| `scenario-04.sdk.jsonl` | SDK capture v1 | 55 / 56 | 0 | Reconstruct |
| `scenario-05.sdk.jsonl` | SDK capture v1 | 40 / 41 | 0 | Reconstruct |
| `scenario-06.sdk.jsonl` | SDK capture v1 | 77 / 77 | 4 | Reconstruct |
| `scenario-07.sdk.jsonl` | SDK capture v1 | 29 / 28 | 0 | Reconstruct |
| `scenario-08.sdk.jsonl` | SDK capture v1 | 117 / 118 | 32 | Reconstruct |
| `scenario-09.sdk.jsonl` | SDK capture v1 | 122 / 121 | 56 | Reconstruct |
| Numbered `01_approach_gaze.jsonl` through `07_moving_away.jsonl`, `decision-pilot-01/03.jsonl`, `run-01.jsonl` | RawObservation | One observation per line | Varies | Estimate |
| `S1-pass-by-01`, `S2-standing-gaze-01`, `S3-close-gaze-01`, `S4-path-crossing-01`, `S5-walking-away-01`, `stationary-approach-01`: `raw.jsonl` / `social.jsonl` | RawObservation / current SocialState | 105, 89, 81, 82, 160, 331 each | Varies | Estimate / load directly |
| Historical `live-*/observations.jsonl` and `live-*/trace.jsonl` | Wrapped observation / historical social and BehaviourIntent | — | — | Rejected |

The nine SDK files contain 730 perception and 727 locomotion records. Each
has one capture session, valid contiguous per-stream sequences and ordered
receipt timestamps. Empty people arrays in scenarios 01/04/05/07 remain empty.
The first perception in 02/03/07 has no earlier locomotion; its robot measurements
remain unavailable. Other attached locomotion records are within the default
one-second freshness limit (maximum observed age: 128,761 microseconds).
No actual detected person has `g_head_position`; other SDK head/eye coordinates
are not substituted.

## Reconstruction and clock provenance

SDK envelopes are validated with the existing capture validator. Attribute
structures for stored packets feed `NavelObservationAdapter`; this imports no
Navel SDK and opens no robot connection. Adapter measurement validation,
millimetres-to-metres conversion, UID filtering and unavailable readings are
retained. One reconstructed raw observation is produced for each perception.

Only the latest locomotion already encountered in the same capture session can
be attached. Freshness uses the difference between recorded receipt monotonic
times; `age > max_age` is stale, while the exact boundary is fresh. A later
packet is never attached retroactively. Missing/stale packets give null robot
measurements and ranges. `--max-locomotion-age` defaults to the live collector's
1.0 seconds and the effective value is recorded.

| Timestamp | Meaning and use |
|---|---|
| SDK `received_monotonic_us` | Robot-host monotonic receipt microseconds; reconstructed raw timestamp, estimator input, freshness and sampling |
| SDK `received_unix_us` | Robot-host Unix wall receipt microseconds; provenance only |
| SDK perception `packet.time`, locomotion `packet.odometry.time` | Original SDK source values; unit/clock unverified, preserved without comparison to receipt time |
| Raw `timestamp` / saved SocialState `robot_timestamp_us` | Original robot-host monotonic collection microseconds; estimation or direct snapshot sampling |
| Result `completed_at` | Replay-host UTC wall completion time; separate from all source clocks |

SDK receipt precedes the live adapter's conversion timestamp. The live latest-only
queue and rate-limited sender may also omit perceptions. These reconstructed
observations are therefore **not guaranteed to reproduce the transmitted live
frames**. No clocks are mapped and no original timestamps are rewritten.

Raw inputs reuse the existing raw reader with line provenance. Raw and SDK inputs
use independent `TrackingPipeline` and `SocialStateEstimator` instances per input
and contiguous source session. Defaults are unchanged; optional `--track-config`
and `--temporal-config` use the existing loaders and effective values are stored.
Generated processing IDs depend on content and session boundaries, never the
descriptive filename. Saved SocialState is validated and used directly, retaining
its session, state ID, config, versions, values and timestamp. Configuration
override flags are rejected for saved states.

Duplicate/backward perception or raw timestamps, backward mixed-stream receipt
times, sequence gaps, malformed data, and reappearing interleaved sessions fail
with a file/line error. Records are never reordered or repaired. Legacy
BehaviourIntent/MONITOR traces have no translation into the current contract.

## Selection and output

All observations reach tracking/estimation before selection, including observations
before `--warmup` and between calls. For each session, the first eligible state
has source time at least `first_state_time + warmup`. The next is the first actual
state at least `last_selected_time + sample_interval`; there is no interpolation
or wall-clock scheduling. Defaults are warmup 0 and interval 1.0 seconds. Interval
0 selects every state. Sessions and input files reset sampling; a positive
`--max-calls` caps selections globally across all inputs. Preparation uses the
same selection cap without making calls. Even after the cap, the remainder of
the inputs is validated and estimated, so later input errors still fail the run.

Each selected moment writes one JSONL row. `source` contains file/content digest,
format, line/record reference, original capture session, opaque processing session,
clock meanings, original record and locomotion provenance. This metadata is
outside the model messages. `processing` records the effective configuration.
`social_state_json` is the exact frozen canonical JSON retained by the returned
`LLMPolicyResult.prompt`; `social_state` is its complete decoded object. The row
reuses all `LLMPolicyResult.to_dict()` diagnostics and stores caller model settings,
sampling settings and UTC completion time. It does not add fields to ModelDecision.

`--prepare-only` calls `build_llm_prompt`, never constructs an Ollama client, and
writes `status: "prepared"`, `ok: null`, with null decision/content/duration.
The required model and URL flags describe intended configuration; preparation
does not check whether that model is installed. Inference rows are `succeeded`
or `failed`; failed calls retain the error and raw response with `decision: null`.
There is no fallback, retry or dropped failure row.

Outputs resolving to an input are rejected; exclusive creation refuses existing
files, including aliases. Source/write failure retains prior output rows, reports
`status: "partial"` and `complete: false`, and exits 2. A fully processed run
prints one JSON summary with observation/selection/success/failure counts,
`rows_written`, and the output location. Selection and inference outcome counts
include a result whose write fails; `rows_written` counts successfully flushed
rows, so a partial run reports that difference. Exit 0 means completed preparation or successful inference;
exit 1 means recorded inference failures; exit 2 means input/config/output failure.
The summary's `complete` describes source processing, not model decision quality.

## Commands

Replace the model placeholder with a model already installed on your Ollama
server. Choose a fresh result path each time. These commands neither start a
service nor download a model.

```bash
# Prepare all nine scenarios; no network calls, no model installation required.
.venv/bin/python -m app.llm_replay var/recordings/scenario-0{1..9}.sdk.jsonl \
  --format sdk --prepare-only --sample-interval 1 \
  --base-url http://127.0.0.1:11434 --model '<installed-model-name>' \
  --temperature 0 --seed 42 --num-predict 256 \
  --output /tmp/p4p-scenarios-prepared.jsonl

# Three calls on scenario 03, after one source second of warmup.
.venv/bin/python -m app.llm_replay var/recordings/scenario-03.sdk.jsonl \
  --format sdk --warmup 1 --sample-interval 1 --max-calls 3 \
  --base-url http://127.0.0.1:11434 --model '<installed-model-name>' \
  --timeout 30 --temperature 0 --seed 42 --num-predict 256 \
  --output /tmp/p4p-scenario-03-llm.jsonl

# Multiple scenarios, global bound of nine calls.
.venv/bin/python -m app.llm_replay var/recordings/scenario-0{2,3,8}.sdk.jsonl \
  --format sdk --warmup 1 --sample-interval 2 --max-calls 9 \
  --base-url http://127.0.0.1:11434 --model '<installed-model-name>' \
  --timeout 30 --temperature 0 --seed 42 --num-predict 256 \
  --output /tmp/p4p-multiple-scenarios-llm.jsonl

# Already estimated traces preserve recorded config exactly.
.venv/bin/python -m app.llm_replay var/recordings/S3-close-gaze-01/social.jsonl \
  --format social --prepare-only --base-url http://127.0.0.1:11434 \
  --model '<installed-model-name>' --output /tmp/p4p-saved-social-prepared.jsonl
```

Structural example below abbreviates the full source and SocialState objects;
actual JSONL contains both complete objects and the exact canonical state string.

```json
{"schema_version":1,"status":"prepared","source":{"input_path":"var/recordings/scenario-03.sdk.jsonl","input_format":"sdk","reconstructed":true,"line_number":1,"original_capture_session":"3425596259d0482da6d9275fa8b10c83","processing_session_id":"replay-<content/session-digest>","timestamps":{"received_monotonic_us":13766270418,"received_unix_us":"<recorded wall time>","sdk_perception_timestamp":"<original packet.time>"},"source_record":"<complete SDK envelope>"},"processing":"<effective configs and versions>","sampling":{"interval_us":1000000,"warmup_us":0,"max_calls":null,"selected_index":1},"social_state":"<complete frozen SocialState>","social_state_json":"<exact canonical JSON sent as observation data>","source_state_id":"replay-<digest>:1","session_id":"replay-<digest>","source_robot_timestamp_us":13766270418,"prompt_version":"social-state-llm-v10","ollama_configuration":"<model, URL, timeout, generation settings>","ok":null,"decision":null,"error":null,"requested_model":"<installed-model-name>","returned_model":null,"raw_content":null,"request_duration_s":null,"completed_at":"<replay-host UTC ISO time>","completion_clock":"replay-host UTC wall clock; independent of source clocks"}
```

For successful inference the decision is the unchanged two-field
`{"action":"ENGAGE","reason":"..."}` contract; failures instead contain the
existing Ollama diagnostic category/message/status and available raw content.

## Later camera matching

Original capture session, source file digest, SDK source values, receipt clocks,
stream sequence and source record are retained for a later explicit matching
step. There is no image reference in RawObservation or SocialState and no clock
mapping established here. SDK and camera source timestamp equality cannot be
assumed for every frame; capture frequencies differ. This step does not read
camera manifests, match frames, convert images or make VLM requests.

## Verification on the current recordings

The results below are historical verification from 2026-10-07 under prompt v3.
Saved SocialState files must match the current schema; reconstruct raw or SDK
recordings to regenerate traces after removed-field changes.

Preparation-only CLI runs on 2026-10-07 completed with interval 1 second and
warmup 0, without a model call:

| Input group | Observations processed | Prepared snapshots | Errors |
|---|---:|---:|---:|
| Nine SDK scenarios | 730 | 73 | 0 |
| Sixteen current raw recordings | 2,643 | 261 | 0 |
| Six current saved SocialState traces | 848 | 83 | 0 |

Prepared counts for SDK scenarios 01 through 09 were respectively
9, 8, 11, 6, 4, 8, 3, 12, 12. The decoded snapshots, frozen canonical strings,
source timestamps and prepared/null diagnostics were checked for consistency.

The following **183 tests passed**, including 40 new replay tests and 143
existing contract/client/policy/recording/tracking/social/command regressions:

```bash
.venv/bin/python -B -m unittest \
  tests.test_llm_replay_inputs tests.test_llm_replay \
  tests.test_llm_policy tests.test_llm_runner \
  tests.test_model_decision tests.test_ollama tests.test_recording \
  tests.test_tracks tests.test_social_state tests.test_social_integration \
  tests.test_policy_rules tests.test_target_lock \
  tests.test_decision_dispatch tests.test_command_feedback
```

Automated inference uses fake results and checks transport-independent replay,
not action quality. The availability probe
`curl --noproxy '*' --silent --show-error --max-time 2 http://127.0.0.1:11434/api/tags`
returned connection refused. No real inference, service startup or model download
was performed. The inference commands above are ready for an available server
and installed model.
