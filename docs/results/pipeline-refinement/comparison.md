# Development recording endpoint comparison

Survey videos were trimmed; end offsets are missing. Actual survey-end actions and human scores are unavailable for all nine scenarios and both policies. `NOT_READY` is not an action. No favourable scores are assigned to missing observations, errors or unrun inference.

## Survey video end (primary evaluation)

| Scenario | Rule before | Rule after | LLM before | LLM after | Human support/rating |
|---|---|---|---|---|---|
| S1 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |
| S2 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |
| S3 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |
| S4 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |
| S5 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |
| S6 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |
| S7 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |
| S8 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |
| S9 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |

Primary coverage is **0/9** for each policy/version. This is unresolved synchronisation, not measured social accuracy.

## SDK end (separate diagnostic, not the survey endpoint)

| Scenario | Rule before | Rule after | LLM before | LLM after | After cue availability | After support / rating | Modal / original agreement |
|---|---|---|---|---|---|---|---|
| S1 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | no current person; path/gesture unavailable | unavailable | unavailable |
| S2 | NOT_READY | ENGAGE | NOT_READY | ENGAGE | SUSTAINED, INTERACTION_RANGE; path/gesture UNKNOWN | 11/37 (29.7%) / 3.78 | False / False |
| S3 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | no current person; path/gesture unavailable | unavailable | unavailable |
| S4 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | no current person; path/gesture unavailable | unavailable | unavailable |
| S5 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | no current person; path/gesture unavailable | unavailable | unavailable |
| S6 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | no current person; path/gesture unavailable | unavailable | unavailable |
| S7 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | no current person; path/gesture unavailable | unavailable | unavailable |
| S8 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | UNKNOWN, APPROACHABLE; path/gesture UNKNOWN | unavailable | unavailable |
| S9 | NOT_READY | NOT_READY | NOT_READY | NOT_READY | no current person; path/gesture unavailable | unavailable | unavailable |

SDK-end coverage: **0/9 before, 1/9 after** for both policies. S2 scores are conditional comparisons at a different endpoint. Its chosen ENGAGE has support 11/37 and rating 3.78 (36 valid ratings); APPROACH has support 18/37 and rating 3.65. Neither modal nor original-label agreement is achieved at this diagnostic point.

All nine camera-end proxies are NOT_READY/STATE_STALE for both policies/versions, because SDK tails end 7.2–19.6 seconds earlier. No action is extrapolated across that gap.

## Availability, source time and latency

| Scenario | Person frames / perceptions | SDK end (receipt µs) | Camera end (receipt µs) | SDK age at camera end (s) | SDK-end hold after | Rule / LLM latency after (s) |
|---|---:|---:|---:|---:|---|---|
| S1 | 0/91 | 13360773995 | 13373190465 | 12.416 | NO_VISIBLE_PERSON | not run |
| S2 | 27/84 | 13679790093 | 13696062494 | 16.272 | READY | 0.000009 / 1.274 |
| S3 | 110/115 | 13777649720 | 13785705521 | 8.056 | NO_VISIBLE_PERSON | not run |
| S4 | 0/55 | 13834515508 | 13842322353 | 7.807 | NO_VISIBLE_PERSON | not run |
| S5 | 0/40 | 13868495952 | 13877956267 | 9.460 | NO_VISIBLE_PERSON | not run |
| S6 | 4/77 | 14126307539 | 14136031320 | 9.724 | NO_VISIBLE_PERSON | not run |
| S7 | 0/29 | 14240307014 | 14259936248 | 19.629 | NO_VISIBLE_PERSON | not run |
| S8 | 32/117 | 14299278413 | 14310228350 | 10.950 | INSUFFICIENT_GAZE_EVIDENCE | not run |
| S9 | 56/122 | 14483510522 | 14490736217 | 7.226 | NO_VISIBLE_PERSON | not run |

No endpoint inference errors occurred. Ineligible LLM cases made no calls. All actions have NOT_EXECUTED_OFFLINE execution status. Source time is robot-host monotonic receipt time; it is not Unix time or verified exposure time.

## Fixed-state policy ablation

Original v3 and refined v6 prompts receive identical canonical bytes on 11 eligible frozen states (eight S3, two S9, one S2). These are diagnostic states, not 11 independent encounters.

| Prompt | Calls | Errors | APPROACH in interaction range / ENGAGE in approachable range | Mean latency (s) |
|---|---:|---:|---:|---:|
| original-v3 | 11 | 0 | 8 | 5.687 |
| refined-v6 | 11 | 0 | 0 | 5.425 |

Rule actions are identical before/after on these frozen states. Recorded rule action/coverage changes come from temporal preprocessing. The prompt reduces distance-semantics errors in this sample. The LLM still chooses CONTINUE for the two attentive APPROACHABLE S9 diagnostic states and gives weak distance-based explanations. It did not observe the pass gesture. This is not evidence of gesture understanding or policy superiority.

Full per-scenario/per-policy scores, cue availability, latency and execution/error status are in `before-after.csv`; exact inputs and outputs are in each run folder. Frame traces and action counts are separate in `states.jsonl`, `diagnostics.jsonl` and `sensor-audit.json`.
