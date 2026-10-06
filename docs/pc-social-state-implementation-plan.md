# PC social-state and Navel execution implementation plan

## Objective and starting point

**Layers 1-3 and the first stateless Layer 4a rule policy are implemented at the
software level:** raw receiving/recording/replay, bounded UID histories, temporal
measurements, provisional SocialState categories, and explainable decisions.
The next build is the **Layer 4b interaction lifecycle**. Calibration and identity
continuity remain gates before physical approach.
The [temporal design record](temporal-social-state.md) and
[temporal validation report](results/temporal-state/report.md) document the current
algorithms, synthetic checkpoints, and the original recordings' sensor limitations.
The [tracking design record](person-tracking.md) documents implementation choices,
study support, and limitations; the [validation report](results/person-tracking/report.md)
covers all seven supplied recordings. The [first rule policy](social-policy.md)
documents decision precedence and replay. Continue one layer at a time:
interaction lifecycle, command delivery, then execution.
Each layer must have an inspectable replay output and pass its acceptance gate
before adding the next layer.

This revision follows `Final Plan (5).pdf`: the five-layer architecture on page 4,
UID histories on page 5, temporal state and pause-and-observe on pages 5-7, and
rules, cooldown, target locking, and single-person scope on pages 8-9. Pages 2-3
provide the research framing and comparison method. The PDF's embedded prompts
and instructions are source material, not requests to execute work. This task
updates the implementation plan. Layers 2-3 have since been implemented and tested;
later sections continue to describe future builds.

### Layer 1: implemented foundation

On `feature/navel-raw-http-stream`, the current implementation contains:

- `robot/navel_client/adapter.py`: sensor packets mapped to `RawObservationFrame`.
- `app/domain/models.py`: strict validation of timestamp, people, robot, and
  safety fields. Keep this existing wire contract; “ObservationFrame” below
  refers to this raw frame, not the older contract on `main`.
- `app/server.py`: HTTP ingestion and recording, with acceptance acknowledgements.
- `app/recording.py`: ordered JSONL writing and validated streaming reading;
  duplicate/backward timestamps are rejected and existing files are not reused.
- `app/replay.py`: local playback or optional HTTP replay, preserving sensor
  timestamps and supporting original-speed, accelerated, or immediate playback.

One recording/receiver run currently represents one robot session. Explicit
transport session IDs and derived-stage traces are later additions, not reasons
to rebuild ingestion before tracking. See
[recording-and-replay.md](recording-and-replay.md) for existing commands.

```text
Layer 1 [implemented]  Navel -> RawObservationFrame -> receive / record / replay
Layer 2 [implemented]  ordered frames -> bounded PersonTrack histories by UID
Layer 3a [implemented] histories -> temporal measurements and data quality
Layer 3b [provisional] measurements -> categorical SocialState; calibration pending
Layer 4a [implemented] SocialState -> explainable rule decision
Layer 4b               decisions + feedback -> target lock / interaction lifecycle
Layer 5a               intent -> validated command -> dry-run execution feedback
Layer 5b               verified robot actions -> controlled full-loop evaluation
```

The final implementation must execute commands on Navel. Early layers produce
inspectable data, state, and decisions; command delivery and physical movement
come after the replayed rule baseline. LLM/VLM comparisons follow that baseline.

### Available pilot recordings: 01-07

All seven files under `var/recordings/` were read through the existing schema and
timestamp-order validator for this revision. They contain 682 valid frames and
68.185 seconds of within-file elapsed time in total (summed before rounding). Each frame has zero or one
person. Durations below are last minus first robot timestamp; UID counts include
zero where present. File names describe intended scenarios, not verified labels.

