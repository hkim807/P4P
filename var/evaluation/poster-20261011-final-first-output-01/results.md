# Final-preset first-output poster evaluation

Uses `config/policy-comparison-final.json`: social-rules-v7, social-state-llm-v14, qwen2.5:7b, with the final temporal and tracking settings.

**Selection is the first validated policy output during each replay, including CONTINUE.** A valid output is retained even if the client rejects it. These results replace the previous development-preset/clip-end condition for the requested poster comparison. Earlier run artifacts are preserved; their 4/9 endpoint figure is not this condition.

All 9/9 recordings reach readiness. Whole-recording coverage is 304/574 ready perception frames (53.0%, frame-weighted). The independent scenario count is nine; three repetitions do not create 27 independent encounters.

| Repetition | Policy | Valid outputs / 9 | Client accepted / 9 |
|---|---|---:|---:|
| 1 | rules | 9 | 9 |
| 1 | llm | 9 | 1 |
| 2 | rules | 9 | 9 |
| 2 | llm | 9 | 1 |
| 3 | rules | 9 | 9 |
| 3 | llm | 9 | 1 |

| Scenario | Rules by repetition | LLM by repetition | Rules accepted / 3 | LLM accepted / 3 |
|---|---|---|---:|---:|
| S1 | CONTINUE, CONTINUE, CONTINUE | APPROACH, YIELD, YIELD | 3 | 0 |
| S2 | APPROACH, APPROACH, APPROACH | APPROACH, APPROACH, APPROACH | 3 | 0 |
| S3 | ENGAGE, ENGAGE, ENGAGE | ENGAGE, ENGAGE, ENGAGE | 3 | 0 |
| S4 | YIELD, YIELD, YIELD | APPROACH, APPROACH, APPROACH | 3 | 0 |
| S5 | CONTINUE, CONTINUE, CONTINUE | CONTINUE, CONTINUE, CONTINUE | 3 | 3 |
| S6 | CONTINUE, CONTINUE, CONTINUE | CONTINUE, CONTINUE, CONTINUE | 3 | 0 |
| S7 | CONTINUE, CONTINUE, CONTINUE | CONTINUE, CONTINUE, CONTINUE | 3 | 0 |
| S8 | CONTINUE, CONTINUE, CONTINUE | CONTINUE, CONTINUE, CONTINUE | 3 | 0 |
| S9 | APPROACH, APPROACH, APPROACH | CONTINUE, CONTINUE, CONTINUE | 3 | 0 |

Across 27 measured model requests, median request duration was 10.034 s (range 2.662–17.020 s). Encounter output time also includes the wait for evidence. Timing columns preserve unavailable values rather than zero.

First eligible canonical states match across policies in 27/27 pairs; 0 pairs have no paired eligible state. Raw inputs, responses, failures, late outputs and delivery evidence are retained under operational-repeat-*/. The LLM makes its own decisions without rule fallback or correction.

RQ1: the adapter, tracker and temporal estimator provide common, inspectable observation histories and validity flags. With the final preset, no-person observations can permit CONTINUE after one second. This explains early empty-scene outputs and does not prove physical route clearance. First decisions can precede later gaze events. Path-conflict/pass-gesture inputs remain UNKNOWN when unmeasured; distance change is robot-relative. These are evidence-availability measurements, not perception accuracy.

RQ2: policy outputs can be compared on identical initial evidence, but human preference/appropriateness scores remain unavailable. The 42-response workbook was not supplied, and first-output timing differs from the survey question at video end. Requested development labels are retained as context only, not human ground truth.

Three fresh recorded-speed repetitions used alternating policy order and serial model requests, with warmup logged separately. Client freshness/readiness/expiry checks were preserved. Rejection categories are derived from recorded delivery errors and failed acceptance predicates. Source age at delivery is recorded; exact model-completion source age and isolated rule inference duration were not separately measured.

These nine recordings were previously used for tuning. No claim of generalisation, human comfort, physical safety or navigation success follows from this replay. No robot transport or movement was executed. All frozen source, code and configuration hashes were checked unchanged before and after execution.
