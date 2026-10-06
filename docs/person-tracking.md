# Person tracking: implementation, rationale, and validation

The first new processing layer after raw reception/replay is now implemented:
`RawObservationFrame -> UID histories`. This is Layer 2 in the implementation
plan. It preserves what each SDK UID was observed doing over time. It does not
estimate gaze categories, human motion, engagement, or issue robot commands.

The supplied Navel recordings 01-07 are the development/replay inputs (called
“mock data” in the implementation request). They contain actual captured sensor
frames; separate synthetic fixtures exercise edge cases not present in them.
See the [generated validation report](results/person-tracking/report.md) and
[machine-readable results](results/person-tracking/summary.json).

## What was added

| Component | Responsibility |
| --- | --- |
| `app/state/tracks.py` | Pure UID history manager, validated configuration, acquisition/missing/reacquisition/loss events, bounded retention |
| `app/pipeline.py` | Shared offline/live tracking entry point, ordered raw persistence and trace writing |
| `app/track.py` | Replay one recording into an inspectable JSONL track trace; console summary on stderr |
| `app/validate_tracking.py` | Audit source fidelity, lifecycle bounds, replay determinism, and grace-period sensitivity; generate reports and hashes |
| `config/person-tracking.json` | Explicit development defaults, also embedded in every snapshot |
| `app/server.py` | Optional tracking on the existing HTTP receiver via `--tracking-output` |
| `tests/test_tracks.py`, `tests/test_tracking_pipeline.py`, `tests/test_tracking_recordings.py` | Synthetic edge cases, failure/concurrency tests, all-seven-recording regression checks |

No new dependencies or robot-side changes are required. Raw-only reception and
`app.replay` retain their existing behavior. Tracking is opt-in for the receiver.

## Research basis and limits

These studies motivate the architecture; none establishes the correct Navel
history length, grace period, or identity semantics. Numerical settings below
are explicit engineering choices tested on these inputs, not published findings.