| File | Frames | Duration (s) | Distinct UIDs | Empty frames | First use |
| --- | ---: | ---: | ---: | ---: | --- |
| `01_approach_gaze.jsonl` | 127 | 12.689 | 3 | 48 | UID changes, disappearance, later distance/gaze features |
| `02_approach_no_gaze.jsonl` | 103 | 10.318 | 5 | 31 | UID changes and gaze calibration against intended no-gaze condition |
| `03_stationary_gaze.jsonl` | 83 | 8.287 | 1 | 0 | Simplest continuous-UID tracking demonstration; distance-noise baseline |
| `04_stationary_no_gaze.jsonl` | 114 | 11.379 | 3 | 50 | Missing/reappearing tracks and no-gaze calibration |
| `05_stationary_intermittent_gaze_eyeball.jsonl` | 90 | 9.026 | 1 | 0 | Second continuous-UID demonstration; eye-only gaze sensitivity |
| `06_stationary_intermittent_gaze_heaead_motion.jsonl` | 79 | 7.898 | 2 | 8 | Brief gaps and changed UID during head-motion condition |
| `07_moving_away.jsonl` | 86 | 8.586 | 7 | 1 | UID fragmentation and later increasing-distance features |

Keep the existing filenames, including the spelling in recording 06. Each file
starts a fresh tracker session, even when a numeric UID appears in another file.
The median inter-frame gap is about 85-86 ms, with maxima around 167-170 ms;
use timestamps, not an assumed fixed frame rate.

Observed limitations that shape the next layers:

- Recordings 01, 02, 04, 06, and 07 contain multiple UIDs over time. This is not
  evidence of multiple physical people or reliable identity continuity. A basic
  UID tracker must show those as separate tracks; it must not silently stitch
  them together because a filename suggests one participant.
- UID `0` occurs in 01, 02, and 07. The current schema and adapter accept it.
  Preserve it as a separate key and expose an identity-quality warning until
  its SDK semantics are checked; do not treat it as missing via a truthiness test
  or assume it is a stable person identity for later target locking.
- Valid gaze scores in 03 span approximately 0.896-0.987, while eye-only
  intermittent gaze in 05 spans 0.913-0.971. The no-gaze recordings also include
  high scores. A guessed threshold such as 0.8 will not establish the intended
  distinction. Compare annotated intervals before choosing gaze categories;
  report sensor limitations if these conditions cannot be separated.
- All seven have some null gaze measurements. No observed person has null
  distance, so add synthetic null-distance cases to test that behavior.
- Yaw is zero wherever available; recording 05 has null robot velocities, and
  07 includes forward velocity 0.003 m/s. These readings alone do not verify
  stationary-base conditions or yaw accuracy.

Use these as development/pilot inputs. Add a small sidecar manifest with file
hash, scenario, verified robot motion conditions, and manually annotated intervals
when calibrating. Scenario names and human labels must never become policy
inputs. Collect independent repetitions for held-out evaluation.

## Scope and constraints

Initial research scope is interaction-initiation during a fixed roaming route,
using gaze and relative distance history. Evaluate one intended interaction
target at a time. The input can contain several people, so histories remain
isolated by UID, but simultaneous multi-person tracking/selection and group
reasoning remain out of scope for the first baseline, as in the PDF. Keeping
different UID keys is necessary to avoid mixing samples when IDs change; it does not imply a
multi-person policy. Unexpected multiple visible people cause deferral.

The four social decisions are `CONTINUE`, `APPROACH`, `ENGAGE`, and `YIELD`.
`OBSERVE`, `COOLDOWN`, and `DEFER` describe internal processing, not extra labels
silently folded into those four decisions. A physical stop/hold command is also
distinct from the social reason for yielding. Local obstacle protection can
interrupt any action, independently of the social policy.

Important limits of the current stream:

- `timestamp` is robot-host monotonic microseconds. It is neither UTC nor a
  clock that can be subtracted from the PC's monotonic clock.
- Robot forward/yaw velocity is nullable and uses the newest locomotion packet,
  not an exactly synchronized measurement. Angular velocity still needs live
  validation from the existing on-robot diagnostic.
- Distances changing over time indicate relative separation changing. They do
  not establish that the person is moving when the robot is also moving.
- Optional Cartesian head position is in `CAM_HEAD`. A changing head-camera
  frame is not a calibrated robot-base/world frame.
- Lidar/sonar arrays currently retain native SDK units. Verify units, sensor
  ordering, update behavior, and invalid/no-return conventions before using
  thresholds expressed in metres for execution.
- There is no image, person speech, route state, command completion, or verified
  pedestrian-path-conflict field. Those facts must not be inferred from gaze.

