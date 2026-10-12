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
