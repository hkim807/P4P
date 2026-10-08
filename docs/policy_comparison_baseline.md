# Development-set baseline: rule and language-model social decisions

This report describes the working-tree refinement of `feature/ollama-llm-vlm-policies`, based on commit `e6bdbcc`, on 7 October 2026. The nine recordings are a **development/calibration set**, not held-out evaluation data. Recorded behavior, engineering expectations and future participant judgments are kept separate.

The main findings are (1) unequal SDK/camera capture durations that may exclude parts of the acted encounters, (2) insufficient structured person evidence in several files, and (3) an unrepresented pass gesture in Scenario 09. The final run compares 730 states and makes 162 real model calls; strong overall agreement is dominated by no-person observations and must not be described as social accuracy.

## A. Research problem

Navel is intended to follow a fixed laboratory guide route. A social policy determines when continuing that task is appropriate, when a person should receive priority, and when to approach or initiate conversation. Detecting a face alone does not answer these questions: looking may signal curiosity rather than a request; range changes can result from robot movement; a person can look while walking away or gesturing for the robot to pass.

The primary research question is: **How does an LLM policy differ from a deterministic policy when both receive exactly the same structured social observation?** This is a comparison of high-level classifications, not path-planner quality, collision avoidance, conversation quality or physical navigation performance. A later state-plus-RGB comparison asks a different question about the benefit of additional information.

Human-aware navigation includes comfort and social interaction as well as physical navigation [1]. The earlier receptionist study already cited in this repository illustrates why spatial categories alone can produce unwanted or late interaction and motivates temporal evidence [2]. Attention is an indirect correlate of engagement, not consent or a direct measurement of intention [3]. These findings motivate the architecture, **not** the numerical thresholds or claimed correctness of any of our four actions.

## B. Architecture and audit

```text
SDK perception + locomotion capture / raw observation stream
  -> NavelObservationAdapter
  -> TrackingPipeline / UID histories
  -> SocialStateEstimator
  -> detached canonical SocialState JSON + common readiness
        -> deterministic rules
        -> LLM classification
  -> canonical action or internal NOT_READY / inference ERROR
  -> target lock, command planner and external robot execution scripts
```

The evaluation branch stops at classifications and never invokes robot execution.

| Layer | Input → output; implementation | Rationale and limitations |
|---|---|---|
| Capture | SDK packets → timestamped JSONL; `robot/navel_client/main.py`, `sdk_capture.py` | Preserve full packets before reduction. Receipt monotonic time, receipt UTC and SDK time are distinct clocks. SDK and camera capture remain separate streams; the current recordings have unequal stream durations. |
| Adapter | perception + newest fresh locomotion → `RawObservationFrame`; `robot/navel_client/adapter.py` | Validates UID, range and gaze; converts `dist_mm` to metres; preserves unavailable values as null. Does not fabricate body tracks from faces. |
| Replay | SDK envelopes → same adapter, tracker and estimator; `app/replay/llm_inputs.py` | Validates ordering, stream sequences and session boundaries. Latest earlier-or-equal locomotion receipt with age ≤1 s is used. No future locomotion is used. |
| Tracking | raw frames → bounded per-UID, per-epoch histories; `app/state/tracks.py` | Session/epoch boundaries prevent accidental stitching. No face recognition or invented re-identification. Missing detections are retained briefly but not treated as current observations. |
| Features | histories → coverage, slope, fit quality and validity; `app/state/features.py` | Time-weighted adjacent gaze evidence and robust range fitting reduce dependence on sample frequency. Gaps break evidence rather than becoming negative gaze. |
| Estimation | evidence → categories, changes and readiness; `app/state/estimator.py`, `social_models.py` | Hysteresis limits boundary flicker. Retains supporting measurements and explicit unknowns. No interaction probability is claimed. |
| Rules | shared state → action/reason/optional selected target; `app/policy/rules.py` | Small ordered decision table; no recording names or scenario labels. |
| LLM | identical state JSON + fixed instructions → strict action/reason; `app/policy/llm.py`, `app/inference/ollama.py` | `classify_llm` applies shared readiness. Network and parsing failures remain errors. `decide_llm` is retained as an ungated, low-level inference probe for older standalone/live audit tooling. Use the new comparison runner for the primary experiment. |
| Controller | policy proposal + lifecycle/feedback → bounded commands; `app/policy/target_lock.py`, `app/commands.py` | Locking, identity rebound, leases, one-time greetings, cooldown and execution feedback are outside the comparison. |
| Robot executor | correlated commands → user-supplied scripts; `robot/navel_client/physical_executor.py` | Physical APPROACH/ENGAGE are opt-in. Fixed-route navigation and a physical YIELD/resume implementation are not supplied by this experiment. Model outputs remain observational in the existing live server. |
| Evaluation | discovery → states, sampled comparisons, repetitions, summaries; `evaluation/` | Every observation reaches production preprocessing before sampling. Separate machine-readable source/state/output artifacts support failure analysis. |

**Replay is production-code-equivalent, not guaranteed byte-for-byte historical live replay.** These SDK captures do not preserve the exact original observation-collection instant, network delay or newest-frame queue drops. Replay uses perception receipt time as collection time. Reconstructing all packets can retain observations that a live sender would have dropped. No claim of exact historical live frames is made.

### Available raw evidence

All nine files contain perception and locomotion envelopes. Perception packets can contain UID, `id_score`, `dist_mm`, `gaze_overlap`, a face rectangle, facial landmarks, `head_position`, gaze vectors, facial-expression outputs and `g_eye_left`, `g_eye_right`, `g_nose`, `g_gaze` vectors in named coordinate systems. These are **facial** observations. No hand landmarks, full body skeleton, explicit pass gesture, route geometry or person-path association is recorded. `g_head_position` is null for every detected person. The adapter currently consumes UID/range/gaze and a valid CAM_HEAD `g_head_position` when available; this last position is absent in these recordings.