For the first motion-dependent policy, collect stationary-base recordings and
then implement pause-and-observe on the robot. Treat human radial motion as
`UNKNOWN` whenever stationarity is unverified. Continue computing relative
distance trend for diagnostics while moving. Full ego-motion compensation is a
later experiment requiring reliable yaw and geometry.

## Layers and their interfaces

| Layer | Responsibility | Input -> output | Proposed location |
| --- | --- | --- | --- |
| 1. Ingestion (implemented; extend minimally) | Validate and preserve raw frames; add serialized downstream processing | Input frame -> accepted frame with local session context | Existing `app/server.py`, `app/recording.py`; proposed `app/pipeline.py` |
| 2. Track manager | Maintain bounded, ordered histories and visibility lifecycle for each UID | Envelope -> track snapshots | `app/state/tracks.py` |
| 3a. Feature extraction | Compute temporal gaze, robust distance trend, and data quality | Track snapshot -> temporal features | `app/state/features.py` |
| 3b. State estimator | Publish categorical state with supporting evidence and robot/safety validity | Features + context -> `SocialState` | `app/state/estimator.py` |
| 4. Interaction policy | Apply rule table, observation state, target lock, and cooldown | `SocialState` + policy context -> `BehaviorIntent` or defer | `app/policy/rules.py`, `app/policy/session.py` |
| 5a. Intent/command validation | Check target, age, action requirements, mode, and capability; construct bounded commands | Intent + newest state -> command or rejection | `app/commands/validator.py`, `app/commands/service.py` |
| 5b. Robot executor | Own route pause/resume, local checks, SDK actions, cancellation, and watchdog | Command -> execution events | `robot/navel_client/execution/` |
| Recording/replay | Record correlated stages and replay through identical PC logic | Envelopes/events -> reproducible traces | Existing `app/recording.py`, `app/replay.py`; separate derived-trace writer |

Put thresholds and timing in a versioned PC configuration file such as
`config/social-policy.json`. The sensor adapter should not gain gaze or engagement
thresholds. Keep Flask/Pydantic on the PC; robot code should retain the SDK plus
standard-library dependency boundary where practical.

### Input/session envelope

Keep the existing sensor JSON unchanged. Layer 2 needs only a local session key,
frame sequence, and the original timestamp around the validated frame. Use a
caller-supplied stable session key for replay and a fresh one per receiver run.
Grow this into the following envelope when integrating live traces and commands:

```text
ObservationEnvelope
  source_id
  session_id
  ingest_sequence
  robot_timestamp_us
  pc_received_monotonic_ns
  raw_frame
  quality_flags
```

For one robot, source identity can be receiver configuration. Add an explicit
stream-session handshake or transport header generated once per robot process
when command delivery is introduced. A timestamp regression must not silently
mix old histories with a new robot session. Duplicate/out-of-order frames remain
rejected from the accepted raw JSONL and must not update histories or issue
commands; a separate diagnostic trace may record the rejection reason.

When Layer 2 is connected live, serialize timestamp acceptance, raw persistence,
and tracker updates together for each session inside the threaded Flask server.
The existing writer lock serializes recording only; a callback outside that lock
is not sufficient to guarantee tracker update order. Use
bounded queues/history rather than starting an unconstrained worker per frame.
Keep raw recording separate from processing success: an accepted/persisted frame
may still have `processing_status: failed` and `command: null`.

### SocialState contract

Use one scene object containing person states, with at most one selected target:

```text
SocialState
  schema_version, state_id, source_id, session_id, ingest_sequence
  robot_timestamp_us
  config_version
  robot:
    linear_velocity, angular_velocity
    motion_state: STATIONARY / MOVING / UNKNOWN
    measurement_validity
  people[]:
    uid, track_epoch
    visibility: OBSERVED / TEMPORARILY_MISSING / LOST
    track_age_s, time_since_seen_s
    latest_distance_m
    gaze_state: NONE / INTERMITTENT / SUSTAINED / UNKNOWN
    distance_zone: TOO_CLOSE / INTERACTION_RANGE / APPROACHABLE / FAR / UNKNOWN
    relative_distance_trend: DECREASING / STABLE / INCREASING / UNKNOWN
    human_radial_motion: TOWARD / STATIONARY / AWAY / UNKNOWN
    evidence:
      gaze_fraction, gaze_valid_coverage_s, sustained_gaze_s
      distance_slope_mps, distance_valid_span_s, distance_fit_residual
      valid_sample_counts
    validity_flags
  safety:
    range_data_status: VALID / UNKNOWN / STALE
    calibrated_range_summary
    local_execution_status
  active_target_uid, active_target_track_epoch
```

