# Recorded camera association (Step 4)

`python -m app.image_match` adds `image_matching` to each original Step 3 replay
row. It uses explicitly supplied camera manifests and preserves the row's source,
complete frozen SocialState, exact `social_state_json`, decision IDs, sampling,
configuration, preparation/inference status and model diagnostics. It never
rebuilds a state, resamples decisions, contacts a model or executes robot actions.

## Recordings and established provenance

All nine `var/recordings/scenario-0N.cameras/frames.jsonl` files are stored capture
v1 manifests. Each event contains `capture_version`, original `session_id`,
`camera` (`head` or `chest`), per-camera `sequence`, `event`,
`received_monotonic_us`, `received_unix_us`, and `file`. A `frame` also has the
unchanged camera SDK `timestamp_us`, `width` and `height`, with a relative PPM
path. An `unavailable` event has a reason and `file: null`.

The nine manifests contain 179 head frames (22, 25, 20, 14, 14, 18, 23, 23, 20)
and nine chest-unavailable events reporting the missing SDK camera class. There
are no head-unavailable events. Every head image is readable P6 RGB8, 640×480,
maxval 255, with 921,600 raster bytes. Stored file size is 921,615 bytes including
the header. Camera session IDs equal their corresponding SDK capture IDs in the
records themselves; filenames are never used to establish a matching session.

Per-camera sequences and timestamps are ordered with no duplicate frame keys in
these recordings. Global mixed-camera receipt ordering is not sorted in scenarios
02/03/04/08 because independent camera workers enqueue at different times. The
reader does not reject those valid manifests or rewrite their order.

| Basis | What is established |
|---|---|
| Reception monotonic time | Both collectors use `time.monotonic_ns() // 1000` on the robot host. `main.py` passes the SDK capture session into `CameraCapture` in both collection paths. Known v1 co-captures with the same original session therefore share that receipt clock. |
| Reception Unix time | Both use `time.time_ns() // 1000`; retained as separate wall-clock provenance, never used for selection or clock conversion. |
| SDK perception `packet.time` | Serializer preserves the value without conversion. Its clock/unit mapping is not established. |
| Camera SDK `timestamp_us` | Collector forwards the SDK member unchanged. Its name nominally denotes microseconds, but the capture code does not establish exposure synchronization or a mapping to perception time. |
| Equal SDK values | 75 of all 730 perceptions have a numerically equal head-frame timestamp; 26 of the selected 73 moments do. This supports **exact recorded timestamp equality**, not hardware/exposure synchronization. |

Capture v1 does not carry a separate host or clock-domain identifier. Reception
matching is supported for this repository's known SDK/camera co-capture format,
requiring the original v1 perception envelope, matching session and consistent
duplicated receipt timestamps. Raw/saved-social inputs without that provenance
cannot supply it through a processing session, filename or estimation timestamp.
There is no generic clock mapping or Unix/SDK/estimation-time substitution.

## Deterministic matching

1. Restrict events to `source.original_capture_session` and the selected camera
   (default `head`). Missing session/camera/frame availability gives an explicit
   failure; no other session or camera is borrowed.
2. Prefer unchanged integer equality between recorded perception `packet.time`
   and camera `timestamp_us`. Method: `exact_recorded_sdk_timestamp`. SDK
   difference is zero in unverified recorded SDK units. No receipt-age limit is
   applied to this distinct method.
3. With `--allow-receipt-match` and an explicit `--max-frame-age`, if no exact
   candidate exists, use the newest frame with camera receipt **at or before**
   perception receipt. Method: `reception_monotonic`. The configured age boundary
   is inclusive. Fractional microsecond maximums are floored, never rounded up.
4. If the chosen matching key has multiple candidates, equivalence requires the
   same complete manifest record (with normalized resolved image path) and the
   same resolved image. Only proven equivalent references are sorted into a
   canonical reference, with all equivalent manifest references retained.
   Different sequence/provenance/image references yield `ambiguous_candidates`;
   there is no arbitrary first-choice or fallback.
5. Validate the selected image. A missing, unreadable, invalid or dimension-mismatched
   image remains a failure; no alternative is silently substituted.

