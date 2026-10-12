# Frozen baseline and inference profile — stages 1 and 2

Frozen commit: `72ccb64462303a103b236e105b14291f9adb67bf` on `fix/LLM-refinement`. All implementation/configuration and frozen-file hashes were verified after measurement.

The first eligible decision and time to its complete output are the operational result. The full current SocialState, prompt v14, qwen2.5:7b, temperature 0, seed 42, 8192 context and 192 output tokens were preserved. No production files or policy thresholds were changed.

Three warm-model repetitions produced 27/27 valid first outputs, 18/27 aligned outputs and 5/27 accepted first outputs. The frozen rule decisions align in 9/9 scenarios.

This counts action agreement, not execution correctness. Acceptance is separate and can fail after a finite clip ends. S3 and S8 retain multiple allowed actions; S8 first resolves from an empty scene.

Median first-eligible-to-observed-output time: **6.21 s**; range **1.80–12.32 s**.

## Primary first-decision results

| Scenario | Expected action(s) | First eligible after motion | First output by repetition | Eligible → output, repetitions 1 / 2 / 3 | Motion → output, repetitions 1 / 2 / 3 | Accepted, repetitions 1 / 2 / 3 |
| --- | --- | ---: | --- | --- | --- | --- |
| S1 | CONTINUE | 1.18s | APPROACH / APPROACH / APPROACH | 12.32s / 6.15s / 6.70s | 13.50s / 7.33s / 7.88s | No / No / No |
| S2 | APPROACH | 1.10s | APPROACH / APPROACH / APPROACH | 6.34s / 6.70s / 6.75s | 7.44s / 7.80s / 7.86s | No / No / No |
| S3 | ENGAGE, APPROACH | 1.21s | ENGAGE / ENGAGE / ENGAGE | 5.53s / 5.88s / 6.24s | 6.74s / 7.09s / 7.45s | No / No / No |
| S4 | YIELD | 1.10s | APPROACH / APPROACH / APPROACH | 6.27s / 6.49s / 6.42s | 7.37s / 7.60s / 7.52s | No / No / No |
| S5 | CONTINUE | 1.17s | CONTINUE / CONTINUE / CONTINUE | 2.01s / 1.88s / 2.24s | 3.17s / 3.05s / 3.40s | Yes / Yes / Yes |
| S6 | CONTINUE | 1.11s | CONTINUE / CONTINUE / CONTINUE | 6.30s / 5.62s / 5.75s | 7.41s / 6.73s / 6.86s | No / No / No |
| S7 | CONTINUE | 1.71s | CONTINUE / CONTINUE / CONTINUE | 8.60s / 6.21s / 6.07s | 10.30s / 7.92s / 7.77s | No / Yes / Yes |
| S8 | APPROACH, ENGAGE, CONTINUE | 1.17s | CONTINUE / CONTINUE / CONTINUE | 2.49s / 1.80s / 2.10s | 3.66s / 2.98s / 3.28s | No / No / No |
| S9 | APPROACH | 1.14s | CONTINUE / CONTINUE / CONTINUE | 6.74s / 5.95s / 6.32s | 7.88s / 7.09s / 7.46s | No / No / No |

## Where warm inference time goes

These percentages use sums over all primary trials, rather than averages of individual percentages. Durations are reported by Ollama; prompt processing includes uncached tokens. Model warmth and prompt-cache warmth are different conditions.

| Component | Sum across primary trials | Share of Ollama total |
| --- | ---: | ---: |
| Model loading | 0.13s | 0.1% |
| Prompt processing | 115.73s | 77.4% |
| Answer generation | 33.09s | 22.1% |
| Other native runtime work | — | 0.4% |

Median input count: 1766 tokens; median output count: 20 tokens.

Outside Ollama: median pre-inference processing/scheduling was 0.030s; request preparation 0.000598s; response validation 0.000313s; and validation completion to observed output 0.017s. The median first-ready observation endpoint took 0.003s (overlapping part of processing/scheduling; do not add these twice).

