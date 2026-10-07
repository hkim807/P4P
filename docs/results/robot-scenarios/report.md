# S1–S5 robot pipeline diagnosis

Analysis date: 6 October 2026. Ground truth is the user's description of the
five acted scenarios; the robot base was stationary in all five.

The transport and downstream software compose correctly on these recordings.
The main limitations are attention measurement, person identity continuity,
distance/target attribution in S2, and temporal estimation across brief detection
gaps. S3 is a positive dry-run integration result. S1/S4 produce no commands,
but that outcome is largely caused by unavailable evidence rather than correct
recognition of no attention. Do not loosen timing thresholds before addressing
attention calibration.

## Scope and verification

Read all 27 JSONL files: 517 raw frames, corresponding tracking/social/lock/command
rows, and four execution feedback rows. Each scenario's five frame-level streams
have matching lengths and aligned timestamps/state identifiers. Raw, social,
lock, command and feedback payloads validated through the applicable models and
command ledger. Observed tracking samples preserve the corresponding raw values.

Replaying recorded tracking snapshots reproduces every SocialState, allowing
1e-12 numerical tolerance for tiny floating-point residual differences. Full raw
tracking and lock replay matches S1/S3/S4/S5 exactly. S2 begins at ingest sequence
127, epoch 5, with an existing lock released by `RELEASED_STREAM_GAP`; earlier
session state is absent, so full initial-state replay cannot be asserted there.
Command planning reproduces all five command streams using the recorded locks
and inserting recorded execution events after their source frames.

All within-file frame gaps are below the configured 250 ms limit; mean delivery
rate is approximately 10 Hz. Thus the motion failures below are predominantly
person-observation gaps/UID changes, not gaps in the supplied frame stream.
These files cannot reveal perception frames dropped before HTTP recording.

Counts and hashes are in [summary.json](summary.json). Cue counts exclude UID 0
and temporarily missing tracks; policy counts are per frame. Repeated decisions
are not repeated commands. No application code or production config was changed.

## Results by scenario

| Scenario | Raw frames / duration | Frames containing a person | Actual command result | Interpretation |
| --- | ---: | ---: | --- | --- |
| S1: pass by, no gaze | 105 / 10.47 s | 40 | None | All observed nonzero-UID gaze states UNKNOWN; no `NO_ATTENTION` decision. Evidence fragmentation prevents action. |
| S2: stand ~4 m left, gaze | 89 / 8.99 s | 89 | One APPROACH, then RECEIVED → SIMULATED | Positive command plumbing, but the selected UID is mostly measured at ~2.1 m. Target attribution/range is unresolved. |
| S3: stand ~1 m, gaze | 81 / 8.13 s | 81 | One ENGAGE, then RECEIVED → SIMULATED | Strongest positive result: stable UID, plausible range, sustained attention and stationary motion. |
| S4: walk toward, no gaze | 82 / 8.16 s | 29 | None | No valid TOWARD estimate and no NONE gaze classification. Continuous distance support never reaches the required 1 s. |
| S5: start behind, walk away, gaze | 160 / 16.13 s | 83 | None | AWAY detected on 23 observations; gaze never SUSTAINED. Correct away handling during one continuous segment. |

### S1

Four SDK UIDs, including UID 0, produce five track epochs. There are 10 MISSING
events and five REACQUIRED events. All 40 non-null raw gaze scores are between
0.920 and 0.975 despite the no-gaze instruction. The estimator labels all 37
nonzero-UID observations UNKNOWN, with only three valid distance trends, all
STABLE. This does not demonstrate successful no-gaze or pass-by recognition.

The pure policy gives 40 DEFER and 65 CONTINUE/no-visible-person decisions.
The lock changes many no-person frames to DEFER while holding an absent target,
then times out. No UID rebound is accepted and no command is issued.

The first recorded range is 2.52 m, not the reported ~4 m starting distance.
The exact acted start time is unannotated, so this alone cannot establish a
distance calibration error. Range-only motion cannot establish a lateral pass-by.

### S2

The selected UID 121024494 is present on 88/89 frames. Its median range is
2.109 m; another UID, 601280211, has a median range of 4.706 m. Twelve frames
contain multiple observed UIDs. Without person labels or video, the farther
track cannot be identified as Jeruh and the nearer one cannot be identified as
an operator, duplicate or erroneous detection.

The selected UID also makes four isolated excursions from roughly 2 m to
4.75–5.14 m and back, yielding eight transitions larger than 0.4 m. The jump
guard correctly rejects unreliable motion evidence. The selected UID remains
the same, so this is not merely a missing-grace parameter problem; SDK identity
or range attribution needs checking.

