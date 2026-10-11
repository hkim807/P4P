# Reproduction and verification

The immutable manifest identifies the final preset, effective defaults, prompt, model digest, runtime and frozen code/source hashes. All hashes match after replay/reporting. Every operational observation trace is checked against its exact causal source prefix; ingestion counts agree. First eligible canonical state hashes match across policies when available. All 54 unique scenario/policy/repetition rows are retained, and exported CSV counts reconcile to summary.json. Human scores remain blank. See tests.log for first-output stopping, asynchronous ingestion, stale/expired delivery and evaluation tests.

Reproduce with local Ollama access from the repository root; choose a new output directory:

```sh
.venv/bin/python -m evaluation.poster_first_output --output var/evaluation/poster-final-first-NEW --prepare --run --report
```

The original run's endpoint inference is intentionally absent. A first output is not the same as an accepted or physically executed action. Survey scores require human references for this decision moment.
