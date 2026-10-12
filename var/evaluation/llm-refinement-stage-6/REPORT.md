# Stage 6: additional temporal summaries

Three causal additions were tested independently with DecisionState base evidence, v16 instructions, rules, estimator thresholds, model/runtime and generation/acceptance settings fixed. Both policies receive the same enriched state. Only a positive measured summary is added; inactive summaries produce byte-identical v3 inputs. v4 identifies inputs containing the optional temporal extension.

## Protocol

The sealed stage 5 v16 control is reused and its actual requests/state hashes verified. Original first eligible timestamps and separate later diagnostic timestamps remain frozen. A changed first-input arm receives three warm S1–S9 operational repetitions plus nine later probes. If all nine first inputs are identical to control, no new primary trials are run; later probes cannot substitute for a primary score. Expected-action sets, including broad S3/S8 sets, are unchanged.

All 574 frames in every mode preserve readiness and complete frozen v7 decisions, reasons, targets and IDs; 402 remain eligible. Existing non-summary state fields and full SocialState snapshots remain unchanged. Every actual model request is checked against frozen messages and input hashes. No physical actions are executed.

## Additions

- **LOOKING_CHANGE**: Some measured looking occurred earlier in the recent window; the latest gaze sample is not looking at the robot.
- **REPEATED_LOOKING**: Multiple measured looking episodes with measured non-looking between them; individual episode durations are not established.
- **DETECTION_RESUMED**: This track is detected again after missing detections; its gaze during the detection gap is unknown.

## First-decision results

| Variant | Active first inputs | Alignment | Median complete output | Median prompt tokens | Accepted |
| --- | --- | ---: | ---: | ---: | ---: |
| Control, no summary | — | 24/27 | 3.04s | 917 | 22/27 |
| LOOKING_CHANGE | S4, S6, S7 | 24/27 | 3.05s | 918 | 22/27 |
| REPEATED_LOOKING | None | Not run | Not measured | Identical inputs | Not measured |
| DETECTION_RESUMED | S7 | 24/27 | 3.06s | 917 | 24/27 |

Selection under the predeclared action-alignment rule: **NONE**. A summary must strictly improve primary alignment without losing valid outputs to be enabled; ties retain the smaller baseline. Latency, acceptance and explanations are reported separately. Sequential/reused-control timing is not a randomized causal latency estimate.

Every first action remains identical to control: S4 still chooses CONTINUE rather than the expected YIELD. Both new operational arms return 27/27 valid outputs. Aligned-and-accepted counts are 19/27 for looking change and 21/27 for detection resumption, versus 19/27 for control. Acceptance does not establish action quality or hardware execution.

There were **81 new model calls**: 54 first-decision calls and 27 later diagnostics. The control's 36 calls are reused historical measurements, not new trials. Repeated looking has no new first-decision score because all nine first inputs are identical to control.

The overall median token count hides the cost at affected first inputs:

| Variant and first input | Control tokens → variant tokens | Control median time → variant median time |
| --- | --- | --- |
| Looking change, S4 | 927 → 964 | 3.16s → 3.28s |
| Looking change, S6 | 917 → 954 | 3.12s → 3.31s |
| Looking change, S7 | 918 → 955 | 3.34s → 3.28s |
| Detection resumed, S7 | 918 → 954 | 3.34s → 3.29s |

## First explanations — distinct answers

### LOOKING_CHANGE

- S1: **CONTINUE** — Person outside interaction range, no sustained looking.
- S2: **APPROACH** — Person within social range, sustained gaze at the robot.
- S3: **ENGAGE** — Sustained gaze and proximity within interaction range.
- S4: **CONTINUE** — Decreasing distance without sustained looking or spatial need.
- S5: **CONTINUE** — No people observed and robot is moving.
- S6: **CONTINUE** — No recent looking and low gaze fraction outside interaction range.
- S7: **CONTINUE** — Intermittent looking outside conversation distance
- S8: **CONTINUE** — No people observed and robot is moving.
- S9: **APPROACH** — Person detected at approachable distance with sustained gaze.
### DETECTION_RESUMED