Locomotion includes odometry time, XYZ position, quaternion-shaped orientation, all linear/angular velocity components, two lidar ranges and three sonar ranges. The raw frame retains forward/yaw velocity and ranges. Pose and other components remain in the source capture for future work. A zero sonar reading is not interpreted as a collision or a human. Range samples alone do not identify a person or the fixed route.

Head camera manifests reference 640×480 PPM frames. Chest-camera manifests report that the camera class is absent in the installed SDK. The recorded unavailable events are evidence of a missing stream, not blank images. Camera timestamps are not automatically synchronized with odometry by numerical resemblance. Images are excluded from both primary policies.

### Critical acquisition finding: unequal recording windows

The head-camera and SDK streams share capture session IDs, but **all nine camera recordings continue after their SDK perception files end**. Contiguous SDK sequence numbers cannot prove the recording contains its entire tail. The reproducible [stream coverage audit](results/policy-baseline/stream-coverage.json) records exact spans, overlap frame numbers and unavailable streams.

For example, Scenario 01 contains 9.00 s of SDK perception versus 21.50 s of head-camera receipts; Scenario 04 contains 5.40 s versus 13.28 s; Scenario 09 contains 12.09 s versus 19.39 s. In 01 only camera sequences 2–9 fall within the SDK receipt window; in 04 only 2–6; in 09 only 2–12. A visual inspection of 01 head frame 12 and 04 head frame 11 shows a person, but **these frames occur after the SDK files end**, so they cannot demonstrate a detector failure at the corresponding time. Sampled overlapping frames in 01/04 show mostly ceiling. The earlier assumption that zero detections necessarily meant a missed acted encounter is therefore unsupported.

Visual inspection of all 20 Scenario 09 head frames shows ceiling-dominated framing, with the person near the lower/right edge and hands only intermittently usefully framed. Sequence 7 lies in the SDK window and shows an extended hand/arm; interpreting its communicative meaning still needs temporal/contextual validation. A later VLM cannot be assumed to observe a clear gesture in every matched image.

The cause of the shorter SDK tails is not established by these files. The collector queues SDK and camera streams separately; shutdown drains each only until `request_timeout` and logs pending queues. An undrained SDK backlog is a plausible acquisition failure to investigate, **not a proven cause** without the run logs. Check queue warnings, saved final per-stream sequence acknowledgments and coverage before calling a trial complete. A local durable packet spool and explicit start/end markers would prevent silent acceptance of partial server recordings. No raw observations are invented to fill the gap in this evaluation.

The odometry does not fully match the scripted roaming descriptions: Scenario 05 has only about 0.00043 m net displacement and maximum forward speed 0.008 m/s; Scenario 07 has zero displacement and zero reported forward speed. Record these deviations rather than replacing measured velocities with the intended scenario narrative.

### Moving-base inference boundary

For a person-world position `p_h`, robot-world position `p_r` and unit bearing `u`, range rate is approximately `dr/dt = u · (v_h − v_r)`. Recovering human velocity requires direction and compatible coordinate/time transforms, not just scalar distance and forward speed. Camera-relative positions additionally change with head motion. The available facial vectors and odometry are promising future inputs, but this dataset lacks a verified camera-to-base/head transform, trustworthy head-pose synchronization and route geometry. The `head_position` field is not assumed to be a metric translation solely from its name.

Consequently:

- `relative_distance_trend` remains usable while the robot moves.
- `human_radial_motion` becomes UNKNOWN unless both base velocities stayed within tolerance throughout a reliable distance segment.
- `EGO_MOTION_UNCOMPENSATED` makes that limitation explicit. Robot-induced range change cannot be quantified from this reduced frame.
- Even stationary-base `STATIONARY` means stable **radial range**, not zero world velocity; a crossing person can maintain range.
- `path_relation` and `pass_gesture` are UNKNOWN in every replayed state. Their explicit fields allow future measured cues, not scenario-specific filling.

A stronger estimator would transform a verified person position into a common world/odometry frame and fit its velocity with uncertainty; predict path intersection only after providing the route footprint and prediction horizon. Trajectory-prediction work such as Social LSTM [5] operates on positional histories; a scalar-range label is not an equivalent substitute.

## C. Shared SocialState and temporal calculation

The runtime class is `SocialState`. The transport schema remains in `schemas/v1/`; the estimator identity is `temporal-social-v2`. Historical saved states carrying the old estimator identity require explicit migration/replay; do not silently mix versions. `calibration_status` remains PROVISIONAL even though an experimental software snapshot is frozen.

The comparison serializes the complete state with sorted keys, compact separators, UTF-8 and nonfinite numbers forbidden. The rule receives a detached validation of those bytes; the LLM's user message contains those same bytes. Each model attempt records their SHA-256. Neither receives scenario IDs, descriptions, expected answers, survey results, controller outcomes or RGB. Opaque source-derived IDs remain in both inputs and are documented as provenance, not semantic cues.

| Top-level fields | Meaning |
|---|---|
| `schema_version`, `estimator_version` | Transport generation and estimator implementation identity. |
| `state_id`, `session_id`, `ingest_sequence` | Snapshot/session identity and increasing frame sequence. Replay session IDs derive from source identity rather than scenario names. |
| `robot_timestamp_us` | Monotonic collection/receipt time, not wall-clock UTC. |
| `config_version`, `config` | Hash-tagged effective temporal settings; every parameter is retained. |
| `calibration_status` | PROVISIONAL: no validated universal thresholds. |
| `observation_readiness`, `readiness_reason` | Recomputed common eligibility. READY does not establish physical safety or person-detection recall. |
| `robot` | Measured velocities and motion/validity description. |
| `people` | Current and briefly retained tracks, with measurements and evidence. |
| `cue_changes` | UID, epoch, field name, previous and current category; compare within an epoch. Read the current person fields to establish current state. |
| `track_events` | Tracker lifecycle event dictionaries, UID/epoch and event-specific counters/reasons; provenance rather than intent. |
| `active_target_uid`, `active_target_track_epoch` | Null: target selection belongs to policy/controller, not the observation. Person UID/epoch remain available to both policies. |
| `range_data_status` | UNKNOWN: no collision or path interpretation is provided. |

