# LLM refinement experiment log

## Baseline v1 — 12 October 2026: stages 1 and 2

**Objective:** freeze the current evaluation and identify where first-decision
latency comes from before changing SocialState, instructions or models.

**Frozen implementation:** commit `72ccb64462303a103b236e105b14291f9adb67bf`
on `fix/LLM-refinement`. Copies and hashes of code, configuration, recordings,
first/later snapshots and prompts are saved with runtime/model metadata.

**Evaluation:** first eligible observation is the operational input; complete
output timing is measured from both eligibility and robot motion onset.
Equal weight for nine scenarios, with the original allowed-action lists.
Invalid/missing first outputs cannot be replaced by a better later retry.
The last eligible observation before each clip ends is frozen as a separate
diagnostic input and excluded from the primary score.

**Changes made:** added a profiling wrapper, freeze/reproduction commands,
report generation and contract tests. The wrapper records original Ollama
responses and native timing/token metrics. Production policy, preprocessing,
SocialState, prompt, model settings and acceptance checks remain unchanged.

**Measurements:** three warm-model repetitions of all nine operational trials,
nine later diagnostics and one separate cold-load probe. Actual production
rule and LLM input hashes matched in all 27 operational trials; rule outputs
matched the freeze in every trial. The 79 selected regression/profile tests
passed.

**Results:** rule action alignment 9/9; LLM action alignment 6/9 in each
repetition (18/27), with failures at S1, S4 and S9. All 27 first outputs were
valid. Live replay accepted 1/9, 2/9 and 2/9 respectively, reported separately
from alignment. Median first-eligible-to-observed-output time was 6.21 seconds,
range 1.80–12.32 seconds.

**Timing:** prompt processing consumed 77.35% of summed warm Ollama time;
answer generation 22.12%; loading 0.09%; other native work 0.44%. Median input
was 1,766 tokens and output 20 tokens. A warm model does not guarantee a cached
prompt: S1 initially had zero cached tokens and took 10.77 seconds to process
its prompt. The separate cold S1 probe spent 3.17 seconds loading, 9.99 seconds
processing the prompt and 1.22 seconds generating (14.41 seconds native total).

**Conclusion:** this stage establishes a measurement baseline; it introduces
no claimed performance improvement. Prompt processing is the main measured
target for the next compact shared-state experiment. Cold loading and
finite-clip freshness remain distinct issues.

**Next stage:** reduce the shared decision representation while preserving the
tuned rules' outputs, then measure against the frozen first snapshots under
the same warm/order conditions. Do not widen age limits to count that as an
inference-speed improvement.

Artifacts: [full report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/REPORT.md),
[frozen contract](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-1-2/freeze/protocol.json),
[profiling method](/Users/eddiekim/Documents/Projects/P4P/docs/llm-baseline-profiling.md).

## Stage 3 — measured 12 October, documented 13 October 2026

**Objective:** reduce irrelevant decision input while preserving the tuned
rule behaviour and the frozen first-decision comparison.

**Change:** added a shared compact DecisionState. It retains observed people,
the existing social categories/labels, robot motion state, readiness and all
numeric operands used by the rules. Seven rule constants are cached outside
the observation payload; IDs/timestamps remain in correlation context.
Configuration, raw head/nose coordinates and unused fit statistics stay in
the full SocialState diagnostic record. Both live and offline LLM audits now
save full diagnostics and the exact compact input separately.

**Controls:** the v14 system instructions and user prefix, estimator/tracking
settings, rule conditions/precedence, model digest/runtime, generation options,
allowed actions, first/later timestamps and acceptance limits were unchanged.
Prompt metadata is `social-state-llm-v14-compact-v1` to identify the payload
change. No new semantic labels or action recommendations were introduced.

**Rule verification:** the original frozen implementation and compact policy
match on all 574 frames: readiness, actions, reasons, target UIDs/epochs and
source/decision IDs. The eligible count remains 402. Independent threshold,
null, attention, missing/multiple-person and readiness boundary tests pass.
Actual compact rule/LLM input hashes match in all 27 operational trials.

**Results:** LLM first-action alignment increased from 6/9 to 8/9 in each of
three repetitions (18/27 to 24/27). S1 now continues and S9 now approaches;
S4 remains incorrect, selecting CONTINUE instead of YIELD. Median prompt
size, including unchanged instructions, fell from 1,766 to 794 tokens (55.0%).
Median eligibility-to-complete-output time fell from 6.21 to 2.76 seconds
(55.6%). All 27 first outputs were valid.

