# Two-recording V2 gaze scorer

V2 has been fitted to selected intervals from both `gaze-v2-calibration` SDK
recordings. The frozen model is `config/gaze-score-v2.json`. It is an
**experimental candidate, not recommended for live policy use**: several
fitting results improve, but cross-recording performance is poor and direct
gaze often becomes ambiguous. The existing live pipeline and V1 are unchanged.

## Run the frozen model

```bash
python3 -m evaluation.gaze_score_v2 score \
  var/recordings/NEW-TEST-sdk.jsonl \
  --model config/gaze-score-v2.json \
  --output-dir var/gaze-scores/new-test-v2
```

This automatically extracts SDK features with the model's saved configuration.
It writes `scores.jsonl`, a model copy and `report.json` in a new directory.
Existing output directories are refused. No robot SDK, cameras, external model
server or extra Python dependencies are required.

The completed combined training outputs are in
`var/gaze-scores/two-run-v2/`: `model.json`, `report.json`,
`calibration_1-scores.jsonl` and `calibration_2-scores.jsonl`.

To reproduce fitting and comparisons:

```bash
python3 -m evaluation.gaze_score_v2 train config/gaze-v2-training.json \
  --baseline-model var/gaze-scores/first-test-v1/model.json \
  --output-dir var/gaze-scores/two-run-v2-repeat
```

The optional baseline is the original V1 trained on the first gaze test. The
new model fits only the two V2 recordings listed in the manifest. Source and
label paths in the manifest are relative to the manifest directory.

## Inputs and fitting

V2 uses five inputs: signed gaze x/z angle, signed gaze elevation and the three
SDK person-head rotation components in their original units. It applies a
0.3-second trailing median jointly to those components, resetting on invalid
gaze, missing head pose, duplicate/missing IDs, disappearance, capture gaps or
session changes. It does not use identity, time, distance, face position or SDK
overlap as classifier inputs.

The model standardizes those inputs using fitting data only, then uses a
logistic classifier with five linear terms, five squared terms and ten
pairwise interactions. Interactions allow the meaning of an eye direction to
depend on head pose. This is a learned association, not a physical transform:
the SDK axes and head rotation conventions remain unverified. Component
medians also assume the small frontal-angle range in these recordings; this
is not a general circular-angle filter for directions crossing +/-180 degrees.

Training uses fixed L2 regularization of 0.05, equal total weight per recording,
equal class weight within each recording and equal action-phase weight within
each class. The first 0.3 seconds of every annotated interval are excluded to
avoid carrying the preceding phase through the median window. There are 977
usable labelled samples (392 DIRECT, 585 AWAY); adjacent samples are highly
correlated and do not represent 977 independent trials. All three optimizer
runs converged under the fixed gradient tolerance of 1e-6.

Scores >=0.7 classify DIRECT, <=0.3 AWAY, and intermediate scores AMBIGUOUS.
These cutoffs have not been tuned or validated. UNKNOWN has a null score;
missing measurements are never replaced with 0.5. A finite score near 0.5 is
AMBIGUOUS and is distinct from the SDK's suspected 0.5 default bundle. V2
also requires usable head pose; it does not silently fall back to V1.

## Labels and exclusions

The versioned labels are in `config/gaze-v2-calibration-labels.json` and
`config/gaze-v2-calibration-2-labels.json`, bound to the SDK files by SHA-256
and session ID. Both are provisional and retain the uncertainty in the files:

- Calibration 1: early eye phases follow the user's reported eight-second
  schedule from first visibility, with margins excluded. The final direct
  hold is inferred from the end of the visible sequence. The head-action
  section (94-153 seconds) is excluded because the actual timing cannot be
  resolved from the stated schedule. This recording supplies no labelled
  head-turn examples for cross-recording fitting.
- Calibration 2: use the settled action intervals inspected before retraining.
  Neutral returns were brief. Initial settling, transition periods and
  unassigned neutral resets are excluded. The head-and-eyes-left segment
  includes only the established late turn, 61-64 seconds.

These are not independently timestamped ground-truth annotations. Their
uncertainty can explain part of the conflicting relationships between runs.
Do not describe these experiments as validated eye-contact accuracy.

## Results

Scores below are phase medians on calibration 2's annotated interiors, after
the same 0.3-second boundary exclusion for both models. V2 has fitted these
examples; this table describes fitting behaviour only.

| Intended condition | Original V1 | Combined V2 |
| --- | ---: | ---: |
| Initial direct | 0.96 | 0.47 |
| Eyes left | 0.95 | 0.55 |
| Eyes right | 0.09 | 0.13 |
| Eyes up | 0.81 | 0.28 |
| Eyes down | 0.94 | 0.49 |
| Head and eyes left | 0.06 | 0.11 |
| Head and eyes right | 0.001 | 0.04 |
| Head left, eyes direct | 0.94 | 0.81 |
| Head right, eyes direct | 0.01 | 0.95 |
| Final direct | 0.96 | 0.48 |

V2 learns the head-right/eyes-direct distinction and reduces some false-high
eye-away scores. However, it also makes neutral direct gaze ambiguous. In
calibration 1, most away and initial-direct scores are also ambiguous. The
combined model gives a decisive DIRECT/AWAY label to only 13.5% of annotated
calibration-1 samples and 41.9% of calibration-2 samples. Scoring availability
over those samples is 97.7% and 98.0%, respectively; the large undecided fraction
is mostly ambiguity, not missing tracking.

Before fitting the combined model, each recording was excluded in turn from
coefficient fitting and feature standardization. No random adjacent-frame
split was used and the regularization/cutoffs were fixed across folds.

| Evaluation recording | V1 balanced accuracy | V2 fitted on the other recording |
| --- | ---: | ---: |
| Calibration 1 | 50.0% | 22.0% |
| Calibration 2 | 60.8% | 45.6% |

Balanced accuracy here is the average of DIRECT recall and AWAY recall on
scorable labelled samples using a forced 0.5 binary threshold. It is a
diagnostic distinct from the 0.3/0.7 abstaining states. UNKNOWN samples are
excluded from that calculation but included in coverage and state counts in
the report. The folds have disjoint fitting data, but both recordings already
informed the feature design and approximate labels, so these are exploratory
cross-recording diagnostics, not an independent evaluation.

The combined model's corresponding fitting balanced accuracies are 83.2% and
64.2%; those values must not be substituted for the poor cross-recording
results. The final model has seen both recordings and has no unseen test set.

The evidence does not establish a reliable gaze classifier. It suggests
conflicting labels, session/pose dependence, limited example diversity, or
some combination. Do not compensate by selecting thresholds on these same
results and claiming generalization. The next evaluation needs independent
action timestamps and matched conditions, especially direct gaze versus
eye-only away and head-right/eyes-direct.

## Verification

```bash
python3 -m unittest tests.test_gaze_score_v2 tests.test_gaze_score tests.test_gaze_features
```

29 tests cover directional/head interactions, fitting-only normalization,
phase/run weights, UNKNOWN handling, session/UID isolation, label checksums,
fold exclusion, preservation of V1, and save/load replay. Replaying the frozen
V2 model reproduces the training run's score file byte for byte.