`STATIONARY` for human radial motion means little radial movement; it does not
prove the person is motionless in every direction. A lateral passerby can have
almost constant range. Avoid a numeric engagement probability unless it has
been calibrated against labels. Data coverage and reasons are sufficient for
the interpretable baseline.

### Policy, command, and execution contracts

Separate a proposal from an instruction and its actual result:

```text
BehaviorIntent
  decision_id, source_state_id, session_id, policy_version
  decision_status: DECIDED / DEFERRED
  action: CONTINUE / APPROACH / ENGAGE / YIELD / null
  target_uid, target_track_epoch, reason_codes
  observation_directive: CONTINUE_OBSERVING / PAUSE_AND_OBSERVE / NONE

RobotCommand
  command_id, session_id, sequence
  source_robot_timestamp_us, source_state_id
  action, target_uid, target_track_epoch, bounded_parameters
  max_source_age_ms, execution_lease_ms

ExecutionEvent
  command_id, session_id
  status: RECEIVED / STARTED / COMPLETED / CANCELLED / REJECTED / FAILED
  reason, robot_timestamp_us, local_controller_state
```

An HTTP acknowledgement means a frame was accepted, not that a movement was
completed. A successful SDK method call may likewise only mean that an action
was submitted. Enter cooldown on an appropriate execution completion/interaction
event, not every time an `ENGAGE` proposal is generated.

## Layer 3 temporal algorithms

**History and tracking.** Start with a configurable rolling history of roughly
three seconds. Store original samples, not fabricated interpolated observations.
Track first/last seen time, missing intervals, and an epoch for a newly acquired
track. Expire missing tracks after a configurable grace period. Retention during
occlusion is memory, not evidence that a person remains visible or targetable.

**Gaze.** Classify each valid sample using separate looking-entry/exit thresholds,
then measure looking over a temporal window. Because HTTP can drop frames, prefer
a time-weighted fraction with bounded sample support. Do not extend the last
value through long gaps. Require minimum valid coverage, elapsed span, and sample
count before calling gaze sustained. Use separate category-entry/exit criteria
and a dwell time to prevent flickering between sustained/intermittent/none.
All-missing gaze produces `UNKNOWN`, not `NONE`.

**Distance.** Fit a robust slope to recent valid `(time_s, distance_m)` samples.
A small-window median of pairwise slopes is a simple deterministic starting
point; bound sample count for predictable cost. Reject implausible jumps and
large gaps rather than joining unrelated pieces of history. Retain the signed
slope and a fit-quality measure. Negative slope means separation is decreasing;
positive means increasing. Calibrate a deadband from stationary-person recordings.

**Motion interpretation.** The implemented estimator converts a relative trend
to human radial motion only when both recorded robot velocities are present and
near zero at every sample in the fitted distance segment. Validate the live
velocity channels and their freshness before using this label for physical
actions. Head/camera motion and face-distance noise remain limitations even
during stationary-base observation.

**Distance zones.** Calibrate the boundaries of four regions with separate entry/exit
limits. Do not adopt the PDF's mixed near/mid/far wording as several competing
enums. Keep one vocabulary throughout schemas, rules, traces, and evaluation.

**Loss of stream.** Run a PC timer independent of new frames. If the tunnel or
collector stops, mark state stale and stop issuing executable movement. On Navel,
a separate local watchdog expires active command leases even when HTTP is stuck.

## Layer-by-layer build sequence

Use one small branch/PR per increment, with a replay demonstration and a written
acceptance result. The sequence below replaces the old “build ingestion/replay
first” milestones. Layer 1 is complete at the software-contract level; physical
sensor calibration remains a separate task before interpreting cues or moving.

