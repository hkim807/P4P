# Stage 5: refine LLM instructions

The experiment compares the previous v14 instructions and new v16 instructions on the exact same current DecisionState v3, after unsupported Navel cues were removed. Both policies still receive the same shared state. Only system instructions, user prefix and prompt-version metadata differ. The old prompt is supplied through temporary runtime overrides; model results and acceptance checks are not patched.

## Controls and scoring

All 574 frames preserve complete frozen v7 decisions/readiness (402 eligible). Original first eligible timestamps, later diagnostic timestamps, expected-action sets and nine-scenario weighting are unchanged. Both arms use the same model digest/Ollama version, temperature 0, seed 42, context 8192, output limit 192, scenario order, three warm repetitions and acceptance limits. All control trials precede all refined trials; these are sequential runs, not randomized or simultaneous timing measurements. Native cache/token/timing measurements are saved.

First complete outputs are the primary score. No later answer replaces a failed first decision. Each arm has 27 operational trials and nine later diagnostics. Actual requests are checked against frozen instructions and shared-state snapshots; all 27 paired operational state hashes are identical. Prior stage 3/4 results are historical context, not the prompt-only control, because their state payloads differ.

## Instruction changes

- Name the current DecisionState and remove references to missing-track history and old uppercase label aliases.
- Separate current gaze, recent gaze history, coverage fraction and continuous looking. Gaze scores are not probabilities of interest.
- Treat proximity as a configured reference band and distinguish moving closer from speaking at the current distance; it is not an invitation.
- Interpret corridor position and separation together while preserving uncertainty about individual motion, clearance and collision.
- Consider room for passage separately from interaction. Giving space does not require claiming a measured collision or a wish to pass.
- Explain decisive observations without asserting interest, willingness, gestures or permission as facts. Preserve the two-field JSON contract and 25-word sentence request.

## Operational results

| Metric | v14 control on v3 | v16 refined on v3 |
| --- | ---: | ---: |
| First-action alignment | 24/27 | 24/27 |
| Median eligibility → complete observed output | 3.31s | 3.04s |
| Median prompt tokens | 842 | 917 |
| Accepted by live replay | 19/27 | 22/27 |
| Aligned and accepted | 16/27 | 19/27 |
| Valid first outputs | 27/27 | 27/27 |

| Scenario | Allowed actions | v14 first actions | v16 first actions | v14 median output | v16 median output |
| --- | --- | --- | --- | ---: | ---: |
| S1 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3.08s | 2.76s |
| S2 | APPROACH | APPROACH / APPROACH / APPROACH | APPROACH / APPROACH / APPROACH | 3.58s | 3.04s |
| S3 | ENGAGE, APPROACH | ENGAGE / ENGAGE / ENGAGE | ENGAGE / ENGAGE / ENGAGE | 3.26s | 3.05s |
| S4 | YIELD | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3.34s | 3.16s |
| S5 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 1.43s | 1.40s |
| S6 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3.36s | 3.12s |
| S7 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3.24s | 3.34s |
| S8 | APPROACH, ENGAGE, CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | 1.80s | 1.54s |
| S9 | APPROACH | APPROACH / APPROACH / APPROACH | APPROACH / APPROACH / APPROACH | 3.44s | 3.09s |

## First-output explanations

### v14 control

- S1: **CONTINUE** — Person beyond conversation distance, decreasing relative distance.
- S2: **APPROACH** — Sustained gaze and decreasing relative distance indicate interest and proximity.
- S3: **ENGAGE** — Sustained gaze within interaction range indicates willingness to interact.
- S4: **CONTINUE** — Person not looking at robot and beyond conversation distance.
- S5: **CONTINUE** — Robot is moving; no person observed.
- S6: **CONTINUE** — Person not looking at the robot and beyond conversation distance.
- S7: **CONTINUE** — Person intermittently looks at the robot but is beyond conversation distance.
- S8: **CONTINUE** — Robot is moving; no person observed.
- S9: **APPROACH** — Sustained gaze and face detected, beyond conversation distance.
### v16 refined

- S1: **CONTINUE** — Person outside interaction range, no sustained looking.
- S2: **APPROACH** — Person within social range, sustained gaze at the robot.
- S3: **ENGAGE** — Sustained gaze and proximity within interaction range.
- S4: **CONTINUE** — Person outside interaction range, decreasing distance but no sustained looking.
- S5: **CONTINUE** — No people observed and robot is moving.
- S6: **CONTINUE** — No recent looking and low gaze fraction outside interaction range.
- S7: **CONTINUE** — Intermittent looking but no sustained gaze or closing distance.
- S8: **CONTINUE** — No people observed and robot is moving.
- S9: **APPROACH** — Person detected at approachable distance with sustained gaze.

## Later diagnostics — excluded from operational score

| Scenario | Rule | v14 | v16 | v16 reason |
| --- | --- | --- | --- | --- |
| S1 | CONTINUE | CONTINUE | CONTINUE | No people observed and robot is moving. |
| S2 | APPROACH | APPROACH | APPROACH | Sustained gaze and steady proximity outside interaction range |
| S3 | ENGAGE | ENGAGE | ENGAGE | Sustained gaze and face detected within interaction range. |
| S4 | CONTINUE | APPROACH | CONTINUE | Person within interaction range but not looking at the robot. |
| S5 | CONTINUE | CONTINUE | CONTINUE | No people observed and robot is moving. |
| S6 | CONTINUE | APPROACH | CONTINUE | Intermittent looking and no sustained gaze. |
| S7 | CONTINUE | APPROACH | APPROACH | Person within interaction range with intermittent looking. |
| S8 | CONTINUE | CONTINUE | CONTINUE | Person outside interaction range, increasing distance. |
| S9 | APPROACH | CONTINUE | APPROACH | Sustained gaze and steady proximity outside conversation distance. |

