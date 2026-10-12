# Main-branch LLM implementation and recording replay

Reviewed on 12 October 2026 against main commit `f2f7bfed8172350db6906a4f75bd08a6c1484445` (merge of PR #10, path conflict). GitHub's current `refs/heads/main` was independently checked and matched this checkout. Application/configuration sources were unchanged; existing poster work was preserved.

The current versions are `uid-tracking-v3`, `temporal-social-v6`, `social-rules-v7`, and `social-state-llm-v14`. Some introductory README statements and older freeze manifests describe earlier branches or policy versions. This review uses executable code and explicitly selected configuration.

The implementation runs from SDK measurements through temporal estimation into a text-only LLM decision. It includes readable evidence labels, but the model can still contradict those labels. This replay produced nine first decisions plus 46 full-recording diagnostic decisions. All 55 were valid structured outputs. Six first decisions matched the allowed actions in the tuning contract; two first decisions passed the simulated live client's acceptance checks. These are different measurements.
The implementation runs from SDK measurements through temporal estimation into a text-only LLM decision. It includes readable evidence labels, but the model can still contradict those labels. This replay produced nine first decisions plus 46 full-recording diagnostic decisions. All 55 were valid structured outputs. Six first decisions matched the allowed actions in the tuning contract; two first decisions passed the simulated live client's acceptance checks. These are different measurements.

## Sensor acquisition and reduction

`robot.next_frame()` supplies perception and `robot.next_locomotion()` supplies odometry/ranges in concurrent collectors. Each perception packet becomes one RawObservationFrame, paired with the most recently received locomotion packet. Locomotion older than 1 second is unavailable. The SDK adapter selects:

| SDK data | RawObservationFrame field | Conversion and use |
| --- | --- | --- |
| Person `uid` | `people[].uid` | Nonnegative integer identity for tracking; duplicate IDs retain the first entry; invalid IDs are skipped. |
| Person `dist_mm` | `distance_m` | Divide by 1000. This scalar drives proximity and the fitted separation trend. |
| Person `gaze_overlap` | `gaze_overlap` | Retain finite [0,1] score. This is the SDK's estimated overlap, not an independently computed eye-contact probability. |
| Person `face` bounding box | `face_detected` | Set true only when all four coordinates are finite and the box has positive width/height. Missing/invalid boxes provide no positive evidence; they are not converted to false. |
| `g_head_position` in CAM_HEAD | `optional_relative_head_position` | Retain x/y/z in metres and frame label; exposed as descriptive geometry. Head rotation is not used as position. |
| Unique nonzero `g_nose` in CAM_HEAD | `optional_relative_nose_position` | Retain x/y/z in metres. Used for the forward-corridor classifier and optional UID reassociation. Other frames are not substituted. |
| `odometry.velocity.linear_x` | `robot.linear_velocity` | Signed forward m/s. |
| `odometry.velocity.angular_z` | `robot.angular_velocity` | Yaw rad/s. |
| LIDAR distance array | `safety.lidar` | Retain native values and slots, front/back ordering. |
| SONAR distance array | `safety.sonar` | Retain native values and slots, front-right/front-left/back ordering. |
| Robot monotonic clock at adapter conversion | `timestamp` | Microseconds; not UTC or the SDK packet's time. |

Nonfinite values, booleans, negative distances/ranges, and out-of-range gaze become null. Invalid optional positions are omitted. Missing values are not replaced by zero. Numeric gaze 0.5 is usable low gaze in the current default and final preset; `gaze_unavailable_score` can explicitly exclude another source's sentinel.

LiDAR/sonar arrays are transported and stored, but are not included in SocialState or the structured LLM prompt. Their native units are not converted or established by this implementation. The LLM does not receive an obstacle map, camera pixels, microphone/audio, gestures, speech, robot position/map, full person skeletal landmarks, lateral velocity, or head rotation. Camera capture/VLM and complete SDK packet capture are separate optional channels. A face bounding box is reduced to positive detection evidence, not sent as an image.

Implementation: [adapter](/Users/eddiekim/Documents/Projects/P4P/robot/navel_client/adapter.py:35), [raw schema](/Users/eddiekim/Documents/Projects/P4P/app/domain/models.py:25).

## HTTP transport and server processing

The robot retains one pending observation, replacing it when newer data arrives. The sender also drains newer pending frames before a send. It starts at most one observation POST every 0.1 seconds by default, using a worker thread so network I/O does not block acquisition. Consequently, a slow server drops intermediate outgoing observations while preserving the latest. Default request timeout is 5 seconds; the transport has no automatic retry loop for an individual POST.

The normal request is JSON to `POST /api/v1/observations` on the computer, normally port 6060. A model trial wraps the raw observation with optional SDK provenance and trial identity. Trial creation and result polling use `/api/v1/model-trials` and `/api/v1/model-trials/result`. Optional complete SDK and camera streams use `/api/v1/sdk-packets` and `/api/v1/camera-frames`.

The server strictly validates the raw schema and rejects malformed/extra fields and duplicate UIDs. Duplicate/backward frame timestamps are rejected with 409. Raw data is persisted before derived processing. Tracking/estimation failure after persistence is explicitly returned as a failed processing status, with no decision. Processing order is serialized.

When social/model processing is enabled, the chain is:

```text
SDK perception + latest fresh locomotion
  -> NavelObservationAdapter -> RawObservationFrame
  -> HTTP observation endpoint -> validation/raw JSONL
  -> TrackManager -> SocialStateEstimator -> complete SocialState
  -> shared readiness gate -> frozen JSON + LLM instructions
  -> local Ollama /api/chat -> strict {action, reason}
  -> correlated asynchronous trial result -> robot acceptance
  -> selected behaviour handler when physical execution is explicitly enabled
```

The social pipeline also independently computes rule proposals, target locks and correlated command proposals. Its ordinary response keys `policy_decision`, `final_decision`, `target_lock`, and `robot_command` are from that rule path. Enabling LLM inference does not turn `final_decision` into the LLM answer. The LLM result is delivered separately through the model-trial result envelope or recorded in model audit output. This distinction matters when inspecting an HTTP response.

Implementation: [collector](/Users/eddiekim/Documents/Projects/P4P/robot/navel_client/main.py:63), [transport](/Users/eddiekim/Documents/Projects/P4P/robot/navel_client/transport.py:29), [observation endpoint](/Users/eddiekim/Documents/Projects/P4P/app/server.py:176), [social pipeline](/Users/eddiekim/Documents/Projects/P4P/app/social_pipeline.py:18).

## Tracking and temporal calculations

Tracking keys are session, SDK UID and acquisition epoch. Histories are bounded to 3 seconds, 64 samples per track and 32 tracks by the final preset. A disappeared person is `TEMPORARILY_MISSING` for up to 0.75 seconds, then lost; a newly acquired track receives a new epoch. Events include ACQUIRED, MISSING, REACQUIRED, LOST and REASSOCIATED.

The final tracking preset can preserve an epoch across a changed SDK UID when exactly one retained track and one current person have adjacent observed nose points within 0.25 seconds and 0.45 metres. This is explicitly flagged `NOSE_REASSOCIATED_UNVERIFIED`; it does not verify physical identity. The default tracker disables this reassociation. Every state also includes track age and time since last seen, computed from robot monotonic timestamps.

**Gaze sample state.** Each usable score establishes true at or above `looking_enter`, false at or below `looking_exit`, and retains the previous boolean between them. A gap, missing sample or nonadjacent observation resets this memory. In the final preset, near thresholds are 0.93/0.90; beyond 2.75 m they are 0.97/0.95. Thus a score such as 0.94 can be non-looking at longer range; the overlap score alone does not determine the temporal label.

**Time-weighted gaze evidence.** Only consecutive observation sequence numbers with positive gaps no longer than 0.25 seconds contribute intervals. Both endpoint sample states must be usable. `gaze_valid_coverage_s` sums those intervals; `looking_time_s` sums intervals whose starting state is true. `gaze_fraction = looking_time_s / gaze_valid_coverage_s`. `gaze_coverage_fraction = gaze_valid_coverage_s / window_span_s`. This is a duration fraction, not the arithmetic mean of overlap scores. `mean_gaze_overlap` is a separate arithmetic diagnostic.

`looking_bouts` counts a leading true state and observed false-to-true transitions; detector gaps do not manufacture extra bouts. `sustained_gaze_s` is the trailing continuous run of adjacent true/true intervals. Latest measured score and boolean are exposed separately.

Gaze is valid only with a currently observed usable sample, at least 5 usable samples, at least 1 second of window span, 0.6 seconds of valid coverage, and at least 50% coverage in the final preset. Invalid evidence makes `gaze_state` UNKNOWN immediately.

**Gaze category.** With valid evidence, SUSTAINED requires currently looking and gaze fraction at least 0.80 on entry or 0.65 while retaining SUSTAINED, plus the configured trailing-run minimum. NONE requires fraction at most 0.20 on entry or 0.35 while retaining NONE. Other valid cases are INTERMITTENT. Category candidates must last the configured dwell before replacing the old category. Final dwell and trailing-run minimum are both zero; defaults are 0.3 and 0.4 seconds. Current gaze and historical category can therefore differ, especially with hysteresis.

**Distance zone.** Classify current valid distance as TOO_CLOSE, INTERACTION_RANGE, APPROACHABLE or FAR. Final boundaries are 0.6, 2.0 and 6.0 m with zero hysteresis: ≤0.6, >0.6–2.0, >2.0–6.0, and >6.0. Defaults use 0.6/1.5/3.0 m with 0.1 m hysteresis. With hysteresis, a zone changes only beyond the boundary plus/minus the band; initial equality handling differs from the zero-hysteresis configuration. Missing/current-jump-invalid distance produces UNKNOWN.

**Relative distance trend.** Use the newest contiguous valid-distance segment in the window. Nulls, observation gaps and jumps larger than `3.0 m/s × interval + 0.05 m` break the segment. Fit at least 5 samples spanning 1 second, downsampling to at most 32 evenly spaced samples when necessary. The slope is the median of all pairwise slopes (Theil–Sen); the intercept is the median of `distance − slope × time`. Fit residual is RMS error. A trend is valid only if the current distance is valid and residual ≤0.1 m. Slopes below −0.1 m/s are DECREASING, above +0.1 INCREASING; values in between are STABLE. Otherwise the trend is UNKNOWN. A numeric slope can remain present even when invalid: consumers must check validity.

This measures separation, not human walking velocity. Robot movement can produce the same decreasing/increasing trend; there is no ego-motion compensation or trajectory prediction.

**Nose corridor.** Under the fixed-head assumption, transform CAM_HEAD nose x/y with configured camera planar offset and yaw:

```text
forward = camera_x + cos(yaw) * x - sin(yaw) * y
lateral = camera_y + sin(yaw) * x + cos(yaw) * y
```

Forward ≤0 gives BEHIND. Otherwise absolute lateral position inside the corridor gives IN_WAY; outside gives TO_SIDE. Final half-width is 0.40 m and hysteresis 0.05 m: a previous IN_WAY is retained out to 0.45 m; a previous TO_SIDE enters at 0.35 m. Initial classification uses 0.40 m. Missing/invalid geometry or disabled fixed-head assumption gives UNKNOWN. Geometry uses x/y; z is checked for validity but not used in the planar classifier. These are current position proxies, not verified collision risk, clearance or a curved-route occupancy model. Head motion invalidates the fixed-camera interpretation unless handled by an upstream transform; this code does not compensate it.

**Robot motion.** MOVING if a valid absolute forward speed exceeds 0.02 m/s or yaw speed exceeds 0.03 rad/s. STATIONARY requires both measurements valid and within tolerance. Otherwise UNKNOWN. Missing components add VELOCITY_INCOMPLETE. Partial velocity can still establish MOVING.

**Empty scene and changes.** Empty-observation duration starts at the first continuously empty observation and resets on a visible person or a source gap >0.25 seconds. The state records this duration and count. `cue_changes` lists previous/current gaze category, distance zone, separation trend and path-position changes. Track lifecycle events remain separately available.

Implementation: [tracking](/Users/eddiekim/Documents/Projects/P4P/app/state/tracks.py:82), [feature extraction](/Users/eddiekim/Documents/Projects/P4P/app/state/features.py:48), [estimator](/Users/eddiekim/Documents/Projects/P4P/app/state/estimator.py:29), [corridor](/Users/eddiekim/Documents/Projects/P4P/app/state/path_position.py:9).

## SocialState fields and readable labels

The LLM gets the complete current SocialState, including:

| Group | Included fields |
| --- | --- |
| Provenance/configuration | Schema and estimator versions, state/session IDs, ingest sequence, robot timestamp, configuration hash/version, full effective TemporalConfig, PROVISIONAL calibration status. |
| Readiness | `observation_readiness`, `readiness_reason`, empty-scene duration/sample count. |
| Robot | Raw forward/yaw velocity, derived motion state and measurement-validity flags. |
| Person identity/lifecycle | UID, track epoch, OBSERVED/TEMPORARILY_MISSING, track age, time since seen. |
| Person cues | Latest distance, gaze category, distance zone, relative trend, face evidence, head/nose geometry, corridor category and forward/lateral coordinates. |
| Optional measured semantics | `path_relation`: UNKNOWN/CLEAR/CONFLICT; `pass_gesture`: UNKNOWN/PASS. The SDK adapter does not infer either. These recordings leave both UNKNOWN. |
| Evidence | Window span; mean/latest gaze; effective looking thresholds; looking time/bouts; gaze fraction; coverage duration/fraction/count; trailing sustained run; distance slope/span/residual/sample counts/fit counts/segment-start timestamp/jump count; gaze/trend/latest-distance validity booleans. |
| Quality | Identity, visibility, missing gaze/distance, insufficient coverage, pending category dwell, distance jumps and unreliable-fit flags. |
| Changes | Cue transitions and track events from the current update; this is not a full previous-state conversation transcript. |

`observation_labels` version 1 provides these exact aliases:

| Label field | Values |
| --- | --- |
| `attention_over_time` | LITTLE_OR_NO_LOOKING_AT_ROBOT, INTERMITTENT_LOOKING_AT_ROBOT, SUSTAINED_LOOKING_AT_ROBOT, UNKNOWN |
| `current_gaze` | LOOKING_AT_ROBOT, NOT_LOOKING_AT_ROBOT, UNKNOWN |
| `proximity` | CLOSER_THAN_CONVERSATION_DISTANCE, WITHIN_CONVERSATION_DISTANCE, BEYOND_CONVERSATION_DISTANCE, BEYOND_CONFIGURED_SOCIAL_RANGE, UNKNOWN |
| `corridor_position` | IN_ROBOT_FORWARD_CORRIDOR, BESIDE_ROBOT_FORWARD_CORRIDOR, BEHIND_ROBOT, UNKNOWN |
| `relative_separation` | GETTING_CLOSER, STEADY, GETTING_FARTHER, UNKNOWN |

Aliases are recomputed from authoritative categories and validity during validation, even if a saved state supplies stale labels. Temporarily missing people get UNKNOWN aliases. Current gaze can become known before temporal gaze evidence is sufficient. Low attention is distinguished from unavailable attention. These labels do not encode engagement intent, willingness, pass permission, predicted collision or recommended action. Both policies can access the same labels.

Contract: [SocialState and label derivation](/Users/eddiekim/Documents/Projects/P4P/app/state/social_models.py:127).

## Decision eligibility, LLM request and execution

Shared readiness ordinarily requires exactly one currently observed person, valid current distance/zone and usable temporal gaze with a settled NONE/INTERMITTENT/SUSTAINED category. It does not require a valid distance trend, stationary robot or target lock. Multiple people hold observation. Explicit upstream CONFLICT bypasses gaze/distance warmup after the single-person check; measured PASS bypasses gaze warmup after valid distance. The final preset also allows readiness after ≥1 second of continuous empty observations and ≥5 samples. Defaults disable that empty-scene readiness.

An unready state is a status with null action, not a fifth DEFER action. Reasons include NO_VISIBLE_PERSON, MULTIPLE_VISIBLE_PEOPLE, DISTANCE_UNKNOWN, INSUFFICIENT_GAZE_EVIDENCE and GAZE_CATEGORY_NOT_READY.

Production `submit_trial` and `classify_llm` apply this gate. Low-level `decide_llm`, the standalone replay CLI, and ordinary non-trial live audit sampling do not enforce it themselves. The prompt's claim that readiness has already been checked therefore applies to gated use, not every entry point. This review's full diagnostic explicitly calls `classify_llm` only on ready states.

The prompt builder revalidates and detaches the whole state, recomputes labels/readiness, and freezes sorted compact JSON. Each request contains a system message and one user message with that JSON. It does not include intended scenario names, expected actions, rule decisions, raw SDK history, earlier model replies, images or an extra model target field. The temporal cues already summarize prior measurements.

Prompt v14 asks for independent social judgment balancing safety/comfort, willingness to interact and fixed-route progress. It defines four actions and explains descriptive labels, missing evidence and relative-motion limits. It does not impose the rule policy's precedence or thresholds as action instructions; the effective configuration is still visible in the JSON. No rule correction or fallback replaces an LLM answer. In particular, disabling recurring attention is a rule setting the LLM sees, not a hard LLM constraint.

Ollama receives `POST /api/chat` with `stream: false`, the strict final-decision JSON schema and generation settings. This review used the repository's final preset: local `qwen2.5:7b`, temperature 0, seed 42, 8192 context tokens, 192 output tokens, 120-second HTTP timeout. Installed Ollama was version 0.34.0. These are explicit evaluation settings; the server defaults to model inference disabled and requires a caller-selected model.

The model returns exactly `{"action": "CONTINUE|APPROACH|ENGAGE|YIELD", "reason": "..."}`. Validation rejects invalid actions, extra/missing fields, duplicate keys, malformed JSON, code fences and blank reasons. The requested one-sentence/25-word explanation is prompt guidance, not an enforced length check. Semantic fidelity to the observations is not validated. Errors return null decisions and diagnostic categories rather than invented actions.

The live worker freezes inputs, runs independently of sensor ingestion and audits source identity, request settings, raw model content, output/error and duration. Ordinary audit sampling defaults to a 1-second source interval, with one worker and four waiting jobs; overload drops new selected jobs. Single trials submit the first eligible state and keep that request until completion/failure rather than issuing a new request on every frame.

The robot checks trial/session/request correlation, current readiness and freshness before latching one result. Defaults allow model source age ≤10 seconds, latest observation age ≤1 second and a 30-second trial wait. Invalid/expired model requests may retry. A valid model JSON response is not automatically executable. Once accepted, the trial latches its first action and does not reconsider later cues.

When the robot's execution mode is enabled, CONTINUE finishes the retained route; APPROACH stops the route, uses the local bounded approach controller, verifies the approach and greets; ENGAGE stops/remains still and speaks; YIELD runs the implemented backward/turn escape, pass speech, 3-second wait, nominal return and short advance. The LLM chooses the action, not motion distances/speeds. The current behaviour handlers exist in main; old descriptions suggesting all physical handlers still need to be supplied are stale. A separate script-driven physical-executor interface also remains. This replay instantiated neither a robot connection nor physical execution.

Implementation: [readiness](/Users/eddiekim/Documents/Projects/P4P/app/state/social_models.py:245), [prompt/policy](/Users/eddiekim/Documents/Projects/P4P/app/policy/llm.py:12), [Ollama client](/Users/eddiekim/Documents/Projects/P4P/app/inference/ollama.py:169), [worker](/Users/eddiekim/Documents/Projects/P4P/app/inference/live.py:75), [robot acceptance](/Users/eddiekim/Documents/Projects/P4P/robot/navel_client/single_trial.py:98), [handlers](/Users/eddiekim/Documents/Projects/P4P/robot/navel_client/behaviour_dispatch.py:19).

## Fresh replay method and interpretation

Inputs were exactly the nine files in `var/evaluation/final-scenarios-audit/trimmed`, with hashes checked against the final-scenario contract. No additional trim or invented gesture/path measurements were applied. Every scenario starts a fresh pipeline. SDK reconstruction uses receipt order and perception `received_monotonic_us`; it pairs the latest already-received fresh locomotion packet, never future data. Original clocks/packets and reconstructed raw frames are retained in the diagnostics.

Two passes were run:

1. The existing `scripts.replay_first_action` runner fed production Flask endpoints at the recorded rate, kept observations arriving during inference, and stopped at the first validated LLM output. Production SingleTrial independently checked live acceptance. The runner stops on the validated audit output even when rejected for execution; it does not prove that a later retry could not succeed. HTTP receiver handling was in-process, so this does not measure LAN latency.
2. A full-recording causal diagnostic estimated all 574 perception frames. It selected the first ready frame and subsequent ready frames at least one source second apart, calling the current `classify_llm` policy synchronously. All 402 ready frames were counted; 46 were selected for actual inference. This preserves the complete sensor timeline but is not a simulation of asynchronous real-time execution. Unselected states were still processed. Sampled times can omit brief intervening decisions.

The preset was selected explicitly from `config/policy-comparison-final.json`. It was tuned on these same user-labelled recordings, so action agreement is development-set fit, not held-out accuracy or independent human ground truth. Defaults differ materially: for example, S5 has no ready states under default processing, versus 58 with the final preset's empty-scene permission; S7 has 1 default-ready frame versus 30 final-ready frames. The complete default/final readiness comparison is saved separately.

No claim of observing walking, gestures or willingness is warranted merely from a scenario filename. S5 contains no person after motion onset. S8's first decision precedes the person appearing, so it cannot demonstrate understanding of walking away while looking back. S9 has no measured pass gesture and is classified beside the corridor by the nose proxy.

The 111 selected tracking, SocialState, integration, LLM policy/replay and final-scenario tests passed. No implementation was changed to influence results.

## Results

The following tables and detailed explanations were generated from this fresh run, not copied from historical evaluations.

| Scenario | Allowed first action(s) in tuning contract | First LLM action | Input time after motion | Output time from replay start | Live client accepted |
| --- | --- | --- | ---: | ---: | --- |
| S1 | CONTINUE | APPROACH | 1.18 s | 13.50 s | No |
| S2 | APPROACH | APPROACH | 1.10 s | 8.16 s | No |
| S3 | ENGAGE, APPROACH | ENGAGE | 1.21 s | 7.51 s | No |
| S4 | YIELD | APPROACH | 1.10 s | 8.05 s | No |
| S5 | CONTINUE | CONTINUE | 1.17 s | 3.15 s | Yes |
| S6 | CONTINUE | CONTINUE | 1.11 s | 7.37 s | No |
| S7 | CONTINUE | CONTINUE | 1.71 s | 8.68 s | Yes |
| S8 | APPROACH, ENGAGE, CONTINUE | CONTINUE | 1.17 s | 3.17 s | No |
| S9 | APPROACH | CONTINUE | 1.14 s | 7.94 s | No |

Input times use the first motion-onset locomotion timestamp. Output times use replay start at the first perception receipt; these origins differ slightly. First-decision agreement is 6/9 (66.7%); valid first outputs are 9/9; accepted first outputs are 2/9. Model inference alone took 1.98–12.09 seconds in this pass.

| Scenario | First reason, verbatim | Acceptance explanation |
| --- | --- | --- |
| S1 | Person getting closer and within approachable distance. | Model source exceeded 10 s; latest recorded observation also stale. |
| S2 | Person is getting closer and looking at the robot. | Recording ended; latest observation age 3.49 s exceeds 1 s. |
| S3 | Sustained looking at robot within conversation distance. | Recording ended; latest observation age 2.89 s exceeds 1 s. |
| S4 | Person getting closer and within approachable distance. | Current readiness lost and latest observation stale (1.99 s). |
| S5 | No people observed. | Accepted with fresh continuing empty-scene observations. |
| S6 | Person beyond conversation distance, no looking at robot. | Recording ended; latest observation age 2.80 s exceeds 1 s. |
| S7 | Person is beyond conversation distance and not looking at the robot. | Accepted while sensor observations remained fresh. |
| S8 | No people observed. | Person appeared; latest state was no longer ready when empty-scene result arrived. |
| S9 | Person is beyond conversation distance and not looking at the robot. | Recording ended; latest observation age 4.55 s exceeds 1 s. |

The stale-observation rejections describe these finite recordings. A live stream continuing beyond the saved clip could remain fresh; this run does not establish a 2/9 hardware acceptance rate. S8 was rejected on changing evidence rather than old observation age.

| Scenario | Perception frames | Ready frames | Sampled outputs, in source-time order |
| --- | ---: | ---: | --- |
| S1 | 105 | 82 | 1.18s APPROACH; 2.30s CONTINUE; 3.37s CONTINUE; 4.39s APPROACH; 5.51s ENGAGE; 6.58s APPROACH; 7.80s CONTINUE; 8.89s CONTINUE; 9.89s CONTINUE |
| S2 | 48 | 37 | 1.10s APPROACH; 2.13s CONTINUE; 3.21s CONTINUE; 4.23s CONTINUE |
| S3 | 47 | 36 | 1.21s ENGAGE; 2.30s ENGAGE; 3.41s ENGAGE; 4.48s ENGAGE |
| S4 | 62 | 50 | 1.10s APPROACH; 2.22s CONTINUE; 3.29s CONTINUE; 4.41s CONTINUE; 5.48s CONTINUE |
| S5 | 69 | 58 | 1.17s CONTINUE; 2.25s CONTINUE; 3.25s CONTINUE; 4.37s CONTINUE; 5.46s CONTINUE; 6.54s CONTINUE |
| S6 | 47 | 35 | 1.11s CONTINUE; 2.14s APPROACH; 3.25s CONTINUE; 4.31s APPROACH |
| S7 | 89 | 30 | 1.71s CONTINUE; 2.73s APPROACH; 5.22s CONTINUE; 7.74s CONTINUE; 8.82s ENGAGE |
| S8 | 72 | 50 | 1.17s CONTINUE; 2.26s CONTINUE; 3.87s YIELD; 4.98s YIELD; 6.05s CONTINUE; 7.16s CONTINUE |
| S9 | 35 | 24 | 1.14s CONTINUE; 2.17s APPROACH; 3.24s CONTINUE |

All 46 sampled diagnostic outputs were valid: 9 APPROACH, 29 CONTINUE, 6 ENGAGE, 2 YIELD. Median request duration was 5.53 seconds; range 1.71–7.52 seconds.

**Evidence-based findings:**

- S1: First input was NONE gaze, currently not looking, 3.50 m, decreasing separation, TO_SIDE. APPROACH was selected despite low measured attention. At 6.58 s it selected APPROACH while its own reason said “within conversation distance” (actual 1.20 m), exposing inconsistency with the stated action meanings.
- S2: First APPROACH had sustained/current looking at 2.32 m. Later all three diagnostic samples selected CONTINUE even though the input remained SUSTAINED and LOOKING_AT_ROBOT; at 2.13 s the reason falsely said the person was not looking.
- S3: All four sampled decisions were ENGAGE with sustained/current looking within conversation range. This is the most consistent attentive-interaction recording in this diagnostic pass.
- S4: First input had distance 3.33 m, NONE gaze, NOT_LOOKING_AT_ROBOT, IN_WAY, face_detected true and valid −0.313 m/s separation slope. The rule proposal from that same state is YIELD. The LLM selected APPROACH, followed by four sampled CONTINUE decisions. It never yielded at the sampled moments.
- S5: All six sampled decisions were CONTINUE based on no observed people. This is empty-scene handling, not evidence that walking away was recognised.
- S6: First CONTINUE uses the early low-gaze state, before the later cue changes. Full diagnostics alternated CONTINUE/APPROACH; there was no SUSTAINED gaze category across the processed observed frames. Some distance segments broke on jumps.
- S7: First CONTINUE used INTERMITTENT attention at 2.40 m. Later samples included APPROACH and ENGAGE despite no SUSTAINED category across observed frames. The final rule preset disables recurring attention as an interaction trigger; the LLM was not constrained to follow that rule setting. Numerous acquisition/loss events and unavailable intervals affected readiness.
- S8: First CONTINUE used an empty scene. Once a person appeared, two sampled YIELD outputs used increasing separation, NONE gaze and TO_SIDE; the reasons provided no obstruction evidence. This is a questionable yield justification, not inferred path conflict. Later samples continued.
- S9: First input at 2.29 m had SUSTAINED_LOOKING_AT_ROBOT and LOOKING_AT_ROBOT, but the reason said “not looking.” The same contradiction recurred at 3.24 s; only the middle sample chose APPROACH. pass_gesture and path_relation stayed UNKNOWN, so no output demonstrates pass-gesture recognition.

Labels reduce ambiguity in the input, but valid JSON is not a semantic guarantee. Here the model sometimes invented an incorrect gaze description, treated relative closing as sufficient for approach, and missed a measured in-corridor yield opportunity. The current prompt permits independent judgment; the implementation performs no evidence-consistency check on the reason or action.

## Saved artifacts and reproduction

- [First-action summary](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/main-llm-review-20261012/first-action/summary.json), with acceptance details, expired deliveries, first requests and source timings.
- [First-action manifest](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/main-llm-review-20261012/first-action/manifest.json), with exact source/config hashes, model inventory/version and replay method.
- [Full-recording summary](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/main-llm-review-20261012/full-recording/summary.json), with frame/readiness/cue counts and every sampled action/reason/timing.
- [Diagnostic manifest](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/main-llm-review-20261012/full-recording/manifest.json), with full prompt and actual configuration.
- [Default versus final readiness](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/main-llm-review-20261012/readiness-comparison.json).
- [S4 first ready state](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/main-llm-review-20261012/full-recording/S4/first-ready-state.json) and [exact S4 prompt](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/main-llm-review-20261012/full-recording/S4/first-ready-prompt.txt).
- [S9 sampled model outputs and actual inputs](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/main-llm-review-20261012/full-recording/S9/llm.jsonl).

Every `full-recording/S*/states.jsonl` contains all reconstructed observations, complete SocialStates and an independently computed rule decision when ready. Every `full-recording/S*/llm.jsonl` contains sampled LLM decisions, reasons, durations and exact inputs. Every `first-action/llm/S*` directory contains observation/state/model traces, endpoint events and result.json. Neither expected actions nor rule outputs were passed to the LLM.

Reproduce with fresh output directories and the installed local model:

```sh
.venv/bin/python -B -m scripts.replay_first_action \
  --settings config/policy-comparison-final.json \
  --output var/evaluation/main-llm-review-new/first-action \
  --policies llm --warm-model
```

The full-recording diagnostic runner is saved beside this report. Its ROOT destination is exclusive and must be changed for a new run. Temperature 0 and a seed improve repeatability, but outputs can still differ with model/runtime/hardware changes; the manifests identify this run.
