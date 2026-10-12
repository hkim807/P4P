# Stages 7 and 8: inference speed and alternative models

**Recommendation: retain Qwen 2.5 7B, context 8192 and output ceiling 192.** None of the tested changes meets the predeclared quality/speed criteria. Smaller context gives a small timing change; smaller/newer models produce more first-action errors.

DecisionState v3, v16 instructions and all tuned rules remain fixed. The original first eligible decision is scored in three operational repetitions per arm; the nine later observations are diagnostic only. Both policies receive identical state, and all 574 frames retain original rule decisions/readiness (402 eligible). No physical robot actions are executed.

## Stage 7: inference settings

The setting screens are direct first-snapshot calls, not operational scores. Fresh controls are measured, full messages and prompt token counts are preserved, and at least 256 tokens of context headroom are required. Operational arm order alternates by repetition. A setting must preserve every scenario’s alignment and valid untruncated outputs, improve median time by at least 10%, and avoid a worse 95th percentile.

| Arm | Alignment | Valid | Median complete output | 95th percentile | Accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| control | 24/27 | 27/27 | 3.13s | 5.84s | 21/27 |
| context-2048 | 24/27 | 27/27 | 3.07s | 5.91s | 21/27 |

Selected inference setting: **control**. These figures include receiver scheduling and output observation, not just native model generation.

Context 2048 lowers the operational median by 1.7%, while the 95th percentile is 1.2% slower. Ollama reports loaded memory of approximately 4.62 GB versus 4.98 GB at context 8192, both entirely on GPU. This is a memory saving, not evidence of a useful speed improvement under the selection criterion.

### Direct settings screens — not operational scores

| Setting | Valid first-snapshot responses | Median direct client time |
| --- | ---: | ---: |
| control | 9/9 | 3.14s |
| context-2048 | 9/9 | 2.99s |
| output-64 | 9/9 | 3.06s |
| context-2048-output-64 | 9/9 | 3.00s |

Every screen retains the same eight aligned actions and the same prompt-token counts. Context 2048 has the fastest screen median and is the candidate chosen for operational confirmation. Reducing the output ceiling gives no useful benefit: the control already ends its answers well below 64 tokens. The combined change is also not faster than context reduction alone in this screen.

## Stage 8: model comparison

All measured models use **control** settings. Model order rotates between repetitions, with one model loaded at a time and its weights warmed before each block. The Qwen 2.5 7B control is measured freshly again. The same application messages, structured-output schema, seed and temperature are used; model-specific templates, weights, tokenizers and quantization are recorded.

| Model | First alignment by repetition | Valid | Median complete output | 95th percentile | Accepted | Aligned and accepted |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| qwen25-7b | 8/9 / 8/9 / 8/9 | 27/27 | 3.20s | 6.34s | 20/27 | 17/27 |
| qwen25-3b | 4/9 / 4/9 / 4/9 | 27/27 | 1.81s | 3.12s | 22/27 | 12/27 |
| qwen3-4b-instruct | 7/9 / 7/9 / 7/9 | 27/27 | 2.49s | 4.46s | 24/27 | 18/27 |
| gemma3-4b | 5/9 / 5/9 / 5/9 | 27/27 | 2.96s | 4.08s | 21/27 | 10/27 |

Selected model under the predeclared quality/speed criterion: **qwen25-7b**.

## First actions

| Scenario | Allowed actions | Qwen 2.5 7B | Qwen 2.5 3B | Qwen 3 4B Instruct | Gemma 3 4B |
| --- | --- | --- | --- | --- | --- |
| S1 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | YIELD / YIELD / YIELD | APPROACH / APPROACH / APPROACH | CONTINUE / CONTINUE / CONTINUE |
| S2 | APPROACH | APPROACH / APPROACH / APPROACH | YIELD / YIELD / YIELD | APPROACH / APPROACH / APPROACH | CONTINUE / CONTINUE / CONTINUE |
| S3 | ENGAGE, APPROACH | ENGAGE / ENGAGE / ENGAGE | ENGAGE / ENGAGE / ENGAGE | APPROACH / APPROACH / APPROACH | CONTINUE / CONTINUE / CONTINUE |
| S4 | YIELD | CONTINUE / CONTINUE / CONTINUE | YIELD / YIELD / YIELD | APPROACH / APPROACH / APPROACH | CONTINUE / CONTINUE / CONTINUE |
| S5 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE |
| S6 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | YIELD / YIELD / YIELD | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE |
| S7 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | YIELD / YIELD / YIELD | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE |
| S8 | APPROACH, ENGAGE, CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE | CONTINUE / CONTINUE / CONTINUE |
| S9 | APPROACH | APPROACH / APPROACH / APPROACH | YIELD / YIELD / YIELD | APPROACH / APPROACH / APPROACH | CONTINUE / CONTINUE / CONTINUE |