| Layer / status | Deliverable | Acceptance gate before proceeding |
| --- | --- | --- |
| 1. Raw input, recording, replay - implemented | Existing schema, receiver, JSONL writer/reader, replay CLI | All seven recordings validate; existing raw playback still works. |
| 2. Person tracking - implemented | Bounded UID histories, visibility lifecycle, session isolation, replay track trace | Passed: all 682 frames; continuous histories in 03/05; separate changed UIDs; lifecycle edge cases; identical traces at replay speeds 0/1/2 and through the receiver. See the linked validation report. |
| 3a. Temporal measurements - implemented | Windowed gaze evidence, robust distance slope, coverage and gap handling | Seven recordings and controlled stimuli pass source-time, validity, and replay checks. Evidence traces expose gaps/UID fragmentation. |
| 3b. SocialState - implemented with provisional thresholds | Gaze categories, distance zones, relative trend and conditional human radial motion, validity | Synthetic pattern checkpoints pass; output carries evidence/config/uncertainty. Calibration and human-labeled evaluation remain pending; original 04/05 show gaze-score ambiguity. |
| 4a. Rule decision - implemented | Pure rule table over SocialState; action or defer with reason ID | Branch tests and live/replay decision parity pass. No commands or robot dependency. See the [rule policy](social-policy.md). |
| 4b. Interaction lifecycle | Observe/decide, target lock, cooldown, completion/cancellation handling | Simulated feedback demonstrates the full state progression; missing/changed UIDs never transfer a lock; repeated frames do not retrigger engagement. Live inspection emits the same stage outputs as replay and detects stream loss. |
| 5a. Commands and dry-run round trip | Full session envelope, intent validation, command parsing/deduplication, fake executor, execution events | Real HTTP through the existing tunnel delivers correlated commands and feedback. Stale/lost-target/old-session/duplicate/unsupported commands are rejected or deduplicated. |
| 5b. Controlled physical execution | Verified local pause/hold/resume, one utterance, bounded approach, cancellation/watchdog | Enable and demonstrate one capability at a time, including target loss, obstacle-data loss, tunnel loss, operator override, and route arbitration. |
| Full-loop evaluation | Roam -> observe -> decide -> action -> feedback -> cooldown/resume | Repeated scenarios have matching state/decision/execution logs, frozen configuration, independent evaluation data, and reported human-alignment and execution metrics. |

### Layer 2: implemented increment and acceptance contract

**Purpose:** given a stream of accepted frames, answer “which UID was observed,
when was it seen, and what raw measurements have we retained for it?” It does
not yet answer whether a person wants an interaction.

Implemented files are `app/state/tracks.py`, `tests/test_tracks.py`, and the
shared PC processing entry point `app/pipeline.py`. `app.track` emits replay
snapshots; `app.validate_tracking` reproduces the seven-recording audit. The
existing `app/recording.py` and `app/replay.py` remain the foundation. Tracking
has no Flask, Navel SDK, or actuator dependency. See the design record for
effective defaults and operation; the original acceptance contract follows.

Minimum interface:

```text
TrackManager(config, session_id)
  update(validated_frame) -> TrackSnapshot
  reset(new_session_id)   -> empty session

TrackSnapshot
  session_id, frame_sequence, robot_timestamp_us
  tracks[]:
    uid, track_epoch
    first_seen_us, last_seen_us
    visibility: OBSERVED / TEMPORARILY_MISSING
    time_since_seen_s, observation_count, retained_sample_count
    identity_quality_flags
    samples[]: timestamp_us, distance_m, gaze_overlap, optional head position
  events[]: type (ACQUIRED / MISSING / REACQUIRED / LOST), uid, track_epoch, reason
```

`LOST` is an emitted terminal event at expiry; the expired track is removed from
the active map. Later state traces may carry that terminal state for the event,
but must not retain an unbounded archive of lost people in memory.

Processing rules:

1. Scope the key by `(session_id, uid, track_epoch)`. UID is an opaque tracking
   key, never a numeric feature in a social decision. Assign a fresh deterministic
   session-local epoch on every acquisition after expiry; no cross-session identity.
2. Process each accepted timestamp once in increasing order. A duplicate or
   backward frame must leave the complete tracker unchanged. Validate/order-check
   before mutation, including in direct offline use.
3. Before associating the new observations, expire tracks whose last-seen age
   exceeds the missing grace period. This prevents a same-UID return after a long
   frame gap from accidentally reviving an expired history.
