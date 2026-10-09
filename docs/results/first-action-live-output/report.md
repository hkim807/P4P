The nine scenario SDK captures were replayed at recorded speed through the production receiver, tracker, temporal estimator, policy, and SingleTrial acceptance checks. Each run stopped at the first validated policy output, including CONTINUE; scenarios without an eligible output ended at source exhaustion. Pending inference was allowed to resolve after source exhaustion. No robot actions were executed.

| Scenario | Rule first output | Time | Client accepts | LLM first output | Time | Client accepts |
|---|---|---:|---|---|---:|---|
| S1 | No action | — | — | No action | — | — |
| S2 | ENGAGE | 8.28s | Yes | ENGAGE | 12.39s | No |
| S3 | ENGAGE | 1.19s | Yes | ENGAGE | 6.50s | No |
| S4 | No action | — | — | No action | — | — |
| S5 | No action | — | — | No action | — | — |
| S6 | No action | — | — | No action | — | — |
| S7 | No action | — | — | No action | — | — |
| S8 | No action | — | — | No action | — | — |
| S9 | APPROACH | 1.22s | Yes | CONTINUE | 8.90s | No |

S2 becomes eligible at 8.27 seconds, on its final SDK frame. The LLM takes 4.07 seconds and returns ENGAGE; the latest observation is then 4.12 seconds old, exceeding the client’s 1-second freshness limit. It is a resolved policy output, not an accepted robot decision.

S3 becomes eligible at 1.19 seconds. The LLM returns ENGAGE at 6.50 seconds, when the current gaze category is no longer ready. The current frame is fresh but the client rejects delivery. The initial diagnostic run accepted ENGAGE at 7.18 seconds, showing that variable inference completion time interacts with changing readiness.

S9 becomes eligible at 1.21 seconds. Rules immediately output APPROACH. The LLM returns CONTINUE at 8.90 seconds, when no person is visible, so the client rejects it. The first LLM input is the exact same structured SocialState used for the first rule output; later frames update client eligibility while inference runs.

S1, S4, S5 and S7 contain no detected people in the SDK stream. S6 has four person frames, insufficient for gaze evidence; S8 has 32 discontinuous person frames, also insufficient. These runs make no LLM calls and produce no fallback action. Their status is RECORDING_ENDED_NO_ACTION rather than a placeholder survey-endpoint result.

The runner uses the frozen development configuration and qwen2.5:7b (temperature 0, seed 42, context 8192, output limit 192), with model weights loaded before encounter clocks start. Generation and prompt processing still take real time. The client uses the unchanged 30-second trial timeout, 1-second current-observation age limit, and 10-second model-source age limit. Polling occurs every 20ms. The maximum observed dispatch lag was 8.87ms.

Raw SDK observations are reconstructed through NavelObservationAdapter using only prior locomotion packets, then submitted to the real /api/v1/observations and model-trial endpoints via the Flask test client. Every frame is ingested in order. The separate reconstruction estimator is not used for decisions. This preserves production application behavior; it cannot reproduce original network delay, collection delay, SDK perception errors or outgoing queue drops. Camera pixels are not reprocessed and this is not a VLM condition.

The stopped rule and LLM runs have different lengths because LLM inference is asynchronous. All three first eligible inputs are verified identical as structured JSON across policies. No frames after output resolution are ingested. This is a first-action development replay, so the human survey preferences for trimmed video endings are not accuracy labels for these earlier decisions.

The initial delivery-focused diagnostic is retained at ../first-action-live-replay. Its S2 request expired at 10 seconds and its audit later recorded ENGAGE after 15.22 seconds of inference. The final runner explicitly preserves validated model output separately from live-client acceptance, including expired delivery. No production policy, prompt, timeout or readiness logic was changed between these runs.

Reproduce from the repository root with local Ollama running (choose a new output directory):

```sh
.venv/bin/python -m scripts.replay_first_action --output var/first-action-new-run --warm-model
```

Each scenario directory retains result.json, models.jsonl for LLM runs, and losslessly compressed observation, state and receiver-event traces. summary.json and results.csv contain all 18 results. manifest.json records model identity, settings and runner hash; verification.json records source integrity, frame counts, causal prefixes and identical initial policy inputs. Latency and acceptance can vary with machine load and model cache state.

Validation: all 79 targeted receiver/client/replay/refinement tests passed, including five new tests for first-output stopping, empty source termination, asynchronous observations, stale EOF delivery and expired model delivery. The unchanged route-shutdown poll_stall test failed during the first check while inference ran, then passed when the same suite was rerun after replay. Frozen source/configuration hashes and all nine source capture hashes still match.
