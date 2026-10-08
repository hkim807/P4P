The rule policies on feature/ollama-llm-vlm-policies (c6b988f) and feature/end-to-end-pipeline-refinement (3b5fdc8) do not produce identical outputs. The source branch uses social-rules-v2; the current branch uses social-rules-v3. This report evaluates deterministic rules only. No Ollama requests or robot actions were executed.

Native pipeline replay used all nine unchanged SDK recordings and all 730 perception frames in source order. The active checkout was preserved; source-branch files were read from a Git archive in an isolated temporary directory. First policy outputs were selected after completing each causal replay. No hardware/client acceptance is implied.

| Scenario | Ollama branch first policy output | Current branch first policy output |
|---|---|---|
| S1 | CONTINUE at 0.00s | No action |
| S2 | CONTINUE at 0.00s | ENGAGE at 8.27s |
| S3 | ENGAGE at 1.40s | ENGAGE at 1.19s |
| S4 | CONTINUE at 0.00s | No action |
| S5 | CONTINUE at 0.00s | No action |
| S6 | CONTINUE at 0.00s | No action |
| S7 | CONTINUE at 0.00s | No action |
| S8 | CONTINUE at 0.00s | No action |
| S9 | APPROACH at 1.51s | APPROACH at 1.21s |

The old branch outputs CONTINUE immediately in S1, S2, S4, S5, S6, S7 and S8 because their first SDK frame contains no person. S3 and S9 start with detected people, so their first outputs require temporal evidence. This is a policy-level comparison: the old SocialPipeline does not expose the current two-field final_decision response, and these first proposals are not results from executing a current SingleTrial client against that branch.

Native actions differ on 459/730 frames. Of those, 436 are old CONTINUE to current no action. The other 23 differences arise from temporal preprocessing in these captures: lowering minimum valid gaze coverage from 0.8s to 0.6s and category dwell from 0.3s to 0.1s. The 2s window, 1s minimum history span, 5-sample minimum and distance thresholds remain the same.

On identical current structured observations, both rules choose the same action on all 125 frames where the current rule policy resolves an action. Across all 730 shared states, there are 436 differences, all old CONTINUE versus current no action. That agreement does not establish general rule equivalence: these recordings do not exercise the distinguishing path, gesture, proximity, recurring-attention and attention-transition conditions.

The current-with-old-temporal control differs from the current native pipeline on exactly 23 frames, confirming the temporal contribution separately from rule/readiness changes in this dataset. Per-frame transport decisions, reasons and canonical actions are retained in compressed traces. DEFER is treated as no action for comparison; it is not one of the four executable actions.

Representative condition probes use identical supplied cues and common configuration fields, with new evidence fields removed when validating the old schema. Ten of 23 canonical outputs differ:

| Condition | Ollama rules v2 | Current rules v3 |
|---|---|---|
| No detected person | CONTINUE | No action |
| Two people, explicit conflict on one | YIELD | No action |
| Too close, gaze unavailable | DEFER | YIELD |
| Pass gesture, gaze unavailable | DEFER | CONTINUE |
| Qualifying recurring attention nearby | CONTINUE | ENGAGE |
| Qualifying recurring attention in approach range | CONTINUE | APPROACH |
| Attention ended, category still sustained | ENGAGE | CONTINUE |
| Looking resumed for only 0.2 seconds, category still sustained | ENGAGE | CONTINUE |
| Attentive nearby person moving away | CONTINUE | ENGAGE |
| Attentive nearby person, increasing relative separation while robot moves | CONTINUE | ENGAGE |

On the old branch, proximity is explicitly blocked by readiness (PERSON_TOO_CLOSE), so proximity alone cannot produce YIELD. Explicit CONFLICT can produce YIELD and overrides the multiple-person gate there; current readiness requires exactly one observed person first. An old PASS observation must still pass gaze readiness. Old INTERMITTENT always yields CONTINUE, regardless of repeated attention. Old SUSTAINED does not inspect the current looking state or renewed looking-run length. Old reliable increasing separation vetoes nearby ENGAGE; current rules allow a nearby attentive greeting before the separation check.

Ordinary sustained attention at stable conversation distance still yields ENGAGE on both policies; stable approach-range attention yields APPROACH; incidental glances, no attention and far people still yield CONTINUE once eligible. Retained but currently missing tracks, invalid distance and ordinary insufficient gaze produce no executable action on both, although the old transport returns DEFER.

The SDK adapter and estimator on the source branch do not populate explicit path/pass measurements from live packets. Consequently the synthetic path/pass probes evaluate classifier capabilities, not observed SDK gesture or route-conflict detection. No interpretation of these comparisons as survey accuracy is justified because the trimmed survey endpoints remain unresolved.

Validation: all nine source hashes match the existing evaluation contract; 730 frames per native/control/shared replay align by scenario, frame, source line and source hash. Six source-branch rule tests and 23 current policy/refinement tests passed. The current frozen production/configuration hashes still match. No source-branch or current production code was modified.

Artifacts: manifest.json identifies source commits and comparison methods; inputs.json and cases.json retain all 23 condition probes; summaries list first outputs, SDK-end outputs and full-stream action counts. The *-comparison.json files retain aggregate counts and every differing frame. runner.py reproduces the calculation against a checkout/archive supplied with --repo.