4. For each currently observed UID, create or update its track and append the
   actual sample. Preserve null measurements and the original robot timestamp.
   A null gaze/distance still means the UID was observed.
5. A UID omitted from an accepted frame becomes temporarily missing. Empty
   `people` lists still advance time, prune histories, and drive loss events.
   Do not append invented zero/null person samples or carry the last gaze through
   the gap. A missing track is not a currently targetable person.
6. Reappearance of the same UID within grace keeps its epoch and retained history,
   with a gap/reacquisition event. Reappearance after expiry starts a new epoch.
   A different UID always starts a separate track, even with similar distance.
   Do not merge IDs or introduce person re-identification in this increment.
7. Prune all active histories, including missing tracks, using source time.
   Start with a configurable 3-second retention window (the PDF suggests 1-3
   seconds), plus a maximum samples-per-track and maximum active-track count.
   Keep first-seen metadata independent of the retained window. Define and trace
   deterministic capacity eviction; do not allow silent, unbounded growth.
8. Make missing grace and history/capacity limits explicit configuration. Grace
   must accommodate ordinary observed frame gaps, but its initial value is a
   development setting to validate, not a calibrated identity guarantee.

**Demonstration order:** replay 03, then 05, then all seven files separately.
Export a derived JSONL trace outside the raw input files, showing source file/
session, sequence, UID/epoch, visibility, first/last seen, sample count, retained
time span, and lifecycle events. Preserve the original recordings. Support a
readable console summary without requiring a UI.

Use the existing `read_frames` iterator and `replay(..., emit=...)` hook to feed
the same processing function used by the receiver. First prove the pure tracker
offline, then attach it after successful persistence in the receiver's serialized
processing path. Include the received robot context in the pipeline for later
features; the tracker need not interpret it.

Event-time calculations use `(timestamp_us - reference_us) / 1_000_000` and never
replay wall-clock speed. Replay EOF is a run boundary, not an invented later
sensor observation: close/reset the run without manufacturing disappearance
samples. A test may advance an explicit virtual clock to exercise expiry. Later
live stream-loss handling uses a separate PC watchdog; a silent transport is
not evidence that a visible person left.

**Acceptance tests:** continuous UID; two alternating UIDs with no sample mixing;
null gaze/distance; UID 0; empty frames; return just within grace and just beyond
it; same UID after a long input gap; history/capacity bounds; duplicate/regressing
timestamps with no mutation; new-session reset; identical outputs at replay
speeds 0/1/2 after excluding runtime timing metadata. Synthetic fixtures cover
edge cases missing from the seven real recordings. A minimal two-UID fixture
checks isolation only, not multi-person policy behavior.

**Done means:** the seven recordings can be replayed into independently scoped,
bounded track traces; their ID changes and gaps are visible; live frames can use
the same tracker entry point; existing raw recording/replay still works. Gaze
classification, slope estimation, social decisions, target selection, cooldown,
and robot commands belong to subsequent layers.

### Layer 3: measurements first, then categories

Implemented in `app/state/features.py`, `app/state/estimator.py`, and
`app/state/social_models.py`, with `app.social` replay and `app.validate_social`
validation. The receiver enables the same layer with `--social-output`.
The implementation follows the acceptance specification below; numerical settings
remain provisional. Robot-side persistence (`save` in the user's installed SDK;
`set_persist` in the public reference) is still not integrated. UID changes must
continue to split evidence until independently justified identity association exists.

Layer 3a should emit the evidence described in "Layer 3 temporal algorithms"
before tuning labels. Keep features scoped to UID/epoch and retain robot motion context over
the same window. Start with the continuous histories in 03/05; treat fragmented
clips as a test of coverage/unknown handling rather than fitting one slope across
all detected IDs. A clip called `moving_away` need not yield a valid `AWAY` label
at every frame.

Layer 3b then turns that evidence into the SocialState contract. Calibrate gaze
thresholds, category dwell, distance zones, slope deadband, and minimum coverage
on annotated development intervals. Use a single versioned configuration and
inspect the evidence beside every category. Insufficient sensor discrimination
is an experimental result, not a reason to force the expected filename label.

### Layer 4: rules first, then stateful interaction

