# Stage 4: shared semantic descriptions

DecisionState v2 replaces terse observation aliases with deterministic measurement descriptions and adds face, upstream path/gesture and robot-motion descriptions. Both policies receive the same object. Raw categories, numeric operands, full SocialState, tracking, readiness, rule conditions and precedence are unchanged. The preprocessing labels infer no intent, willingness, pass permission or predicted collision.

## Experimental controls

The stage 1/2 first eligible and separate later timestamps, expected-action lists, model digest, Ollama version, generation options, scenario order and acceptance limits are reused. Three warm repetitions are scored only on their first output. The system instructions and user prefix are byte-identical to stage 3 and the original baseline; prompt metadata alone changes. The sealed stage 3 results are verified before and after this experiment. No physical robot action is executed.

All 574 frames match the original frozen rule code for readiness, actions, reasons, target IDs/epochs and source/decision IDs; 402 remain eligible. All 27 operational inputs match between production rules and actual model messages. All 18 first/later snapshots retain every stage 3 non-label operand exactly.

## First-decision results

| Metric | Stage 3 compact | Stage 4 descriptions |
| --- | ---: | ---: |
| Action alignment | 24/27 | 24/27 |
| Median eligibility → complete observed output | 2.76s | 3.72s |
| Median prompt tokens | 794 | 880 |
| Live replay acceptance | 24/27 | 22/27 |
| Aligned and accepted | 21/27 | 19/27 |
| Valid first outputs | 27/27 | 27/27 |

Median latency changed by +35.0% and prompt tokens by +10.8% relative to stage 3. These are separate warm runs, with cache/native timing counts retained; wall time also depends on runtime conditions.

| Scenario | Allowed actions | Stage 3 first actions | Stage 4 first actions (repeat 1 / 2 / 3) | Stage 4 median output time | Accepted |
| --- | --- | --- | --- | ---: | ---: |
| S1 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3.85s | 3/3 |
| S2 | APPROACH | APPROACH / APPROACH / APPROACH | APPROACH / APPROACH / APPROACH | 3.83s | 2/3 |
| S3 | ENGAGE, APPROACH | ENGAGE / ENGAGE / ENGAGE | ENGAGE / ENGAGE / ENGAGE | 3.81s | 3/3 |
| S4 | YIELD | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3.86s | 3/3 |
| S5 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 1.74s | 3/3 |
| S6 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3.61s | 3/3 |
| S7 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3.72s | 2/3 |
| S8 | APPROACH, ENGAGE, CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 1.59s | 2/3 |
| S9 | APPROACH | APPROACH / APPROACH / APPROACH | APPROACH / APPROACH / APPROACH | 3.37s | 1/3 |

## Actual first-output explanations

Distinct answers and their frequency are shown below; every original answer and native response is retained in the measurements.

- S1: **CONTINUE** (3/3): Person not looking at the robot and beyond conversation distance.
- S2: **APPROACH** (3/3): Sustained gaze and decreasing distance indicate interest in interaction.
- S3: **ENGAGE** (3/3): Sustained gaze within interaction range indicates willingness to interact.
- S4: **CONTINUE** (3/3): Person not looking at the robot and beyond conversation distance.
- S5: **CONTINUE** (3/3): Robot is moving; no person observed.
- S6: **CONTINUE** (3/3): Person not looking at the robot and beyond conversation distance.
- S7: **CONTINUE** (3/3): Person not looking at the robot and beyond conversation distance.
- S8: **CONTINUE** (3/3): Robot is moving; no person observed.
- S9: **APPROACH** (3/3): Sustained gaze and proximity within social range

## Rejected delivery

