# V1 gaze feature extractor

An [experimental scoring model](gaze-scoring-model.md) now consumes these
features and produces provisional scores while preserving UNKNOWN estimates.

Extract offline features from the complete SDK capture before calibrating a
replacement gaze score. This runs on the computer using Python's standard
library; it requires neither camera streaming nor the robot SDK. It does not
change the live adapter, social-state estimator, or policy.

From the repository root:

```bash
python3 -m evaluation.gaze_features \
  var/recordings/gaze-tracking-test-recording-cameras/gaze-tracking-test-recording-sdk.jsonl \
  --output-dir var/gaze-features/first-test-v1
```

Choose a new output directory on each run. The extractor creates:

- `features.jsonl`: one row per perception packet, with a `persons` array.
  Empty arrays are preserved so no-detection intervals remain visible.
- `summary.json`: input SHA-256, extractor version, configuration, frame/person
  counts, availability, UID counts per session, and distributions. This file is
  written only after successful extraction; a failed run can leave a partial
  features file but no success summary.

Locomotion packets are counted and skipped. Use the **SDK JSONL**, not the raw
observation or social-state recording: those discard the gaze vector. Capture
envelopes are validated; errors identify the input line. Within a session,
perception receipt times and sequence numbers must increase. Blank lines are
ignored. `elapsed_s` is measured from the session's first perception packet,
including its initial no-person period. This is the time basis for future
annotation intervals. It does not start when a person first appears.

## Feature definitions

| Output | Meaning |
| --- | --- |
| `raw` | Original gaze/overlap, person head pose, face, landmarks, distance, identity score, and all five coordinate-labelled geometry lists, retaining nulls and serialized nonfinite strings. |
| `feature_status` | `AVAILABLE` for a finite, nonzero gaze vector; otherwise `UNKNOWN`. This is numerical availability, not tracking confidence or an attention class. |
| `invalid_reason` | `suspected_sdk_default`, `zero_gaze_vector`, or `missing_or_nonfinite_gaze`. |
| `suspected_sdk_default` | Exactly 0.5 overlap together with a near-zero gaze vector and near-zero head-pose components. This interpretation remains unconfirmed by the vendor. |
| `gaze_norm`, `gaze_unit` | Vector length and normalized x/y/z, with unavailable directions set to null. |
| `reference_angle_deg` | `acos(dot(normalized gaze, normalized reference))`, in degrees. Reference defaults to `(0, 0, -1)`. |
| `gaze_xz_angle_deg` | `atan2(gaze.x, -gaze.z)` in degrees, a signed angle in the raw vector's x/z plane. |
| `gaze_elevation_deg` | `atan2(gaze.y, hypot(gaze.x, gaze.z))` in degrees. Sign follows the raw SDK axes; physical up/down is unverified. |
| `head_pose_xyz_raw` | Finite person head-rotation components in original SDK units/order; not robot head pose. |
| `head_pose_usable` | Finite components and no suspected default bundle. All-zero rotation alone is not rejected; this flag is not model confidence. |
| `distance_m` | Positive finite `dist_mm` converted to metres. |
| `face_center_px`, `face_size_px` | Centre and width/height from a finite box with positive dimensions; no camera identity or bearing is assumed. |
| `id_score_raw_finite` | Finite SDK identity score, without interpreting it as gaze confidence. |
| `reference_angle_median_deg` | Trailing receipt-time median for this UID. Defaults to a 0.3-second window. |
| `temporal_sample_count`, `temporal_span_s` | Support for the median; a newly observed UID initially has just one sample and zero span. |

A raw overlap of 0.5 alone is **not** discarded. Zero, absent or nonfinite gaze
vectors never receive an angular feature, even if overlap is high. SDK strings
such as `"NaN"` remain visible in `raw` but cannot enter numerical features.

Smoothing is isolated by session and positive integer UID. It resets after
invalid gaze, absence from a perception packet, a sequence gap, a receipt gap
greater than 0.5 seconds, or a session change. Duplicate or invalid UIDs retain
instantaneous features but receive no smoothed feature. UID switches are not
stitched together. The window and gap limit are configurable with `--window-s`
and `--max-gap-s`. A trailing median introduces response delay; it is a feature
for evaluation, not yet a selected live-control filter.

## What remains to calibrate

`reference_frame_verified` is always false in V1. The default reference axis
reproduces the exploratory analysis of the first gaze recording. A small angle
means alignment with that mathematical axis, **not established eye contact**.
`--reference-direction X Y Z` can change the axis, but it does not establish its
physical meaning. The x/z and elevation features always use the raw SDK axes.

Do not combine `Person.gaze` with `CAM_HEAD` positions, interpret `g_gaze` as a
point or direction, convert head rotations to a gaze direction, or compensate
for robot head movement until the coordinate conventions and units are
confirmed. Raw geometry is retained so these features can be added later.
Source timestamps are retained without unit conversion or comparison with the
receipt clock; repeated/stale source measurements are not detected by V1.

No labels are inferred from timing, no `DIRECT`/`AWAY` classes are emitted, and
no 0–1 probability is fitted. The first test supports feature development;
additional timestamped trials are needed for calibration and held-out
evaluation. Keep whole trials (and participants, when available) separate
between training and evaluation rather than randomly splitting neighbouring
frames. Record head-following state, distance, actor position and gaze target.
Direction alone cannot resolve fixation on collinear targets at different depths.

Run the feature tests:

```bash
python3 -m unittest tests.test_gaze_features
```