Implement the table as a deterministic function of SocialState before adding
interaction memory. The policy consumes categorical cues and validity; raw gaze
thresholds stay in Layer 3. UID is carried for target binding only. Then wrap the
rule function with observation timing, lock management, cooldown, and simulated
execution events. A lock binds `(session_id, uid, track_epoch)`, not UID alone;
a re-acquired new epoch cannot inherit an old interaction automatically.

Cooldown prevents repeated engagement for a known track, but UID churn can still
make the same physical person look new. Report that limitation and quantify it
in replay/robot trials; do not claim UID-only cooldown solves re-identification.

## Initial rules and state-machine behavior

Apply validity and controller feasibility before the social table. The policy
must expose the rule ID and measured evidence used for every decision.

| Evidence / condition | Proposal |
| --- | --- |
| Sustained gaze, reliable toward/stable radial motion, interaction-range distance | `ENGAGE` with the current visible UID |
| Sustained gaze, reliable toward/stable radial motion, approachable distance | `APPROACH` with the current visible UID |
| Reliably moving away, or enough valid evidence for no attention | `CONTINUE` when route continuation is permitted |
| Intermittent gaze, insufficient coverage, unknown human motion, or ambiguous target | Defer and observe; request bounded pause-and-observe when available |
| Confirmed pedestrian route conflict from future verified geometry/local context | `YIELD` |
| Too-close distance, invalid/stale control inputs, or local obstruction | Block approach; robot-local hold/cancel under execution protection |

The PDF suggests approaching sustained-gaze people at far/mid distances. Narrow
this initially to a calibrated approachable region and the executor's verified
reach. Extend to far distances only after tested target visibility/navigation.
The current stream alone cannot distinguish a pedestrian conflict from a generic
obstacle. It can support conservative holds without claiming a socially identified
yield. Add explicit conflict context if `YIELD` needs a separate experimental label.

Policy state progression:

```text
ROAM -> OBSERVE -> DECIDE
DECIDE -> CONTINUE -> ROAM
DECIDE -> APPROACH -> ENGAGE -> COOLDOWN -> ROAM
DECIDE -> ENGAGE -> COOLDOWN -> ROAM
DECIDE -> YIELD -> OBSERVE or ROAM
Any active action -> cancelled/failed/lost/stale -> HOLD and re-evaluate
```

Pause-and-observe is a robot execution behavior: request a pause, confirm the
base has stopped, and only then collect a fresh stationary observation window.
The PC must not assume a pause proposal stopped the robot. While approaching,
range changes cannot be relabelled as human motion without compensation; use
executor feedback and current distance/visibility for action progress.

Target locking applies across observe/approach/engage. Release it on verified
completion, cancellation, loss, expiry, or session reset. Cooldown belongs to
the track/interaction context and prevents the same visible person retriggering
a greeting. It must not stop local obstacle protection or operator cancellation.

## Returning commands over the existing tunnel

Use the HTTP response to the existing observation POST for initial command
delivery. That response already travels from PC to Navel over the established
reverse tunnel; a second robot-facing HTTP server is unnecessary.

```text
POST /api/v1/observations -> accepted, processing_status, state_id, intent, command
POST /api/v1/execution-events -> execution-status acknowledgement
```

Run the rule estimator synchronously in the per-session processing path while
it stays bounded and fast. Return the latest validated command or null. Attach
session identity to both routes. A processing failure may preserve raw input
but must return `command: null`. Later slow model calls use a bounded asynchronous
policy worker; they must not block collection or execution protection.

Command age should be tied to the robot timestamp of the source frame. Navel
can compare that with its current monotonic time in the same session, including
the request/response delay. Do not compare PC wall-clock time with robot monotonic
time. An execution lease begins only after a command passes local validation;
refresh/renew it explicitly without repeating an already-started action.

Deduplication requires a stable command ID for one logical action, monotonic
command sequence within the session, and robot tracking of accepted/completed
IDs. Receipt of a duplicate returns its known status instead of calling the SDK
again. Superseding commands cancel the previous motion through verified physical
cancellation, not merely by forgetting a Python task.

On the robot, one controller arbitrates fixed-route motion, approach, holds,
speech, and operator override. Keep the HTTP worker and local watchdog independent.
Re-check the latest local person/sensor data before starting or continuing an
action; PC validation alone is insufficient after network delay.

