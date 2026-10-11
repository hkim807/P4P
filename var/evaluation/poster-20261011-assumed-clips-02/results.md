# Poster evaluation results

Candidate: social-rules-v7, social-state-llm-v14, qwen2.5:7b. The comparison JSON still declares v5/v10; effective temporal defaults and code hashes are frozen in manifest.json. No production tuning was performed.

## Source selection and alignment

The user explicitly replaced the original nine captures with final-scenarios-audit/trimmed as assumed survey clips. Their last perception is the assumed endpoint. These are different capture sessions from scenario-01..09; motion-onset trims are verified against final_scenario originals by packet/session/timestamp equality. The original nine remain a separately labelled source audit. No camera/video file establishes exposure timing or survey-video end: **0/9 independently verified, 9/9 assumed endpoints**. Timing uncertainty is unknown, so numerical alignment sensitivity cannot be bounded.

## RQ1 — representation and evidence availability

The adapter, UID tracker and temporal estimator produce shared state with visibility, gaze history/current gaze, distance zone and robot-relative trend, evidence validity, descriptive labels, robot motion and readiness. Canonical state bytes match across endpoint policies. Retained missing tracks are distinct from observed people. In this configuration, sustained no-person CONTINUE is disabled by its effective null default.

User-selected clips contain **574 perception frames**, with detections in **429/574** and readiness in **66/574** (11.5%, frame-weighted). At least one ready state occurs in **5/9** clips. These frames and repeated calls are not independent encounters.

| Scenario | Frames | Detected frames | Ready frames | First ready (s) | Endpoint hold |
|---|---:|---:|---:|---:|---|
| S1 | 105 | 67 | 0 | unavailable | NO_VISIBLE_PERSON |
| S2 | 48 | 48 | 25 | 2.271054 | READY |
| S3 | 47 | 47 | 24 | 2.297567 | READY |
| S4 | 62 | 61 | 0 | unavailable | NO_VISIBLE_PERSON |
| S5 | 69 | 0 | 0 | unavailable | NO_VISIBLE_PERSON |
| S6 | 47 | 47 | 7 | 2.268696 | INSUFFICIENT_GAZE_EVIDENCE |
| S7 | 89 | 79 | 0 | unavailable | INSUFFICIENT_GAZE_EVIDENCE |
| S8 | 72 | 45 | 1 | 7.078454 | READY |
| S9 | 35 | 35 | 9 | 2.270286 | READY |

Separately, the originally requested scenario-01..09 captures contain 730 frames, 229 detected-person frames and 102 ready frames across 2/9 captures. Their camera/SDK receipt-tail offsets and source audit are retained; their proxy decisions are never human-scored.

These are availability diagnostics, not perception accuracy. UID gaps/epochs do not independently establish identity errors. Conflict and pass-gesture measurements remain UNKNOWN throughout these inputs; corridor position is not collision prediction or verified clearance. Relative separation cannot identify human motion. Missing detection does not prove an empty route.

## RQ2 — endpoint policies and human reference

At the assumed endpoints, **4/9** states are ready; rules return **4/9** valid outputs. The LLM returns **20/20** valid requested outputs, with 0 failures. Five fixed-seed calls per eligible state describe repeatability, not five encounters. See endpoint_decisions.csv for every repeat and duration.

| Scenario | Rules | LLM valid-repeat distribution | Valid / failed / ineligible LLM repeats |
|---|---|---|---|
| S1 | unavailable | {} | 0 / 0 / 5 |
| S2 | APPROACH | {"CONTINUE": 5} | 5 / 0 / 0 |
| S3 | ENGAGE | {"ENGAGE": 5} | 5 / 0 / 0 |
| S4 | unavailable | {} | 0 / 0 / 5 |
| S5 | unavailable | {} | 0 / 0 / 5 |
| S6 | unavailable | {} | 0 / 0 / 5 |
| S7 | unavailable | {} | 0 / 0 / 5 |
| S8 | CONTINUE | {"CONTINUE": 5} | 5 / 0 / 0 |
| S9 | CONTINUE | {"APPROACH": 5} | 5 / 0 / 0 |

The authoritative workbook is unavailable in this environment. Its claimed 42 respondents, hash and preparation counts have not been independently reproduced. The historical 37-response reference is deliberately unused. Human preference bars, appropriateness, modal agreement and paired LLM-minus-rule human differences are unavailable. Blank CSV fields mean unavailable, never zero. **Direct RQ2 comparison is blocked**; the assumed endpoint outputs cannot by themselves establish alignment or LLM superiority.

## Operational first-output replay

| Repeat | Policy | Outputs / 9 | Accepted / 9 | Rejections |
|---|---|---:|---:|---|
| 1 | rules | 5 | 5 | {} |
| 1 | llm | 5 | 0 | {"LATEST_OBSERVATION_STALE": 4, "LATEST_OBSERVATION_STALE|CURRENT_STATE_NOT_READY": 1} |
| 2 | rules | 5 | 5 | {} |
| 2 | llm | 5 | 0 | {"LATEST_OBSERVATION_STALE": 4, "LATEST_OBSERVATION_STALE|CURRENT_STATE_NOT_READY": 1} |
| 3 | rules | 5 | 5 | {} |
| 3 | llm | 5 | 0 | {"LATEST_OBSERVATION_STALE": 4, "LATEST_OBSERVATION_STALE|CURRENT_STATE_NOT_READY": 1} |

Across 15 operational model requests from repeated scenarios, median request duration was 8.689 s (range 8.318–9.527 s). These are request timings, not independent encounter outcomes.

First eligible states match in 15 pairs; 12 pairs have no eligible state for both policies. No mismatched pair was observed. All 54 scenario/policy/repetition rows are present. Failed/no-output scenarios remain in every nine-scenario denominator.

Warmup responses and elapsed times are stored separately. Inference remains asynchronous while newer observations update client readiness. Model request duration is the actual measured request time; encounter elapsed output time includes evidence accumulation. Rule receiver-request time includes pipeline processing and is not isolated rule inference. Source age at client delivery is measured; exact model-completion age is unavailable because audit UTC and reconstructed source clocks are independent. Request duration provides a lower bound. Pending/late audits, delivery errors and acceptance evidence are retained in per-run result/events/models files.

## Limits and remaining prerequisites

This is reuse of development captures, including prior policy/prompt tuning, with a user-assumed survey mapping. The blocked sandbox attempt is preserved in sibling poster-20261011-assumed-clips-01 and excluded. This run uses local Ollama with measured timing on this host; results may vary with load. Replay reproduces saved observations, not original collection/network delays, perception correctness or physical motion.

Needed: the actual 42-response workbook (sheet Form responses 1); video assets plus trim/exposure-clock evidence for independent synchronisation if a verified RQ2 claim is desired; new live encounters and physical measurements for claims about comfort, safety or navigation. No robot hardware was contacted and no physical action was executed.

The runner completed all measurements, then exited with an integrity-check error because the workbook-only reporting helper was improved during replay. That helper was not imported or used by the execution runner. The original manifest remains unchanged; execution-completion.json records the before/after helper hashes. All recording, policy, reconstruction, configuration and execution-runner hashes still match. Reporting verification checks every completed row and paired state hash.
