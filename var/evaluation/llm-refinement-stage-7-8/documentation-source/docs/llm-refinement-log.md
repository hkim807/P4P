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

## Follow-up — 13 October 2026: remove unsupported Navel cues

**Request:** remove pass gestures and measured path relations completely,
because Navel sends neither; clarify whether proximity labels contain a policy
choice rather than a raw observation.

**Change:** removed the two fields from RawPerson, PersonSocialState, estimator
construction, DecisionPerson and published schemas. Removed their two English
descriptions, readiness bypasses, rule branches and reason descriptions. The
supported nose-based corridor category and closing/low-gaze yield condition
remain. DecisionState/description version is 3; prompt metadata is
`social-state-llm-v14-semantic-v3-navel-cues`. No remaining thresholds, category
calculations, system instructions, proximity descriptions or physical actions
were changed.

**Historical data:** stage 1–4 freezes/results remain untouched. Current live
contracts reject the removed fields. Offline SocialState readers can remove
only archived UNKNOWN placeholders on a copy, preserving original source
records. Claimed CLEAR/CONFLICT/PASS measurements require the old frozen
implementation. The rule oracle now loads the original frozen SocialState
model together with its rule code, so current schema/readiness changes cannot
contaminate the reference. Paired preparation retains the original full-state
hash separately from the hash with only unsupported placeholders removed.

**Verification:** all 574 frames retain complete v7 decisions, readiness,
reasons, targets and IDs; 402 are eligible and first rule actions remain 9/9
aligned. Focused tests cover end-to-end absence, schema equality, strict live
rejection, no readiness shortcuts, offline migration, first-output timing and
supported rule boundaries. The full suite ran 666 methods: all new checks pass;
the same pre-existing VLM/LLM wording method fails four subcases. No new LLM
inference runs were performed, so stage 4's accuracy/latency numbers continue to
describe its frozen v2 payload, not this cleanup.

**Proximity:** the exact distance is measured; conversation/social range names
refer to configured categories. The final preset has boundaries 0.6/2/6 m.
They do not establish interest or prescribe an action. The compact input
currently leaves those numeric definitions implicit. A future experiment can
either explain a fixed shared operating range clearly or use neutral metric
labels when testing each policy's own spatial judgment. This follow-up does
not silently change proximity or the comparison contract.

Details: [supported cue contract](/Users/eddiekim/Documents/Projects/P4P/docs/navel-supported-cues.md).

## Stage 5 — 13 October 2026: refine evidence interpretation instructions

**Objective:** adapt instructions to the current shared DecisionState v3 and
improve interpretation without changing preprocessing, rules or model settings.

**Change:** the selected prompt is `decision-state-llm-v16`, with a
DecisionState user prefix. Removed obsolete missing-track/uppercase-label
references. Clarified current gaze versus history, covered-time fraction versus
continuous looking, unknown versus absent evidence, configured proximity bands,
robot-relative separation, face-detection limits and observed spatial need for
space. Reasons are requested to cite observations without claiming interest or
permission. The four action definitions and two-field output contract remain
unchanged; no rule thresholds, scenario examples, fallback or output correction
were introduced.

**Controls:** saved the old v14 instructions before editing. Measured a fresh
v14 control on the same current v3 payload, so cue removal does not confound the
prompt comparison. Both arms use the original first/later timestamps, expected
actions, model digest/runtime, generation options and acceptance limits. Three
warm S1–S9 operational repetitions and nine later diagnostics were measured per
arm. Subsequent candidates reuse the sealed measured control. Actual messages,
transport bytes and all 27 paired first-input hashes are verified. Trials are
sequential, not randomized; runtime/cache effects also affect timing.

**Candidate history:** v15 over-weighted giving space, selecting YIELD for
S1/S2/S6/S9 and scoring 5/9 in all three runs. It was rejected and preserved.
Balanced v16 restored 8/9 alignment in each run. A final v17 screen scored 8/9
but exchanged S4's failure for an incorrect YIELD in S1 beside the corridor;
it was rejected without operational repetitions. Production was restored
byte-for-byte to the measured v16 implementation. No per-scenario answers are
selected from different candidates.