| Per-person fields | Calculation / missing-data behavior |
|---|---|
| `uid`, `track_epoch` | SDK track identity and locally assigned incarnation; not confirmed individual identity. |
| `visibility` | OBSERVED on a current detection, otherwise TEMPORARILY_MISSING until expiry. |
| `track_age_s`, `time_since_seen_s` | Source-time age from first/last sighting in this epoch. |
| `latest_distance_m` | Latest retained range; may remain numeric on a missing track. Require `latest_distance_valid` before use. |
| `gaze_state` | NONE / INTERMITTENT / SUSTAINED / UNKNOWN under the temporal rules below. |
| `distance_zone` | TOO_CLOSE / INTERACTION_RANGE / APPROACHABLE / FAR / UNKNOWN with hysteresis. |
| `relative_distance_trend` | DECREASING / STABLE / INCREASING / UNKNOWN from reliable slope. |
| `human_radial_motion` | TOWARD / STATIONARY / AWAY mapped from that slope only with stationary-base confirmation; otherwise UNKNOWN. |
| `path_relation` | UNKNOWN / CLEAR / CONFLICT; production estimator currently only emits UNKNOWN. |
| `pass_gesture` | UNKNOWN / PASS; production estimator currently only emits UNKNOWN. |
| `relative_head_position` | Latest optional CAM_HEAD vector preserved for audit; null here. Never treated as a world position or proof of ego-motion compensation. |
| `validity_flags` | Reasons for missing/insufficient/rejected evidence and unverified stationary base, including uncompensated ego-motion. |
| `evidence` | Measurements below; numeric estimates and their validity are distinct. |

| Evidence fields | Definition |
|---|---|
| `window_span_s` | Oldest retained in-window observation to current source time. |
| `mean_gaze_overlap` | Arithmetic mean of available raw overlap samples; null if none. Diagnostic only, not gaze fraction. |
| `gaze_fraction` | Looking duration divided by valid adjacent-gaze duration; null with zero coverage. |
| `gaze_valid_coverage_s`, `gaze_coverage_fraction` | Adjacent valid duration, and that duration divided by window span. |
| `gaze_valid_samples` | Samples with known hysteretic looking state. |
| `sustained_gaze_s` | Trailing uninterrupted looking duration; zero when missing or after a non-looking/gapped interval. |
| `gaze_valid` | Current observed gaze plus all sample, span and coverage requirements. |
| `distance_slope_mps` | Median of all pairwise slopes in a bounded subset of the newest contiguous distance segment. |
| `distance_fit_residual_m` | RMS residual about median-intercept robust line; slope/residual null without sufficient evidence. |
| `distance_valid_span_s`, `distance_valid_samples` | Span and sample count in the newest valid contiguous segment. |
| `distance_fit_samples` | Number used in fit, capped by configuration. |
| `distance_window_start_us` | First segment sample timestamp, or null without a segment. |
| `distance_jump_count` | Number of implausible jump boundaries in the current window. |
| `latest_distance_valid` | Current observed, nonnull, nonrejected distance. |
| `distance_trend_valid` | Valid latest distance, sufficient fit support and acceptable residual. |
| `stationary_window_confirmed` | Both base velocities available within tolerance at every distance-segment sample; by itself does not establish a valid trend. |

`robot.linear_velocity` is signed forward m/s, `angular_velocity` signed yaw rad/s. `motion_state` is MOVING when either exceeds tolerance; STATIONARY when both are known and within tolerance; otherwise UNKNOWN. `measurement_validity` records incomplete velocity measurements. Rotation is not assigned a clockwise convention by this layer.

### Temporal thresholds and missingness

The baseline retains `config/temporal-state.json`: a 2 s window, at least 1 s span and five samples, 0.8 s valid gaze coverage and 60% coverage. Adjacent observations must also have consecutive source frame sequences and a gap ≤0.25 s. Missing frames cannot be repaired merely by widening a time gap.

A sample enters looking at overlap ≥0.8 and exits at ≤0.7. Values between inherit the previous known state; after a gap or null there may be no such state. For each valid adjacent pair, the left sample's looking state contributes its interval duration. Continuous run length requires both endpoints looking. Category entry requires fraction ≥0.8 for SUSTAINED and ≤0.2 for NONE; exit bands are 0.65 and 0.35 respectively. Other sufficiently supported patterns are INTERMITTENT. SUSTAINED additionally requires a trailing 0.4 s continuous run. Candidate categories must persist for 0.3 s. Invalid current gaze returns UNKNOWN immediately. Thus a recently interrupted otherwise high-attention window can legitimately become INTERMITTENT: this is a temporal category, not a literal annotation of an entire scripted clip.

Range fits break at nulls, missing sequences, excessive gaps or jumps larger than `3 m/s × dt + 0.05 m`. The newest segment needs five samples and 1 s span. At most 32 evenly spaced samples are fitted. RMS residual must be ≤0.1 m; absolute slope ≤0.1 m/s is STABLE. Base tolerances are 0.02 m/s forward and 0.03 rad/s yaw. Do not interpret a failed fit as stationary motion.

