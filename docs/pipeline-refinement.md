# Refined social decision pipeline

Work is on `feature/end-to-end-pipeline-refinement`, based on `33d9a78`.
The requested Ollama branch exists as `feature/ollama-llm-vlm-policies` at
`c6b988f`. Its older integration was already merged into the target branch.
Useful additions from its remaining commit were brought across selectively:
the action vocabulary, explicit context limit, source/camera audit, comparison
design and historical baseline artifacts. Its DEFER transport and no-person
CONTINUE behaviour were not imported because they conflict with the newer
production trial lifecycle. See [import provenance](results/pipeline-refinement/branch-import.json).
The existing untracked `var/evaluation/` files were preserved.

## Evaluation contract and evidence

The workbook contains 37 responses per scenario. All supplied preferred-action
counts and rounded means match the source. Ratings exclude unavailable answers
independently for each action: valid rating counts range from 34 to 37.
`config/human-reference.json` retains exact means, rating denominators, preferred
counts including unable-to-judge, and the original intended labels. It contains
no respondent timestamps or comments. The PDF explicitly specifies the immediate
next action at the video end and independent appropriateness ratings. The source
workbook/PDF were read without modification. Verification and SHA-256 digests are
in [reference-verification.json](results/pipeline-refinement/reference-verification.json).

The user confirmed the survey videos were trimmed from the longer recordings.
Trim offsets are not available. Therefore **no actual survey-end action or human
accuracy is claimed**. `config/evaluation-contract.json` freezes two separate
diagnostic endpoints for each scenario: SDK perception end and camera end.
Survey-end rows explicitly report NOT_READY/TRIM_OFFSETS_MISSING. All camera-end
proxies report NOT_READY/STATE_STALE. Missing points/observations, invalid output,
unrun inference and execution errors receive no human rating. See the complete
[before-and-after table](results/pipeline-refinement/comparison.md) and
[per-policy CSV](results/pipeline-refinement/before-after.csv).

Selection uses the last perception received at or before a declared endpoint,
never the last READY frame and never a future frame. SDK reconstruction uses the
original adapter with robot-host monotonic receipt time as collection time, then
the same tracker and estimator as production. Only prior locomotion packets no
older than one second are used. A state more than 0.25 s behind the endpoint is
stale. Per-person evidence comes from the preceding two-second temporal window;
tracking retains three seconds and at most 64 samples. Nulls, sequence gaps,
invalid jumps and missing detections retain their existing evidence boundaries.

SDK and camera receipt clocks share a co-capture session. Neither SDK timestamps,
Unix timestamps nor filenames establish camera exposure synchronisation. Live
queue drops and collection delay cannot be reconstructed. The current camera
tails are 7.226–19.629 s longer than SDK perception. The root cause cannot be
inferred from contiguous saved sequences without capture-end markers. Adding
trim offsets later requires explicit `survey_video_end` timestamps in a new
contract with mapping VERIFIED and preserved source hashes. SDK/camera diagnostic
points retain their diagnostic meaning even after a video mapping is verified.

## Perception audit

SDK person detection coverage remains 0/91, 27/84, 110/115, 0/55, 0/40,
4/77, 0/29, 32/117 and 56/122 for S1–S9 respectively. S1/S4/S5/S7 have
no person observations at all. S2/S6/S8 have fragmented detections and UID
changes; S3 loses its person just before SDK end; S9 has no retained person at
SDK end. The revised implementation does not repair those missing detections.

Images at the last camera receipt before SDK end and at camera end were inspected
for all nine scenarios. Many show mostly ceiling, with people near the image edge
or outside view. That is evidence of inadequate framing in these samples, not a
calibrated explanation of every missing SDK detection. SDK `g_nose` exists for
detected people, while `g_head_position` expected by the optional head-position
adapter is absent. Unlabelled `head_position` and nose coordinates were not
relabeled as head positions or converted into route geometry.

No recorded input contains a measured path-conflict or pass-gesture cue. Those
remain UNKNOWN for both policies. Range sensors have unverified units and do not
establish a person's route relation. Moving-base relative range does not identify
human motion without ego-motion compensation. Missing detection is a perception
failure; fragmented coverage/pending gaze category is a temporal/readiness
failure; an action inconsistent with a supplied valid distance zone is a policy
failure. Hardware execution has not been measured by this offline evaluation.
Full scenario diagnostics are in each run's `sensor-audit.json` and `states.jsonl`.

## Implementation

