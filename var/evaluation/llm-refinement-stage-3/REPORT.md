# Stage 3: compact shared decision state

The full SocialState remains the diagnostic record. Both policies now consume the same compact DecisionState, containing observed people, existing categories/labels and every numeric operand required by the tuned v7 rules. Policy constants are cached outside observation data, and identifiers remain in a correlation context. Raw geometry, unused fit statistics and configuration are not sent to the LLM. System instructions and generation/acceptance settings were unchanged.

## Rule preservation

The actual frozen v7 implementation was loaded as the reference. Readiness, actions, reason codes, target IDs/epochs and decision/source/session IDs match on every one of 574 frames (402 eligible). Independent boundary tests cover nulls, exact thresholds, recurring attention, path/pass precedence, multiple/missing people, empty intervals and failure flags.

## Frozen operational comparison

First eligible timestamps are the original frozen timestamps; later diagnostics use the separately frozen last eligible timestamp. All 27 operational rule/LLM inputs were checked for identical compact content and full source equivalence. No later success substitutes for an incorrect first output.

| Metric | Full baseline | Compact |
| --- | ---: | ---: |
| First-action alignment, 27 trials | 18/27 | 24/27 |
| Median eligibility → complete observed output | 6.21s | 2.76s |
| Median input tokens, including unchanged instructions | 1766 | 794 |
| Live replay acceptance | 5/27 | 24/27 |

Median input reduction: 55.0%. Median complete-output latency reduction: 55.6%. These are separate warm-model runs on the same model/runtime and scenario order, not simultaneous measurements; cache counts and timings are retained.

S1 and S9 now match their expected first actions. S4 remains incorrect: CONTINUE instead of YIELD in all three runs. Acceptance and alignment have different misses despite matching totals: the incorrect S4 action was accepted; the aligned S7 CONTINUE was rejected because current gaze evidence was insufficient when it arrived. S7's latest observations were fresh (0.08–0.13 seconds old); this was a readiness gap, not inference expiry. Aligned-and-accepted first outputs total 21/27.

| Scenario | Frozen rule action | Compact first action, repetitions 1 / 2 / 3 | Baseline median eligibility → output | Compact median eligibility → output |
| --- | --- | --- | ---: | ---: |
| S1 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 6.70s | 2.78s |
| S2 | APPROACH | APPROACH / APPROACH / APPROACH | 6.70s | 3.00s |
| S3 | ENGAGE | ENGAGE / ENGAGE / ENGAGE | 5.88s | 2.64s |
| S4 | YIELD | CONTINUE / CONTINUE / CONTINUE | 6.42s | 3.01s |
| S5 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 2.01s | 1.19s |
| S6 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 5.75s | 2.88s |
| S7 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 6.21s | 2.52s |
| S8 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 2.10s | 1.12s |
| S9 | APPROACH | APPROACH / APPROACH / APPROACH | 6.32s | 2.74s |

## Later diagnostics (excluded from score)

| Scenario | Frozen later time | Rule action | Compact LLM action |
| --- | ---: | --- | --- |
| S1 | 10.47s | CONTINUE | CONTINUE |
| S2 | 4.71s | APPROACH | APPROACH |
| S3 | 4.70s | ENGAGE | ENGAGE |
| S4 | 6.00s | CONTINUE | APPROACH |
| S5 | 6.87s | CONTINUE | CONTINUE |
| S6 | 4.62s | CONTINUE | APPROACH |
| S7 | 8.82s | CONTINUE | ENGAGE |
| S8 | 7.16s | CONTINUE | CONTINUE |
| S9 | 3.46s | APPROACH | APPROACH |

The original expected-action lists, including the multiple actions for S3/S8, remain unchanged. S8 first resolves before the person appears. Live acceptance can still fail because a finite recording ends or current readiness changes. These are tuning-set results, not held-out accuracy; no physical robot action was executed.

## Regression verification

All stage 3 rule-preservation, prompt, receiver, replay and boundary checks pass. The complete suite ran 658 test methods with HTTP fixture servers enabled. One pre-existing VLM wording-consistency test fails four subcases: it demands verbatim action definitions across differently worded VLM and LLM prompts. The LLM instructions are byte-identical to the frozen baseline, so neither prompt was changed to repair this unrelated assertion. Test logs and a reproduction of the existing wording mismatch are retained under `verification`.

- [Exact first S4 compact input](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/snapshots/S4/first.decision-state.json)
- [First-action results and timing](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/measurements/operational-summary.json)
- [All-frame rule parity](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/rule-parity.json)
- [Comparison metrics](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/comparison.json)
- [Frozen original baseline](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/REPORT.md)