Distance boundaries are 0.6, 1.5 and 3.0 m, with 0.1 m hysteresis. Initial exact boundaries enter the upper zone; subsequent transitions require passing the boundary plus/minus hysteresis. These are engineering defaults, not universal proxemic laws. Proxemics research links useful spacing to interaction and perceptual constraints [4]; it does not establish these values for Navel.

Tracking retains 3 s history, missing grace 0.75 s, 64 samples per track and at most 32 tracks. The temporal window must fit inside tracking history. Longer grace preserves identity bookkeeping, but does not invent observation continuity or gaze during missing periods.

### Calibration performed

`evaluation.calibrate` compares the baseline with (a) 0.5 s minimum span, 0.4 s coverage and 0.2 s dwell; (b) maximum gap widened to 0.5 s. Results are in `results/policy-baseline/sensitivity.json`.

The shorter-evidence setting raises Scenario 03 ENGAGE frames from 75 to 83 and Scenario 09 APPROACH frames from 20 to 27. It does not produce ready gaze in 02, 06 or 08. Widening the gap changes no decisions because missing source observations still break adjacency. This does not establish better social accuracy. **Keep the conservative baseline settings; repair perception and obtain frame-level cue annotations before lowering thresholds.** All observed gaze scores in Scenario 06's brief detection are already above 0.92, so simply lowering an overlap threshold would not solve the temporal problem.

## D. Canonical actions and readiness

`app/domain/actions.py` is the single experimental action definition used by model parsing and rules; LLM/VLM instructions share its definitions.

| Action | Social meaning | Controller responsibility |
|---|---|---|
| CONTINUE | Deliberately keep following the guide route; insufficient reason to interact/intervene. | Maintain/resume route subject to independent safety. |
| YIELD | Give a person priority for a measured likely path conflict. | Slow/stop, allow passage, then resume; route-specific implementation remains external. |
| APPROACH | Interaction appears warranted but conversational distance has not been reached. | Orient, plan a safe approach, stop at configured distance. |
| ENGAGE | Initiate interaction at suitable current distance. | Orient, stop, greet once, manage conversation/cooldown. |

**NOT_READY** has null action and is excluded from action distributions/agreement. It means insufficient or ambiguous observation evidence. **ERROR** has null action and preserves failed inference diagnostics. Neither is CONTINUE. Legacy controller `PolicyDecision.decision = DEFER` remains for wire compatibility; its `.action` is null and `.status` is NOT_READY. The experimental evaluator never counts DEFER as a fifth action. `ORIENT` and physical safety `STOP` are controller behaviors. Historical model STOP is deliberately rejected rather than silently reinterpreted as YIELD, since their meanings differ.

Common eligibility: measured path conflict is immediately actionable; no current or retained tracks permits route CONTINUE; temporarily missing retained tracks defer; multiple current people defer unless conflict is known; unknown/invalid distance, TOO_CLOSE, or unready gaze defer. TOO_CLOSE requires controller-level accommodation, not a fabricated social path-conflict label. No detections can yield READY for route classification while perception has actually missed a human: this is separately reported as sensor coverage, not social success.

## E. Transparent rule baseline

Evaluate the following rows in order. Stale/failed processing defers before the table. All cues must be current/valid as enforced by common eligibility.

| Condition | Outcome | Rationale |
|---|---|---|
| Any observed measured CONFLICT | YIELD | Courtesy priority precedes initiating interaction. |
| No observed or retained person | CONTINUE | No observed social reason to interrupt the assigned route. |
| Common readiness fails | NOT_READY | Do not turn unknown evidence into a positive or negative social assertion. |
| Reliable represented PASS gesture | CONTINUE | Explicit invitation to pass outweighs attention. This is a future measured cue, absent here. |
| NONE or INTERMITTENT gaze | CONTINUE | Incidental attention alone does not justify diverting a guide robot. |
| Reliable human radial AWAY with stationary base | CONTINUE | Avoid chasing a retreating person. |
| FAR | CONTINUE | Sustained looking beyond approach range alone does not justify leaving the route. |
| Reliable INCREASING relative separation | CONTINUE | Conservative non-pursuit, without claiming the human caused the separation. |
| SUSTAINED + INTERACTION_RANGE | ENGAGE | Sufficient temporal attention at conversational distance. |
| SUSTAINED + APPROACHABLE | APPROACH | Same attention evidence but further movement would be needed. |

Unknown absolute human motion is no longer an unconditional block during roaming. This admits possible false invitations: stable radial range is not stationary body motion and gaze is not consent. UNKNOWN path relation is not CLEAR; independent navigation safety must still govern execution. The non-pursuit rule can miss a stationary person when the robot itself increases separation. These are declared baseline choices for human evaluation, not established optimal behavior.

The complete **840-row category matrix**, generated by `evaluation.policy_matrix`, is `results/policy-baseline/policy-matrix.csv`. It crosses four gaze categories, five zones, three path categories, two gesture categories and seven consistent motion/trend descriptions. Unavailable-cue combinations, missing people, multiple people and stale inputs are additionally tested. CLEAR/CONFLICT/PASS rows are interface/synthetic coverage; the recordings cannot validate their perception.

## F. LLM comparison

The installed local model is Qwen2.5 7B Instruct, Ollama tag `qwen2.5:7b`, quantization Q4_K_M. The exact model digest, Ollama version, model options, prompt text/hash, code hashes, source hashes and config values are saved in the run manifest. Qwen's technical report describes the model family [8]; it does not validate social-navigation judgments in this experiment.

The primary run uses temperature 0, seed 42, an explicit 8192-token context window, at most 192 generated tokens and a 120 s request timeout, with three identical requests per eligible sampled state. Every source frame is preprocessed; model comparisons are sampled at the first frame and then the first frame ≥1 s after the previous sample. No chat history accumulates across decisions. Repetition measures stability under this configuration, not a distribution over temperatures or models. Determinism is not promised across hardware/runtime versions.

