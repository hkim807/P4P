# LLM v14: nine trimmed scenarios

Actual local `qwen2.5:7b` responses: **6/9 match the requested action options**, **9/9 are valid responses**, and **1/9 were accepted by the production trial client**. Median model request time: **11.18 s**. No physical actions were executed.

The motion-trimmed SDK recordings were replayed at recorded speed through the production receiver, shared readiness gate and `SingleTrial`. Prompt v14 and estimator v6 include the shared descriptive labels. The model weights were loaded before trial clocks started; prompt processing and generation are included in inference time. Each input matches the original run-02 first eligible state exactly apart from the added labels and estimator version. Rule actions below are evaluated from those same submitted states.

| Scenario | Requested | Rule | LLM | Inference (s) | Accepted? |
| --- | --- | --- | --- | ---: | --- |
| S1 | CONTINUE | CONTINUE | APPROACH | 18.34 | No |
| S2 | APPROACH | APPROACH | APPROACH | 10.93 | No |
| S3 | ENGAGE / APPROACH | ENGAGE | ENGAGE | 12.29 | No |
| S4 | YIELD | YIELD | APPROACH | 12.27 | No |
| S5 | CONTINUE | CONTINUE | CONTINUE | 3.78 | Yes |
| S6 | CONTINUE | CONTINUE | CONTINUE | 11.42 | No |
| S7 | CONTINUE | CONTINUE | CONTINUE | 10.89 | No |
| S8 | APPROACH / ENGAGE / CONTINUE | CONTINUE | CONTINUE | 3.62 | No |
| S9 | APPROACH | APPROACH | CONTINUE | 11.18 | No |

## Exact model explanations

- **S1 — APPROACH:** Person getting closer beyond conversation distance.
- **S2 — APPROACH:** Person is getting closer and looking at the robot.
- **S3 — ENGAGE:** Sustained looking at robot within conversation distance.
- **S4 — APPROACH:** Person getting closer beyond conversation distance.
- **S5 — CONTINUE:** No people observed.
- **S6 — CONTINUE:** Person beyond conversation distance, no looking at robot.
- **S7 — CONTINUE:** Person's gaze is intermittent, beyond conversation distance.
- **S8 — CONTINUE:** No people observed.
- **S9 — CONTINUE:** Person beyond conversation distance, no sustained looking.

S1 and S4 still choose APPROACH based on closing distance despite low gaze. S4 also has an occupied forward corridor. S9 claims there is no sustained looking even though both the original gaze category and the new attention label explicitly report sustained looking. These failures show that clearer labels do not ensure the model uses the evidence correctly.

Compared with the earlier v13 sweep, S3 now selects ENGAGE, and S6/S7 select CONTINUE. S9 changes from the desired APPROACH to CONTINUE. Requested-option matches increase from 5/9 to 6/9 in these individual runs; this is not a controlled estimate of label effectiveness. Only S5 passes live acceptance. The seven visible-person responses exceed the 10 s source-age limit and arrive after recording end. S8 returns in time but its current observation is no longer eligible.

## Interpretation limits

These nine recordings were used for policy tuning; this is not held-out evaluation. One sample per scenario does not establish reliability. The S3 requested options allow either ENGAGE or APPROACH, although its actual first-state distance is within conversation range. S5 and S8 submit an initially empty state; S8 does not test reasoning about a visible departing person. S6 submits before the later brief glance. No gesture recognition is available for S9.

Valid JSON, matching an expected action and freshness acceptance are separate outcomes. The client retains a 10 s model-source age limit, 1 s latest-observation age limit, current readiness checks and 30 s trial timeout. Recording end can leave the latest observation stale even when inference takes less than 10 s. See per-scenario acceptance details in `results.json`.

This is a real-time replay, whereas the v13 prompt comparison was an offline frozen-state sweep. Cache residency, load and concurrent replay processing affect timings; differences cannot be attributed solely to labels. The saved placement snapshot reports zero GPU memory for this model.

## Evidence and reproduction

[Run manifest](run-01/manifest.json), [raw summary](run-01/summary.json), [combined results](results.json), [model placement](run-01/placement.json), [frozen system prompt](run-01/system-prompt.txt). Each `run-01/llm/S*/models.jsonl` retains the full submitted state, raw model content, parsed decision, model configuration, prompt version and inference duration. The manifest freezes source hashes, model identity and effective configurations.

Run from the repository root with a fresh output directory:

```sh
python3 -m scripts.replay_first_action --settings config/policy-comparison-final.json \
  --output var/evaluation/llm-prompt-v14/new-run --policies llm --warm-model
```

Regenerate this report from the saved run:

```sh
PYTHONPATH=. python3 var/evaluation/llm-prompt-v14/build_report.py
```