## Timing components

Medians below are native telemetry for the first requests. Components have independent medians and should not be added to reconstruct a median total. Prompt cache counts and token counts are retained per call.

| Model | Prompt evaluation | Generation | Model load | Prompt tokens | Generated tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| qwen25-7b | 1.88s | 1.27s | 0.003s | 917 | 21 |
| qwen25-3b | 0.84s | 0.91s | 0.002s | 917 | 32 |
| qwen3-4b-instruct | 1.07s | 1.28s | 0.002s | 917 | 33 |
| gemma3-4b | 1.78s | 1.03s | 0.003s | 985 | 27 |

## First explanations — distinct answers

### qwen25-7b

- S1: **CONTINUE** — Person outside interaction range, no sustained looking.
- S2: **APPROACH** — Person within social range, sustained gaze at the robot.
- S3: **ENGAGE** — Sustained gaze and proximity within interaction range.
- S4: **CONTINUE** — Person outside interaction range, decreasing distance but no sustained looking.
- S5: **CONTINUE** — No people observed and robot is moving.
- S6: **CONTINUE** — No recent looking and low gaze fraction outside interaction range.
- S7: **CONTINUE** — Intermittent looking but no sustained gaze or closing distance.
- S8: **CONTINUE** — No people observed and robot is moving.
- S9: **APPROACH** — Person detected at approachable distance with sustained gaze.
### qwen25-3b

- S1: **YIELD** — Person is looking away and decreasing distance, suggesting obstruction; robot needs space to continue.
- S2: **YIELD** — Person is looking at the robot and closing the distance, indicating a potential obstruction.
- S3: **ENGAGE** — Person is looking at the robot within conversation distance and has sustained gaze, indicating interest in interaction.
- S4: **YIELD** — Person is looking away and robot-person distance is decreasing, suggesting obstruction without clear interest or permission.
- S5: **CONTINUE** — No person observed; robot is moving, no immediate interaction needed.
- S6: **YIELD** — Person detected within corridor, looking away; robot needs space to pass.
- S7: **YIELD** — Person is within conversation distance and intermittently looking, suggesting a potential interaction request.
- S8: **CONTINUE** — No person observed; robot is moving, no immediate interaction needed.
- S9: **YIELD** — Person is looking at the robot from a distance, suggesting a need for space without approaching interaction.
### qwen3-4b-instruct

- S1: **APPROACH** — A face is detected with decreasing distance and low gaze, indicating a person approaching within social range.
- S2: **APPROACH** — A face is detected with sustained gaze and decreasing distance, indicating clear interest and approachability.
- S3: **APPROACH** — A person is within interaction range, steadily looking at the robot, and in the forward corridor, indicating a clear opportunity for engagement.
- S4: **APPROACH** — A face is detected with decreasing distance and low gaze, indicating a person approaching within social range.
- S5: **CONTINUE** — No observed people or gaze, and no detected interaction target or spatial need.
- S6: **CONTINUE** — No sustained gaze or proximity within interaction range; face detected but not looking, and distance trend is unavailable.
- S7: **CONTINUE** — Intermittent gaze and no sustained attention indicate no interest; person is beyond conversation distance and position is unknown.
- S8: **CONTINUE** — No observed people or gaze, and no interaction target detected in the environment.
- S9: **APPROACH** — A face is detected with sustained gaze and high looking fraction, beyond conversation distance but within social range, indicating interest and need for interaction.
### gemma3-4b

- S1: **CONTINUE** — The person is beyond conversation distance and shows no sustained gaze, justifying continued route progress.
- S2: **CONTINUE** — The person is observed with sustained gaze and decreasing distance, but remains outside interaction range.
- S3: **CONTINUE** — The robot is within interaction range with sustained gaze and a steady distance, requiring continued route progress.
- S4: **CONTINUE** — The person is beyond conversation distance and shows little to no looking at the robot.
- S5: **CONTINUE** — No people observed and the robot is currently moving, so it should continue its route.
- S6: **CONTINUE** — The person is beyond conversation distance and shows little to no looking at the robot.
- S7: **CONTINUE** — The person is within the approachable zone with intermittent looking, but distance is too great to initiate interaction.
- S8: **CONTINUE** — No people observed and the robot is currently moving, so it should continue its route.
- S9: **CONTINUE** — The person is within the approachable zone with sustained gaze, but beyond interaction range.

## Later observations — excluded from primary score

