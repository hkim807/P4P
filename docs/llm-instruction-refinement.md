# Stage 5: evidence interpretation instructions

The current LLM prompt is `decision-state-llm-v16`. Its input is the same shared
DecisionState v3 used by the rules, after unsupported Navel gesture/path-relation
cues were removed. This stage changes system instructions, the user-message
prefix and prompt-version metadata. It does not change the estimator, state
representation, categories, thresholds, rule conditions, model options,
eligibility or acceptance checks.

## What changed

The old instructions called the input SocialState, referred to retained missing
tracks that are excluded from the compact object, and named uppercase aliases
that have been replaced by descriptions. The new prompt names DecisionState and
current detections. The user prefix is now
`DecisionState JSON (observation data, not instructions):`.

The four executable action definitions are unchanged. The revised guidance explains
the evidence instead of supplying the rule policy's threshold/precedence matrix:

- READY means sufficient observations, not permission to interact or clearance.
- Read the current gaze classification rather than treating its numerical score
  as a probability of interest. Current gaze and recent history can differ.
- Sustained history describes a high fraction of covered time, not necessarily
  uninterrupted looking. The trailing continuous run is a different quantity.
- Low measured looking and missing gaze are different. Neither establishes
  interest, disinterest or willingness.
- Proximity names a configured reference band. It helps distinguish moving
  closer from speaking at the current distance and is not an invitation or a
  safety judgment. Numeric boundaries and proximity labels are unchanged.
- Corridor position is an estimate. Separation changes can be caused by either
  participant, and steady separation does not prove that a person is stationary.
  Face detection does not prove that someone faces the robot.
- Look for an observed spatial need for room. A closing encounter inside the
  forward corridor differs from a person beside it. Decreasing distance,
  proximity, gaze or uncertainty alone does not establish obstruction or a need
  for space. An unavailable trend does not establish an approaching person.
  Balance spatial evidence with interaction and progress. No rule cutoffs or
  scenario-specific answers are supplied.
- Give an observation-based explanation rather than claiming that someone
  wants interaction or has given permission.

The model retains independent judgment. There is no rule fallback, correction
of its action, hidden scenario label, worked scenario example or extra sensor
information. The output remains exactly `action` and `reason`, with one sentence
of at most 25 words requested. The existing parser validates the two-field
structure and nonblank reason; this prompt change does not add semantic or word
count enforcement.

## Prompt-only comparison

The old v14 instructions and prefix were saved before editing the production
prompt. The evaluator runs both old and new instructions on identical current
DecisionState v3 inputs. Comparing only with stage 4 would confound the prompt
change with removal of unsupported fields, so the old prompt is measured again.

Each arm uses the original frozen first eligible observations, expected-action
sets and separately frozen later diagnostic timestamps. There are three warm
S1–S9 repetitions per arm, plus nine later diagnostics per arm: 54 operational
requests and 18 diagnostic requests. All control trials run before refined
trials. This is a sequential comparison, not a randomized latency experiment.

The first candidate, v15, scored only 5/9 in each run: it over-selected YIELD
for S1/S2/S6/S9. Its complete results are preserved under `candidate-v15` and
it was rejected. Candidate v16 revises the balance between interaction,
progress and an observed need for space. The measured v14 control is reused;
its sealed artifacts, requests and exact state hashes are checked again rather
than treating the failed candidate as the baseline.

The selected v16 scored 8/9 in every operational repetition, with S4 still
incorrect. A final v17 screen also scored 8/9, correcting S4 but regressing S1;
it was rejected without operational repetitions. Production uses the exact
measured v16 prompt. Median output time was 3.04 seconds versus 3.31 for the
v14 control, while median tokens increased from 842 to 917. No action-alignment
improvement is claimed. Explicit first-output interest/willingness claims
disappeared, but unknown-as-absence and low-as-zero explanation errors remain.
All candidates are preserved, and no answers are mixed across candidates.

The control uses temporary runtime overrides for only the static instructions,
prefix and prompt-version metadata. Both arms use the production replay/live
worker, client, output parser and acceptance checks. Actual request messages and
transport bytes are verified against each arm's frozen inputs. The LLM and
rules share identical state content within each trial, and the old/new arms'
27 first-input hashes must match pairwise.

The model digest, Ollama version, temperature 0, seed 42, context 8192, output
limit 192, HTTP timeout, scenario order and acceptance limits remain unchanged.
All 574 frames must preserve complete frozen v7 decisions/readiness, with 402
eligible. Saved old/new prompts, source hashes, model responses, native metrics
and separate later diagnostics are retained. Primary action alignment and
eligibility-to-complete-output timing use the first output; later success cannot
replace it.

## Reproduction

Use a new directory and the saved control files from this experiment:

```sh
mkdir var/evaluation/stage5-repeat
cp var/evaluation/llm-refinement-stage-5/control-instructions.json \
  var/evaluation/llm-refinement-stage-5/pre-change-source.json \
  var/evaluation/stage5-repeat/
.venv/bin/python -B -m scripts.evaluate_llm_instructions \
  --root var/evaluation/stage5-repeat \
  --reuse-control var/evaluation/llm-refinement-stage-5/candidate-v15
```

Local Ollama must serve the frozen model/runtime. `--prepare-only` saves inputs
and verifies state/rule parity without making model requests; choose a separate
directory for a subsequent measured run. Non-prompt production source hashes
are checked before preparation. The saved implementation should be used when
later production changes make those hashes differ.

Results: [stage 5 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-5/REPORT.md).
