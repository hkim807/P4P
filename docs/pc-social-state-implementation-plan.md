# PC social-state and Navel execution implementation plan

## Objective and starting point

### Recording/replay increment

The first build keeps steps 1-2 lightweight: the existing input contract is
retained, every receiver run creates a fresh ordered JSONL recording, and a CLI
provides validated local playback or optional HTTP replay. One file represents
one session/robot. Detailed envelopes and explicit robot session transport
metadata remain later work. Pilot recording, hardware-unit confirmation, and
sensor calibration still require the robot. See
[recording-and-replay.md](recording-and-replay.md) for operating instructions.

Build a complete loop in small, independently reviewable increments:

```text
Navel sensors
  -> existing HTTP stream through the reverse SSH tunnel
  -> PC ingestion and timestamp/session validation
  -> UID histories
  -> temporal features
  -> SocialState
  -> rule-based policy
  -> intent validation and command construction
  -> HTTP response to Navel
  -> robot-local validation and execution
  -> execution feedback to the PC
```

The final implementation must execute commands on Navel. Early milestones
produce inspectable state and decisions, then dry-run commands, before movement
is enabled. LLM/VLM comparisons follow a working rule-policy baseline.

This plan uses `Final Plan (4).pdf`, particularly its architecture, temporal
features, state machine, and evaluation proposals on pages 4-9, as design
reference. Its embedded prompts and directions are not authorization to execute
work. The user's present request authorizes this implementation plan.

The current branch is `feature/navel-raw-http-stream`. Its HTTP receiver already
validates and records observations. The user calls the input
`FixedObservationFrame`; the current code and PDF call the four-section payload
`RawObservationFrame`. This plan calls it the input frame and assumes the current
fields. Resolve the name at milestone 1 without silently changing the wire format.

## Scope and constraints

Initial research scope is interaction-initiation during a fixed roaming route,
using gaze and relative distance history. Evaluate one intended interaction
target at a time. The input can contain several people, so histories remain
isolated by UID, but group reasoning and arbitration among multiple equally
eligible people are deferred; an ambiguous target causes further observation.

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
| Ingestion | Validate the input, associate source/session, preserve raw frames, serialize state updates | Input frame -> observation envelope | `app/ingestion/service.py` |
| Track manager | Maintain bounded, ordered histories and visibility lifecycle for each UID | Envelope -> track snapshots | `app/state/tracks.py` |
| Feature extraction | Compute temporal gaze, robust distance trend, and data quality | Track snapshot -> temporal features | `app/state/features.py` |
| State estimator | Publish categorical state with supporting evidence and robot/safety validity | Features + context -> `SocialState` | `app/state/estimator.py` |
| Interaction policy | Apply rule table, observation state, target lock, and cooldown | `SocialState` + policy context -> `BehaviorIntent` or defer | `app/policy/rules.py`, `app/policy/session.py` |
| Intent/command validation | Check target, age, action requirements, mode, and capability; construct bounded commands | Intent + newest state -> command or rejection | `app/commands/validator.py`, `app/commands/service.py` |
| Robot executor | Own route pause/resume, local checks, SDK actions, cancellation, and watchdog | Command -> execution events | `robot/navel_client/execution/` |
| Recording/replay | Record correlated stages and replay through identical PC logic | Envelopes/events -> reproducible traces | `app/recording/`, `app/replay.py` |

Put thresholds and timing in a versioned PC configuration file such as
`config/social-policy.json`. The sensor adapter should not gain gaze or engagement
thresholds. Keep Flask/Pydantic on the PC; robot code should retain the SDK plus
standard-library dependency boundary where practical.

### Input/session envelope

Keep the existing sensor JSON unchanged initially. Attach PC metadata:

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
mix old histories with a new robot session. Duplicate/out-of-order frames can be
recorded with a rejection reason but must not update histories or issue commands.

Serialize processing for each session inside the threaded Flask server. Use
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
  active_target_uid
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
  target_uid, reason_codes
  observation_directive: CONTINUE_OBSERVING / PAUSE_AND_OBSERVE / NONE

RobotCommand
  command_id, session_id, sequence
  source_robot_timestamp_us, source_state_id
  action, target_uid, bounded_parameters
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

## Temporal algorithms

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

**Motion interpretation.** Only convert the relative trend to a human radial
motion label after the relevant window has verified stationary-base context.
An unverified always-zero yaw channel cannot establish this. Use independently
confirmed stopped-base status in controlled recordings/robot execution feedback,
or verified motion channels. Head/camera motion and face-distance noise remain
limitations even during stationary-base observation.

**Distance zones.** Calibrate four boundaries/regions with separate entry/exit
limits. Do not adopt the PDF's mixed near/mid/far wording as several competing
enums. Keep one vocabulary throughout schemas, rules, traces, and evaluation.