The frozen prompt is `social-state-llm-v4`; the versioned settings recipe is `config/policy-comparison.json`. The prompt supplies fixed-route context, canonical actions, field meanings and missing-information cautions, followed by the complete shared state. It does not give the rule table, scenario description or expected answer. Gaze remains measured attention, not a proven request. Target selection is not a second quantitative model task: the shared gate admits one observed candidate except for path-conflict decisions.

Output is exactly `{ "action": ..., "reason": ... }`. The reason is qualitative and is not a faithful trace of internal reasoning or a correctness guarantee. No uncalibrated confidence number is manufactured. Strict local validation rejects unknown actions, extra fields, duplicate JSON keys, nonfinite values, whitespace-only reasons, code fences and surrounding prose. Ollama's schema support does not replace local validation. There are no retries or substituted CONTINUE/rule fallback outputs. Errors and response latency remain in the evidence.

Grounded robotics systems such as SayCan illustrate separating language proposals from affordances/execution [7]. This implementation borrows that architectural caution, not SayCan's method or demonstrated performance.

### Prompt calibration, without hidden relabeling

A completed v2 development run and its responses are retained under `results/policy-baseline/prompt-v2/`. It achieved 100% structured-output success but only 12.5% rule agreement in Scenario 03. Its explanations sometimes denied the sustained gaze or interaction-range values present in the input. A category clarification probe improved factual citation but still treated unknown path/gesture as a reason to reject interaction. A shorter service-role prompt (v3) recognized interaction opportunities but used APPROACH as a synonym for starting interaction even at conversation distance. Its probe and interrupted development-run files are calibration artifacts, not additional completed nine-scenario evaluations.

The final v4 wording explicitly distinguishes movement to conversational distance from engaging at an already suitable distance. This clarifies the common action semantics; it does not supply scenario numbers, survey answers or the full rule decision table. All raw model actions remain untouched: no postprocessor changes APPROACH into ENGAGE. This tuning is another reason these recordings cannot count as held-out validation. Model-request latency from calibration probes sharing a server is not a deployment benchmark.

## G. VLM boundary

The existing VLM is an **image-only** baseline. Its output actions now use the same canonical definitions; its one-image input still cannot establish sustained gaze or motion. Camera matching, checksum checks and PPM-to-PNG encoding are retained. No VLM results are claimed in this report.

The next comparison should explicitly add an optional shared-state-plus-RGB condition, using frozen timestamp association and both modalities in the audit. Give it the same decision timing and action definitions, but acknowledge its extra information. RT-2 demonstrates transfer of visual-language knowledge to robotic control in other tasks [9]; that is motivation for testing additional visual context, not evidence that this system understands a pass gesture. A temporal visual model or independently validated pose/gesture extractor may be necessary.

## H. Nine recorded scenarios and failure decomposition

The descriptions below are the experimenter's intended situations. They are not policy inputs or human-consensus labels. No completed human-survey results were found in the repository.

| Scenario | Intended situation | Recorded structured evidence and earliest limitation |
|---|---|---|
| 01 | Moving robot; passer-by without gaze | 0/91 person frames. Acquisition/visibility limitation; a CONTINUE output cannot demonstrate correct recognition of a passer-by. |
| 02 | Person beside path, unobstructing, continuous gaze | 27/84 person frames, 8 track epochs; maximum contiguous distance span 0.896 s. Gaze UNKNOWN on every detected instance despite high overlap. Perception/temporal limitation dominates. |
| 03 | Person beside route, within approach distance, continuous gaze | 110/115 person frames (111 person instances), mostly INTERACTION_RANGE rather than the intended broad 'approachable' description. Supports ENGAGE under measured range. Occasional gaps/extra track produce not-ready periods and temporal category changes. |
| 04 | Human enters robot path without gaze | 0/55 person frames; no structured route conflict. Acquisition/visibility and representation limitation; no policy can justify measured YIELD from these inputs. This is the most consequential coverage gap. |
| 05 | Human walks away without gaze | 0/40 person frames; robot effectively stationary by recorded odometry. Acquisition/visibility limitation and mismatch between intended movement context and recorded realization. |
| 06 | Passer-by with brief glance | 4/77 person frames, 3 track epochs, maximum valid gaze coverage 0.127 s. Temporal abstention is appropriate for this evidence; the remainder is no-detection CONTINUE, not recognition of a glance. |
| 07 | Person off path, intermittent gaze | 0/29 person frames; robot stationary in odometry. Intermittent gaze cannot be estimated at all. Acquisition/visibility/context mismatch. |
| 08 | Human walks away while continuously looking | 32/117 person frames, 8 track epochs, maximum contiguous range support 0.213 s. UNKNOWN gaze/motion dominates; recorded evidence cannot distinguish human retreat reliably. Perception/temporal ambiguity. |
| 09 | Person beside path, continuous gaze and gesture to pass | 56/122 person frames, 1 epoch, enough sustained gaze to trigger approach; gesture absent from representation. Information limitation, not a permissible scenario-specific exception. |

In Scenario 09, a non-VLM hand-keypoint classifier is a possible future perception component, but none is implemented or validated here and the stream contains only facial landmarks. It would need temporal hand/body tracking and gesture validation under ordinary operation. Neither facial expression nor high gaze overlap is a defensible proxy for 'please pass'. If two situations yield the same state, deterministic rules necessarily give the same answer; a text model also has no evidential basis for distinguishing them. VLM benefit is a hypothesis requiring visible, temporally adequate gesture evidence.

Measured per-scenario policy distributions, last-valid actions, ending status, timing, disagreement and repetitions are in [the generated results](results/policy-baseline/results.md), [JSON](results/policy-baseline/summary.json) and [CSV](results/policy-baseline/summary.csv). Detailed transitions and every model response are retained in the local evaluation directory; compact audit artifacts are versionable without copying images.

