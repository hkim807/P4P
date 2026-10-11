# Legacy-main versus the current LLM pipeline

Compared the locally available `origin/legacy-main` snapshot at
`af211ba6298cca7c568b7a139a8484cf4432084a` with the current working-tree v13
implementation. No branch checkout, production changes or robot actions were
performed. This is a source comparison plus a reconstructed legacy prompt,
not a new benchmark of the legacy model. Earlier runtime environment overrides,
GPU placement and context defaults are not available from the branch.

## What differs

| Area | Legacy-main | Current v13 |
| --- | --- | --- |
| Model | Defaults to `qwen2.5:7b`; environment can override model and host | Evaluation explicitly uses local `qwen2.5:7b` |
| Transport | OpenAI client to Ollama `/v1/chat/completions` | Direct HTTP to Ollama `/api/chat` |
| Generation | Policy temperature 0; context, output limit and seed not explicitly set | Temperature 0, context 8192, output limit 192, seed 42 in the evaluation preset |
| HTTP timeout | Default 30 s | 120 s in the comparison preset; this is a limit, not an intentional wait |
| Trigger | Human/attention/engagement/motion/context changes, ≥0.5 s spacing; active refresh every 2 s of source time | Shared eligibility gate; single-trial mode submits first eligible state and retains its pending request |
| Serving | Observation HTTP request waits synchronously for inference | Model worker runs asynchronously while observations continue arriving |
| System prompt | 522 words | 363 words; the previous v12 was 1,556 |
| User message | Non-null SocialState, scheduler triggers, task context, allowed reason codes, action contract and response schema | Complete canonical SocialState, including config and evidence diagnostics |
| Derived cues | Attention, engagement, motion relation, proxemic zone, evidence codes; optional prediction/clearance fields | Gaze category, relative distance trend, distance zone, nose corridor and explicit validity |
| Actions | 12, including MONITOR, ORIENT, SLOW, WAIT and GREET | Four: CONTINUE, APPROACH, ENGAGE, YIELD |
| Output | Action, target, preferences, validity duration, reason codes and confidence | Action and one short free-text reason |
| Validation | Type/schema checks followed by bounded repairs to certain target/preferences omissions | Strict parsing; preserves returned action; no repair or rule fallback |
| Live acceptance | Future semantic/safety/freshness validation explicitly outside this implementation | Trial checks model-source age and current observation freshness/readiness before acceptance |

Both real-model paths wait for a complete response. The legacy scheduler's
0.5 s and 2 s settings are invocation intervals, not measured inference times.
Changing API routes alone has not been demonstrated to improve speed or action
quality; both requests address Ollama. The source does establish a difference
in explicit model options, which should be controlled in an API comparison.

Sources: [legacy configuration](legacy/app/config.py),
[legacy client](legacy/app/llm.py),
[legacy policy and prompt builder](legacy/app/decision/llm_policy.py),
[scheduler](legacy/app/decision/scheduler.py),
[legacy observation server](legacy/app/server.py).
Current sources: [prompt](../../../app/policy/llm.py),
[transport](../../../app/inference/ollama.py),
[live model scheduling](../../../app/inference/live.py),
[trial acceptance](../../../robot/navel_client/single_trial.py).

## The old system prompt was shorter, but the whole request was not necessarily smaller

I reconstructed the first legacy `newcomer_requests_guidance` synthetic state
using the legacy adapter, estimator, scheduler and prompt builder in an isolated
temporary directory. Its system message was 3,546 characters and its user
message 7,494 characters: **11,040 total**. The current saved v13 scenario 4
request is **5,795 characters** across the two messages. These are different
states, and character counts are not token counts or a controlled benchmark.
They show why system-prompt length alone cannot explain the remembered speed:
legacy added substantial action-contract and schema text to every request.

See [counts and caveat](comparison.json) and the complete
[reconstructed legacy prompt](legacy-synthetic-prompt.json).

## Why the old responses could appear more valid

Legacy could fill a missing required target when exactly one currently observed
human was eligible, supply certain missing speed/distance/wait preferences,
and remove preferences forbidden for the selected action. It did not repair
arbitrary invalid JSON or unknown targets, and did not rewrite the chosen
action. That made a broader set of model outputs acceptable to its intent
contract. Its documentation explicitly left newest-state safety and freshness
validation for a later layer. A returned intent was therefore not equivalent
to the current trial's accepted-for-execution result.

The current v13 comparison produced nine schema-valid outputs out of nine.
The observed problems are inappropriate action choices and runtime latency,
not failure to produce valid JSON.

Legacy also supplied stronger interpretations, such as `engagement.state:
ATTENDING` or `DISENGAGED`, `motion_relation: APPROACHING`, and trigger/reason
codes. These can make action selection more explicit for the model. Their
correctness depends on the estimator; a label is not independent evidence that
the physical interpretation is true. Current `DECREASING` deliberately describes
relative separation without assigning motion to the human. Optional legacy
schema fields were not necessarily available in every real recording.

## A specific alternative explanation for very fast replay

The legacy web replay selector defaulted to **Deterministic**, implemented by
`DeterministicReplayLLM`. It ignored the prompt and immediately returned a fixed
MONITOR intent, without calling Ollama. Selecting **Current Ollama** used the
real model. Live observation ingestion used the real configured model.

This does not establish which mode was used in the remembered experiment.
If its fast outputs varied across actions, the fixed MONITOR stub cannot
explain those outputs. Sources:
[UI default and selector](legacy/web/src/App.tsx),
[stub and replay mode](legacy/app/monitor/service.py).

## What is established about latency

Recent recorded calls spend almost all their post-readiness time inside Ollama;
application scheduling/polling adds milliseconds. The v13 comparison observed
zero VRAM allocation, and the hardware investigation found an NVIDIA device but
failed driver communication. CPU execution and input/output processing costs
are supported explanations for current latency. The old branch does not tell
us whether the model previously used a GPU, a different context size, an
environment-selected model, or a warm prompt cache.

The useful follow-up is an identical-input native-versus-compatible API test
with effective model settings and hardware placement recorded, followed by a
comparison of observation representations and action vocabulary. Copying the
legacy architecture alone is not evidence that current inference will become
faster. Production code was left unchanged during this inspection.