**Results:** selected v16 retains every v14 first action, with 24/27 alignment
and S4 still incorrect (CONTINUE rather than YIELD). Median eligibility-to-
complete-output time was 3.04 seconds versus 3.31 for the v14/v3 control (-8.1%).
Median input tokens increased from 842 to 917 (+8.9%). Both arms had 27/27 valid
first outputs. Live acceptance was 22/27 versus 19/27; aligned-and-accepted was
19/27 versus 16/27. Finite replay acceptance remains separate from correctness.

**Explanation review:** explicit interest/willingness claims disappeared from
selected first-output reasons. Remaining errors include S7 describing an
unavailable trend as no closing distance, and S6 describing low nonzero looking
as no recent looking. Instruction precision does not guarantee semantic
grounding. Later diagnostics remain separate and cannot improve the primary
score. No semantic acceptance validator was added.

**Verification:** all 574 frames preserve complete frozen rule decisions and
readiness, with 402 eligible. The final measured source hashes match production.
The full v16 suite ran 666 methods with no new failures or HTTP errors; the
same pre-existing VLM/LLM verbatim-action-wording method fails four subcases.
Focused checks passed again after restoring v16. Historical freezes and rejected
candidates are preserved. No physical robot execution occurred.

**Interpretation:** stage 5 clarifies the instruction contract and improves
measured completion time in this sequential comparison, but produces no primary
action-alignment improvement. The selected implementation is retained with its
limits documented; the nine recordings remain a tuning set.

Artifacts: [stage 5 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/REPORT.md),
[selected instructions](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/refined-instructions.txt),
[instruction method](/Users/eddiekim/Documents/Projects/P4P/docs/llm-instruction-refinement.md).

## Stage 6 — 13 October 2026: test causal temporal summaries

**Objective:** test whether a small combination of existing temporal evidence
improves the first decision, with the base state and v16 instructions fixed.

**Changes tested independently:** earlier measured looking with latest
not-looking (`LOOKING_CHANGE`); multiple measured looking bouts
(`REPEATED_LOOKING`); and detection resumption after a missing-detection gap
(`DETECTION_RESUMED`). Descriptions do not infer intent, individual motion,
short episode duration, looking away during a detection gap or a person
physically returning. Only positive applicable summaries are included.

**Controls:** reused the sealed stage 5 v16 control, rather than measuring a
fresh control. Original first/later timestamps and expected actions remain
fixed. Both policies receive the exact same representation. All 574 frames in
all four modes retain complete frozen v7 decisions and readiness; 402 remain
eligible. Other state fields, full SocialState, prompt, thresholds, model
digest/runtime, generation settings and acceptance limits are unchanged.
Actual first requests and paired input hashes are verified, as are later
requests. Historical stages 1–5 remain untouched.

**Results:** looking change appears at first in S4/S6/S7; detection resumption
in S7. Each received three warm operational repetitions and nine later probes.
Both retain 24/27 alignment (8/9 in every run), with S4 still choosing CONTINUE
instead of YIELD. Median complete-output time is 3.05 and 3.06 seconds,
respectively, versus the reused control's 3.04 seconds. Acceptance is 22/27
and 24/27 versus 22/27; validity is 27/27 in each. Sequential timing and finite
recording acceptance do not establish a causal improvement in live performance.

Repeated looking changes none of the nine first inputs, so it receives no new
primary score or timing result. Its nine later probes change S7 from APPROACH
to ENGAGE. That does not improve the frozen first-decision score or establish
that the later action is correct. In total, 81 new model calls were made:
54 primary and 27 later, separate from the 36 reused control calls.

**Interpretation:** no variant improves primary action alignment. S7's first
reason stops presenting an unknown trend as absent motion, but S6 still says
“no recent looking” despite positive measured looking. Extra descriptions do
not guarantee faithful use of the evidence. Adding a summary costs 36–37
input tokens in the affected first snapshots; the overall median hides this
because most first inputs are unchanged.

**Selection:** retain `NONE` as the production default. The experimental
extension is available for future tests: applicable summaries identify state
version 4; inactive summaries are omitted, preserving byte-identical v3
inputs. No tuned rule condition or production instruction changed.

**Verification:** the full suite ran 673 test methods; the same pre-existing
VLM/LLM verbatim-wording method fails four subcases, with no new failures or
errors. All 31 final focused checks pass. A final serializer compatibility
adjustment avoids requiring a newer Pydantic than the declared dependency
floor; 2,296 canonical payload comparisons (574 frames × four modes) prove
byte equivalence to the measured implementation. Measured source copies and
the final source are preserved separately. No physical robot execution occurred.