There are 26 APPROACH policy/effective decisions, but only one command, at
1.521 s into this excerpt, targeting UID 121024494 / epoch 5. Feedback arrives
about 65 ms after the source frame and records RECEIVED then SIMULATED, with
`NO_PHYSICAL_ACTION`. This proves one successful observed dry-run round trip.

At a verified 4 m, the current 3 m approachable boundary would classify the
person as FAR. An APPROACH here is therefore not sufficient evidence that the
intended 4 m scenario passed. Resolve target/range attribution before changing
the approachable boundary. The initial stream-gap release belongs to omitted
prior session history; there is no >250 ms gap inside this excerpt.

### S3

One UID and one epoch persist across all 81 frames. Range is 0.882–0.957 m,
median 0.927 m, consistent with an approximate 1 m placement. Every frame is
INTERACTION_RANGE. All fitted distance trends are STABLE; valid slopes are
between -0.0235 and +0.0195 m/s, comfortably within the 0.1 m/s deadband.

Gaze becomes SUSTAINED at 1.403 s. The first frame has null base velocities,
so stationary-base confirmation waits until that sample leaves the 2 s fitting
window. The first ENGAGE command is issued at 2.119 s; 51 frame-level ENGAGE
decisions produce one command. RECEIVED/SIMULATED feedback follows about 56 ms
after the command's source frame. Physical speech or engagement is not tested.

A single null gaze at 3.726 s resets the category, giving nine UNKNOWN frames;
SUSTAINED returns at 4.609 s. This is an availability/dwell effect during a
constant-gaze scenario, not evidence that the person stopped looking.

### S4

There are three SDK UIDs including UID 0, 12 MISSING events and nine REACQUIRED
events. All 28 non-null gaze scores are 0.930–0.989 despite the no-gaze condition.
All 28 nonzero-UID observations have UNKNOWN gaze and UNKNOWN radial motion.
The longest retained contiguous distance segment is only 0.505 s versus the
required 1 s. There is no valid slope or TOWARD label anywhere in this run.

The pure policy produces 53 CONTINUE/no-visible-person and 29 DEFER decisions.
The effective lock produces 70 DEFER and 12 CONTINUE decisions, reflecting
missing-target holds and identity uncertainty. No command is issued. The file
name includes path crossing, but the supplied ground truth is walking toward;
these fields also cannot establish a route conflict or justify YIELD.

### S5

The first person observation arrives at 6.623 s; the first 66 frames contain
no detected person. Starting behind the robot could explain this, but neither
field of view nor head orientation is logged, so the explanation is unverified.
There are six SDK UIDs and eight epochs across the run.

During the continuous interval, AWAY is recognized on 23 observations from
9.824 to 12.012 s. Valid distance slopes are +0.120 to +0.353 m/s. The pure
policy produces 21 CONTINUE/PERSON_MOVING_AWAY decisions and two
CONTINUE/NO_ATTENTION decisions, with the remaining observed-person frames
deferring. The lock accepts two guarded UID rebounds and releases by policy
when away evidence becomes available. These handoffs are plausible under the
single-actor description, but the logs cannot establish identity accuracy.

Gaze is UNKNOWN on 64 observations, INTERMITTENT on 17 and NONE on two;
there are no SUSTAINED observations despite the acted constant gaze. Forty-eight
of 82 non-null gaze values are exactly 0.5. Verify the installed SDK's meaning
of this repeated value before treating it as measured no attention; the adapter
passes it through and does not invent it.

## Stage diagnosis and recommended changes

1. **Collection, recording and pipeline composition: working in these runs.**
   Timestamp order, trace correlation, raw-value preservation and replay checks
   agree. Once available, base velocities are consistently zero, matching the
   stationary-base ground truth. Initial missing locomotion is preserved as
   unknown rather than fabricated zero. Prefer collecting a fresh locomotion
   packet before beginning timed trials to avoid the S3 startup delay.

2. **Attention measurement: highest-priority calibration/feature problem.**
   No-gaze S1/S4 scores overlap positive S2/S3 scores substantially. Raising
   thresholds alone cannot be justified from these five short trials. Capture
   the installed SDK's gaze validity/confidence and face/head orientation if
   available; verify whether gaze overlap measures the intended attention cue.
   Annotate head-facing versus eye-only gaze and investigate exactly-0.5 scores.
   Treat availability separately from negative attention. Consider a bounded
   confidence decay for isolated nulls while requiring current valid evidence
   before issuing commands.

