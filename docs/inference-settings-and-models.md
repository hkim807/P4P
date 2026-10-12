# Stages 7 and 8: inference settings and alternative models

These experiments hold the shared DecisionState v3, disabled temporal additions,
v16 instructions, tuned v7 rules, estimator and acceptance checks fixed. Every
model receives the same messages and state. The recordings and original first
eligible timestamps remain unchanged. First-action alignment and time until the
complete output is observed are primary; later snapshots are diagnostics only.

Stage 7 screens three changes independently against a fresh Qwen 2.5 7B control:
context 8192 → 2048, maximum output 192 → 64, and both together. Temperature 0
and seed 42 remain fixed. A screen makes one direct call per scenario; it is not
a production replay score. A promising setting receives three operational
repetitions and nine later diagnostics, alongside a freshly measured control.
Arm order alternates by repetition.

Selection requires no per-scenario alignment loss, all valid and untruncated
first outputs, at least 10% lower median time and no worse 95th percentile. A
smaller output limit is a ceiling, not an instruction to stop sooner when an
answer is already complete. The full prompt and at least 256 tokens of headroom
must fit. Messages, structured-output schema and actual transport bytes are
verified; input truncation is prohibited.

Stage 8 compares Qwen 2.5 7B with Qwen 2.5 3B, Qwen 3 4B Instruct and Gemma 3
4B. The Instruct tag avoids introducing a separate thinking-output pipeline.
Gemma receives text only. All use the selected stage 7 settings. Each model has
three operational repetitions and nine later diagnostics. Model order rotates
between repetitions; one model is loaded at a time, with weights warmed before
each block. No later observation primes the primary trials. Local model digests,
templates, quantization metadata, runtime and loaded-memory information are
recorded. Different tokenizers/templates naturally produce different token counts.

Model selection prioritises higher first-action alignment without new scenario
losses or invalid outputs. Equal-quality models can be selected for at least
10% faster median time with no worse 95th percentile. Reasons are reviewed
against the evidence separately; strict JSON validity is not semantic accuracy.

All 574 frames are checked against the original frozen rule implementation, with
402 eligible frames. No physical robot execution occurs. Acceptance reflects
finite recorded streams and is separate from correctness. The nine scenarios
are a tuning set; repeated deterministic trials measure stability on that set,
not independent generalisation accuracy. Warm weights do not mean a warm prompt
cache. Load time, prompt evaluation, generation, cache counts and delivery time
are retained separately.

The commands below create a new artifact directory. Download the named local
models first and keep Ollama running. Original evaluations are read-only.

```sh
.venv/bin/python -B -m scripts.evaluate_inference_variants prepare --output var/evaluation/inference-variants-new
.venv/bin/python -B -m scripts.evaluate_inference_variants screen --output var/evaluation/inference-variants-new
.venv/bin/python -B -m scripts.evaluate_inference_variants stage7 --output var/evaluation/inference-variants-new --settings context-2048-output-64
# Use the stage 7 selected settings name for stage 8:
.venv/bin/python -B -m scripts.evaluate_inference_variants stage8 --output var/evaluation/inference-variants-new --settings control
```

API behaviour is documented in the [Ollama chat API](https://docs.ollama.com/api/chat),
[context/preload/keep-alive FAQ](https://docs.ollama.com/faq) and
[parameter reference](https://docs.ollama.com/modelfile). Model tags are from the
official [Qwen 2.5 3B](https://ollama.com/library/qwen2.5:3b),
[Qwen 3 4B Instruct](https://ollama.com/library/qwen3:4b-instruct) and
[Gemma 3 4B](https://ollama.com/library/gemma3:4b) registry pages.

Measured results: [stages 7/8 report](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-7-8/REPORT.md).

Retain Qwen 2.5 7B with context 8192, maximum output 192, temperature 0 and seed
42. Context 2048 preserves 8/9 alignment but improves median complete-output
time only from 3.13 to 3.07 seconds (1.7%), with a slightly worse 95th percentile.
The output-ceiling and combined screens also show no useful speed gain.

The fresh model control scores 8/9 in every repetition at median 3.20 seconds.
Qwen 3 4B Instruct scores 7/9 at 2.49 seconds; Qwen 2.5 3B scores 4/9 at 1.81
seconds; Gemma 3 4B scores 5/9 at 2.96 seconds. Faster models introduce new
judgement and evidence-description errors. No model or setting is promoted.
The application, rules, estimator, shared state and prompt remain unchanged.

Warm the selected model **before** the encounter, using the same context size:

```sh
curl -s http://127.0.0.1:11434/api/generate -d '{"model":"qwen2.5:7b","prompt":"","stream":false,"keep_alive":"5m","options":{"num_ctx":8192}}'
```

This loads weights; it does not prime an encounter’s state or remove all prompt
evaluation time. The measured operational trials already use warm weights, so
this is a condition needed to reproduce their timings, not a newly measured
gain. Repeat warming after idle expiry; keep only the intended model resident.

The receiver can use the retained settings explicitly with new output paths:

```sh
.venv/bin/python -B -m app.server --host 127.0.0.1 --port 6060 \
  --output var/recordings/llm-v16-new.raw.jsonl \
  --social-output var/recordings/llm-v16-new.social.jsonl \
  --tracking-config config/person-tracking-final-scenarios.json \
  --temporal-config config/temporal-final-scenarios.json \
  --model-inference llm --llm-model qwen2.5:7b \
  --llm-num-ctx 8192 --llm-num-predict 192 \
  --llm-temperature 0 --llm-seed 42 --llm-timeout 120 \
  --model-output var/recordings/llm-v16-new.models.jsonl
```

This command is documented for future use; the evaluation did not connect to
the robot or execute actions.