Receipt differences are **camera receipt minus perception receipt**, in
microseconds; negative means the camera arrived earlier. Exact associations also
record this separate difference. If an exact SDK-equal frame arrived later, the
positive receipt difference and an explicit offline/live-availability limitation
are retained. Every actual exact match inspected here arrived before perception.
Approximation never uses future receipt frames. Selection never examines visual
content, model action or scenario expectations.

## Validation and output

Stored manifests differ from the live RGB/base64 transport envelope, so the
existing transport validator cannot be reused by inventing image bytes. No
stored-image reader existed. `app/camera/recordings.py` adds a narrow read-only
P6 RGB8 checker: regular readable file, bounded header/file size, P6/maxval,
positive dimensions, exact raster length and manifest dimension agreement. It
retains a SHA-256 digest and file byte count. It supports header comments and
preserves initial raster whitespace/`#` bytes. Images are never modified,
decoded into another format, resized or encoded for Ollama.

Image paths must be relative to and confined within the manifest directory;
absolute paths, parent traversal and symlink escapes fail with source location.
Confinement is rechecked when the selected file is read. Missing selected files
produce per-row `missing_image`, while malformed manifests fail the run before
matching. Other per-row categories include `missing_capture_session`,
`missing_camera_session`, `no_camera_frame`, `camera_unavailable`,
`unsupported_timestamp_provenance`, `no_exact_timestamp`, `no_prior_frame`,
`frame_too_old`, `ambiguous_candidates`, `unreadable_image`, `invalid_image` and
`image_dimension_mismatch`.

The replay reader validates complete canonical snapshots, correlation and the
existing preparation/inference result structure without changing their values.
Rows already containing `image_matching` are refused so a previous association
cannot be overwritten. `CameraMatcher.match(row)` returns an independent matching
result; later VLM replay can use that frame and the existing frozen state.

The CLI adds one field and preserves original row order/count during a completed
pass. Original top-level `status`, `ok`, `error` and decision fields remain
independent of nested matching status. Output paths resolving to the replay,
any supplied manifest or referenced image (including a missing image) are
rejected. Exclusive creation refuses existing outputs and aliases.

| Exit | Summary meaning |
|---|---|
| 0 | `complete`: all rows matched |
| 1 | `complete_with_unmatched`: complete pass, unmatched rows retained |
| 2 | `failed` or `partial`, `complete: false`: malformed input/configuration or write failure |

Summaries include valid rows processed, rows flushed, match counts by method and
failure counts by category. A write failure can leave counts for an attempted
result greater than `rows_written`; prior output is retained and explicitly
reported partial. No inference or action is manufactured from a matching failure.

## Commands with the existing recordings

Choose a new output path each time. The model/URL arguments below are only the
existing Step 3 preparation metadata; no server/model is needed.

```bash
# Recreate the selected Step 3 decision moments, interval 1 source second.
.venv/bin/python -m app.llm_replay var/recordings/scenario-0{1..9}.sdk.jsonl \
  --format sdk --prepare-only --sample-interval 1 \
  --base-url http://127.0.0.1:11434 --model preparation-only \
  --temperature 0 --seed 42 --num-predict 256 \
  --output /tmp/p4p-step4-prepared.jsonl

# Default head-camera exact recorded timestamp matching.
.venv/bin/python -m app.image_match /tmp/p4p-step4-prepared.jsonl \
  --manifests var/recordings/scenario-0{1..9}.cameras/frames.jsonl \
  --output /tmp/p4p-step4-exact.jsonl

# Separate exact-first pass with explicit prior receipt matching, maximum 1 s.
.venv/bin/python -m app.image_match /tmp/p4p-step4-prepared.jsonl \
  --manifests var/recordings/scenario-0{1..9}.cameras/frames.jsonl \
  --allow-receipt-match --max-frame-age 1 \
  --output /tmp/p4p-step4-receipt-1s.jsonl
```

## Actual validation results

On 2026-10-07, fresh Step 3 preparation processed all 730 perceptions and selected
73 moments. Both actual matching passes wrote all 73 rows. Removing only
`image_matching` from every output reproduced its original input row exactly,
including its canonical SocialState string. Machine-readable evidence is in
[`results/image-matching-step4.json`](results/image-matching-step4.json).

