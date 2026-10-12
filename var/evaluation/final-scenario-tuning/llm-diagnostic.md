# LLM scheduling, latency and repeated CONTINUE diagnosis

The run-02 audit contains exactly one actual model call for each scenario.
`LiveModelRunner.submit_trial` freezes the first eligible SocialState and retains
the pending request while newer observations arrive. It does not update an
in-flight prompt. A failed request can be retried only when the client explicitly
acknowledges its request ID. This differs from the separate diagnostic sampling
mode, which selects states at a configurable interval (default 1 s) and uses a
bounded queue. Neither mode guarantees inference on every SocialState.

In run-02, eligibility took approximately 1.1 s from the first perception frame
(2.09 s for S7). Actual Ollama HTTP calls took 4.74–18.00 s. Only 0.003–0.025 s
of the elapsed time after eligibility was outside that call. Thus application
queueing and result polling do not explain the long delay in these runs.
The model receives one frozen summary of accumulated temporal evidence, not a
continuously updated stream of observations during generation.

The local `/api/ps` check during this investigation reported `size_vram: 0` for
`qwen2.5:7b` (7.6B parameters, Q4_K_M, context 8192), indicating CPU execution.
That placement check describes this investigation, not historical GPU telemetry;
the old audit did not retain placement or the detailed Ollama timing counters.

## Controlled scenario 3 probes

The saved scenario 3 SocialState was held fixed across three sequential calls.
Model, temperature 0, seed 42, context 8192 and output limit 192 were unchanged.
No expected scenario action was inserted into the observation. The concise
prompt contains general decision precedence. These are diagnostic calls, not
new live trial results; production code and prior reports were not overwritten.

| Probe | Model action | Input tokens | Output tokens | Input evaluation (s) | Generation (s) | Total HTTP time (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Original prompt and schema | CONTINUE | 3533 | 54 | 31.95 | 3.81 | 38.82 |
| Original prompt, reordered action enum | APPROACH | 3533 | 76 | 0.08 | 5.80 | 5.92 |
| Concise prompt, original schema | ENGAGE | 1531 | 27 | 15.88 | 1.75 | 17.71 |

The first call also reported 3.04 s load time. The second reused the same prompt,
so its much lower input evaluation time is consistent with prompt caching. The
short prompt changed the prefix and required input processing again. These
sequential calls therefore do not constitute a controlled throughput benchmark.
They do demonstrate that prompt processing can dominate latency.

The original system prompt has 1,556 whitespace-separated words and 11,979
characters, plus roughly 3,100–3,300 characters of SocialState when a person is
present. It devotes extensive instructions to exception cases, particularly
YIELD. The model's S2/S3 explanations suggest it sometimes jumps from a failed
YIELD check to CONTINUE instead of applying the attention/distance branches.
S4 and S9 also contain incorrect numeric comparisons, and S7's explanation
names categories absent from the source state.

All four actions are allowed by the request schema. The production parser does
not substitute CONTINUE on errors; the recorded raw model text explicitly
contains CONTINUE. The different source states also reach the model correctly.
Changing only the enum order produced a different but still incorrect action,
showing sensitivity to an irrelevant formatting change in this probe. Changing
the instructions to a concise ordered policy produced the correct ENGAGE for
S3. This supports prompt/model behaviour as a contributing problem; it does not
establish a unique cause or validate the concise prompt across all nine cases.

The next policy revision should test concise decision precedence and shorter
reasons across all nine saved states, then measure live delivery separately.
Increasing freshness limits alone would retain obsolete observations for longer
without addressing the incorrect actions or inference cost.

Exact requests, raw replies and Ollama timing counters are in
[llm-diagnostic.json](llm-diagnostic.json). The probe code is
[diagnose_llm.py](diagnose_llm.py). No physical robot actions were executed.

## Follow-up: hardware and earlier configuration

The evaluation connects to `http://127.0.0.1:11434`, so inference runs on this
computer's local Ollama service. A subsequent read-only hardware check found an
NVIDIA display device on PCI, but `nvidia-smi` failed with “couldn't communicate
with the NVIDIA driver.” This is evidence of unavailable NVIDIA acceleration in
the checked environment; it does not establish when that condition began.
The subsequent `/api/ps` response was empty, meaning the model was no longer
resident at that point. The earlier populated response reported zero VRAM use.

The previous comparison preset and the October 8 first-action manifest already
used `qwen2.5:7b`, context 8192 and output limit 192. The recent tuning did not
switch to a larger model. The system prompt did grow: v6 had 1,131 words / 8,552
characters; v10 had 1,329 / 10,084; v12 has 1,556 / 11,979. Those counts exclude
the SocialState JSON. The earlier fixed-state v6 study reported mean latency
5.425 s over 11 calls (see `docs/results/pipeline-refinement/comparison.md`).
Its inputs and runtime conditions differ from the current nine scenarios, so
that is not a controlled measurement of the regression's cause. Historical GPU
placement was not captured, and a change from GPU to CPU cannot be asserted.

The current transport uses `stream: false` and accepts the complete action-plus-
reason JSON, so explanation generation contributes to response latency. In the
measured first-action runs, pipeline scheduling/polling overhead after readiness
was only milliseconds. Prompt processing, CPU inference, response length and
cache residency explain the observed costs more directly than a scheduling delay.
