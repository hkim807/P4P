# Development-set baseline results

Counts describe frame-level outputs, not independent trials or human accuracy. Rule uses every frame; LLM uses the declared sampling interval. Agreement uses matched sampled states only. NOT_READY is excluded from action agreement. Last valid action is not an episode verdict.

| Scenario | Detected / frames | Rule counts | LLM counts | Agreement | Not ready |
|---|---:|---|---|---:|---:|
| scenario-01 | 0/91 | {'CONTINUE': 91} | {} | — | 0.0% |
| scenario-02 | 27/84 | {'CONTINUE': 28, 'NOT_READY': 56} | {} | — | 66.7% |
| scenario-03 | 110/115 | {'NOT_READY': 34, 'ENGAGE': 75, 'CONTINUE': 6} | {} | — | 29.6% |
| scenario-04 | 0/55 | {'CONTINUE': 55} | {} | — | 0.0% |
| scenario-05 | 0/40 | {'CONTINUE': 40} | {} | — | 0.0% |
| scenario-06 | 4/77 | {'CONTINUE': 69, 'NOT_READY': 8} | {} | — | 10.4% |
| scenario-07 | 0/29 | {'CONTINUE': 29} | {} | — | 0.0% |
| scenario-08 | 32/117 | {'CONTINUE': 72, 'NOT_READY': 45} | {} | — | 38.5% |
| scenario-09 | 56/122 | {'NOT_READY': 45, 'APPROACH': 20, 'CONTINUE': 57} | {} | — | 36.9% |