| Scenario | Input tokens, repetitions 1 / 2 / 3 | Cached tokens, repetitions 1 / 2 / 3 | Mean load | Mean prompt | Mean generation | Mean transport minus native total |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| S1 | 1807 / 1807 / 1807 | 0 / 918 / 918 | 0.008s | 6.922s | 1.255s | 0.064s |
| S2 | 1827 / 1827 / 1827 | 918 / 918 / 918 | 0.025s | 5.058s | 1.390s | 0.014s |
| S3 | 1766 / 1766 / 1766 | 939 / 939 / 939 | 0.003s | 4.544s | 1.265s | 0.003s |
| S4 | 1797 / 1797 / 1797 | 918 / 918 / 918 | 0.001s | 5.075s | 1.241s | 0.003s |
| S5 | 1094 / 1094 / 1094 | 915 / 915 / 915 | 0.001s | 1.125s | 0.851s | 0.002s |
| S6 | 1720 / 1720 / 1720 | 915 / 915 / 915 | 0.001s | 4.513s | 1.299s | 0.003s |
| S7 | 1835 / 1835 / 1835 | 918 / 918 / 918 | 0.002s | 5.367s | 1.435s | 0.004s |
| S8 | 1100 / 1100 / 1100 | 915 / 915 / 915 | 0.001s | 1.115s | 0.896s | 0.003s |
| S9 | 1749 / 1749 / 1749 | 915 / 915 / 915 | 0.002s | 4.858s | 1.400s | 0.003s |

## Cold-loading probe

The model was explicitly unloaded before a separate call on the frozen first S1 state. This is excluded from operational alignment and live acceptance.

| Load | Prompt | Generation | Ollama total |
| ---: | ---: | ---: | ---: |
| 3.17s | 9.99s | 1.22s | 14.41s |

## Later encounter diagnosis

These are the frozen last eligible snapshots, evaluated only after all primary runs. They are not replacements for first decisions and do not enter the primary score.

| Scenario | Later snapshot after motion | Rule action | LLM action | LLM reason |
| --- | ---: | --- | --- | --- |
| S1 | 10.47s | CONTINUE | CONTINUE | No people observed and robot is moving. |
| S2 | 4.71s | APPROACH | CONTINUE | Person is beyond conversation distance and looking at the robot. |
| S3 | 4.70s | ENGAGE | ENGAGE | Sustained looking at robot within conversation distance. |
| S4 | 6.00s | CONTINUE | APPROACH | Person within conversation distance, getting closer. |
| S5 | 6.87s | CONTINUE | CONTINUE | No people observed. |
| S6 | 4.62s | CONTINUE | APPROACH | Person within conversation distance, intermittent looking at robot. |
| S7 | 8.82s | CONTINUE | ENGAGE | Person within conversation distance, looking at robot. |
| S8 | 7.16s | CONTINUE | CONTINUE | Person is beyond conversation distance and not looking at the robot. |
| S9 | 3.46s | APPROACH | CONTINUE | Person is beyond conversation distance and looking at the robot. |

## Interpretation and next experiment

Prompt processing accounted for 77.4% of warm Ollama time. This supports testing a compact shared decision state next, while preserving the tuned rules and comparing the same first snapshots. It does not yet measure the benefit of that reduction.

A warmed model can still have uncached prompt tokens; the initially uncached S1 prompt and later partially cached prompts should not be conflated with loading delay. Repetition order and cache counts are preserved for future comparisons.

The wrapper observes unmodified requests/responses. Its small audit-file writing cost occurs after validation and is included in receiver-observed output timing. Pre-inference delay combines replay dispatch, processing, scheduling and prompt construction; transport residual includes local overhead and is not a LAN benchmark. Full timing records remain available for inspection.

- [Frozen protocol](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/freeze/protocol.json)
- [Freeze manifest](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/freeze/manifest.json)
- [Runtime and model identity](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/freeze/runtime.json)
- [Primary results and detailed timing](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/measurements/operational-summary.json)
- [Aggregate metrics](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/measurements/aggregate.json)
- [Later diagnostics](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/measurements/later-summary.json)
- [Verification](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/measurements/verification.json)

- [Identical policy-input verification](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/measurements/paired-input-verification.json)

Each repetition/scenario retains original endpoint events, model outputs and observations, plus the exact profiled request/response and native metrics. The source copy and profiler are preserved under freeze.

Reproduction and method are documented in [the profiling guide](/Users/eddiekim/Documents/Projects/P4P/docs/llm-baseline-profiling.md).