## I. Baseline results and metric definitions

The generated table is the authoritative measurement record; no desired action is used to score the recordings.

- Rule action counts use all 730 perception frames. LLM distributions use selected eligible states and three repetitions. Compare policies only on matched states, not raw marginal counts with different denominators.
- NOT_READY rates count shared evidence ineligibility, distinct from schema-invalid input or inference errors. No-person READY frames remain visible in detection coverage statistics.
- Action agreement excludes failed/unready model outputs. Detected-person agreement is additionally reported to expose inflation from empty observations.
- Repeat consistency is the modal fraction among successful repeated actions for each state, averaged across states with at least two successful outputs. Structured success uses attempted calls; shared-gate skips are not calls. Error categories are preserved separately.
- First-decision delay is source time from the first current valid-range person detection to the first nonnull action at/after it; it may be CONTINUE after a person disappears. First-interaction delay is separately reported for APPROACH/ENGAGE. No detection yields null delay. Model delay reflects sampling plus source evidence, **not execution completion**; wall-clock request latency is a separate field.
- Last-valid action and ending status are separate. Neither a final empty frame nor a majority of many empty frames is an episode-level ground-truth judgment.

Human reference files map each scenario to `preferred` and `least_appropriate` probability dictionaries over the four actions. Values must be finite, nonnegative and sum to one. Unavailable surveys remain null. With supplied distributions the summarizer calculates expected preferred/least-appropriate support and final-rule support; these are not binary accuracy or an assumed consensus. Scenario-level survey judgments and frame-level decisions have different units: predefine an episode decision point before making final human-comparison claims.

<!-- measured-results:start -->
Measured run: `var/evaluation/baseline-v4`, prompt `social-state-llm-v4`. 730 frames; 73 sampled states; 162 model calls, 162 valid structured outputs. Matched action agreement 98.1% (159/162); observed-person agreement 90.0% (27/30). Mean model request latency 2.083 s.

| Scenario | Detected / frames | Rule counts | LLM counts | Agreement | Not ready |
|---|---:|---|---|---:|---:|
| scenario-01 | 0/91 | {'CONTINUE': 91} | {'CONTINUE': 27} | 100.0% | 0.0% |
| scenario-02 | 27/84 | {'CONTINUE': 28, 'NOT_READY': 56} | {'CONTINUE': 9} | 100.0% | 66.7% |
| scenario-03 | 110/115 | {'NOT_READY': 34, 'ENGAGE': 75, 'CONTINUE': 6} | {'ENGAGE': 18, 'CONTINUE': 3, 'APPROACH': 3} | 87.5% | 29.6% |
| scenario-04 | 0/55 | {'CONTINUE': 55} | {'CONTINUE': 18} | 100.0% | 0.0% |
| scenario-05 | 0/40 | {'CONTINUE': 40} | {'CONTINUE': 12} | 100.0% | 0.0% |
| scenario-06 | 4/77 | {'CONTINUE': 69, 'NOT_READY': 8} | {'CONTINUE': 21} | 100.0% | 10.4% |
| scenario-07 | 0/29 | {'CONTINUE': 29} | {'CONTINUE': 9} | 100.0% | 0.0% |
| scenario-08 | 32/117 | {'CONTINUE': 72, 'NOT_READY': 45} | {'CONTINUE': 21} | 100.0% | 38.5% |
| scenario-09 | 56/122 | {'NOT_READY': 45, 'APPROACH': 20, 'CONTINUE': 57} | {'APPROACH': 6, 'CONTINUE': 15} | 100.0% | 36.9% |

| Scenario | Rule first decision / interaction delay (s) | LLM first decision / interaction delay (s) | Last valid rule / LLM actions |
|---|---|---|---|
| scenario-01 | — / — | — / — | CONTINUE / {'1': 'CONTINUE', '2': 'CONTINUE', '3': 'CONTINUE'} |
| scenario-02 | 1.428 / — | 4.411 / — | CONTINUE / {'1': 'CONTINUE', '2': 'CONTINUE', '3': 'CONTINUE'} |
| scenario-03 | 1.401 / 1.401 | 2.096 / 2.096 | ENGAGE / {'1': 'APPROACH', '2': 'APPROACH', '3': 'APPROACH'} |
| scenario-04 | — / — | — / — | CONTINUE / {'1': 'CONTINUE', '2': 'CONTINUE', '3': 'CONTINUE'} |
| scenario-05 | — / — | — / — | CONTINUE / {'1': 'CONTINUE', '2': 'CONTINUE', '3': 'CONTINUE'} |
| scenario-06 | — / — | — / — | CONTINUE / {'1': 'CONTINUE', '2': 'CONTINUE', '3': 'CONTINUE'} |
| scenario-07 | — / — | — / — | CONTINUE / {'1': 'CONTINUE', '2': 'CONTINUE', '3': 'CONTINUE'} |
| scenario-08 | — / — | — / — | CONTINUE / {'1': 'CONTINUE', '2': 'CONTINUE', '3': 'CONTINUE'} |
| scenario-09 | 1.509 / 1.509 | 2.200 / 2.200 | CONTINUE / {'1': 'CONTINUE', '2': 'CONTINUE', '3': 'CONTINUE'} |
<!-- measured-results:end -->

### Observed policy differences and timing

In the final v4 run, 159/162 successful paired model calls agree with the rule (98.1%). Only 30 calls involve a currently observed person, with 27/30 agreement (90%). All 54 eligible sampled states have identical actions across their three fixed-seed, temperature-zero repetitions. This establishes repeatability for this run, not stochastic robustness. The 19 ineligible sampled states produce 57 logged NOT_READY outcomes without model calls. All 162 attempted calls returned valid structured output; format success is distinct from social or semantic correctness.

