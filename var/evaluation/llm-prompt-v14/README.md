# Shared descriptive SocialState labels

[Actual nine-scenario LLM results](results.md) — local model responses, timings and live-client acceptance from the motion-trimmed recordings.

[Failure diagnosis](failure-diagnostic-01/README.md) — full-input reproduction and reduced-input probes for S1, S4 and S9.

Estimator v6 adds `people[].observation_labels` version 1. Both policies receive the same complete state. Rule conditions, readiness, thresholds and measurements are unchanged. Prompt v14 describes these aliases while leaving the LLM to choose among the four actions.

[Scenario 4 prompt](scenario-4-prompt.md) · [exact messages](scenario-4-messages.json)

`NONE` is described as `LITTLE_OR_NO_LOOKING_AT_ROBOT`, distinct from `UNKNOWN`. `APPROACHABLE` becomes the neutral proximity description `BEYOND_CONVERSATION_DISTANCE`. Current gaze is separate from temporal gaze; corridor position is not a collision prediction; getting closer is robot-relative separation, not proof the human is walking toward the robot.

Missing tracks and invalid evidence do not receive positive descriptions. Existing saved states load without labels and derive them during validation. Supplied stale labels are recomputed. All original fields remain available, with no rule answer or inferred interaction intent added.

The S4 state adds 257 characters of descriptive aliases; the system prompt is 376 words. This change targets interpretation. The follow-up [evaluation](results.md) records actual Ollama outputs; it does not isolate the causal effect of labels from prompt changes or execution conditions. No physical robot actions were run; historical v12/v13 outputs remain historical.

Validation covers unknown/missing cues, conflicting current and historical attention, stale-label replacement, exact rule/LLM input equality, preservation of every original field in all nine saved first-decision states, and replay of all nine trimmed recordings. Rule first actions remain `CONTINUE, APPROACH, ENGAGE, YIELD, CONTINUE, CONTINUE, CONTINUE, CONTINUE, APPROACH`.

All 162 focused state, policy, replay and receiver tests passed (see [test log](tests.log)). The asynchronous receiver test hung under the sandbox; the complete focused suite passed outside it in 3.654 seconds. Model responses in these tests are fakes; no physical robot was used.

Reproduce from the repository root:

```sh
python3 -m unittest tests.test_observation_labels tests.test_social_state tests.test_social_integration tests.test_llm_policy tests.test_llm_replay tests.test_llm_replay_inputs tests.test_final_scenario_tuning tests.test_live_models tests.test_live_model_receiver tests.test_pipeline_refinement tests.test_first_action_replay tests.test_ollama -q
```