One `observation_hold_reason` now governs rule decisions, structured LLM trial
submission, and the new evaluator. No-person observations remain NOT_READY.
The robot accepts the server's correlated readiness for both policies rather
than letting the LLM use a distance trend when rule gaze evidence is unavailable.
Low-level `decide_llm`, output-only model logs and old single-snapshot replay APIs
remain ungated diagnostic probes; they are not eligible-trial comparisons.
VLM eligibility is a separate image condition and is not included in these results.

Temporal evidence now includes current looking state, looking duration, observed
looking bouts and mean overlap. Gaps cannot manufacture new bouts. Rules stop
using a held SUSTAINED category immediately when current gaze is no longer
looking, and a renewed glance must satisfy the continuous-run requirement.
Qualifying intermittent attention requires at least two observed bouts, at least
one second looking, 45% looking fraction and current looking, with valid coverage.
A single brief glance still supports CONTINUE.

Explicit upstream path conflict takes priority, then provisional TOO_CLOSE
spacing, then a measured invitation to pass. Optional raw `path_relation` and
`pass_gesture` measurements pass through tracking and state construction;
the SDK adapter supplies neither and no gesture detector was invented. Missing
tracks clear these cues. Proximity yielding remains a measured spacing response,
not evidence of a route conflict. The existing single-person scope is retained.

Sustained or qualifying recurring attention inside interaction range supports
ENGAGE, including attentive retreat: speaking does not move closer. Outside
interaction range, a valid increasing separation trend prevents pursuit.
Distance hysteresis and ego-motion restrictions remain in place. Rules are
version `social-rules-v3`; the robot validator accepts this version alongside v2.
The final LLM prompt is `social-state-llm-v6`, with the same cue distinctions and
explicit distance/action consistency. Outputs remain independent model choices
with no correction or rule fallback. Schemas were regenerated. VLM action wording
was updated consistently (`image-only-vlm-v4`), without a VLM performance claim.

## Experiments and results

Before editing policy/state behaviour, the baseline replay processed all 730
perceptions, using the original rules v2 and LLM prompt v3. Model settings for all
comparisons were the installed `qwen2.5:7b` Q4_K_M model, temperature 0, seed 42,
8192 context tokens, 192 output tokens and 120-second request timeout. Exact model
digest and Ollama 0.34.0 identity are retained in manifests. No model was downloaded.

Eight exploratory configurations varied span 0.8/1.0 s, valid gaps 0.25/0.35 s
and category dwell 0.2/0.3 s, with coverage 75% of span. None made an SDK endpoint
ready. A subsequent twelve-configuration screen held distance span at 1.0 s and
gaze coverage fraction at 60%, varying minimum valid gaze coverage 0.6/0.8 s,
dwell 0.1/0.2/0.3 s and valid gaps 0.25/0.35 s. Only coverage 0.6 s with dwell
0.1 s made S2 ready at SDK end. Widening gaps produced no benefit and was rejected.
The selected file is `config/temporal-development-frozen.json`. Its continuous
gaze run minimum remains 0.4 s; reducing redundant dwell is not treating a single
glance as sustained attention. Neither distance nor gaze-overlap boundaries were
tuned to scenario labels. Recurring-attention thresholds are conservative
development hypotheses verified with boundary tests; these recordings cannot
calibrate them because S7 has no detected person.

SDK-end coverage changes from 0/9 to 1/9 for each policy: both choose ENGAGE in S2
at approximately 1.04 m. The conditional human comparison is 11/37 preferred
support, mean rating 3.78 and no modal/original-label agreement. APPROACH has
18/37 preferred support but mean 3.65. This supports preserving the distinction
between preferred immediate movement and appropriateness of a greeting.
**This is a diagnostic improvement in temporal readiness, not demonstrated
survey-end alignment or improved perception.**

S3's reference favours YIELD (18/37), but ENGAGE has the highest mean rating
(3.78 versus 3.57 for YIELD). Route relation is unavailable and the SDK endpoint
has a missing person. Earlier ready states are not substituted for this endpoint.
The policy yields if a conflict is measured and otherwise can greet an attentive
person already at conversation distance. Lowering a distance boundary to force
YIELD would resolve the ambiguity without the necessary evidence, so it was not
done. S7/S8's survey-supported interactions remain distinct from their original
CONTINUE labels. S9 requires a cue that the structured pipeline cannot measure.

