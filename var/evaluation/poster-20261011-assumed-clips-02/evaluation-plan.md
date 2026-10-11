# Frozen evaluation design

RQ1: structure gaze/distance histories into a shared SocialState and measure evidence availability.
RQ2: compare policy decisions with preferred actions and independent 1–5 appropriateness ratings.

The latest user instruction treats final-scenarios-audit/trimmed SDK recordings as the exact survey clips. This is an explicit assumption, not independently verified synchronisation. The last retained perception defines each assumed clip endpoint; unknown video timing uncertainty prevents a verified-endpoint claim. Nine original scenario-01..09 captures are audited separately, never pooled with the final captures as additional independent scenarios.

Reconstruct each selected capture causally from its first perception via the existing adapter, tracker and estimator. Select last perception at/before endpoint, max age 0.25s, unchanged production readiness. Freeze one canonical SocialState for both policies. One rule call and five sequential LLM calls per eligible endpoint, fixed model/seed/settings, no retries, fallback or majority vote. All nine scenarios remain in coverage tables. No diagnostic framewise model calls.

Operational: three complete recorded-speed repetitions, fresh production receiver and SingleTrial per scenario/policy. Within each scenario run rules/LLM, then LLM/rules, then rules/LLM. Warm weights separately before each repetition. Stop at first validated output including CONTINUE or existing termination. Ingest asynchronously; preserve pending, expired, rejected and late output. No hardware or physical execution.

Use 42-response source workbook if supplied; never substitute historical 37-response counts or preparation checks. Anonymous row indices only; no timestamps, identifying details or free text. Valid preference denominator includes unable-to-judge; missing and unexpected excluded and counted. Independent valid integer ratings 1–5 only, with missing/non-rating separate. Modal action set retains ties; highest mean rating separate.

Verified-endpoint metrics require verified mapping and workbook. Assumed-endpoint human metrics, if the workbook becomes available, must be labelled conditional on the user's assumption. SDK/camera proxies and first actions receive no human scores. Preference support = action votes / valid preference responses; appropriateness = mean valid action rating; modal agreement = membership in modal action set. Per-scenario LLM repeat means before equal-scenario paired differences; both policies must have valid outputs. Failures get no score. No intervals/significance planned, no generalisation from this development set; repeats/frames are not independent encounters.

Report whole-recording and causal endpoint-prefix evidence coverage, first readiness, UID gaps/epochs, exact holds. Coverage is frame-weighted with explicit numerator/denominator. Distance change is robot-relative, not human motion; missing people do not prove clear space; absent conflict/pass cues remain UNKNOWN. Separate request durations, elapsed encounter times, source age and acceptance. Rule receiver-request time includes processing and is not isolated inference latency.

Sources/configuration/code hashes and actual versions are frozen in manifest.json before inference. Preparation metadata says v5/v10; current code takes precedence. Missing workbook or mappings do not stop independent audits/replay. Reports may not claim comfort, safety, navigation success or physically executed ratings.