## Delivery rejections

- control S1 repeat 1: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.098631, "model_source_age_s": 6.386583, "trial_phase": "OBSERVING"}`
- control S7 repeat 1: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.035064, "model_source_age_s": 3.369331, "trial_phase": "OBSERVING"}`
- control S8 repeat 1: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.058534, "model_source_age_s": 1.860072, "trial_phase": "OBSERVING"}`
- control S9 repeat 1: `{"current_readiness": true, "failure_reason": null, "latest_observation_age_s": 1.205574, "model_source_age_s": 3.522337, "trial_phase": "OBSERVING"}`
- control S7 repeat 2: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.036864, "model_source_age_s": 3.242686, "trial_phase": "OBSERVING"}`
- control S9 repeat 2: `{"current_readiness": true, "failure_reason": null, "latest_observation_age_s": 1.119143, "model_source_age_s": 3.435906, "trial_phase": "OBSERVING"}`
- control S7 repeat 3: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.029527, "model_source_age_s": 3.235349, "trial_phase": "OBSERVING"}`
- control S8 repeat 3: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.123899, "model_source_age_s": 1.799743, "trial_phase": "OBSERVING"}`
- refined S1 repeat 1: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.033495, "model_source_age_s": 6.321447, "trial_phase": "OBSERVING"}`
- refined S7 repeat 1: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.133482, "model_source_age_s": 3.339304, "trial_phase": "OBSERVING"}`
- refined S7 repeat 2: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.036734, "model_source_age_s": 3.371001, "trial_phase": "OBSERVING"}`
- refined S7 repeat 3: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.083845, "model_source_age_s": 3.289667, "trial_phase": "OBSERVING"}`
- refined S8 repeat 3: `{"current_readiness": false, "failure_reason": null, "latest_observation_age_s": 0.083351, "model_source_age_s": 1.884889, "trial_phase": "OBSERVING"}`

Acceptance is distinct from correctness and is affected by finite-clip freshness/readiness. S8 first resolves in the empty scene before a person appears. The broad S3/S8 allowed sets are unchanged. These recordings are a tuning set, not held-out evidence of real-world performance. Reason text is retained for qualitative review; no automatic semantic-validation claim is made. No physical robot action was executed.

- [Old instructions](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/control-instructions.txt)
- [New instructions](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/refined-instructions.txt)
- [Metrics](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/comparison.json)
- [Paired state verification](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/paired-arm-verification.json)

The previously measured v14 control was reused from /Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/candidate-v15; it was not measured again for this candidate. Its sealed artifacts, actual requests and all state hashes were verified. Candidate v15 was rejected for over-selecting YIELD (5/9 alignment in each run); this follow-up balances interaction and progress with an observed spatial need for space.

This candidate restored 8/9 alignment in all runs but still missed S4. Its S7 explanation also treated an unavailable trend as no closing distance. It is retained as an intermediate comparison.

## Selected implementation and candidate history

The production prompt is v16, restored byte-for-byte to the measured implementation. v15 was rejected after three runs at 5/9 alignment because it over-selected YIELD. v16 scored 8/9 in all three runs, preserving every old-prompt first action. A final v17 screen scored 8/9 but exchanged S4's miss for an incorrect YIELD in S1; it was rejected without operational repetitions. The default uses one whole selected prompt, never the best answer from different candidates for each scenario.

The final instruction interpretation is clearer, but primary action alignment did not improve. S4 remains CONTINUE instead of YIELD. The selected prompt's median complete-output time was 3.04 seconds versus the freshly measured old-prompt control's 3.31 seconds (-8.1%); median input tokens increased from 842 to 917 (+8.9%). Sequential runtime/cache effects also affect these timings. Live acceptance rose from 19/27 to 22/27 and remains a separate finite-replay measure.

## Explanation review

Explicit claims that gaze indicates interest (S2) or willingness (S3) disappeared from the selected prompt's first-output explanations. This is a qualitative observation about the saved answers, not a new semantic acceptance check. Problems remain: S7 says “no ... closing distance” despite an unavailable trend, and S6 says “No recent looking” despite a low nonzero gaze fraction. The instructions distinguish these cases but do not guarantee that the model follows them. Later diagnostic disagreements remain excluded from the operational score.

All first outputs in the control and selected arms are valid, with reasons under 25 whitespace-separated words. Validity does not establish factual grounding or action correctness. No rule fallback, post-processing correction or wider freshness limits were introduced.

## Verification

The final implementation matches the measured source hashes. All 574 frames preserve complete frozen rule decisions/readiness; 402 remain eligible. All 27 selected/control first-input hashes match. Actual system/user messages and transport bytes were verified for 36 calls per arm. The full suite ran 666 test methods with local HTTP fixtures: no new failures or errors; the same pre-existing VLM/LLM action-wording method fails four subcases. The action definitions themselves were unchanged by this stage.

- [Rejected v15 experiment](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/candidate-v15/REPORT.md)
- [Intermediate v16 experiment](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/candidate-v16/REPORT.md)
- [Rejected v17 screen](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/candidate-v17-screen/REPORT.md)
- [Instruction guide](/Users/eddiekim/Documents/Projects/P4P/docs/llm-instruction-refinement.md)
