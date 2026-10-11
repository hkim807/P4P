# Independent social-reasoning prompt: v13

The system prompt now asks the LLM to make its own social-navigation judgment from the shared SocialState. It defines the four executable actions, social objectives, essential evidence meanings, and a strict JSON response. It removes the rule algorithm, numeric action cutoffs, estimator internals and repeated exception lists. One sentence of at most 25 words is requested for the explanation.

**System prompt: 1,556 → 363 words (77% shorter).** The complete SocialState, shared readiness gate, output schema, model and generation settings are unchanged. This comparison still uses engineered temporal categories, not raw camera perception. The 25-word instruction is not enforced by truncating or rewriting responses.

[Old prompt](prompt-v12.txt) · [New prompt](prompt-v13.txt) · [Manifest](comparison-01/manifest.json) · [Exact requests and responses](comparison-01/results.json)

## Identical-state comparison

One sweep of v12 followed by one sweep of v13 on the nine saved first-eligible SocialStates from the trimmed recordings. Each pair uses identical serialized observation bytes. Scenario labels, desired actions and rule outputs were not sent to the model. No output correction or rule fallback was applied, and the prompt was not revised in response to these nine outputs.

This is an offline prompt comparison: timings are HTTP inference durations, not elapsed time since motion starts. It does not test live freshness acceptance or execute robot actions.

| Scenario | v12 action | v13 action | v12 inference (s) | v13 inference (s) |
| --- | --- | --- | ---: | ---: |
| S1 | CONTINUE | APPROACH | 41.33 | 20.72 |
| S2 | CONTINUE | APPROACH | 17.50 | 10.40 |
| S3 | CONTINUE | APPROACH | 13.30 | 9.51 |
| S4 | CONTINUE | APPROACH | 16.63 | 10.37 |
| S5 | CONTINUE | CONTINUE | 5.70 | 3.26 |
| S6 | CONTINUE | APPROACH | 16.65 | 10.56 |
| S7 | CONTINUE | APPROACH | 12.35 | 10.44 |
| S8 | CONTINUE | CONTINUE | 5.24 | 3.49 |
| S9 | CONTINUE | APPROACH | 16.63 | 9.85 |

Median inference over all nine calls: **16.63 → 10.37 s**. Excluding the first call of each sweep: **14.96 → 10.11 s**, a 32% reduction in this run. Valid outputs: 9/9 before, 9/9 after.

**Decision quality remains limited.** In this run v13 selected APPROACH for every state with a detected person and CONTINUE for the two empty states. S1, S4, S6 and S7 differ from the requested scenario actions. S3 selects APPROACH despite already being within conversation range, contrary to the action definition. The S1/S4 explanations also attribute relative closing to human movement without supporting evidence. Valid JSON is not evidence of correct social judgment. This result supports a latency improvement, not an accuracy improvement.

Both sweeps used local `qwen2.5:7b`, temperature 0, seed 42, context 8192 and output limit 192. The manifest records model identity and placement after the first call of each sweep. Raw responses retain prompt-token counts, generated-token counts and load/input/generation durations. Cache residency, model loading and fixed sweep order affect timing; this is one local comparison, not a general performance benchmark. Shorter instructions do not resolve unavailable GPU acceleration.

## Model explanations

- **S1:** Relative distance is decreasing, indicating the person may approach the robot.
- **S2:** Person's distance is decreasing and within approachable range.
- **S3:** Person within interaction range and sustained gaze.
- **S4:** Relative distance is decreasing, indicating the person is approaching the robot.
- **S5:** No people observed and robot is moving.
- **S6:** Person within approachable distance but not looking at the robot.
- **S7:** Person within approachable distance and intermittent gaze.
- **S8:** No people observed and robot is moving.
- **S9:** Person within approachable distance and sustained gaze.

## Reproduce

From the repository root, using a new output directory:

```bash
python3 -m scripts.compare_social_prompts \
  --source-run var/evaluation/final-scenario-tuning/run-02 \
  --baseline-prompt var/evaluation/llm-prompt-v13/prompt-v12.txt \
  --output var/evaluation/llm-prompt-v13/new-comparison
```

For a real-time replay with v13, use `scripts.replay_first_action` and `config/policy-comparison-final.json` as described in [the preset guide](../../../docs/final-scenario-tuning.md). The original v12 live results in `final-scenario-tuning/run-02` remain unchanged.

Verification: 131 prompt, replay, transport and live-model integration tests passed. See [test log](tests.log). No hardware configuration or physical action code was changed.