3. **Tracking/identity: lifecycle code works; physical continuity is weak.**
   The UID tracker correctly exposes missing/reappearing/expired tracks rather
   than silently merging people. S1/S4/S5 suffer frequent fragmentation.
   Increasing the 0.75 s missing grace only retains histories; it does not
   join different UIDs or repair temporal adjacency. Audit S2's range changes
   under a persistent UID before relying on distance-only reassociation. Record
   usable geometry and head pose; optional CAM_HEAD position is absent in every
   supplied observation. Do not combine social histories across a UID rebound
   without independently validated continuity.

4. **Temporal motion: valid estimates are sensible; availability needs logic work.**
   S3's stationary and S5's away labels agree with the acted behavior. S4 never
   yields toward evidence. `features.adjacent` requires consecutive frame
   sequences as well as a time gap ≤250 ms, so even one omitted person frame
   breaks a distance fit. Increasing max_gap_s alone will not repair this.
   Investigate a separate bounded-gap distance fit using real samples, explicit
   coverage/gap limits and unchanged identity/jump checks. Do not impute gaze
   across gaps. Separate distance support thresholds from gaze thresholds and
   calibrate using repeated stationary/toward/away trials. The present speed
   deadband works on the valid segments here; the main failure is insufficient
   support, not rejection by fit residual.

5. **Policy and lock: implemented rules behave consistently; product semantics need review.**
   Policy appropriately suppresses interaction when away evidence is valid and
   defers during multiple-person ambiguity. However, every visible person can
   acquire a logical lock during uncertain gaze, and a missing target can extend
   DEFER. Decide whether DEFER should ever pause a route and whether acquisition
   should require credible attention. None of these traces contains a physical
   DEFER command. Handle verified background people through explicit target
   selection only after identity attribution is reliable. Make a deliberate
   choice about interaction beyond 3 m after range calibration. Scalar distance
   and gaze do not support route-conflict/YIELD behavior.

6. **Command/feedback: observed dry-run delivery and deduplication work.**
   S2/S3 each issue exactly one correlated command despite repeated actionable
   decisions, then stop sending after simulated feedback. There are no STARTED,
   COMPLETED, FAILED or CANCELLED physical execution records. The five stationary
   trials cannot validate base-motion compensation, approach execution, speech,
   lease cancellation or route pause/resume. Safety ranges are logged but do not
   enter the social policy; every non-null sonar array is all zero. Verify range
   semantics and response separately before using them for physical behavior.

## Offline sensitivity checks

Recomputed social states from recorded tracking snapshots with one configuration
change at a time, then applied the pure policy. These are diagnostic experiments,
not recommended production settings and not recorded physical outcomes.

| Configuration | S1 APPROACH frames | S2 APPROACH frames | S3 ENGAGE frames |
| --- | ---: | ---: | ---: |
| Recorded defaults | 0 | 26 | 51 |
| min_span_s: 1.0 → 0.5 | 2 | 36 | 51 |
| looking_enter/exit: 0.95/0.93 | 0 | 26 | 45 |
| looking_enter/exit: 0.98/0.96 | 0 | 0 | 0 |

S4/S5 issue no APPROACH/ENGAGE proposals in these variants. Shortening support
creates false approach proposals in no-gaze S1. Raising gaze thresholds far
enough to be stricter also removes the intended positive S2/S3 interactions.
Zero false actions in the default S1/S4 traces is not proof that attention
thresholds distinguish those conditions, because evidence fragmentation masks
the high scores.

## Overall next step

Run a labeled calibration and perception-diagnostics session before broad
parameter tuning or physical execution. Use marked 1/2/3/4 m positions, one
known actor initially, synchronized video or source-time annotations, and a
fresh receiver session for each trial. Log SDK identity, raw range/gaze plus
available confidence/validity, coordinate-frame geometry, and head-focus/head
pose state. Repeat sustained gaze, head facing with eyes averted, head turned
away, passing, toward and away conditions; then repeat with an additional
background person and head following on/off.

First resolve whether the intended actor's range/UID is correct and whether
attention can be separated at the sensor level. Next implement and evaluate
bounded-gap distance estimation and isolated-null gaze handling. Use labeled
replay to measure false action proposals per negative trial, positive-trial
latency, motion-evidence coverage, identity handoff errors and duplicate commands.
Tune the temporal parameters and distance boundaries against those measures,
then validate physical execution separately. The immediate bottleneck is the
reliability of perception evidence reaching otherwise consistent downstream
rules.
