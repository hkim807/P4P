# Experimental V1 gaze score

An experimental [two-recording V2 model](gaze-scoring-v2.md) now uses signed
gaze directions and head pose. Its cross-recording checks are poor; it is saved
as a separate candidate and does not replace V1 or the live policy.

The scorer turns the feature extractor's smoothed angle into a 0–1 alignment
score. It is an offline baseline fitted to the first gaze test, not a calibrated
probability that a person is looking at the robot. It uses Python's standard
library and does not change the live robot policy.

## Run it

Train and score the first recording from the repository root:

```bash
python3 -m evaluation.gaze_score train \
  var/recordings/gaze-tracking-test-recording-cameras/gaze-tracking-test-recording-sdk.jsonl \
  --labels config/gaze-first-test-labels.json \
  --output-dir var/gaze-scores/first-test-v1
```

This run has already been generated. Use a different output directory to repeat
it; existing directories are refused. The outputs are:

- `model.json`: coefficients, feature configuration, thresholds, training
  labels, class counts, source checksum, and limitations.
- `scores.jsonl`: all extracted perception frames, including empty frames and
  original features. Each person gets `gaze_score`, `gaze_state`, `score_reason`.
- `report.json`: score coverage, state counts, score distributions, and results
  per labelled phase. A completed report is written last.

Apply the saved model to a new recording without refitting it:

```bash
python3 -m evaluation.gaze_score score \
  var/recordings/NEW-TEST-sdk.jsonl \
  --model var/gaze-scores/first-test-v1/model.json \
  --output-dir var/gaze-scores/new-test-v1
```

Feature extraction runs automatically with the settings saved in the model,
so there is no separate extraction step. Keep this model fixed during the next
test. Optionally pass `--labels` with annotations for that new recording to
produce a phase report. Labels require the correct SDK file SHA-256, session,
explicit actor UID, ordered nonoverlapping `[start_s, end_s)` intervals,
`DIRECT`/`AWAY` targets and `train`/`diagnostic` splits. Time starts at the first
perception packet, including empty frames. Labels are used for reporting only
by the `score` command; it never refits the model.

## Model and score meaning

The model is a two-parameter logistic curve:

```text
angle = reference_angle_median_deg
score = sigmoid(intercept + slope * angle / 10)
```

The fitted slope is constrained to be nonpositive: increasing deviation cannot
increase alignment. Training minimizes class-balanced logistic loss with a
fixed slope penalty of 0.01. Class balancing gives equal total weight to direct
and away examples; it does not estimate the actual frequency of eye contact.
The scorer uses the extractor's 0.3-second trailing median, isolated by UID,
and resets on invalid gaze, absence, duplicate IDs and tracking gaps. It does
not add a separate hysteresis state machine.

| Output | Meaning |
| --- | --- |
| `DIRECT`, score >= 0.7 | Stronger alignment evidence under this experimental model. |
| `AWAY`, score <= 0.3 | Weaker alignment evidence under this experimental model. |
| `AMBIGUOUS`, between 0.3 and 0.7 | A usable estimate near the learned boundary. |
| `UNKNOWN`, score null | Missing/invalid gaze or unusable/duplicate identity; never replaced with 0.5. |

The 0.3/0.7 cutoffs are provisional choices, not validated operating thresholds.
The old SDK overlap is retained for inspection but is not an input to the
fitted curve. SDK overlap 0.5 alone does not cause rejection: missing gaze and
the suspected zero-vector default bundle are handled by the extractor.

The reusable API is `GazeScoreModel.from_dict(artifact).predict(person_features)`.
Call it on the output of `GazeFeatureExtractor(model.feature_config)` so angle
reference and smoothing match training. The CLI enforces that configuration
by re-extracting from SDK data. Do not feed a different extractor configuration
to `predict` directly.

## First-test labels and limitations

`config/gaze-first-test-labels.json` records the approximate action windows and
actor IDs. They are inferred from the user's action sequence **and inspection
of these same signals**, not independently timestamped ground truth. That
circularity limits every result from this recording.

The initial direct phase and four eye-only phases fit the model. The first
0.3 seconds of each fitting interval are excluded to avoid carrying samples
across its label boundary. Head turns and final direct gaze are excluded from
fitting and reported as diagnostics. These are from a recording already used
to develop the features; they are not independent validation. Phase summaries
include the full labelled windows and only the assigned actor UID. Other
persons are still scored in the output and included in overall counts.

The model deliberately uses one angular feature because this is one person in
one session with uncertain labels. It does not infer the physical gaze frame,
correct for the robot's head movement, or solve fixation depth for a person
standing directly behind the robot. The final direct phase and eyes-up phase
overlap in angle: this baseline cannot reliably separate them. Adding a
probability-shaped mapping does not recover missing information.

Use another recording with independently logged instructions, repeat direct
gaze between conditions, and include off-centre positions and head-following
states. Report UNKNOWN coverage and ambiguous decisions alongside any future
accuracy figures. Keep entire trials/participants separate when fitting and
evaluating. The existing live gaze thresholds must not be assumed compatible
with these scores.

## First recording results

The fitted curve has intercept `4.223084337793105` and slope
`-3.591144353536963`, using 133 direct and 420 away training samples after
invalid samples, other IDs and interval warm-up are excluded.

| Phase | Use | Median score among scored samples | Scored / actor samples |
| --- | --- | ---: | ---: |
| Initial direct | Fitting phase | 0.889 | 136 / 139 |
| Eyes right | Fitting phase | 0.058 | 118 / 120 |
| Eyes left | Fitting phase | 0.005 | 96 / 102 |
| Eyes down | Fitting phase | 0.046 | 119 / 120 |
| Eyes up | Fitting phase | 0.454 | 98 / 100 |
| Head right | Diagnostic | 0.002 | 39 / 93 |
| Head left | Diagnostic | <0.001 | 12 / 80 |
| Final direct | Diagnostic | 0.366 | 20 / 20 |

The first direct phase and large eye deviations separate well on the data
used for fitting. Eyes-up mostly remains ambiguous, and the short final direct
phase is unreliable: 6 samples classify DIRECT, 8 AWAY and 6 AMBIGUOUS. Do not
report this as an accurate eye-contact detector. The head-turn scores describe
only the few available estimates; most of those phases are UNKNOWN.

Across all frames and IDs, 754 of 916 person samples receive scores (82.3%).
The remaining 162 are UNKNOWN: 148 have missing/invalid gaze, and 14 additional
samples have unusable or duplicate IDs. This differs from the extractor's
768 numerically available vectors because the scorer also requires usable
identity for the smoothed feature. Train-then-save/load replay produces
identical score files. The numerical, missing-data, label-boundary and replay
tests pass; independent behavioural validation is still pending.

Tests:

```bash
python3 -m unittest tests.test_gaze_score tests.test_gaze_features
```
