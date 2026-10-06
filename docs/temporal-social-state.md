# Temporal features and SocialState

Layers 3a and 3b now transform recorded or live `RawObservationFrame` streams into
measured temporal evidence and categorical SocialState. The original files stay
unchanged. Derived JSONL contains estimates, configuration, validity reasons,
track lifecycle events, and cue changes. No interaction decisions, target
selection, robot movement, or identity saving are performed.

The implementation runs on the existing UID/epoch histories. A changed UID is
still a separate person history; saving identities on Navel remains a robot-side
prerequisite for improving continuity. This layer cannot reconstruct identity
associations that the recorded input does not contain.

## Components and outputs

| Component | Responsibility |
| --- | --- |
| `app/state/features.py` | Time-supported gaze evidence, gap detection, robust distance slope and fit quality |
| `app/state/estimator.py` | Gaze/distance hysteresis, categories, motion validity, cue-change events |
| `app/state/social_models.py` | Validated configuration and typed SocialState |
| `app/social_pipeline.py` | Same tracking-to-state computation for live and recorded frames |
| `app/social.py` | Runnable recorded-input to SocialState replay |
| `app/validate_social.py` | Seven-recording audit, synthetic transformations, reproducible reports |
| `app/social_scenarios.py` | Controlled raw-schema-compatible stimuli made from a recorded frame template |
| `config/temporal-state.json` | Provisional thresholds and timing, embedded in every state |
| `schemas/v1/social-state.schema.json` | Public output contract |

Per observed or temporarily missing track, output includes:

- `gaze_state`: `NONE`, `INTERMITTENT`, `SUSTAINED`, or `UNKNOWN`.
- `distance_zone`: `TOO_CLOSE`, `INTERACTION_RANGE`, `APPROACHABLE`, `FAR`, or `UNKNOWN`.
- `relative_distance_trend`: `DECREASING`, `STABLE`, `INCREASING`, or `UNKNOWN`.
- `human_radial_motion`: `TOWARD`, `STATIONARY`, `AWAY`, or `UNKNOWN`.
- `evidence`: coverage, gaze fraction/run duration, sample counts, slope, fit
  residual, segment span, detected jumps, and stationarity from robot velocities.
- `validity_flags`: why evidence is missing, insufficient, or unreliable.

At scene level, `cue_changes` identifies category transitions by UID/epoch and
field; `track_events` retains acquisition/missing/reacquisition/loss information.
Expired tracks leave the people array and appear in terminal tracking events.
`latest_distance_m` can be a retained historical reading for a missing track;
visibility and last-seen age must be consulted. Its current distance zone is then
UNKNOWN. Active targets are null and range-data status remains UNKNOWN because
this layer does not calibrate or interpret lidar/sonar.

## Design decisions and research basis

### 1. Temporal evidence before engagement interpretation

The feature layer consumes original observations, while the estimator consumes
those features. Every output includes the evidence, effective config, a config
hash/version, session ID, source timestamp, and sequence. All age calculations
use source time; changing playback speed does not change categories.