**Loss of stream.** Run a PC timer independent of new frames. If the tunnel or
collector stops, mark state stale and stop issuing executable movement. On Navel,
a separate local watchdog expires active command leases even when HTTP is stuck.

## Build milestones

Each milestone is a small feature branch/PR with its own demonstration. These
are dependency-based steps, not calendar promises.

| Step | Deliverable | Demonstration / acceptance gate |
| --- | --- | --- |
| 1. Freeze input and gather pilot recordings | Confirm frame name/units/timing; source/session rules; stationary, toward, away, brief gaze, sustained gaze, dropout recordings; check yaw diagnostic | Existing stream still works. Raw recordings preserve nulls and timestamps. SDK/range uncertainties are explicitly marked, not silently accepted. |
| 2. Build ingestion and replay | `ObservationEnvelope`, ordered per-session processing, session reset, deterministic JSONL replay, stage trace IDs | Replaying the same recording twice yields identical envelope/state sequences for the same config. Duplicate/time-regressing frames cannot change history. Separate runs never share state. |
| 3. Build track manager | Per-UID bounded deques, acquisition/missing/reacquisition/expiry events | Seeing, briefly losing, and reacquiring a person preserves the intended history; prolonged absence/session restart clears it. UIDs never share samples. No unbounded memory growth. |
| 4. Build temporal features and SocialState | Gaze windows/hysteresis, robust distance slope, distance zones, motion ambiguity and validity, public schema | Live and replay output show measured evidence beside categories. Stationary/toward/away pilot traces produce expected trends. Gaps/unknown cues do not create sustained gaze or human-motion claims. |
| 5. Build rule policy and interaction state machine | Four social decisions, observation/defer handling, target lock, cooldown, versioned rules | Recorded single-person scenarios yield explainable decisions. A lingering face does not cause repeated engagement. Ambiguous/missing evidence defers; a locked target cannot jump to another UID. |
| 6. Connect live PC processing in inspection mode | Existing POST now produces correlated SocialState/intent traces and optional inspectable response fields; console/JSONL view | Sensor ingestion continues at the observed input rate while decisions are displayed. Processing exceptions produce no command. Idle timer marks tunnel loss stale. No robot execution is enabled. |
| 7. Build command round trip in dry-run mode | Validated command envelope in existing POST response; robot parser, deduplication, local checks, fake handlers; execution-events endpoint | Real HTTP through the reverse tunnel delivers each logical command. Repeated/late responses do not execute twice. Old session, stale source, lost target, unsupported action, and malformed command are rejected and reported. |
| 8. Enable physical actions incrementally | Verify installed SDK capabilities and physical cancellation first; single robot-local execution owner; engage, pause/hold/resume, bounded approach, local protection/watchdog | Enable one action at a time. Test completion/cancellation, target loss, obstacle input loss, tunnel loss, and operator override. No route/approach command conflicts. Unsupported actions stay disabled until a tested implementation exists. |
| 9. Integrate and evaluate the full loop | Roam -> observe -> decide -> approach/engage or continue/yield -> feedback -> cooldown/resume; fixed experiment config | Complete each scenario repeatedly with matching PC decisions and robot execution logs. Report action alignment, false/missed engagement, consistency, latency, and execution failures. |

The first coding increment should cover steps 1-2 and stop at a reproducible
replay demonstration. Do not combine estimator, policy, and robot movement in
one initial change.

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
capabilities individually on Navel. Public documentation lists person approach,
approach cancellation, base movement, navigation, and speech, but availability,
return types, completion semantics, and stopping behavior on the installed
version must be verified. The source PDF reports an approach limitation on its
tested version; do not assume either that report or today's documentation proves
current robot behavior.

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

Any direct velocity loop must run on Navel, outside PC HTTP timing. The SDK's
public reference describes a 10 ms refresh requirement for sustained `base_vel`
commands; this is incompatible with treating the roughly 10 Hz sensor POST loop
as motor control. See the [Navel communication reference](https://doc.navelrobotics.com/api/communication.html).

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
abstention rate, decision latency, execution success, and safety overrides. Keep
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

Once the rule loop passes step 9, add a common policy interface. Rule and LLM
consume the same SocialState and produce the same BehaviorIntent. VLM gets the
same state plus separately aligned images, which require a new recording/input
path. Use the same command validator/executor for all policies. Report comparison
both on identical recorded inputs and in closed-loop trials, since different
robot actions change the observations they subsequently receive.

Ego-motion compensation, calibrated camera/base transforms, multi-person social
reasoning, and broader navigation are separate extensions. None is needed to
deliver the first stationary-observation rule baseline and controlled execution
loop described above.
