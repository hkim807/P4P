# LLM refinement: stages 1–8 and final selection

Summary as of 13 October 2026. The priorities are accuracy of the first eligible
action and time until its complete output is observed by the pipeline.

The strongest improvement came from compacting the shared state: first-action
alignment increased from 6/9 to 8/9, while median delay fell substantially.
The final supported pipeline retains 8/9 alignment, with a warm-model median
of roughly 3.0–3.2 seconds after the first eligible observation. None of the
later experiments fixes S4 without introducing other mistakes.

## What each stage established

| Stage | Main finding | Selection and results |
| --- | --- | --- |
| 1 — Freeze evaluation | Locked the nine recordings, expected actions and first eligible timestamps. Later snapshots are separate diagnostics and cannot repair a wrong first decision. | Keep this scoring contract. [Freeze and baseline report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/REPORT.md). |
| 2 — Measure timing | Original LLM scores 6/9 in every repetition at median 6.21s. Prompt processing consumes about 77% of native warm inference time; full inputs have median 1,766 tokens. | Reduce input before changing models or acceptance limits. Timing is in the same stages 1/2 report. |
| 3 — Compact shared DecisionState | Removes configuration/provenance, raw geometry and unused diagnostics from model text while retaining every supported rule operand. Alignment rises to 8/9; median input falls to 794 tokens and output delay to 2.76s. | Retain compact projection. It is the largest measured gain. [Stage 3 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/REPORT.md). |
| 4 — Semantic descriptions | Explains current gaze, gaze history, proximity, corridor position and relative separation without adding intent. Alignment remains 8/9, while median delay rises to 3.72s in this run. | Meaning is clearer, but no accuracy gain is demonstrated. The final supported v3 retains these descriptions after cue cleanup. [Stage 4 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/REPORT.md). |
| 5 — Refine instructions | Selected v16 clarifies evidence validity, temporal gaze, configured range, relative movement and giving space. Alignment stays 8/9; median delay is 3.04s versus a fresh v14/v3 control's 3.31s. More aggressive variants introduce other errors. | Retain v16; no further action-accuracy improvement is established. [Stage 5 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/REPORT.md). |
| 6 — Temporal summaries | Earlier looking/latest not-looking and detection resumption retain 8/9 at 3.05–3.06s. Repeated looking changes none of the frozen first inputs. | Keep additional summaries disabled. Later changes do not improve first-action scoring. [Stage 6 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-6/REPORT.md). |
| 7 — Inference settings | Context 2048 preserves 8/9 but changes median delay only from 3.13 to 3.07s; the 95th percentile worsens slightly. Output ceiling 64 and the combined setting provide no useful screen gain. | Retain context 8192 and output ceiling 192. [Stages 7/8 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/REPORT.md). |
| 8 — Alternative models | Smaller/newer models answer faster but make more first-action mistakes. Qwen 2.5 7B remains best of the tested models. | Retain Qwen 2.5 7B. Model results and every action/reason are in the same stages 7/8 report. |

An additional cleanup between stages 4 and 5 removed measured pass gestures
and path relations from the pipeline because Navel supplies neither. Derived
nose-based corridor position remains. Supported-input rule behaviour is
preserved on all 574 frames. See the [cue-removal report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/navel-cue-removal-20261013/REPORT.md).

## Model comparison on the final shared state and instructions

| Model | First-action alignment in every repetition | Median complete output | 95th percentile |
| --- | ---: | ---: | ---: |
| Qwen 2.5 7B | 8/9 | 3.20s | 6.34s |
| Qwen 3 4B Instruct | 7/9 | 2.49s | 4.46s |
| Gemma 3 4B | 5/9 | 2.96s | 4.08s |
| Qwen 2.5 3B | 4/9 | 1.81s | 3.12s |

Each arm has three repetitions; the 7B result is 24 aligned first actions out
of 27, covering eight of the same nine scenarios in each run. All first outputs
pass strict JSON validation. Valid JSON does not establish a correct action or
a faithful explanation.

## Final pipeline for live LLM trials

Navel observations pass through the existing receiver, tracking and temporal
estimator into full SocialState. Full diagnostics are retained. A deterministic
projection produces shared compact DecisionState v3 for both the tuned rules
and the LLM. The LLM receives the compact JSON with v16 instructions and returns
one `{action, reason}` object. Existing correlation, readiness and freshness
checks decide whether its result can be accepted by the live client.

| Component | Retained selection |
| --- | --- |
| Shared evidence | DecisionState v3 with current semantic descriptions; exact numeric rule operands retained |
| Additional temporal summaries | `NONE` |
| Unsupported upstream cues | Pass gestures and measured path relations removed |
| Instructions | `decision-state-llm-v16` |
| Model | `qwen2.5:7b` |
| Generation | Temperature 0; seed 42; context 8192; output ceiling 192 |
| Temporal preset | `config/temporal-final-scenarios.json` |
| Tracking preset | `config/person-tracking-final-scenarios.json` |
| Output validation | Existing four-action enum and nonblank reason, with no semantic correction or rule fallback introduced |
| Acceptance | Existing one-second current-observation limit, ten-second model-source limit and readiness checks |

The [retained settings record](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/recommended-settings.json)
contains the exact model/configuration values. The [live setup guide](/Users/eddiekim/Documents/Projects/P4P/docs/inference-settings-and-models.md)
contains receiver and preload commands. Preload with the same context setting
before the encounter and keep the selected model resident. The measurements
already assume warm weights; this reproduces a measurement condition rather
than proving an additional speed gain.

## Accuracy and live timing limits

The remaining action error is S4: the selected LLM chooses CONTINUE where YIELD
is expected. The tuned rule reference scores 9/9. Both policies receive the
same decision evidence, and all 574 replay frames retain the original complete
rule decisions and readiness, with 402 eligible frames.

The latest selected-model median is 3.20s **after the first eligible observation**;
its 95th percentile is 6.34s. In these recordings, evidence first becomes
eligible a median 1.17s after recorded robot motion starts. The measured median
motion-to-complete-output time is 4.31s. These timings include the
local replay delivery path, not physical robot networking or action execution.

The latest selected-model run accepts 20/27 first outputs; 17/27 are both
aligned and accepted. Those finite-replay counts are secondary to first-action
accuracy and timing. A continuing live stream may remain fresh after a clip
would end, but readiness may change during inference. A faster result can still
be wrong; freshness checks do not validate action semantics.

Stage 3 remains the fastest historical 8/9 result at 2.76s. Its older labels
and instructions are not the current supported v3/v16 combination. The final
pipeline should not be described as proven faster than that historical version,
nor as a proven accuracy improvement from richer labels. The final recommendation
is the selected, currently supported and repeatedly measured baseline. No new
rollback or pipeline change is made by this summary.

The nine recordings are a tuning set; repeated deterministic trials do not
establish accuracy on unseen encounters. No physical robot execution was
performed. The existing full-source traces, actual request bytes, model outputs,
timing and validation records remain available in the linked reports.

Detailed change history: [refinement log](/Users/eddiekim/Documents/Projects/P4P/docs/llm-refinement-log.md).