Michalowski, Sabanovic, and Simmons' receptionist study found limitations in
static spatial engagement models and recommended stronger use of movement and
behavior timing. This motivates examining histories, not a single gaze/distance
pair. It does not prescribe this implementation's window size or thresholds.
[Study, abstract and conclusions](https://homes.luddy.indiana.edu/selmas/MichalowskiSabanovic-AMC2006.pdf).

The HRI study *From Real-time Attention Assessment to “With-me-ness”* treats
engagement as indirectly observable and develops an attention-based measure
over an interaction. This supports reporting cue evidence rather than claiming
that a high gaze score proves a desire to interact. Our categorical baseline is
not that paper's validated model.
[Study and methodology summary](https://uwe-repository.worktribe.com/output/913345/from-real-time-attention-assessment-to-with-me-ness-in-human-robot-interaction).

Accordingly, `SUSTAINED` describes the measured gaze pattern under the current
config. There is no engagement probability or `ENGAGE` decision. Every output
is marked `calibration_status: PROVISIONAL` until a future calibrated configuration
and evaluation establish stronger claims.

### 2. Gaze: bounded temporal support, hysteresis, and dwell

Default feature window: **2 seconds**, within the tracker's 3-second retention.
A raw score enters LOOKING at 0.8 and exits at 0.7. A first score in the band is
unclassified until a threshold is crossed; established state persists inside
the band. Null values and observation gaps reset that sample-level state.

An interval contributes gaze evidence only if both endpoint samples have valid
LOOKING classifications, the person was present in consecutive accepted frames,
and their source-time gap is at most 0.25 seconds. It uses the earlier sample's
classification over that interval. This is a bounded sample-and-hold estimate;
it is not proof of what happened between sensor samples. There is no support
after the newest sample, across a missing UID, or across a long/null interval.

```text
gaze_fraction = supported LOOKING duration / supported valid duration
coverage_fraction = supported valid duration / elapsed retained window span
```

Classification requires at least five classified samples, one second of elapsed
span, 0.8 seconds of valid support, and 60% coverage. Missing people or an invalid
latest gaze yield UNKNOWN immediately. Unknown evidence never counts as NONE.

Category entry/exit thresholds differ: sustained enters at fraction 0.8 and
exits below 0.65; none enters at 0.2 and exits above 0.35. Other valid patterns
are intermittent. Sustained additionally requires a current continuous looking
run of at least 0.4 seconds. A candidate category must persist for 0.3 seconds
before replacing the current category; changes during that dwell retain the
previous valid category. Invalid evidence resets to UNKNOWN immediately, and
category dwell never bridges an observation gap.

These are explicit engineering choices following the final plan's temporal
representation and hysteresis proposal. No cited study establishes these numbers
for Navel. Tests exercise time weighting, warmup, exact dwell, nulls, gaps, and
hysteresis surviving rolling-window eviction.

### 3. Distance: robust slope plus an independent quality check

Use the newest contiguous valid-distance segment within the two-second window.
Null distance, a missing-person frame, a time gap over 0.25 seconds, or an
implausible adjacent change breaks the segment. The provisional jump limit is
`3 m/s * time_gap + 0.05 m`. A jumping current sample cannot supply a distance
zone or motion label; subsequent stable observations must rebuild evidence.
This limit constrains observed relative separation, not an estimate of human speed.

With at least five samples spanning one second, fit the median of all pairwise
slopes (Theil-Sen style). Use at most 32 evenly distributed samples, including
segment endpoints, to bound pairwise computation to 496 slopes. Compute the
intercept as the median of `distance - slope * time`. The method and joint
intercept are described in the [SciPy Theil-Sen reference](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.theilslopes.html).
The small implementation uses Python's standard library and does not add SciPy.

Compute RMS residual around that robust line. A residual over 0.1 m makes the
trend UNKNOWN, while retaining the measured slope/residual for inspection.
Median residual was rejected during testing because alternating noisy readings
can give a misleading zero median residual. RMS provides a separate noise gate;
the fitted slope remains robust to a small isolated outlier.

With valid evidence, slopes below -0.1 m/s are DECREASING, above +0.1 m/s are
INCREASING, and values inside the deadband are STABLE. These are provisional
relative-distance labels. Test cases establish exact slope signs, robustness,
jump handling, and rejection of poor fits; they do not calibrate the deadband.

### 4. Distance categories and robot-motion ambiguity

Initial zone boundaries are 0.6, 1.5, and 3.0 m, with a 0.1 m hysteresis margin.
First valid readings are classified directly. Subsequent transitions must cross
the relevant boundary plus/minus the margin. These are research-development
settings, not proven social-distance preferences or robot stopping distances.

Relative distance changes cannot distinguish human movement from robot movement.
The estimator treats the robot base as stationary when both recorded velocities
are present and within provisional tolerances (0.02 m/s forward, 0.03 rad/s yaw)
at every sample in the fitted distance segment. A missing velocity or measured
robot motion prevents a human-motion label. No separate context file is needed.
`STATIONARY` for the human means stable radial separation, not absence of lateral
movement. Head movement and face-distance noise remain limitations.

### 5. Identity, live integration, and failure behavior

Feature/classification memory is keyed by UID and track epoch within a session,
pruned when tracks expire, and reset for a different session. It cannot transfer
attention evidence to a newly assigned SDK UID. Even a saved identity can have
a new observation epoch after a long absence. Hardware persistence/recognition
must be evaluated separately before claiming continuous physical-person tracking.

The live receiver uses the same SocialPipeline as replay. One outer lock covers
tracking and estimation. Raw frames are written first; optional tracking traces
and social traces use distinct new paths. A derived-stage failure preserves the
raw frame and reports `processing_status: failed`, with `social_estimation` or
`social_trace_write` as applicable. A trace-write failure can leave a gap in the
sidecar while processing memory has advanced; replay the raw file for recovery.

`--tracking-output` alone still enables only UID tracking. `--social-output`
enables tracking plus temporal/social estimation, and adds `social_state` to the
HTTP response. No state update is synthesized at EOF. The separate live
stream-staleness watchdog and execution layer remain future work; a last printed
state is not a promise that the scene is still current after the stream stops.

## Validation and measured findings

The [generated report](results/temporal-state/report.md) and
[machine-readable results](results/temporal-state/summary.json) cover all seven
recordings: 682 frames and 544 observed-person states. Schema checks, measured
coverage bounds, missing-person handling, robot-motion ambiguity, unchanged
source hashes, and identical full traces at replay speeds 0/1/2 are checked.
Virtual pacing makes this deterministic; it is not a latency benchmark.
Receiver/offline parity is tested for every recorded frame, with additional real
localhost HTTP integration tests.

A separate 160-frame stimulus copies the first recorded frame as a template,
then explicitly replaces UID, timestamps, gaze, distance, and robot velocity.
It removes optional head geometry and sensor ranges that would contradict the
injected values. Original recordings are never overwritten. Its robot velocities
are set to zero throughout.

| Checkpoint | Designed gaze | Designed separation | Expected state |
| --- | --- | --- | --- |
| 3.5 s | Low throughout | Constant | NONE / STABLE / STATIONARY |
| 7.5 s | High throughout recent window | Decreasing at 0.2 m/s | SUSTAINED / DECREASING / TOWARD |
| 11.5 s | Alternating high/low | Constant | INTERMITTENT / STABLE / STATIONARY |
| 15.5 s | Low throughout recent window | Increasing at 0.2 m/s | NONE / INCREASING / AWAY |

All four checkpoints pass at the default config. Their expectations are fixed
synthetic checks, so a config that changes their semantics can intentionally fail
the validator. Additional tests cover nulls, missing people, UID/epoch/session
changes, direct transport gaps, robust-fit noise, hysteresis, dwell, output path
collisions, and failure handling.

The original data is not labeled ground truth. Recording 04 (intended no gaze)
produces 22 observed SUSTAINED states; recording 05 (intended eye-only intermittent
gaze) produces 65. These results expose the overlap in measured gaze scores.
There are no NONE classifications in the original seven files under the current
thresholds. Treat this as a calibration/sensor-discrimination finding, not a
validated engagement detector. Fragmented UIDs also leave many windows UNKNOWN.

Before the rule policy, annotate actual behavior intervals, verify identity
persistence and robot velocity quality, compare parameter choices, and freeze a
configuration for independent evaluation. Do not tune by forcing filename labels
or merge IDs merely to obtain a longer feature window.

## Commands

Run one recording and inspect derived states:

```bash
mkdir -p var/temporal-validation
.venv/bin/python -m app.social var/recordings/03_stationary_gaze.jsonl \
  --config config/temporal-state.json \
  --output var/temporal-validation/03-social.jsonl
```

Add `--speed 1` for recorded pacing, or omit `--output` to print JSONL. Completion
counts go to stderr. Robot velocities come from each recorded frame.

Reproduce the report, synthetic transformed inputs, and all state traces:

```bash
.venv/bin/python -m app.validate_social var/recordings/0[1-7]_*.jsonl \
  --config config/temporal-state.json \
  --output-dir var/temporal-validation/experiment-01
```

The output directory must be new. It contains seven `.social.jsonl` files,
`synthetic-patterns.observations.jsonl`, `synthetic-patterns.social.jsonl`,
`summary.json`, and `report.md`. The summary
records source/config/implementation hashes and full per-recording distributions.
The verifier loads the short recordings to compare repeated runs; normal replay
and state processing retain bounded histories.

Enable the same layer live:

```bash
.venv/bin/python -m app.server --host 127.0.0.1 --port 6060 \
  --output var/recordings/social-live-01.jsonl \
  --tracking-output var/temporal-validation/social-live-01.tracks.jsonl \
  --social-output var/temporal-validation/social-live-01.social.jsonl \
  --temporal-config config/temporal-state.json
```

Use the existing reverse tunnel and robot client. All output paths must be new;
tracking output is optional when social output is enabled. No robot-side change
is required to compute these estimates.

Run verification:

```bash
.venv/bin/python -m unittest discover -s tests -v
```
