# Frozen LLM baseline: stages 1 and 2

The baseline is saved under `var/evaluation/llm-refinement-stage-1-2`.
Its `freeze/protocol.json` is the authoritative scoring contract. The baseline
uses the current full SocialState, v14 instructions, tuned rule policy and
final-scenario configurations without changing their behaviour.

## Primary evaluation

Each of the nine motion-trimmed SDK recordings is processed causally from its
start, with a fresh tracking/estimation session. The first READY SocialState is
selected and frozen before inference. Rules and LLM consume the same canonical
state; content hashes and source timestamps are checked.

The first request's output determines action alignment. An invalid, failed or
missing first output is a nonaligned result even if a later retry succeeds.
The unchanged allowed actions are taken from `config/final-scenarios.json`.
Each scenario has equal weight. Three repetitions are measured separately;
there is no selection of the best repetition.

Primary timing reports:

1. Motion onset to the first eligible observation.
2. First eligible observation to the complete validated output observed by the
   replay receiver/client.
3. Motion onset to that output.

Time to complete output is measured, not time to first token. Validity,
alignment and live-client acceptance are separate outcomes. The 30-second
trial deadline, 10-second model-source age and 1-second latest-observation age
remain unchanged. Finite recordings can end before an answer arrives.

## Separate diagnostic point

The later snapshot is the last READY SocialState before each original recording
ends. Its exact timestamp is frozen before any model calls and remains fixed
for comparisons. It uses only data available up to that timestamp. Later
outputs explain the encounter but do not contribute to the operational score
and are not substituted for an incorrect first output.

## Freeze contents

The freeze includes copies and SHA-256 hashes of the implementation sources,
configuration files, schemas, requirements and all nine input recordings;
first/later states, prompts and source provenance; expected actions; Git commit
and branch; Python/platform/dependency versions; Ollama version and model
metadata/digest. The profiler itself is saved for reproduction.

The `verify` command rejects altered frozen files or application/configuration
sources that differ from the baseline. Future changed implementations should
use a separately versioned experiment while retaining this baseline.

## Timing measurement

`scripts.profile_llm_baseline` wraps the existing Ollama transport. The wrapper
passes the original request bytes and timeout unchanged, returns the original
response and OllamaResult, and stores telemetry after existing validation.
Production files, state schema, prompt, generation settings, rules and
acceptance logic are not edited. Tests compare the ordinary and profiled
requests and decision outputs.

Telemetry includes Ollama's `load_duration`, `prompt_eval_duration`,
`eval_duration`, `total_duration`, input/output token counts and cached input
count when supplied. Raw request/response files are retained. These native
durations are in nanoseconds and are also reported in seconds.

The profiler also measures request preparation, transport and response
validation, together with source-to-inference and inference-to-observed-output
intervals. The source-to-inference interval combines replay dispatch, receiver
processing, scheduling and prompt preparation; it is not a pure queue-time
measurement. Transport minus native total is residual overhead, not a LAN
latency measurement. Native unattributed time is reported separately rather
than forcing component sums to equal total.

Model weights are warmed before each operational repetition. This does not
guarantee that the scenario's prompt tokens are cached. Scenario order is
always S1–S9, and cache counts are recorded. Later diagnostic calls run only
after all operational repetitions. A separate S1 first-state probe deliberately
unloads the model first to expose cold loading; it is excluded from the primary
score.

Ollama metric definitions: [usage](https://docs.ollama.com/api/usage).

## Reproduce

From the repository root, with the existing Python environment and local
Ollama serving the installed `qwen2.5:7b` model:

```sh
.venv/bin/python -B -m scripts.profile_llm_baseline freeze \
  --output var/evaluation/llm-baseline-new
.venv/bin/python -B -m scripts.profile_llm_baseline run \
  --output var/evaluation/llm-baseline-new
.venv/bin/python -B -m scripts.profile_llm_baseline verify \
  --output var/evaluation/llm-baseline-new
.venv/bin/python -B -m scripts.report_llm_baseline \
  --output var/evaluation/llm-baseline-new
```

Both freeze and measurement directories use exclusive creation. Existing
baselines/results cannot be overwritten by these commands.

These nine examples remain a tuning set, not held-out evidence. The multiple
allowed actions for S3 and S8 remain as originally configured. S8's first
eligible state precedes the detected person; that result is explicitly
empty-scene handling. No physical robot executor is instantiated.