## Installed SDK verification and executor sequencing

Before movement integration, record the installed SDK version and exercise
capabilities individually on Navel. The source PDF lists version 0.15.3 and
reports an approach limitation on its tested version. Treat this as recorded
project context, not proof of the current
installation. Verify availability, return types, completion semantics, and
physical stopping behavior for each intended capability on the installed robot.

Suggested executor increments:

1. Fake handlers and dry-run acknowledgements for every command type.
2. `ENGAGE` as one predefined utterance with base held stationary, deduplicated.
3. Verified local route pause/hold and resume, including cancellation and manual
   override. Store route progress locally so continue does not restart the route.
4. One short bounded approach with visible target, verified stop distance,
   timeout, cancellation, and local obstacle checks.
5. Full target-locked approach -> engage -> cooldown -> resume transition.

If SDK person approach is unsupported, plan a separate robot-local approach
controller using verified bearing/range geometry or a verified navigation
primitive. A scalar distance and uncalibrated head-camera coordinates do not
justify blindly commanding forward motion. Until that prerequisite is satisfied,
emit a capability rejection rather than pretending `APPROACH` executed.

Any direct velocity loop must run on Navel, outside PC HTTP timing. Verify the
installed SDK's refresh and timeout requirements before implementing one; the
sensor POST loop is not a motor-control clock.

## Test, calibration, and evaluation plan

**PC tests:** ordered/session-isolated ingestion; duplicate/regression handling;
history pruning; dropout/reacquisition; bounded gaze support; hysteresis; noisy
distance slope; valid-coverage minimums; moving-base ambiguity; every rule/state
transition; locked target; stale-state timer; schema and command requirements.

**Round-trip tests:** simulated robot -> real local HTTP -> PC pipeline -> command
parser -> fake executor -> feedback. Include repeated commands, delayed responses,
session restart, execution rejection, persistence/processing failure, target loss,
and missing feedback. Replays never instantiate real SDK actuator handlers.

**Robot checks:** valid/invalid yaw, calibrated native range values, supported
action list, physical cancellation versus task cancellation, measured command
age, obstacle response, watchdog expiration with an HTTP worker stalled, route
arbitration, and stop/resume behavior.

**Calibration:** record gaze glances/sustained attention and stationary/toward/away
movement at multiple distances. Separate calibration recordings from evaluation
recordings. Select gaze thresholds, dwell times, slope deadband, distance zones,
occlusion grace, cooldown, source-age limit, lease/watchdog, and stopping limits
from pilot evidence. Version and freeze them before comparisons.

**Evaluation:** use repeated scenarios with human action ratings, retaining the
distribution rather than treating ambiguity as a single unquestionable label.
Report majority-action agreement, alignment with rating distributions, false and
missed engagement, target switching/repeated engagement, decision consistency,
abstention rate, decision latency, execution success, and safety overrides.
Report inter-rater agreement alongside the human reference distribution. Keep
causal estimates separate from measured values and labels outside policy inputs.

Raw recordings, derived features, states, decisions, commands, and feedback
should share session/frame/state/decision/command IDs. Record source code commit,
configuration version, SDK version, policy version, mode, and processing latency.
PC stage durations use the PC clock; request latency uses the robot clock.

## Reuse from existing branches and later extensions

`main` already has estimator, scheduler, schema, replay, and dry-run behavior
patterns. Reuse focused pieces and their applicable tests. Do not merge the old
pipeline wholesale: its richer contract expects robot-base positions and robot
task/controller metadata, and some features rely on inputs absent from this
stream. The fixed-route speech demo is a useful execution integration reference,
but its task cancellation must be checked against actual physical stop behavior.

Once the rule loop passes full-loop evaluation, add model-policy adapters to
the same policy interface. Rule and LLM consume the same SocialState and produce
the same BehaviorIntent. VLM gets the
same state plus separately aligned images, which require a new recording/input
path. Use the same command validator/executor for all policies. Report comparison
both on identical recorded inputs and in closed-loop trials, since different
robot actions change the observations they subsequently receive.

Ego-motion compensation, calibrated camera/base transforms, multi-person social
reasoning, and broader navigation are separate extensions. None is needed to
deliver the first stationary-observation rule baseline and controlled execution
loop described above.