The sole differing sampled state is Scenario 03, source state `replay-8f9030f65696dfa3909274bb:106`. The rule selects ENGAGE. All three model outputs select APPROACH while the current state says SUSTAINED and INTERACTION_RANGE at approximately 0.63 m. The explanation claims the person is outside conversational range. This is a **model action-semantics/evidence-use failure**, not support for reducing the distance threshold. The raw response is retained; no rule-based correction is applied to model output.

Scenario 03 otherwise yields six sampled ENGAGE states and one CONTINUE state in both policies. The first rule interaction occurs 1.401 s after first valid-range detection; the sampled model first interacts at 2.096 s of source time. Scenario 09 yields two sampled APPROACH states in both policies, with rule interaction at 1.509 s and sampled-model interaction at 2.200 s. These times omit wall-clock inference completion and controller execution. Repeated greetings are not implied by repeated ENGAGE classifications.

For 01/04/05/07 the model only classifies no-person snapshots. In 02, 06 and 08, every currently detected person remains gaze-UNKNOWN; successful model calls occur on no-person states. Model CONTINUE in these files therefore does not show that it correctly understood continuous gaze, brief glancing or retreat. In 09, agreement on APPROACH reveals the shared information boundary: the policies cannot see the intended pass gesture. Participant preference distributions remain unavailable for every scenario.

The rule's all-frame distribution is 447 CONTINUE, 75 ENGAGE, 20 APPROACH and 188 NOT_READY. No measured path conflict is present, so no rule YIELD is exercised by the recordings. Its YIELD branch is verified with synthetic states only. Avoid claiming that the four-action problem has been fully evaluated on these files.

## J. Research interpretation

The useful contribution of this baseline is locating failure layers. A transparent rule can explain its action and evidence conditions exactly, but cannot infer an unobserved path conflict or gesture. An LLM can integrate supplied descriptions differently; it can also contradict explicit measurements or treat uncertainty inconsistently. Agreement between policies on a no-person state does not demonstrate socially competent behavior.

Temporal processing prevents a high single-frame gaze value from immediately initiating an interaction, at the cost of delayed or absent decisions when tracking fragments. This tradeoff requires independent cue annotations and person-detection coverage, not only action judgments. Differences in detected range zones can explain apparently unexpected ENGAGE/APPROACH choices before either policy is blamed.

Only classify a rule or model choice as a policy failure when the relevant state is supported. Otherwise report perception, representation or temporal failure first. An action disagreement may remain a reasonable difference under ambiguous social intent. Human distributions will help describe that ambiguity, rather than erase it.

## K. Methodological limitations

Nine short scripted laboratory recordings, potentially sharing participants, route and session conditions, are not independent generalization trials. Several contain no usable person observations. The scripts themselves are not frame annotations, and odometry shows that some intended roaming scenes were recorded stationary. Policies run open-loop: their actions did not change the recorded future, so later decisions after a hypothetical engagement are counterfactual, not a closed-loop behavioral result.

The same recordings informed diagnosis, rule changes and prompt calibration. They are therefore development data. There are no held-out trials yet, no verified body/gesture perception, no calibrated ego-motion compensation, no route-conflict observation, and no measured physical YIELD behavior. Safety, route resumption and controller timing are not validated by offline action agreement. Model results concern one quantized installed model and runtime configuration. Repeating a fixed seed at temperature zero does not quantify broader stochastic variation, model uncertainty or population-level significance.

Human ratings are preferences shaped by wording, camera viewpoint, culture and context; they are not an objective oracle. Most/least distributions, disagreement between participants, and questionnaire validation should be retained. Social-navigation evaluation guidance explicitly combines contextual/social criteria and repeatable measurement [6]; this report does not claim statistical significance from nine recordings.

## L. Frozen baseline and final experiment

Freeze the source versions/hashes, preprocessing configuration, canonical actions, rule table, exact prompt, model digest/runtime, sampling protocol and image association before collecting final trials. Keep the development recordings and calibration artifacts labeled as such.

Before held-out repetitions, verify complete stream end markers and successful queue draining; replay the full acted interval while still in the laboratory. For held-out repetitions, vary participants, route direction, ranges, glance durations, lighting and clothing while recording both robot and human motion. Capture raw SDK packets, exact transmitted observations, synchronized camera/head pose, route geometry, controller events and an external annotated view. Verify detection coverage without requiring the person to face the head camera. Annotate onset/offset of gaze, human movement, visibility, hand gestures and path crossings independently of policy outputs.

Use a prespecified decision point/window per encounter. Compare rule and LLM on matched structured states; compare state-plus-RGB VLM separately as an information augmentation. Report human preferred/least-action support, episode-level action distributions, readiness/abstention and parse failure rates, first-decision/interaction latency, action transitions, and repeated-call consistency. For physical trials additionally report minimum separation, route completion, yielding/resumption success, unwanted approaches/greetings and participant comfort. Treat episodes/participants as analysis units; frame counts are correlated measurements, not hundreds of independent trials. Use confidence intervals only with enough independent repetitions and an appropriate analysis plan.

The immediate priority is **complete synchronized acquisition, camera framing, person detection/track continuity and path information**, followed by verified motion transforms and gesture sensing. Threshold changes alone cannot recover the missing people in 01/04/05/07 or the missing pass gesture in 09.

## Reproduction

From the repository root, with the declared requirements installed:

