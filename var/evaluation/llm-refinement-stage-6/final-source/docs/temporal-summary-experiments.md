# Stage 6: small causal temporal summaries

Stage 6 tests whether a short description combining existing temporal evidence
helps the LLM interpret an encounter. It does not change the tuned rules,
thresholds, tracking, estimator, v16 prompt, model options or acceptance checks.
Both policies receive the same optional addition to DecisionState.

## Independent variants

| Mode | Exact description | Evidence required |
| --- | --- | --- |
| `LOOKING_CHANGE` | Some measured looking occurred earlier in the recent window; the latest gaze sample is not looking at the robot. | Valid gaze history, known current score/classification, positive looking duration and latest-looking false. |
| `REPEATED_LOOKING` | Multiple measured looking episodes with measured non-looking between them; individual episode durations are not established. | Valid gaze history, known current score/classification and at least two measured looking bouts. |
| `DETECTION_RESUMED` | This track is detected again after missing detections; its gaze during the detection gap is unknown. | An exact current REACQUIRED event matching the observed person's UID, track epoch and current robot timestamp. |

The variants are tested separately, never combined in this experiment.
No statement establishes interest, willingness, looking away during missing
detections, a physical person returning, individual motion or a required action.
Repeated episodes are not described as short: existing aggregate duration and
counts do not establish the length of every episode.

Detection resumption is a **current-update event**, not a new persistent
"recently returned" state. It expires at the next update without a qualifying
event. Nose-based UID reassociation is not a detection gap. New acquisition,
other tracks/epochs and past/future events do not qualify. No arbitrary recency
threshold or new estimator memory is introduced.

## Shared projection and versions

`prepare_decision_input` resolves one shared `TEMPORAL_SUMMARY_MODE`. The default
is `NONE`; the evaluator temporarily selects each variant for both production
rules and LLM input construction throughout that arm's replay. Callers can also
project a SocialState with an explicit `temporal_summary_mode` for offline work.
An already projected DecisionInput retains its frozen representation.

When applicable, an observed person gains `temporal_summary`, with `version: 1`,
`kind` and its fixed English `description`. The state is marked schema version 4
when this extension is present. If there is no applicable summary, the whole
field is omitted and the exact v3 payload remains, including its version.
There is no new UNKNOWN placeholder, negative claim or redundant empty object.

Summary prose is recomputed from its kind during validation. Gaze summaries
that contradict unavailable/changed compact evidence are cleared. Detection
resumption is derived at projection time from the validated full SocialState's
track event. The full SocialState and base DecisionState measurements are not
modified. The original v7 rules can access these fields but do not use them to
change their conditions, reasons, priorities or targets.

## Evaluation

The sealed stage 5 v16 measured control is reused. Its instructions and user
prefix are byte-identical, and all base inputs must match before summaries are
added. Each variant is checked against the actual frozen v7 rules on all 574
frames, with the original 402 eligible frames. Existing fields and rule output
are compared exactly; new summaries are derived without future observations.

Primary scoring still uses the original first eligible decision, with equal
weight for the nine scenarios and the original allowed-action sets. Every
variant that changes a first input receives three warm S1–S9 operational
repetitions plus the nine separately frozen later diagnostics. If a variant
changes none of the first inputs, it receives no new operational score or
latency claim; its later diagnostics remain excluded from primary scoring.

The frozen first inputs activate looking change in S4/S6/S7 and detection
resumption in S7. Repeated looking activates only later, in S4/S6/S7. Coverage
across all 574 frames is recorded separately, so a summary's absence at the
first decision cannot be mistaken for lack of the cue throughout the encounter.

Actual request messages and transport bytes are verified. The production rule
input, response DecisionState and model payload hashes must match. Runtime/model
digest, generation options, first/later timestamps and receiver limits remain
fixed. Reused-control/sequential timing is not a randomized latency estimate.
No physical robot execution is performed.

The predeclared selection rule enables a summary only if primary action
alignment strictly improves without reducing valid first outputs. A tie keeps
the smaller baseline. Timing, acceptance and explanation fidelity are separate
results; later decisions never substitute for the first output. These recordings
are still a tuning set.

Run a new experiment directory with the installed frozen Ollama model served:

```sh
.venv/bin/python -B -m scripts.evaluate_temporal_summaries \
  --output var/evaluation/temporal-summaries-new
```

`--prepare-only` verifies rules, base-state equivalence and causal coverage
without model calls. Use a different new directory for a measured run.

Results: [stage 6 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-6/REPORT.md).