**Acceptance:** increased from 5/27 to 24/27, with eight accepted per run.
This is distinct from alignment: S4's incorrect action was accepted, while
S7's aligned CONTINUE was rejected because current gaze evidence was
insufficient at delivery. S7 observations were fresh; this was a readiness
gap rather than inference expiry. Aligned-and-accepted outputs total 21/27.

**Tests:** 658 test methods were run with local HTTP fixtures enabled. All
stage 3 and integration checks pass. One pre-existing VLM wording-consistency
test fails four subcases because the VLM and LLM action definitions are not
verbatim identical; the unchanged LLM system text was checked against the
frozen baseline. It was not repaired by changing either prompt in this
representation-only experiment.

**Interpretation:** compacting the representation improved measured latency
and first-action fit without altering the rules. The remaining S4 error and
later diagnostic disagreements remain documented. These are separate warm
runs on the same tuning recordings; they do not establish held-out accuracy.

Artifacts: [stage 3 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/REPORT.md),
[representation guide](/Users/eddiekim/Documents/Projects/P4P/docs/compact-decision-state.md),
[comparison metrics](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/comparison.json).

## Stage 4 — 13 October 2026: shared semantic descriptions

**Objective:** make the compact evidence easier to interpret in the robot's
social-navigation context without adding inferred intent or changing the rules.

**Change:** DecisionState v2 replaces terse observation aliases with short
measurement descriptions. Recent gaze history and latest gaze remain separate;
proximity describes configured distance categories; corridor position is an
estimate rather than a collision prediction; relative separation can be caused
by either participant. Added descriptions distinguish face detection from
facing the robot, unknown upstream path/gesture measurements from negative
observations, and robot movement from individual person movement. Labels are
recomputed from authoritative categories/validity during compact validation.
SocialState's original diagnostic labels remain unchanged.

**Controls:** both policies receive exactly the same enriched compact object.
All numeric operands and categories are unchanged. The v14 instructions/user
prefix, model digest/runtime, generation settings, first/later timestamps,
expected-action lists and acceptance limits remain frozen. Prompt metadata is
`social-state-llm-v14-semantic-v2`. Stage 3's sealed results were verified before
and after measurement.

**Verification:** all 574 frames match frozen v7 readiness, complete decisions,
reasons, target IDs/epochs and source/session IDs; 402 remain eligible. The
actual rule and model input hashes match in all 27 operational trials. Every
non-label stage 3 operand matches across all 18 first/later snapshots. All 36
model requests (27 primary, 9 later) retain response/native metrics and verified
exact request bytes.

**Results:** alignment remains 8/9 in each repetition (24/27). S4 still selects
CONTINUE instead of YIELD; no first action changed from stage 3. Median prompt
size rose from 794 to 880 tokens (+10.8%). Median eligibility-to-complete-output
time rose from 2.76 to 3.72 seconds (+35.0%). All 27 first outputs were valid.
Live replay acceptance fell from 24/27 to 22/27 (6/9, 9/9, 7/9); aligned and
accepted fell from 21/27 to 19/27. Three rejections had observations older than
the existing one-second limit; two had current readiness gaps. All model source
ages were below the existing ten-second limit.

**Explanation quality:** the labels themselves do not invent intent, but the
LLM still describes sustained gaze as indicating interest (S2) or willingness
(S3). Semantic descriptions do not guarantee grounded model explanations.
Later diagnostics still disagree with the rules at S4/S6/S7 and do not count
toward the primary score.

**Tests:** 664 methods were run with local HTTP fixtures enabled. All stage 4,
rule parity and integration checks passed. The same pre-existing VLM/LLM
verbatim-action-wording test fails four subcases; no new test failure occurred.
The initial sandbox run's 14 local-HTTP permission errors disappear with fixtures
enabled. The LLM instructions remain byte-identical to the frozen baseline.

**Interpretation:** this stage improves the explicit meaning of the shared
state but does not improve first-action alignment on the tuning recordings.
It has a measured text/latency cost and lower acceptance in this run. Retain
stage 3 as the faster comparison reference; stage 4 is an experiment, not a
claimed operational improvement. Separate warm-run runtime/cache effects also
influence latency. A subsequent prompt experiment can test explanation grounding
and treatment of closing/corridor evidence without encoding recommended actions
in the state. No physical robot action was executed.

Artifacts: [stage 4 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/REPORT.md),
[label meanings and derivation](/Users/eddiekim/Documents/Projects/P4P/docs/semantic-decision-state.md),
[comparison metrics](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-4/comparison.json).