```bash
.venv/bin/python -m evaluation.run_scenarios --output var/evaluation/rules-new
.venv/bin/python -m evaluation.run_scenarios --model qwen2.5:7b --repeats 3 --output var/evaluation/comparison-new
.venv/bin/python -m evaluation.summarize var/evaluation/comparison-new
.venv/bin/python -m evaluation.publish var/evaluation/comparison-new
.venv/bin/python -m evaluation.audit_recordings --output docs/results/policy-baseline/stream-coverage.json
.venv/bin/python -m evaluation.policy_matrix --output docs/results/policy-baseline/policy-matrix.csv
.venv/bin/python -m evaluation.calibrate --output docs/results/policy-baseline/sensitivity.json
.venv/bin/python -m unittest discover -s tests
```

The model must already be installed and Ollama running locally. The runner never downloads a model or falls back to a different one. Output directories must be new to prevent overwriting evidence. `--human-reference path.json`, `--temporal-config`, `--track-config`, `--sample-interval-s`, `--temperature` and `--seed` are explicit experiment controls. `manifest.json` preserves inference-time source/config/model identities; `publication.json` additionally records the final analysis source hashes and full-trace hashes. Selected exact inputs and every response are published, so later diagnostic/report changes do not erase the actual inference evidence. No commit was made automatically.

Full generated traces are ignored under `var/evaluation/`; compact summaries/manifests belong under `docs/results/`.

## Validation

The complete unittest suite passes **524 tests**, including strict output/schema validation, adapter/replay ordering, moving-base non-attribution, missingness and hysteresis, controller correlation, the full rule matrix, recorded detection coverage, shared-input hashes, repeated-call handling, error preservation, source-window overlap and report regeneration. A recorded Scenario 03 replay also matches the full production `SocialPipeline` when fed the same reconstructed raw frames and session identity. This proves code-path parity for reconstructed input, not historical network/queue equivalence. Physical movement and SDK collection on the actual robot were not exercised at home.

## References

[1] Thibault Kruse, Amit Kumar Pandey, Rachid Alami and Alexandra Kirsch. *Human-aware robot navigation: A survey.* Robotics and Autonomous Systems 61(12), 1726–1743, 2013. [DOI 10.1016/j.robot.2013.05.007](https://doi.org/10.1016/j.robot.2013.05.007).

[2] Marek P. Michalowski, Selma Sabanovic and Reid Simmons. *A Spatial Model of Engagement for a Social Robot.* 9th IEEE International Workshop on Advanced Motion Control, 762–767, 2006. [Author-hosted paper](https://homes.luddy.indiana.edu/selmas/MichalowskiSabanovic-AMC2006.pdf). DOI 10.1109/AMC.2006.1631755. Existing project reference, inspected for this report.

[3] Séverin Lemaignan, Fernando Garcia, Alexis Jacq and Pierre Dillenbourg. *From Real-time Attention Assessment to “With-me-ness” in Human-Robot Interaction.* ACM/IEEE International Conference on Human-Robot Interaction, 2016. [DOI 10.1109/HRI.2016.7451747](https://doi.org/10.1109/HRI.2016.7451747), [author-hosted paper](https://academia.skadge.org/publis/lemaignan2016realtime.pdf). Existing project reference, verified against the publication.

[4] Ross Mead and Maja J. Matarić. *Autonomous human–robot proxemics: socially aware navigation based on interaction potential.* Autonomous Robots 41, 1189–1201, 2017 (online 2016). [DOI 10.1007/s10514-016-9572-2](https://doi.org/10.1007/s10514-016-9572-2), [authors' publication record](https://robotics.usc.edu/publications/929/).

[5] Alexandre Alahi, Kratarth Goel, Vignesh Ramanathan, Alexandre Robicquet, Li Fei-Fei and Silvio Savarese. *Social LSTM: Human Trajectory Prediction in Crowded Spaces.* IEEE CVPR, 961–971, 2016. [Official open-access proceedings](https://www.cv-foundation.org/openaccess/content_cvpr_2016/html/Alahi_Social_LSTM_Human_CVPR_2016_paper.html).

[6] Anthony Francis, Claudia Pérez-D'Arpino, Chengshu Li, Fei Xia, Alexandre Alahi, Rachid Alami, Aniket Bera, Abhijat Biswas, Joydeep Biswas, Rohan Chandra, Hao-Tien Lewis Chiang, Michael Everett, Sehoon Ha, Justin Hart, Jonathan P. How, Haresh Karnan, Tsang-Wei Edward Lee, Luis J. Manso, Reuth Mirsky, Sören Pirk, Phani Teja Singamaneni, Peter Stone, Ada V. Taylor, Peter Trautman, Nathan Tsoi, Marynel Vázquez, Xuesu Xiao, Peng Xu, Naoki Yokoyama, Alexander Toshev and Roberto Martín-Martín. *Principles and Guidelines for Evaluating Social Robot Navigation Algorithms.* ACM Transactions on Human-Robot Interaction, published online December 2024. [DOI 10.1145/3700599](https://doi.org/10.1145/3700599), [MIT published-version record](https://hdl.handle.net/1721.1/158075).

[7] Brian Ichter, Anthony Brohan, Yevgen Chebotar et al. *Do As I Can, Not As I Say: Grounding Language in Robotic Affordances.* 6th Conference on Robot Learning, PMLR 205:287–318, 2023. [Official proceedings and full author list](https://proceedings.mlr.press/v205/ichter23a.html). The linked proceedings resolve the full collaboration authorship.

[8] Qwen team (An Yang et al.). *Qwen2.5 Technical Report.* arXiv technical report, 2024; not a peer-reviewed validation of this application. [arXiv:2412.15115 and complete author list](https://arxiv.org/abs/2412.15115).

[9] Brianna Zitkovich et al. *RT-2: Vision-Language-Action Models Transfer Web Knowledge to Robotic Control.* Conference on Robot Learning, PMLR 229, 2165–2183, 2023. [Official proceedings and full author list](https://proceedings.mlr.press/v229/zitkovich23a.html).