- S1: **CONTINUE** — Person outside interaction range, no sustained looking.
- S2: **APPROACH** — Person within social range, sustained gaze at the robot.
- S3: **ENGAGE** — Sustained gaze and proximity within interaction range.
- S4: **CONTINUE** — Person outside interaction range, decreasing distance but no sustained looking.
- S5: **CONTINUE** — No people observed and robot is moving.
- S6: **CONTINUE** — No recent looking and low gaze fraction outside interaction range.
- S7: **CONTINUE** — Intermittent looking outside conversation distance
- S8: **CONTINUE** — No people observed and robot is moving.
- S9: **APPROACH** — Person detected at approachable distance with sustained gaze.

## Later diagnostics — excluded from primary score

Repeated looking changes S7 from APPROACH to ENGAGE only at the later probe. Both differ from the tuned rule's later CONTINUE. This observation neither improves the first-decision score nor supplies an independently labelled later ground truth.

| Scenario | Control | Looking change | Repeated looks | Detection resumed |
| --- | --- | --- | --- | --- |
| S1 | CONTINUE | CONTINUE | CONTINUE | CONTINUE |
| S2 | APPROACH | APPROACH | APPROACH | APPROACH |
| S3 | ENGAGE | ENGAGE | ENGAGE | ENGAGE |
| S4 | CONTINUE | CONTINUE | CONTINUE | CONTINUE |
| S5 | CONTINUE | CONTINUE | CONTINUE | CONTINUE |
| S6 | CONTINUE | CONTINUE | CONTINUE | CONTINUE |
| S7 | APPROACH | APPROACH | ENGAGE | APPROACH |
| S8 | CONTINUE | CONTINUE | CONTINUE | CONTINUE |
| S9 | APPROACH | APPROACH | APPROACH | APPROACH |

## Coverage and limits

- LOOKING_CHANGE: 138 of 574 frames; 136 eligible frames; first points ['S4', 'S6', 'S7']; later points ['S4', 'S6', 'S8'].
- REPEATED_LOOKING: 108 of 574 frames; 106 eligible frames; first points []; later points ['S4', 'S6', 'S7'].
- DETECTION_RESUMED: 5 of 574 frames; 3 eligible frames; first points ['S7']; later points [].

Looking-change summaries require valid history, a known current score/classification, positive measured looking duration and latest not-looking. Repeated looks require at least two measured bouts with valid gaze; no short-duration claim is made. Detection resumption requires an exact current REACQUIRED event for this UID/epoch; REASSOCIATED, new acquisition, other tracks and past/future events do not qualify. Detector loss is not measured looking away or a person leaving/returning.

A REACQUIRED pulse is deliberately limited to the current update; this experiment does not retain a new recent-gap history or introduce a new time threshold. UNKNOWN/unavailable summaries are omitted. The parser does not semantically validate model reasons, so explanations remain subject to qualitative review. Acceptance is not correctness, finite recordings can end before delivery, and S8 first resolves in the empty scene. These are tuning recordings, not held-out results.

S7's first reason no longer claims that an unavailable trend means no closing distance. However, S6 still claims “no recent looking” despite the looking-change summary and positive looking duration. S4 continues to deny a spatial need despite its measured corridor evidence. The additions do not reliably resolve these interpretation errors.

## Final implementation and verification

The default remains `NONE`: no temporal field is sent and production inputs remain byte-identical v3. Experimental positive summaries use state version 4. The production v16 prompt, rule policy, estimator, tracking, configuration and acceptance checks are unchanged.

All 31 final focused checks pass. The full suite ran 673 test methods with no new failures or errors; the existing VLM/LLM verbatim-action-wording method still fails four subcases. Logs and verification records are preserved here.

After measurement, empty-summary omission was changed from a newer Pydantic field option to a wrap serializer that supports the declared Pydantic 2.9 floor. No dependency change was made. All 2,296 canonical payload comparisons (574 frames × four modes) are byte-identical to the measured implementation. Frozen measured source copies remain untouched; the final implementation is recorded separately in `final-source/` and `final-implementation.json`.

The root `results-manifest.json` seals this report, measurements, source snapshots, verification records and test logs. Stages 1–5 retain their original seals.

- [Comparison](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-6/comparison.json)
- [Looking-change inputs/results](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-6/LOOKING_CHANGE/protocol.json)
- [Repeated-looking inputs/results](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-6/REPEATED_LOOKING/protocol.json)
- [Detection-resumption inputs/results](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-6/DETECTION_RESUMED/protocol.json)
