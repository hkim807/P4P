# S1, S4 and S9 failure diagnosis

Four actual local `qwen2.5:7b` calls used the frozen v14 system prompt, identical model settings and the same source observations. No production state, prompt or policy was changed. The reduced inputs retained the existing labels and categories, distance, current gaze classification, gaze fraction, validity, face, position, explicit path/gesture cues and robot motion. Configuration, raw estimator statistics, coordinates, tracking/history events and identifiers were removed. This is a diagnostic ablation, not a fair rule/LLM evaluation condition.

| Input | Original full-state output | Diagnostic output | Model request (s) |
| --- | --- | --- | ---: |
| S9 full | CONTINUE | CONTINUE | 2.59 |
| S1 reduced | APPROACH | APPROACH | 4.26 |
| S4 reduced | APPROACH | CONTINUE | 3.08 |
| S9 reduced | CONTINUE | APPROACH | 3.01 |

## Model explanations

- **S9 full:** Person beyond conversation distance, no sustained looking.
- **S1 reduced:** Person within approachable distance, getting closer.
- **S4 reduced:** Person not looking at robot and beyond conversation distance.
- **S9 reduced:** Sustained looking at robot from beyond conversation distance.

## What the evidence supports

S9 reproduces its contradictory full-state explanation. With the same decisive social cues in the reduced input, it correctly cites sustained gaze and selects APPROACH. This demonstrates sensitivity to input presentation/content. Because several fields were removed together, it does not identify a particular field as the cause. Possible contributors include raw scores versus calibrated categories, configuration thresholds and prior UNKNOWN cue values.

S1 continues to justify APPROACH using approach distance and decreasing separation, despite retained low-attention and beside-corridor labels. This failure persists after reducing the input, so verbosity alone does not explain it. The current prompt supplies general social objectives but leaves the relative weight of invitation, route progress and closing distance to the model.

S4 changes from APPROACH to CONTINUE and now cites low gaze, but still fails to select YIELD. Its corridor label remains IN_ROBOT_FORWARD_CORRIDOR while explicit path_relation remains UNKNOWN; the prompt describes position as an estimate, not a collision prediction. The model does not demonstrate the expected corridor-plus-closing interpretation in either condition.

The input evidence and raw returned responses confirm these are model-output failures, not a missing-label or parsing/fallback problem. JSON schema constrains the action names and output fields, not consistency with social evidence. The repeated full S9 failure completes in 2.59 s, showing that a long response delay is not necessary for this error. Cache/load conditions differ; this is not a latency benchmark.

Exact internal causes are unobservable from these outputs. Each reduced condition has one sample, and this ablation changes several input fields together. More isolated ablations and repetitions would be needed to attribute the failure to individual fields, model capacity or quantization.

[Full requests and raw Ollama responses](results.json) · [Diagnostic design and settings](manifest.json)