Prompt v5 removed many S3 errors but chose ENGAGE for two APPROACHABLE S9 samples.
Final v6 adds an explicit consistency requirement. On 11 byte-identical refined
states, original v3 made eight action/distance violations and v6 made none, with
zero inference errors. Rules chose identical actions before/after on those fixed
states, locating recorded rule improvements in preprocessing. Mean ablation
latencies were 5.687 s (v3) and 5.425 s (v6). The model still chose CONTINUE for two
attentive APPROACHABLE S9 states with weak explanations. Matching the S9 preference
there would not demonstrate pass-gesture recognition.

The sampled traces contain 73 moments, with ten eligible LLM calls in each final
frame-diagnostic replay. These frames are correlated, not independent encounters.
The final S2 endpoint model call took 1.274 s after previous inference; one v5
first/new-prompt call took 17.652 s. Warmup, prompt processing and caching differ,
so mean latency changes are not a speed comparison. A call exceeding the active
trial's default ten-second source expiry would be rejected on the robot even if
the offline decision is valid. No expiry was relaxed to conceal this limitation.

An ablation setup error occurred before inference because the detached old
Pydantic module was not registered; the failed setup manifest/error is retained
and the corrected comparison completed. All endpoint inference errors, execution
statuses and latencies remain visible in the CSV and exact JSONL results.

## Reproduction and frozen live-study candidate

From the repository root, choose a new output folder:

```bash
.venv/bin/python -m evaluation.run_scenarios \
  --contract config/evaluation-contract.json \
  --temporal-config config/temporal-development-frozen.json \
  --model qwen2.5:7b --diagnostic-interval-s 1 \
  --output var/evaluation/refined-new
.venv/bin/python -m evaluation.experiments --output /tmp/p4p-screen-new.json
.venv/bin/python -m evaluation.freeze --verify config/live-study-freeze.json
.venv/bin/python -m unittest discover -s tests
```

Ollama must be running locally with the declared model installed. Omitting
`--model` gives an explicit rule-only comparison. Outputs use exclusive creation;
source SHA-256 checks reject changed recordings. Human distributions never enter
the runtime policy, and neither policy receives images in this comparison.

The receiver can use the same frozen temporal preprocessing and model options:

```bash
.venv/bin/python -m app.server --host 0.0.0.0 --port 6060 \
  --output var/recordings/study-raw-new.jsonl \
  --sdk-output var/recordings/study-sdk-new.jsonl \
  --camera-output-dir var/recordings/study-cameras-new \
  --social-output var/recordings/study-social-new.jsonl \
  --tracking-config config/person-tracking.json \
  --temporal-config config/temporal-development-frozen.json \
  --model-inference llm --model-output var/recordings/study-model-new.jsonl \
  --llm-model qwen2.5:7b --llm-temperature 0 --llm-seed 42 \
  --llm-num-ctx 8192 --llm-num-predict 192 --llm-timeout 120
```

For a robot-side observation/decision dry run, replace the server address:

```bash
python3 -m robot.navel_client.main --server http://PC_ADDRESS:6060 \
  --sdk-capture --camera-capture --model-provenance \
  --single-trial --decision-dry-run --single-trial-policy llm
```

Use `--single-trial-policy rules` for the matched rule condition. The dry run
above does not start route movement or execute social behaviours. Real movement
uses the existing explicit route/execution options and requires hardware checks
described in the physical-executor and route documents. The current timed YIELD
does not verify clearance or exact route return. The new offline results cannot
establish approach success, physical safety or action-completion latency.

`config/live-study-freeze.json` freezes source/configuration hashes, exact prompt,
policy versions, model/runtime identity and inference settings. Verify it before
new data collection. Record externally annotated encounter endpoints, transmitted
raw frames, SDK/camera coverage and controller outcomes under the frozen version.
Keep new live encounters for evaluation and report readiness/expiry/execution
coverage alongside social outcomes. Missing trim offsets and unavailable path,
gesture and ego-motion cues remain precisely identified limitations.

The baseline full suite passed 534 tests. The refined full suite passed 550
tests, including 16 focused regressions for glance duration, recurring/changing
attention, renewed glances, gaps, missing/null cues, distance hysteresis,
proximity/conflict/pass precedence, recorded production/replay parity, identical
canonical inputs, source-time selection, scoring missingness and context options.
Tests exercising temporary HTTP fixtures ran with localhost socket access.
Real model calls were recorded separately from mocked tests. No robot hardware
or new live encounters were used for tuning.
