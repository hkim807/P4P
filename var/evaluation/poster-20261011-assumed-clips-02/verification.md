# Verification

See verification.json for machine checks and tests.log for targeted test output. Source recordings, configuration, production code and execution runner retain their frozen hashes. The workbook-only reporting helper changed during replay without participating in execution; the runner caught this after all measurements and exited with an integrity error. execution-completion.json explicitly records this exception; the original manifest is not rewritten. reporting-manifest.json identifies final reporting code. All 54 endpoint rows (one rules plus five LLM slots per scenario) and 54 operational rows (nine × two × three) are accounted for. Hash equality checks identical canonical endpoint state bytes and first eligible production states. Operational events match exact causal perception prefixes and reported ingestion counts. No future observations are selected; exact endpoint age is zero under the SDK-end assumption. Whole-frame readiness holds reconcile to frame counts. Exported CSVs are re-read and reconcile to summaries. Proxy/operational human-score columns are blank. Missing workbook totals/denominators are NOT_VERIFIED, never inferred from the prompt.

The last retained perception is a user-assumed video endpoint, not verified video timing. No endpoint uncertainty interval is known. No verified-endpoint human estimate exists. Rules request timing includes the production receiver; isolated rule inference and exact model-completion source age were not separately measured.

Reproduce from repository root, choosing a new directory, with the configured local Ollama model available:

```sh
.venv/bin/python -m unittest tests.test_poster_evaluation tests.test_first_action_replay -v
.venv/bin/python -m evaluation.poster_run --output var/evaluation/poster-NEW --prepare
.venv/bin/python -m evaluation.poster_run --output var/evaluation/poster-NEW --run
.venv/bin/python -m evaluation.poster_report --output var/evaluation/poster-NEW
# If available, append: --workbook '/absolute/path/to/responses.xlsx'
```

Python requires localhost network permission for Ollama. Preserve missing-source attempts; do not replace their recorded status. No model substitution or threshold changes are made.