- S2 repeat 1: `{"current_readiness": true, "failure_reason": null, "latest_observation_age_s": 1.03544, "model_source_age_s": 4.637904, "trial_phase": "OBSERVING"}`
- S8 repeat 1: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.083759, "model_source_age_s": 1.885297, "trial_phase": "OBSERVING"}`
- S9 repeat 1: `{"current_readiness": true, "failure_reason": null, "latest_observation_age_s": 1.353198, "model_source_age_s": 3.669961, "trial_phase": "OBSERVING"}`
- S7 repeat 3: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.072244, "model_source_age_s": 3.494416, "trial_phase": "OBSERVING"}`
- S9 repeat 3: `{"current_readiness": true, "failure_reason": null, "latest_observation_age_s": 1.053944, "model_source_age_s": 3.370707, "trial_phase": "OBSERVING"}`

## Later observations — diagnostic only

| Scenario | Frozen later time | Rule action | Stage 4 model action | Reason |
| --- | ---: | --- | --- | --- |
| S1 | 10.47s | CONTINUE | CONTINUE | Robot is moving; no person observed. |
| S2 | 4.71s | APPROACH | APPROACH | Person observed with sustained gaze and within approachable distance. |
| S3 | 4.70s | ENGAGE | ENGAGE | Sustained gaze and face detected within interaction range. |
| S4 | 6.00s | CONTINUE | APPROACH | Person within interaction range, intermittent looking suggests potential interest. |
| S5 | 6.87s | CONTINUE | CONTINUE | Robot is moving; no person observed. |
| S6 | 4.62s | CONTINUE | APPROACH | Person within interaction range, intermittent gaze suggests potential interest. |
| S7 | 8.82s | CONTINUE | APPROACH | Intermittent looking at the robot and within interaction range. |
| S8 | 7.16s | CONTINUE | CONTINUE | Person not looking at the robot and beyond conversation distance. |
| S9 | 3.46s | APPROACH | APPROACH | Person is looking at the robot and within approachable distance. |

Alignment and acceptance measure different things: acceptance checks freshness/readiness and output validity, not whether the selected action is correct. S8 first resolves during the empty scene before a person appears. The original broad allowed-action sets for S3 and S8 remain. These nine recordings are a tuning set; improvement here is not evidence of held-out accuracy.

- [Exact S4 first decision input](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/snapshots/S4/first.decision-state.json)
- [All first outputs and timing](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/measurements/operational-summary.json)
- [Metrics](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/comparison.json)
- [All-frame rule parity](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/rule-parity.json)
- [Stage 3 reference](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/REPORT.md)

## Interpretation and regression checks

The descriptions make the state meaning explicit, but this experiment produced no first-action alignment improvement. Every first action matches stage 3: S4 still chooses CONTINUE instead of YIELD. Added text has a measured cost (+10.8% tokens, +35.0% median output latency); stage 3 remains the faster comparison reference. Separate warm-run cache/runtime effects also influence wall time.

The model continues to infer interest in S2 and willingness in S3 in its explanations. Those claims are model judgments, not facts supplied by the labels. Clearer input descriptions alone do not guarantee grounded explanations. Later S4/S6/S7 outputs still disagree with the rules and remain excluded from the operational score.

Five first deliveries were rejected: S2 repeat 1 and S9 repeats 1/3 exceeded the one-second observation-freshness limit; S8 repeat 1 and S7 repeat 3 had current readiness gaps. None exceeded the ten-second model-source age limit. The wrong S4 action was accepted in all repetitions, illustrating why acceptance is distinct from correctness.

The full suite ran 664 test methods with local HTTP fixtures enabled. All new description, parity and integration checks passed. The only failing method is the already documented VLM/LLM verbatim-action-definition check, with four subcases. The system instructions remain identical to the baseline; no unrelated prompt/test change was made. The initial sandbox's 14 HTTP permission errors disappear when local fixtures are enabled.

- [Full test output](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/verification/full-tests.log)
- [Label meanings](/Users/eddiekim/Documents/Projects/P4P/docs/semantic-decision-state.md)
- [Experiment log](/Users/eddiekim/Documents/Projects/P4P/docs/llm-refinement-log.md)