| Scenario | Rows | Exact matches | Unmatched, exact only | Additional receipt matches ≤1 s | Remaining unmatched |
|---|---:|---:|---:|---:|---:|
| 01 | 9 | 1 | 8 | 8 | 0 |
| 02 | 8 | 4 | 4 | 4 | 0 |
| 03 | 11 | 5 | 6 | 5 | 1 |
| 04 | 6 | 5 | 1 | 1 | 0 |
| 05 | 4 | 2 | 2 | 2 | 0 |
| 06 | 8 | 1 | 7 | 7 | 0 |
| 07 | 3 | 2 | 1 | 0 | 1 |
| 08 | 12 | 5 | 7 | 6 | 1 |
| 09 | 12 | 1 | 11 | 11 | 0 |
| Total | 73 | 26 | 47 | 44 | 3 |

Exact-only coverage is 26/73 (35.6%). Its 47 unmatched rows have
`no_exact_timestamp`. With the separately configured one-second receipt policy,
coverage is 70/73 (95.9%), retaining the same 26 exact matches and adding 44
approximate ones. Approximate signed receipt differences range from −989,429 to
−8,177 µs, median −190,378 µs and mean −208,693.6 µs. All are prior frames.

The three remaining `frame_too_old` rows are scenario03 perception sequence11
(1,043,369 µs), scenario07 sequence11 (1,040,843 µs), and scenario08 sequence42
(1,009,661 µs). The one-second limit was not widened to increase coverage.

## Result examples

Abbreviated successful `image_matching` subtree from scenario01; actual output
also includes complete manifest provenance, equivalent references, Unix receipts,
image validation/digest, policy and synchronization limitations:

```json
{"schema_version":1,"status":"matched","ok":true,"method":"exact_recorded_sdk_timestamp","original_capture_session":"bb414cc5b139453885f52d9895b1f97a","timestamp_basis":"recorded SDK PerceptionData.time == camera frame.timestamp_us, without scaling","time_difference":{"value":0,"units":"recorded SDK timestamp units (mapping unverified)","sign_convention":"camera recorded value minus perception recorded value"},"reception_time_difference_us":-80289,"frame":{"camera":"head","sequence":1,"line_number":2,"relative_image_path":"head/000001-1791271628679451.ppm","width":640,"height":480,"sdk_camera_timestamp_us":1791271628679451,"sdk_perception_timestamp":1791271628679451},"error":null}
```

Abbreviated failure subtree from scenario03; original prepared/model result fields
and frozen state remain present beside it:

```json
{"schema_version":1,"status":"unmatched","ok":false,"method":null,"original_capture_session":"3425596259d0482da6d9275fa8b10c83","policy":{"camera":"head","allow_receipt":true,"max_age_us":1000000},"frame":null,"error":{"category":"frame_too_old","message":"Latest prior frame exceeds the configured maximum age","details":{"age_us":1043369,"max_age_us":1000000}}}
```

## Verification and Step 5 boundary

**83 new focused tests and 163 relevant existing regressions passed (246 total).**
The 237 tests without socket fixtures passed in the default sandbox. The nine
existing camera/SDK capture tests initially could not bind a loopback socket in
the sandbox; an approved local-fixture run passed all nine. No test contacted
Ollama or robot hardware.

```bash
.venv/bin/python -B -m unittest \
  tests.test_camera_recordings tests.test_image_matching tests.test_image_match_cli \
  tests.test_camera_capture tests.test_sdk_capture tests.test_navel_adapter \
  tests.test_llm_replay_inputs tests.test_llm_replay \
  tests.test_llm_policy tests.test_llm_runner tests.test_model_decision tests.test_ollama \
  tests.test_social_state tests.test_tracks tests.test_recording
```

Focused tests cover exact/session/camera isolation, supported receipt provenance,
age boundaries/future exclusion, deterministic/equivalent versus ambiguous keys,
unavailable/missing/bad images, path confinement, frozen row preservation,
unmatched row retention, output protection, and partial runs. Relevant existing
camera/SDK/adapter/Step 1–3/tracking/social regressions also run without Ollama or
robot hardware. Existing capture regressions use temporary loopback HTTP fixtures;
the matching component itself opens no sockets.

Step 5 still needs image encoding and VLM prompting/inference. This step does not
establish exposure synchronization, a new SDK clock mapping, or that reconstructed
SDK replay states equal the originally transmitted live frames. P6 references and
their validated dimensions/digests are ready for a later explicit conversion
step; unmatched rows remain explicit inputs for that runner's policy.
