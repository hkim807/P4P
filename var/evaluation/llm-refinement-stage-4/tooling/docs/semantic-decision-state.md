# Shared measurement descriptions — stage 4

The SocialState-to-DecisionState projection now produces short English
descriptions alongside the unchanged categories and exact numeric evidence.
Both the rule policy and the LLM receive this same DecisionState v2. The rules
continue to use the original numeric/category conditions; descriptive text does
not determine rule actions.

The full SocialState, estimator, thresholds, readiness checks and v7 rules are
unchanged. Its original `observation_labels` v1 remain for diagnostic/replay
compatibility. DecisionState replaces that label block with descriptions v2,
rather than including both label blocks. It also adds
`robot.motion_description`. The current schema is exported at
`schemas/v1/decision-state.schema.json`; the enclosing schema directory still
belongs to API v1, while the DecisionState object's `schema_version` is 2.

## Meaning of each label

| DecisionState label | What it describes | Boundaries on interpretation |
| --- | --- | --- |
| `attention_over_time` | Little/no, intermittent, or high-fraction looking during the recent observation window. | Sustained means a high fraction of covered time, not necessarily uninterrupted looking or interest in interaction. Invalid/insufficient history is described separately from measured low attention. |
| `current_gaze` | Whether the latest available gaze sample is classified as looking at the robot. | This is a threshold/hysteresis classification, separate from recent history. It is not a probability of interest. |
| `proximity` | Closer than, within, or beyond the configured conversation distance; within or beyond configured social range. | It describes the existing distance category. Being within social range does not establish willingness or a safe approach. |
| `corridor_position` | An estimated position inside, beside, or behind the robot's forward corridor. | The current head/nose-based position proxy is retained. Inside does not predict collision; beside does not establish future clearance. |
| `relative_separation` | Robot-person distance decreasing, approximately steady, or increasing. | Either participant can cause a change. Steady separation does not mean the person is stationary. No individual motion or trajectory is invented. |
| `face_detection` | A face detected, no face detected, or unavailable face information. | Detection does not establish that the person faces the robot. Failed detection does not establish that a person has turned away. |
| `measured_path_relation` | An upstream measured CLEAR/CONFLICT value, or no measurement available. | This remains independent of corridor position. The adapter does not infer conflict/clearance from position. All nine recordings leave this measurement UNKNOWN. |
| `measured_pass_gesture` | An upstream measured PASS value, or no gesture measurement available. | An unavailable measurement is not an observed absence of a gesture. No pass gesture, permission or intent is inferred. All nine recordings leave this UNKNOWN. |
| `robot.motion_description` | Robot translation/rotation within or above stationary tolerances, or unavailable motion. | MOVING may include translation or turning. It does not identify who causes the relative distance trend. |

Unknown descriptions name the missing information instead of repeating a
generic `UNKNOWN` word. They do not invent the cause of missing data.
Temporarily missing tracks continue to be excluded from the compact current
decision payload.

## Example: S4's frozen first eligible observation

The shared labels say:

- “Little or no looking at the robot during the recent observation window.”
- “Latest gaze sample is classified as not looking at the robot.”
- “Beyond conversation distance, within the configured social range.”
- “Position estimate is inside the robot forward corridor; future obstruction is unknown.”
- “Robot-person distance is decreasing; either participant may cause this.”
- “A face was detected; facing the robot is not established.”
- “Path conflict or clearance has not been measured.”
- “No pass gesture measurement is available.”

The measured distance remains exactly 3.33460498046875 metres. The gaze score
remains 0.8351539373397827 and the effective exit threshold remains 0.95.
Those numbers are neither rounded nor replaced by the descriptions. The labels
do not say that the person wants to pass, is uninterested, is approaching the
robot, or that YIELD is the required answer. The rule's original combination of
measurements still selects YIELD; the LLM makes its independent judgment.

## Derivation and integrity

`DecisionPerson` derives its labels from its authoritative categories and
validity flags during validation. Known looking history requires `gaze_valid`;
current gaze separately requires a classification and available current score.
Known proximity requires valid, present, nonnegative distance. Known separation
requires a valid trend. Corridor descriptions use the authoritative estimated
category, and measured path/gesture descriptions use only their upstream enums.

`DecisionRobot` derives its description from the authoritative motion category.
Saved or caller-supplied descriptions are recomputed, including when detached
compact input is passed through `prepare_decision_input`. This prevents stale
prose from overriding observations. Full SocialState diagnostics are unmodified.

The v14 system instructions and user prefix remain byte-for-byte unchanged to
isolate the representation experiment. Prompt metadata is now
`social-state-llm-v14-semantic-v2`. The existing system text still defines the
same observation-label concepts; prompt wording changes belong to a separate
experiment.

## Verification and evaluation

The stage 1/2 frozen v7 code is the rule oracle. All 574 frames must match for
readiness, complete decisions, reasons, target IDs/epochs and source/session IDs;
402 remain eligible. Existing boundary/precedence tests continue to run.
Description tests cover all label categories, unavailable/invalid evidence,
latest gaze versus history, stale prose and exact retention of every stage 3
non-label operand across all 18 first/later snapshots. Integration tests compare
the production rule input with the actual model message.

The evaluator verifies the sealed stage 3 artifacts and uses the original nine
recordings, first eligible observations, later diagnostic timestamps and allowed
actions. It runs three warm repetitions with the original model digest/runtime,
order, generation settings and acceptance limits. First-output alignment and
eligibility-to-complete-output latency remain the primary measures. Later
outputs cannot replace a failed first decision. Acceptance is reported separately
because it does not check action correctness.

To reproduce with the installed baseline model served by local Ollama, choose
a new output directory:

```sh
.venv/bin/python -B -m scripts.evaluate_semantic_state \
  --output var/evaluation/semantic-state-new
```

Use `--prepare-only` for parity checks and frozen inputs without model calls.
Labels add text, so the measured token and latency cost must be assessed alongside
action alignment. These scenarios remain a tuning set.

Results: [stage 4 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/REPORT.md).