- Michalowski, Sabanovic, and Simmons (2006), *A Spatial Model of Engagement for
  a Social Robot*, evaluated a receptionist using spatial and head-pose cues.
  Their observations motivated stronger use of movement and attention to the
  timing of behavior. This supports retaining temporal evidence before deciding
  whether to engage; it does not validate our three-second window.
  [Author-hosted paper, abstract and conclusions](https://homes.luddy.indiana.edu/selmas/MichalowskiSabanovic-AMC2006.pdf).
- Bewley et al. (2016), *Simple Online and Realtime Tracking*, explicitly manages
  creation/deletion of track identities and removes sufficiently old tracks.
  Section 3.4 explains the bounded-lifetime rationale and starts a new identity
  after deletion. We borrow this lifecycle principle, not its frame-count
  threshold, Kalman filter, or association algorithm: Navel already supplies UIDs.
  [Paper, section 3.4](https://arxiv.org/html/1602.00763#S3.SS4).
- Wojke, Bewley, and Paulus (2017), *Simple Online and Realtime Tracking with a
  Deep Association Metric*, adds appearance information to improve association
  through occlusion. Our inference for this implementation is that a changed
  SDK UID plus similar scalar distance is insufficient justification to merge
  histories. The current stream has no appearance descriptors or verified
  person re-identification. [Paper](https://arxiv.org/abs/1703.07402).

The implementation is a UID history manager, not SORT/Deep SORT or an independently
validated visual person tracker. Research support, project-specific reasoning,
and measured software results are distinguished in each stage below.

## Stage 1: associate observations using source time and UID

**Decision.** Validate the existing raw schema, reject non-increasing timestamps,
and look up each person by the SDK UID within a session. Use robot microseconds
for all age/window calculations. A fresh receiver run gets a new session ID;
each offline file is processed in a separate pipeline. Replay derives a stable
session key from the file name/content hash, with an optional explicit override.
Reset discards all state and callers must supply a new session ID.
The source data and original timestamps are never rewritten.

**Why.** The research motivates a temporal representation; the concrete association
key comes from the existing Navel contract, not from a study claiming SDK IDs are
persistent identities. The recordings contain variable frame gaps, and replay
can run at any speed. Frame counts or PC wall time would change the meaning of a
history when replay pacing changes. That is a software reproducibility requirement.

**Identity policy.** Every acquisition gets a monotonically increasing,
session-local `track_epoch`. A track is identified by `(session_id, uid, epoch)`.
A different UID always starts a separate history. UID 0 is retained, with
`UID_ZERO_UNVERIFIED` in `identity_quality_flags`; the current contract accepts
zero but its SDK identity meaning has not been established. Numeric UID order
is used only to make output and capacity tie-breaking deterministic, never as a
social cue. Reusing a UID in another file does not connect the sessions.

**Verified.** Tests cover alternating IDs, UID 0, same UID across session resets,
duplicate/backward timestamps, and invalid/mutated model input without advancing
state. Each retained sample in the seven-recording audit is checked against the
original source sample for that exact UID and timestamp. No cross-UID copies occur.

## Stage 2: represent visibility and lifecycle explicitly

**Decision.** Emit lifecycle events and separate observed from temporarily missing:

```text
new UID                          -> ACQUIRED / OBSERVED
UID omitted from accepted frame  -> MISSING / TEMPORARILY_MISSING
same UID returns within grace    -> REACQUIRED / OBSERVED, same epoch
last seen more than grace ago    -> LOST, remove active history
same UID returns after expiry    -> ACQUIRED, new epoch
```

Loss is checked before matching current observations. A long input gap cannot
silently revive an expired history even if no empty frame arrived in between.
A return exactly at grace preserves the epoch; expiry uses strictly `age > grace`.
`MISSING` is emitted once on transition, not on every absent frame. `LOST` is an
event, not an indefinitely retained entry. There is no archive of expired UIDs
inside the tracker; a session-global epoch counter avoids unbounded bookkeeping.

**Why.** Finite track lifetime follows the lifecycle principle in SORT, while our
same-UID grace behavior is a project choice for short detector omissions. Keeping
a history during a gap does not prove visibility or identity continuity. A null
measurement on a present UID is different from an omitted UID, so null gaze or
distance never makes an observed track disappear.

**Default.** `missing_grace_s = 0.75`. The measured maximum inter-frame gaps are
about 0.17 s, so the initial setting tolerates several ordinary frame intervals.
This is provisional; a longer grace reduces fragmentation but also retains old
identity associations longer. No study or label set establishes 0.75 s as optimal.

**Verified.** Tests cover absence, repeated empty frames, returns at 0.750000 s
and 0.750001 s, direct returns after long stream gaps, and independent sessions.
The report repeats all seven recordings at grace 0.25, 0.75, and 1.5 s. Recording
07 yields 15, 11, and 9 acquired epochs respectively. Fewer epochs is not proof of
better tracking without annotated physical-person identities.

EOF does not manufacture a disappearance or advance time. Track states in the
last snapshot are the last observed processing state. A silent live transport
also does not automatically mean a person left: the separate stream-staleness
watchdog remains a later layer and is not implemented here.

## Stage 3: retain bounded, unmodified evidence

**Decision.** Keep samples in `[current_timestamp - 3 seconds, current_timestamp]`
with an additional 64-sample cap per track and a 32-active-track cap. Prune missing
tracks too. Preserve nulls and optional head-position presence, and include the
contemporaneous robot velocity fields with each person sample for later motion
interpretation. Samples are copied on output so consumers cannot mutate history.

**Why.** Temporal social analysis needs original evidence and timing, as motivated
by the engagement study. The final project plan suggests 1-3 seconds; three
seconds retains its upper proposed bound for future feature experiments. This
layer does not decide how much evidence is sufficient. The duration and capacity
limits are engineering resource controls, not calibrated engagement thresholds.
There are no interpolated samples, fabricated gaze values, distance smoothing,
or motion predictions. Missing intervals remain visible through timestamps and
events, ready for the feature layer's coverage rules.

At the observed input rate, the baseline retains at most 31 samples per track,
so the 64-sample cap does not truncate these inputs. The active cap allows UID
churn while bounding memory. If a new UID needs room, evict the oldest absent
track first, with UID as the deterministic tie-breaker; emit `LOST` with reason
`capacity_eviction`. Never evict a currently observed track to admit another in
the same frame. More than 32 simultaneously observed UIDs rejects the entire
tracker update before mutation. The raw receiver can still retain that frame and
report derived processing as failed. This is not a multi-person selection policy.

**Verified.** Tests check exact window boundaries, lifetime versus retained age,
null preservation, optional geometry, copied snapshots, sample caps, capacity
eviction, too-many-observations rejection, and 1,000 changing IDs under a small
capacity. The real-input audit checks every retained timestamp, original value,
epoch observation count, and bound. No baseline track or sample capacity is hit.

## Stage 4: use the same processing logic live and in replay

**Decision.** Both paths call `TrackingPipeline.process`. For the receiver, one
lock covers timestamp acceptance/raw write, tracker update, and derived trace
write. The raw recording stays raw; snapshots go to a separate new JSONL path.
The original writer's internal lock alone would not serialize a later tracker
callback, so the outer pipeline lock is required.

**Why.** This is an engineering consistency requirement, not a behavioral finding:
threaded HTTP requests must not persist frames in one order and update histories
in another. A deterministic replay trace makes subsequent studies auditable.
The full configuration, tracker version, session ID, successful-update sequence,
and source timestamp appear in each snapshot. Audit reports record input,
implementation-source, and output hashes.

**Failure behavior.** A raw write failure returns HTTP 503 and tracking is not
advanced. A derived update or trace-write failure after raw persistence returns
`accepted: true`, `processing_status: failed`, and the failed stage. Raw data is
preserved for replay; it is not rolled back. If only trace writing fails, tracker
state has advanced and the trace will contain a gap. If tracker validation fails,
state has not advanced; `frame_sequence` counts successful tracker updates, not
all persisted raw lines. Operators must inspect the processing status and errors.
The raw frame is already accepted and must not be resubmitted with its old timestamp.
There are no actuator handlers in either path.

**Verified.** Tests check storage/processing failures separately, duplicate
requests, concurrent arrivals, path collisions, and output overwrite refusal.
For all 682 input frames, Flask receiver processing produces byte-identical
canonical tracking traces to the offline pipeline under the same session ID.
Real localhost HTTP tests additionally exercise the SDK-shaped adapter through
transport, persistence, tracking, and response parsing. Hardware sensor accuracy
and the physical robot network are not established by these tests.

The full suite passes 80 tests (28 new tests plus the existing suite), including
the localhost HTTP cases. Recording 03 was also played through the actual CLI at
2x wall-clock pacing; its trace hash matched the reference audit output.

## Stage 5: validate the supplied input and preserve reproducibility

The audit uses an independent source-sample lookup to check output histories,
then reruns the shared pipeline at speeds 0, 1, and 2 with a virtual pacing clock.
It compares hashes of the entire canonical trace, including configuration and
events. Virtual pacing tests determinism without waiting for every recording;
this is not a real-time latency benchmark. The verifier intentionally loads these
short clips to construct its reference; the tracker and single-file replay CLI
operate with bounded histories and streaming input.

At the default configuration, all seven pass: **682 frames, 544 person
observations, and 12,412 retained sample copies** checked. The distinction matters:
a raw sample can appear in several successive rolling snapshots.

| Recording | Acquired epochs | Same-UID reacquisitions | Interpretation |
| --- | ---: | ---: | --- |
| 01 approach/gaze | 4 | 11 | Three raw IDs plus expiry split history; no identity merging |
| 02 approach/no gaze | 8 | 14 | Five raw IDs and repeated short gaps |
| 03 stationary/gaze | 1 | 0 | One continuous track across all 83 frames |
| 04 stationary/no gaze | 4 | 7 | Empty frames and returns drive lifecycle transitions |
| 05 stationary/eye motion | 1 | 0 | One continuous track across all 90 frames |
| 06 stationary/head motion | 2 | 3 | Short gaps preserve the same epoch; changed ID stays separate |
| 07 moving away | 11 | 10 | Seven raw IDs; substantial history fragmentation |

These are contract/lifecycle results, not person identity accuracy. We have no
video-aligned identity ground truth or annotated engagement intervals. The same
physical person may change UID, and a UID may be reused. UID-only tracking cannot
resolve either case reliably. Future features must respect those separate epochs
and missing intervals. The overlap in gaze scores across intended conditions
also remains a calibration concern for the next layer.

## Run and inspect

From the repository root, using the existing virtual environment:

```bash
# Immediate local replay; stdout is a track snapshot for every accepted frame.
.venv/bin/python -m app.track var/recordings/03_stationary_gaze.jsonl \
  --config config/person-tracking.json

# Original-time playback into a NEW trace (parent directory must exist).
.venv/bin/python -m app.track var/recordings/03_stationary_gaze.jsonl \
  --speed 1 --output /tmp/navel-03-tracks.jsonl

# Reproduce the complete seven-recording report and parameter sweep.
# Choose a new output directory for each run; existing results are refused.
.venv/bin/python -m app.validate_tracking var/recordings/0[1-7]_*.jsonl \
  --config config/person-tracking.json --output-dir var/tracking-validation/review-01

# Enable tracking on the existing receiver, with separate NEW raw/derived files.
.venv/bin/python -m app.server --host 127.0.0.1 --port 6060 \
  --output var/recordings/tracking-live-01.jsonl \
  --tracking-output var/tracking-validation/tracking-live-01.jsonl \
  --tracking-config config/person-tracking.json

# Synthetic, recording regression, concurrency, and real HTTP tests.
.venv/bin/python -m unittest discover -s tests -v
```

A snapshot exposes `session_id`, `frame_sequence`, `robot_timestamp_us`, effective
`config`, `robot`, `tracks`, and `events`. Each track contains UID/epoch,
visibility, first/last seen times, age, last-seen age, lifetime observation count,
retained sample count/span, capacity-drop count for the current update, identity
flags, and the original samples. The `app.track` completion summary goes to
stderr so stdout remains valid JSONL.

## Next layer and decision-record convention

Next implement temporal measurements over these histories: coverage, gaze
proportion, distance slope, and their validity. Classification and policy follow
after calibration. For each later stage, append a decision record stating:
problem and interface; chosen behavior; alternatives; primary study and what it
actually supports; project assumptions and tunable values; test/data results;
and limitations. Do not present a project-specific threshold as study-validated
unless the study and applicable calibration evidence actually support it.
