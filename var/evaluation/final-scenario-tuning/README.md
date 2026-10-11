# Final-scenario tuning results

All nine rule-based first actions match the requested options and were accepted by the live client. The actual `qwen2.5:7b` outputs are recorded separately below; they were not corrected by the rules.

Primary run: [run-02 manifest](run-02/manifest.json), [raw summary](run-02/summary.json), [combined evidence](report.json). The manifest stores the model digest, generation settings, full effective configurations and working-tree source hashes. Inputs are the nine motion-trimmed recordings in `../final-scenarios-audit/trimmed/`. These recordings were used to tune the policy and are not a held-out test set.

## First outputs

Rule time is the source observation time after robot motion starts. LLM time is elapsed replay wall time after motion starts, including inference. Both policies submit the same first eligible source state. “Accepted” means the production `SingleTrial` accepted the output with its ordinary freshness limits. No physical robot action was executed.

| Scenario | Requested | Rule first output | Rule time (s) | LLM first output | LLM time (s) | LLM accepted? |
| --- | --- | --- | ---: | --- | ---: | --- |
| S1 | CONTINUE | CONTINUE | 1.18 | CONTINUE | 15.14 | No |
| S2 | APPROACH | APPROACH | 1.10 | CONTINUE | 19.13 | No |
| S3 | ENGAGE / APPROACH | ENGAGE | 1.21 | CONTINUE | 12.97 | No |
| S4 | YIELD | YIELD | 1.10 | CONTINUE | 17.10 | No |
| S5 | CONTINUE | CONTINUE | 1.17 | CONTINUE | 5.92 | Yes |
| S6 | CONTINUE | CONTINUE | 1.11 | CONTINUE | 17.40 | No |
| S7 | CONTINUE | CONTINUE | 2.13 | CONTINUE | 13.96 | No |
| S8 | APPROACH / ENGAGE / CONTINUE | CONTINUE | 1.17 | CONTINUE | 6.27 | No |
| S9 | APPROACH | APPROACH | 1.14 | CONTINUE | 17.70 | No |

## Rule evidence at the first decision

| Scenario | Distance (m) | Nose lateral (m) | Position | Gaze state | Looking fraction | Relative distance trend |
| --- | ---: | ---: | --- | --- | ---: | --- |
| S1 | 3.501 | 0.633 | TO_SIDE | NONE | 0.000 | DECREASING |
| S2 | 2.315 | 0.855 | TO_SIDE | SUSTAINED | 1.000 | DECREASING |
| S3 | 1.663 | 0.064 | IN_WAY | SUSTAINED | 1.000 | STABLE |
| S4 | 3.335 | 0.273 | IN_WAY | NONE | 0.123 | DECREASING |
| S5 | — | — | No detection | Unavailable | — | Unknown |
| S6 | 3.634 | 0.513 | TO_SIDE | NONE | 0.160 | UNKNOWN |
| S7 | 2.315 | 0.935 | TO_SIDE | INTERMITTENT | 0.758 | UNKNOWN |
| S8 | — | — | No detection | Unavailable | — | Unknown |
| S9 | 2.286 | 0.884 | TO_SIDE | SUSTAINED | 1.000 | STABLE |

- S1 and S6 have low temporal attention and a nose to the side of the corridor. S4 is inside the corridor with valid closing and low gaze, so it yields.
- S2 and S9 have sustained gaze beyond 2 m, so they approach. S3 is at 1.663 m and engages.
- S5 continues after a contiguous empty interval. Missing detections do not prove the human is absent from the physical scene.
- S6 decides before its later brief glance. Its first output does not demonstrate recognition of that later event.
- S7 resolves with INTERMITTENT gaze. Recurring attention is disabled as an interaction trigger in this preset.
- S8 decides before the human first appears, from an initial empty interval. It does not establish walking-away or sustained-gaze reasoning for that first output.
- S9 uses gaze and distance; no gesture recognition was introduced.

## LLM interpretation and delivery

The LLM returned CONTINUE for all nine inputs in this run. It disagreed with the desired action in S2, S3, S4 and S9. Its reasons sometimes contradict the supplied evidence: for example, treating sustained gaze within interaction range as a reason to continue, or comparing a distance incorrectly. Matching prompt guidance did not produce matching decisions.

Only S5 was accepted by the live client. S1–S4, S6, S7 and S9 exceeded the 10 s model-source age limit and also arrived after the available recording had ended. S8 returned within 10 s, but its current observation was no longer eligible when the response arrived. Freshness checks were preserved.

Each `run-02/llm/S*/models.jsonl` contains the exact model input, raw response, parsed decision and inference duration. The same directory contains source states, incoming observations, events and the acceptance result. The earlier [run-01](run-01/summary.json) is also retained: S1 reached its 30 s trial timeout, then produced a CONTINUE in the model audit after approximately 37.83 s of inference. That late audit output was not accepted and is not presented as a successful trial.

## Reproduction and verification

Use the [preset instructions](../../../docs/final-scenario-tuning.md). Run the report builder from the repository root to regenerate this report from saved run-02 results.

The 14 focused tuning/replay tests passed. The full suite ran 632 tests and reported 12 subtest errors in two existing cancellation tests: this Python 3.10 environment lacks `asyncio.Task.cancelling()`. The same 12 errors were reproduced on the unmodified baseline. See [full-suite log](tests-full.log) and [baseline comparison](tests-baseline.log).