Artifacts: [stage 6 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-6/REPORT.md),
[summary definitions and method](/Users/eddiekim/Documents/Projects/P4P/docs/temporal-summary-experiments.md).
The next planned stage is inference-setting optimization, with the selected
v16 prompt and default v3 state held fixed.

## Stages 7 and 8 — 13 October 2026: inference settings and models

**Objective:** find faster first outputs without losing action alignment, then
test whether alternative models reduce judgement errors. Shared DecisionState
v3, disabled temporal additions, v16 instructions, original rules, first/later
timestamps, allowed actions and acceptance checks are fixed throughout.

**Stage 7:** screened context 2048, output ceiling 64 and their combination
against a fresh 8192/192 Qwen 2.5 7B control. All direct screens retain eight
aligned actions and valid whole prompts. The fastest screen, context 2048,
received three operational repetitions plus nine later diagnostics, together
with a freshly measured control. Arm order alternated by repetition.

Both operational arms retain 24/27 alignment, 27/27 valid outputs and 21/27
acceptance. Median complete-output time is 3.07 versus 3.13 seconds (-1.7%);
95th percentile is 5.91 versus 5.84 seconds (+1.2%). Context 2048 saves about
0.36 GB loaded memory, but fails the predeclared 10% speed-gain/no-worse-tail
criterion. Reducing the output ceiling does not materially help because answers
already end below 64 tokens. Retain context 8192 and output ceiling 192.

**Stage 8:** downloaded Qwen 2.5 3B, Qwen 3 4B Instruct and Gemma 3 4B. All
were tested with the retained settings alongside another fresh Qwen 2.5 7B
control. Three operational repetitions per model rotate model order; nine later
probes per model remain separate. One model is resident at a time, with warm
weights. Digests, templates, quantization and runtime are recorded; application
messages and structured-output schema are identical across models.

| Model | Alignment in each of three passes | Median complete output | Acceptance |
| --- | --- | ---: | ---: |
| Qwen 2.5 7B | 8/9 | 3.20s | 20/27 |
| Qwen 2.5 3B | 4/9 | 1.81s | 22/27 |
| Qwen 3 4B Instruct | 7/9 | 2.49s | 24/27 |
| Gemma 3 4B | 5/9 | 2.96s | 21/27 |

All models return 27/27 valid first outputs, with no truncation. Qwen 2.5 3B
fixes S4 but falsely yields in S1/S2/S6/S7/S9 and misstates corridor/proximity
evidence. Qwen 3 4B Instruct wrongly approaches S1/S4, infers individual motion
from relative separation and repeatedly claims interest as a fact. Gemma 3 4B
selects CONTINUE for all first scenarios, missing S2/S3/S4/S9. The 7B control
still misses S4 and retains known rationale errors about S6 looking and S7’s
unknown trend. No tested model improves overall judgement quality.

**Selection:** retain Qwen 2.5 7B and the original inference settings. None of
the candidates meets the quality/speed criterion. No application, prompt,
estimator, tracking, schema, rule or receiver-acceptance change is made.

**Verification and limits:** 252 new decision calls: 36 direct screens, 162
operational first trials and 54 later diagnostics. All strict outputs are valid;
all actual messages/settings/schema/transport hashes are checked. Production
rule/LLM input equality is checked for all first trials, and all 574 frames
retain original rule decisions/readiness with 402 eligible. Original freezes
remain unchanged. Six new selection/request/capacity checks pass. The full
suite ran 679 methods with only the same pre-existing VLM/LLM wording method
failing four subcases, with no new failures or errors.

Gemma’s initial download failed on storage-host DNS; a retry succeeded during
stage 7. Download activity overlaps screens/early setting blocks, limiting
latency interpretation; all model-comparison calls follow completion. Results
are local warm-model measurements on an M3/16 GB computer, not held-out accuracy
or physical execution rates. Faster acceptance can accept more wrong actions;
it does not override the primary first-action score.

Artifacts: [stages 7/8 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/REPORT.md),
[method and retained settings](/Users/eddiekim/Documents/Projects/P4P/docs/inference-settings-and-models.md).