| Scenario | Qwen 2.5 7B | Qwen 2.5 3B | Qwen 3 4B Instruct | Gemma 3 4B |
| --- | --- | --- | --- | --- |
| S1 | CONTINUE | CONTINUE | CONTINUE | CONTINUE |
| S2 | APPROACH | YIELD | APPROACH | CONTINUE |
| S3 | ENGAGE | ENGAGE | ENGAGE | CONTINUE |
| S4 | CONTINUE | YIELD | YIELD | CONTINUE |
| S5 | CONTINUE | CONTINUE | CONTINUE | CONTINUE |
| S6 | CONTINUE | YIELD | CONTINUE | CONTINUE |
| S7 | APPROACH | YIELD | APPROACH | CONTINUE |
| S8 | CONTINUE | YIELD | CONTINUE | CONTINUE |
| S9 | APPROACH | YIELD | APPROACH | CONTINUE |

## Controls and limits

There are 252 new decision calls: 36 direct screens, 54 stage 7 first trials plus 18 later probes, and 108 stage 8 first trials plus 36 later probes. Warmup requests are separate and never scored. All 252 decisions pass the existing strict parser; schema validity does not establish action alignment or faithful explanations.

Every actual request is checked against frozen messages, settings, structured-output schema and transport hashes. First production rule/LLM state hashes match. Model digests and Ollama version are checked before every block; other models are unloaded. Production source hashes and historical artifact seals are rechecked. Invalid first outputs remain failures even if a later retry succeeds.

The initial Gemma download failed at a storage-host DNS lookup; its retry succeeded. Download activity overlapped the screens and early stage 7 blocks. Later stage 7 blocks and the entire stage 8 comparison ran after completion. The already small setting-time difference should not be treated as a causal speed gain.

The common protocol retains the original freeze’s unused settings.prompt_version metadata. Actual messages, top-level prompt_version and model audits use decision-state-llm-v16; every request is verified against the selected stage 5 instructions. The recommended settings record uses the current v16 metadata.

Trials run on an Apple M3 with 16 GB memory. This is a small local comparison with rotation/alternation at block level, not a randomized independent statistical experiment. Deterministic repeats and these nine tuning recordings do not establish held-out accuracy. Different model templates and tokenizers are part of the model comparison.

Acceptance is separate from action alignment. A finite recording can end before an otherwise valid action is delivered, or current readiness can change. These rates do not measure physical robot execution. S8 retains its broad allowed-action set and its first decision occurs in the empty scene. Later observations cannot repair a first-action mistake.

Warm measurements assume the model has already been loaded. Preload before the encounter using the same context setting, and keep only the intended model resident. The experiment does not alter Ollama app settings or run models in the cloud.

- [Setting screens](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/stage7/screen-summary.json)
- [Stage 7 comparison](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/stage7/comparison.json)
- [Stage 8 comparison](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/stage8/comparison.json)
- [Frozen protocol](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/common/protocol.json)

## Judgement review and final checks

The selected Qwen 2.5 7B still misses S4. Its first reason prioritises absent sustained looking and conversation range, despite measured IN_WAY corridor position and DECREASING relative separation. In S6 it says no recent looking despite 0.170852 seconds of measured looking. In S7 it says no closing distance despite an unavailable distance trend.

Qwen 2.5 3B fixes S4 with YIELD, but also wrongly yields in S1/S2/S6/S7/S9. S6's reason says within corridor although the state says TO_SIDE. S7's reason says within conversation distance although the state says APPROACHABLE at 2.4046 metres. Looking-away, interest and interaction-request claims exceed the measured cues.

Qwen 3 4B Instruct wrongly approaches S1/S4. Both reasons infer a person approaching from face detection and decreasing relative separation; neither establishes individual motion or interest. S7's action is aligned but its reason claims no interest and unknown position despite measured TO_SIDE. Several correct interaction actions also claim interest as an established fact. S3's APPROACH is aligned under the unchanged allowed set APPROACH/ENGAGE, even though the frozen rule chooses ENGAGE.

Gemma 3 4B chooses CONTINUE for every first scenario. It misses S2/S3/S4/S9; even S3 with interaction-range proximity and sustained gaze remains CONTINUE. These are behavioural errors, not parsing or freshness failures.

All statements describe the fixed nine tuning recordings. Later outputs and acceptance counts cannot repair these first-action mistakes. No model output was physically executed, and no semantic validator was added.

All six new focused checks pass. The full suite ran 679 test methods with only the existing VLM/LLM wording method failing four subcases; no new failures or errors. The final verification and raw test logs are preserved.

- [Verification](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/final-verification.json)
- [Retained settings](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/recommended-settings.json)
