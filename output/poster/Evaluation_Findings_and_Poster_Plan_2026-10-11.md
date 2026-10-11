# Evaluation findings and poster plan

Prepared 11 October 2026 for **Poster_59.pptx — Interactive Social Guide Robot**.

This document consolidates the final-preset, first-output replay and the supplied 42-response human survey. It provides the complete numerical findings, their interpretation, a plan for the existing Results and Conclusion spaces, and text ready to paste. The PowerPoint has not been edited.

## 1. Which evaluation to use

Use `var/evaluation/poster-20261011-final-first-output-01/` for policy and operational results, and `var/evaluation/poster-20261011-human-survey-options-01/` for the verified survey aggregates and figures.

The replay uses **the final scenario preset and the first validated policy output**, including CONTINUE. An output is included even when the client subsequently rejects it. This implements the requested “first available output” selection.

Treat the trimmed recordings in `var/evaluation/final-scenarios-audit/trimmed/` as the survey clips, as requested. Clip identity is an assumption; it has not been independently matched to the survey videos.

The older `poster-20261011-assumed-clips-02/` run used a different preset and endpoint selection. Its “4/9 states ready” figure is superseded for this poster condition. It did not mean that the final preset could only produce outputs for four scenarios. The current run reaches readiness in **9/9 scenarios**.

The saved final-replay report predates receipt of the workbook and still says the human results are unavailable. That statement is now outdated: survey findings are available below. What remains unresolved is a comparison at the same decision time: the survey asks what to do at the **video end**, whereas the replay selects the **first output**. This document updates the interpretation without changing the historical run artifacts.

## 2. Main findings

- All nine final scenarios reached a state eligible for a policy decision. Across the full recordings, 304/574 perception frames were ready (53.0%). These are different measures: eventual scenario readiness and frame coverage.
- Both policies generated valid outputs for all nine scenarios in every repetition. Rules had 9/9 client-accepted outputs; the LLM had 1/9, in each of three repetitions.
- The LLM generated outputs successfully, but 24/27 were rejected by delivery-time checks involving expired requests/source evidence, stale current observations, or current-state readiness. Only S5 was accepted in all three repetitions.
- Median time from the first perception frame to the first output was 1.088 s for rules and 11.457 s for the LLM. Median LLM request duration was 10.034 s. Output elapsed time includes waiting for evidence and is not isolated inference time.
- Initial eligible canonical state hashes matched in all 27 paired comparisons. Rules selected the same action across repetitions in all nine scenarios; the LLM did so in eight, varying in S1.
- The survey contains 42 respondents and complete preference answers for all nine scenarios. Strong preferences included CONTINUE in S1 (35/42, 83.3%) and YIELD in S4 (38/42, 90.5%).
- Most-preferred action and highest mean appropriateness differed in S2 and S3. Human judgments therefore should not be reduced to a single universal “correct” action.
- These results support a working shared-state decision pipeline and describe operational delivery and human preferences. They do not establish human-alignment accuracy, an LLM advantage, physical navigation success, or generalisation to new scenarios.

## 3. Evaluation design and units

| Item | Condition |
| --- | --- |
| Recordings | Nine trimmed final scenarios, S1–S9; previously used for development/tuning |
| Policy preset | config/policy-comparison-final.json |
| Rule / prompt versions | social-rules-v7 / social-state-llm-v14 |
| Temporal / tracking presets | config/temporal-final-scenarios.json / config/person-tracking-final-scenarios.json |
| LLM | qwen2.5:7b; reported parameter size 7.6B; Q4_K_M |
| LLM settings | Temperature 0; seed 42; context 8192; output cap 192 tokens; timeout 120 s |
| Replay | Recorded speed, asynchronous observation ingestion during inference |
| Repetitions | Three paired repetitions per scenario; 54 scenario–policy runs in total |
| Policy order | Rules then LLM; LLM then rules; rules then LLM |
| State isolation | Fresh receiver and single-trial state for each scenario/policy run |
| Warmup | Logged separately before each repetition |
| Output selection | First validated output, including CONTINUE, regardless of client acceptance |
| Delivery constraints | Current observation age ≤1 s; model source age ≤10 s; trial wait 30 s |
| Fallback | No rule fallback or correction of LLM decisions |
| Physical execution | None: replay did not command or move the robot |
| Human survey | 42 respondents; nine preference questions and four action ratings per scenario |

The independent scenario count is **nine**, not 27 or 54. Repetitions measure repeatability on the same recordings. The survey has **42 respondents**, not 378 independent participants: each respondent answered nine preference questions. Frame counts likewise do not create independent encounters.

## 4. RQ1: shared social state and evidence availability

The adapter, tracker and temporal estimator expose common observation histories, person visibility, current and historical gaze evidence, proximity, robot-relative distance trends, robot motion, descriptive state labels, validity flags and readiness. The first eligible canonical state matched between the two policies in **27/27 pairs**, with no missing paired eligible state.

### Coverage of the whole recordings

“Gaze evidence” and “distance trend” below count frames with usable evidence under the configured validity rules. They do not measure accuracy against independently annotated ground truth. Ready frames can occur without a detected person because the final preset permits a no-person CONTINUE decision after its evidence window.

| Scenario | Frames | Person detected | Ready frames (%) | First ready (s) | Gaze evidence | Distance trend |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | 105 | 67 | 76 (72.4%) | 1.076 | 49 | 45 |
| S2 | 48 | 48 | 37 (77.1%) | 1.072 | 37 | 37 |
| S3 | 47 | 47 | 36 (76.6%) | 1.127 | 36 | 36 |
| S4 | 62 | 61 | 11 (17.7%) | 1.086 | 11 | 24 |
| S5 | 69 | 0 | 58 (84.1%) | 1.123 | 0 | 0 |
| S6 | 47 | 47 | 31 (66.0%) | 1.067 | 32 | 10 |
| S7 | 89 | 79 | 11 (12.4%) | 2.090 | 11 | 8 |
| S8 | 72 | 45 | 20 (27.8%) | 1.090 | 4 | 34 |
| S9 | 35 | 35 | 24 (68.6%) | 1.074 | 24 | 24 |
| Total | 574 | 429 | 304 (53.0%) | — | 204 | 218 |

### Interpretation and missing cues

- Readiness is reached in every scenario, but coverage varies substantially: S5 has 84.1% ready frames, while S7 has 12.4% and S4 17.7%. “9/9 reached readiness” does not mean every frame had sufficient evidence.
- S5 has no detected-person frames. Its ready CONTINUE state follows the configured no-person behaviour; this does not prove that the route was physically clear or that no person was present.
- S8 also starts without a detected person, so its first CONTINUE output precedes later person observations. S6's first output precedes a later gaze event. First outputs need not reflect all events visible by the video ending.
- S4 and S6 become ready earlier but are not ready at the final frame: S4 ends with NO_VISIBLE_PERSON and S6 with INSUFFICIENT_GAZE_EVIDENCE. Under this preset, seven scenarios are ready at the end; that is a separate diagnostic from the primary 9/9 eventual-readiness result.
- Tracking records contain ID changes, observation gaps and track restarts. For example, S7 has five distinct recorded IDs and 15 track epochs. These are continuity diagnostics, not counts of distinct real people.
- Measured path-conflict and pass-gesture evidence is absent in these recordings (0/574 frames for each); those fields remain UNKNOWN. A geometric corridor estimate does not establish collision risk or route clearance.
- Distance change describes separation relative to the robot; it cannot by itself establish the person's motion or intent.

**RQ1 finding:** the implementation provides a common, inspectable state for both policies, and all scenarios become actionable under the final preset. Evidence availability remains uneven and several social cues are unmeasured. Perception accuracy was not evaluated.

## 5. Policy outputs, delivery and timing

### Output availability versus client acceptance

A **valid output** is a policy decision that passes output validation. **Client accepted** means that the recorded trial accepted it under the freshness/readiness checks. Neither means that a physical action was executed, nor that the action was socially correct.

| Policy | Valid outputs per repetition | Accepted per repetition | Valid across 3 repetitions | Accepted across 3 repetitions |
| --- | --- | --- | --- | --- |
| Rules | 9/9 | 9/9 | 27/27 | 27/27 |
| LLM | 9/9 | 1/9 | 27/27 | 3/27 |

There were no scenarios without a validated output. All 27 measured LLM requests produced valid outputs; no model parsing/inference failures were recorded in this run. Expiry at delivery is separate from output generation.

### All first actions

Actions are listed in repetition order. Times are medians across the three repetitions, measured from the first perception frame.

| Scenario | Rules (all 3) | LLM (reps 1 / 2 / 3) | Rules accepted | LLM accepted | Rules time (s) | LLM time (s) |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | CONTINUE | APPROACH / YIELD / YIELD | 3/3 | 0/3 | 1.077 | 12.132 |
| S2 | APPROACH | APPROACH / APPROACH / APPROACH | 3/3 | 0/3 | 1.074 | 11.968 |
| S3 | ENGAGE | ENGAGE / ENGAGE / ENGAGE | 3/3 | 0/3 | 1.128 | 11.116 |
| S4 | YIELD | APPROACH / APPROACH / APPROACH | 3/3 | 0/3 | 1.088 | 11.613 |
| S5 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3/3 | 3/3 | 1.124 | 3.811 |
| S6 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3/3 | 0/3 | 1.068 | 11.372 |
| S7 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3/3 | 0/3 | 2.092 | 11.914 |
| S8 | CONTINUE | CONTINUE / CONTINUE / CONTINUE | 3/3 | 0/3 | 1.091 | 3.899 |
| S9 | APPROACH | CONTINUE / CONTINUE / CONTINUE | 3/3 | 0/3 | 1.075 | 11.122 |

The policies agree in S2, S3, S5, S6, S7 and S8, and differ in S1, S4 and S9. This is policy-to-policy agreement, not human agreement. Rules are repeatable in 9/9 scenarios; the LLM is repeatable in 8/9 despite fixed temperature and seed. Three repetitions are too few to characterise its full variability.

Rules satisfy the designer's pre-specified allowed action sets in all nine scenarios in each repetition. Those sets are development expectations, sometimes permitting several actions. They are not independent human ground truth and should not be labelled “100% accuracy.”

### Timing across 27 runs per policy

| Measure | Median | Range |
| --- | --- | --- |
| Rules: elapsed to output | 1.088 s | 1.068–2.092 s |
| LLM: elapsed to output | 11.457 s | 3.789–18.110 s |
| LLM: model request duration | 10.034 s | 2.662–17.020 s |
| LLM: source age at delivery | 10.052 s | 2.666–17.034 s |
| LLM: latest observation age at delivery | 6.253 s | 0.023–8.375 s |
| Rules: source age at delivery | 1.537 ms | 1.103–4.870 ms |
| Rules: receiver request duration | 1.257 ms | 0.843–1.767 ms |

Output elapsed time includes waiting for sufficient evidence. Receiver request duration includes pipeline work and is not isolated rule inference. Exact source age at model completion was not separately instrumented; source age at delivery was measured. The observed timing applies to this model, local runtime and replay setup, not to all LLMs or hardware.

### Why LLM outputs were rejected

The following combinations are mutually exclusive and sum to all 27 LLM runs. Individual predicates overlap within a row.

| Recorded rejection combination | Runs |
| --- | --- |
| Request expired + model source expired + latest observation stale | 11 |
| Request expired + model source expired + latest observation stale + current state not ready | 6 |
| Latest observation stale only | 4 |
| Current state not ready only | 3 |
| Accepted; no rejection | 3 |

Across the 24 rejected outputs, 21 involved stale latest observations, 17 involved expiry, and nine involved current-state unreadiness; these overlapping totals must not be added. The three S8 rejections involved unreadiness without staleness/expiry. Therefore “all LLM failures were caused by latency” would overstate the evidence.

Categories were reconstructed from saved delivery errors and failed acceptance predicates because the trial interface returns a Boolean acceptance result. Short recordings stop supplying new observations; together with request duration and changing readiness this limits delivery. The replay does not isolate a hardware, networking or model-level causal explanation.

## 6. Human survey: all preference and rating results

The supplied workbook is `Human Evaluation of Robot Behaviour (Responses).xlsx`, sheet `Form responses 1`. It contains **42 respondents**. All nine preference questions have 42 valid answers, including “Unable to judge”; there are no missing or unexpected preference values. S3 and S6 each have one unable-to-judge response.

The preference denominator is **42 for every scenario**, retaining those unable-to-judge answers. There are 378 question responses across the 42 people. The four appropriateness ratings per scenario have 39–42 usable responses each: 1,497 valid rating cells and 15 missing/nonrating cells out of 1,512 possible cells. Missing ratings are excluded from that item, not imputed.

### Preferred next action at the video ending

Counts, with the modal action's percentage calculated over all 42 respondents. A modal action is the most frequent choice; it need not have a majority.

| Scenario | CONTINUE | APPROACH | ENGAGE | YIELD | Unable | Most preferred |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | 35 | 1 | 1 | 5 | 0 | CONTINUE: 35/42 (83.3%) |
| S2 | 8 | 19 | 13 | 2 | 0 | APPROACH: 19/42 (45.2%) |
| S3 | 1 | 6 | 16 | 18 | 1 | YIELD: 18/42 (42.9%) |
| S4 | 1 | 2 | 1 | 38 | 0 | YIELD: 38/42 (90.5%) |
| S5 | 24 | 1 | 2 | 15 | 0 | CONTINUE: 24/42 (57.1%) |
| S6 | 31 | 1 | 9 | 0 | 1 | CONTINUE: 31/42 (73.8%) |
| S7 | 6 | 21 | 15 | 0 | 0 | APPROACH: 21/42 (50.0%) |
| S8 | 4 | 8 | 26 | 4 | 0 | ENGAGE: 26/42 (61.9%) |
| S9 | 30 | 4 | 7 | 1 | 0 | CONTINUE: 30/42 (71.4%) |

### Appropriateness ratings

Mean on the 1–5 scale, followed by the usable response count in parentheses. These descriptive means summarise ordinal ratings; small differences are not evidence of a statistically reliable preference. The complete rating distributions and medians appear in Appendix A.

| Scenario | CONTINUE | APPROACH | ENGAGE | YIELD | Highest mean |
| --- | --- | --- | --- | --- | --- |
| S1 | 4.40 (42) | 2.10 (41) | 2.58 (40) | 2.85 (41) | CONTINUE |
| S2 | 2.86 (42) | 3.55 (42) | 3.73 (41) | 2.05 (39) | ENGAGE |
| S3 | 1.48 (42) | 3.02 (42) | 3.83 (42) | 3.45 (42) | ENGAGE |
| S4 | 1.36 (42) | 2.17 (41) | 2.46 (41) | 4.36 (42) | YIELD |
| S5 | 3.86 (42) | 1.76 (42) | 1.83 (42) | 3.00 (42) | CONTINUE |
| S6 | 4.12 (42) | 2.12 (42) | 2.79 (42) | 2.29 (41) | CONTINUE |
| S7 | 2.60 (42) | 3.64 (42) | 3.61 (41) | 1.73 (41) | APPROACH |
| S8 | 2.52 (42) | 3.29 (41) | 4.05 (42) | 2.10 (42) | ENGAGE |
| S9 | 4.02 (42) | 2.22 (41) | 2.93 (42) | 1.83 (42) | CONTINUE |

### Findings by scenario

- **S1:** CONTINUE has strong preference support (35/42, 83.3%) and the highest mean appropriateness (4.40). Rules first choose CONTINUE; LLM first choices vary between APPROACH and YIELD.
- **S2:** APPROACH is the most frequent preference (19/42, 45.2%), but ENGAGE has the highest mean rating (3.73 versus 3.55 for APPROACH). Both policies first choose APPROACH. The preference is a plurality, not a majority.
- **S3:** Preferences are split between YIELD (18/42, 42.9%) and ENGAGE (16/42, 38.1%). ENGAGE has the higher mean rating (3.83 versus 3.45 for YIELD). Both policies first choose ENGAGE. This is a useful example of why preference and acceptability are different measures.
- **S4:** YIELD has the strongest modal preference of the nine scenarios (38/42, 90.5%) and a mean rating of 4.36. Rules first choose YIELD; the LLM first chooses APPROACH in all repetitions. This is a descriptive contrast across decision times, not a validated LLM error rate.
- **S5:** CONTINUE leads (24/42, 57.1%), but YIELD also receives 15/42 preferences (35.7%). Their mean ratings are 3.86 and 3.00. Both policies first choose CONTINUE; this is the only scenario with accepted LLM outputs in every repetition.
- **S6:** CONTINUE leads (31/42, 73.8%) and has a mean rating of 4.12. ENGAGE receives 9/42 preferences. Both policies first choose CONTINUE; their selected evidence precedes a later gaze event.
- **S7:** APPROACH receives 21/42 preferences (50.0%) and ENGAGE 15/42 (35.7%). Their mean ratings are very similar (3.64 and 3.61). Both policies first choose CONTINUE. Do not describe 50.0% as a strict majority or the small mean difference as significant.
- **S8:** ENGAGE leads (26/42, 61.9%) and has a mean rating of 4.05. Both policies first choose CONTINUE using early evidence before a person is detected. This directly illustrates the decision-time mismatch.
- **S9:** CONTINUE leads (30/42, 71.4%) and has a mean rating of 4.02. Rules first choose APPROACH; the LLM first chooses CONTINUE. This alone does not demonstrate superior human alignment by the LLM.

There are no tied modal preferences. Highest mean and modal preference coincide in seven scenarios and differ in S2 and S3. No inferential significance tests or confidence intervals are reported, and no participant-level free-text themes or demographic conclusions have been established by this numerical analysis.

## 7. What the evaluation can answer

**RQ1:** the shared state representation is implemented and inspectable, supplies matching initial evidence to both policies, and reaches readiness in all nine scenarios with the final preset. Coverage and missing-cue findings describe the limits of the available observations.

**RQ2:** the replay describes differences in first actions, repeatability, generation timing and delivery acceptance. The survey describes human preferences and appropriateness at video end. Together they identify useful contrasts, but do not yet give a matched-time human-alignment score for either policy.

Using the same clip does not make an early first decision equivalent to an end-of-video judgment. Do not report first-output “human accuracy,” aggregate human support for selected first actions, or averaged human ratings of those actions as validated comparison scores. Client acceptance is also not a human rating.

To obtain that comparison later, either collect human judgments for the exact first-decision observation point or perform a separate final-preset analysis at the same endpoint shown to respondents. Label that as a new condition and keep it separate from these first-output results. The older development-preset endpoint results cannot fill this gap.

Other limits are the small set of nine development recordings, reused tuning scenarios, assumed clip identity, repeated judgments by the same respondents, missing ratings, unavailable cue annotations, the single local model/runtime configuration, and absence of physical execution. The findings do not demonstrate safety, comfort, collision avoidance, navigation success or performance on unseen encounters.

## 8. Plan for the existing poster

### Preserve the completed sections

Keep the title, Background, Objective, research-question strip, existing pipeline and four-action diagrams, and footer unchanged. Add content only inside the existing Results and Conclusion areas. The current task delivers this plan; it does not modify the presentation.

The source is a single A1 portrait slide. The empty Results box is approximately **541 × 199 mm**, starting at **x 26.5 mm, y 482.9 mm**. The Conclusion box is approximately **541 × 85 mm**, starting at **x 26.5 mm, y 707.7 mm**. Coordinates refer to the slide's top-left corner; retain the existing headings above those boxes and use about 8–10 mm internal margins.

### Recommended layout: survey chart plus operational results

1. **Top strip:** a concise setup line — “42 respondents · 9 recorded scenarios · 3 replay repetitions · final preset · first output”. This avoids using 27 repetitions as the scenario count.
2. **Left, around 60% of Results width:** the nine-row, 100% stacked human-preference chart. Use the same four action colours consistently and retain the unable-to-judge category. Title it “Human preferences at video end (n = 42)”.
3. **Right, around 40%:** the compact valid-output versus client-accepted chart. Title it “First-output replay: availability and delivery”. Show rules 9/9 valid and 9/9 accepted; LLM 9/9 valid and 1/9 accepted. Caption that these counts held in every repetition.
4. **Below the operational panel:** a short RQ1 callout — “All 9 scenarios reached readiness; initial states matched in all 27 paired replays.” If space permits, add “Ready frames: 304/574 (53.0%)” as a separate coverage measure.
5. **Bottom of Results:** one compact insight about survey ambiguity and one timing limitation. Prefer the S3 comparison (YIELD 18 versus ENGAGE 16 preferences; ENGAGE mean 3.83 versus YIELD 3.45) over another large figure.
6. **Conclusion:** a short paragraph addressing both research questions, followed by a three-step future-work line if space permits. Do not repeat the full numerical tables.

This layout gives the survey the main visual space while making the generation-versus-delivery distinction visible. Keep the all-scenario action table, rating distributions, detailed rejection categories and runtime settings in the companion report rather than the poster.

### Other presentation options

| Option | Use | Trade-off |
| --- | --- | --- |
| A. Preference chart + operational panel (recommended) | Shows human choice distributions and replay feasibility together | Detailed appropriateness ratings remain in this report |
| B. Rating heatmap + operational panel | Emphasises that several actions can be rated appropriate; highlight S2/S3 | Mean ratings hide preference vote splits; retain usable-n caption |
| C. Compact scenario table | Columns: scenario, human modal action (%), rule first action, LLM first-action distribution | Dense; must visibly state different decision times and avoid accuracy colours/check marks |

Use one principal human-results visual. Fitting both the full preference chart and full heatmap alongside operational results is likely to reduce legibility in the existing box. No new decorative image is needed: the existing pipeline diagram already explains the architecture.

### Figure assets ready to insert

SVG assets are preferable where PowerPoint supports them because labels remain sharp; PNG versions are available as fallbacks. Exact values are in the linked CSV files. The existing preference figure rounds segment labels, so use one decimal place in accompanying textual claims.

- **Human preference distribution:** [SVG](../../var/evaluation/poster-20261011-human-survey-options-01/option-a-human-preferences.svg) · [PNG](../../var/evaluation/poster-20261011-human-survey-options-01/option-a-human-preferences.png)

- **Appropriateness heatmap:** [SVG](../../var/evaluation/poster-20261011-human-survey-options-01/option-b-appropriateness.svg) · [PNG](../../var/evaluation/poster-20261011-human-survey-options-01/option-b-appropriateness.png)

- **Valid output and client acceptance:** [SVG](../../var/evaluation/poster-20261011-human-survey-options-01/operational-coverage.svg) · [PNG](../../var/evaluation/poster-20261011-human-survey-options-01/operational-coverage.png)

### Suggested small diagram

Use this only if the conclusion has space. It describes proposed next work, not experiments already performed:

```text
Match decision time  →  Improve delivery and cue coverage  →  Test unseen/live encounters
```

Match the existing heading and body styles (Rubik and Noto Sans). Those fonts are not available in the inspected local rendering environment, so check the final layout using the intended fonts before exporting. Do not change completed content to compensate for a fallback-font preview.

## 9. Text ready to paste

### Results: shared state and replay

> All nine final scenarios reached decision readiness, with identical initial states across 27 paired replays. Both policies generated valid first outputs in all scenarios. Client acceptance was 9/9 for rules and 1/9 for the LLM in each of three repetitions. Median time to first output was 1.09 s and 11.46 s, respectively. LLM delivery was limited by expiry, stale observations and changing readiness. No physical actions were executed.

### Results: human survey

> Forty-two respondents judged nine scenarios. CONTINUE was preferred in S1 (83.3%) and YIELD in S4 (90.5%). Judgments were more divided in S3: YIELD received 18 preferences and ENGAGE 16, while ENGAGE had higher mean appropriateness (3.83 versus 3.45). Preference and appropriateness capture different aspects of human judgment.

### Required comparison note

> Survey judgments concern the video ending; replay actions are the first available outputs. Human–policy agreement is therefore not scored. Trimmed recordings are assumed to correspond to the survey clips.

### Conclusion

> The shared social-state pipeline supported both policies across all nine recorded scenarios. Rules achieved more reliable client acceptance in this replay; LLM delivery was limited by freshness and readiness checks. Human judgments showed both strong preferences and scenario-dependent ambiguity. Future evaluation should align decision times, improve cue coverage and delivery, and test unseen scenarios with physical execution.

### Figure captions

**Preference chart:**

> Preferred next action at video end for nine scenarios (42 respondents each). Percentages include “Unable to judge”; segment labels are rounded.

**Operational panel:**

> Both policies produced valid outputs in 9/9 scenarios. The client accepted 9/9 rule outputs and 1/9 LLM outputs in each of three repetitions. First output includes CONTINUE; acceptance does not imply physical execution.

**Optional heatmap:**

> Mean appropriateness on a 1–5 scale; n = 39–42 per scenario/action. Missing ratings are excluded. Descriptive differences are not significance tests.

### Avoid these poster claims

- “Only 4/9 scenarios worked” — superseded condition and ambiguous meaning of readiness.
- “Rules achieved 100% human accuracy” — designer labels and client acceptance are not human ground truth.
- “The LLM failed to output in eight scenarios” — it generated valid outputs in all nine; eight were rejected per repetition.
- “The LLM was less/more socially appropriate” — the current human and policy decisions are not aligned in time.
- “All LLM failures were caused by latency” — unreadiness also caused rejections without staleness or expiry.
- “27 independent scenarios” or “378 participants” — the study has nine recordings and 42 respondents.
- “Validated safe navigation” — no physical actions were executed.

## 10. Evidence, verification and reproduction

The original replay verification reports 14 focused tests passing, reconciliation of the 54 exported decision rows, matching paired initial state hashes, causal observation-prefix checks, and unchanged frozen source/code/configuration hashes. This document consolidates existing verified artifacts; it does not run a new policy evaluation.

Survey verification confirms 42 respondents, all preference denominators equal to 42, no missing/unexpected preference choices, and 15 missing/nonrating rating cells. The supplied workbook differs from the older preparation hash, so the actual received workbook hash below identifies the analysed file; the expected preparation count checks still pass. The numerical export contains no identifying cells or free-text responses.

- [Replay summary](../../var/evaluation/poster-20261011-final-first-output-01/summary.json)

- [Replay manifest and full settings](../../var/evaluation/poster-20261011-final-first-output-01/manifest.json)

- [All 54 decisions](../../var/evaluation/poster-20261011-final-first-output-01/first_action_decisions.csv)

- [Evidence coverage and tracking diagnostics](../../var/evaluation/poster-20261011-final-first-output-01/sensor_coverage.csv)

- [Replay verification](../../var/evaluation/poster-20261011-final-first-output-01/verification.md)

- [Survey aggregate JSON](../../var/evaluation/poster-20261011-human-survey-options-01/human-reference.json)

- [All preference counts](../../var/evaluation/poster-20261011-human-survey-options-01/human_preferences.csv)

- [All rating statistics](../../var/evaluation/poster-20261011-human-survey-options-01/human_ratings.csv)

- [Survey verification](../../var/evaluation/poster-20261011-human-survey-options-01/verification.json)

- [Figure generation script](../../var/evaluation/poster-20261011-human-survey-options-01/build_figures.py)

Provenance:

- Replay source commit: `820d4a98e739a1016254d2e0cdc2a54670673bae`.
- Received workbook SHA-256: `e7ac526de96cc2c0f9658068ad600bcb6fab7684b0829f3417cec3f71c9f6bac`.
- Selected model digest: `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
- Recorded Ollama version: `0.34.0`.
- Original poster SHA-256: `e937ba6f9c3907acf97084a354c2e8c4621bfadf33746d3973738d5fbd3fc5a3`.

To reproduce the replay later, with local Ollama available, use a new output directory from the repository root:

```sh
.venv/bin/python -m evaluation.poster_first_output   --output var/evaluation/poster-final-first-NEW --prepare --run --report
```

Preserve the existing run folders. Earlier original-recording audits and development-preset endpoint runs are historical conditions, not additional independent scenarios to pool with these nine.

## Appendix A. Complete appropriateness distributions

Counts in columns 1–5 refer to the corresponding rating value. Missing/nonrating cells are excluded from n, mean and median. Means are rounded to two decimals here; full precision is in the rating CSV.

| Scenario | Action | n | Missing | Mean | Median | 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | CONTINUE | 42 | 0 | 4.40 | 5 | 2 | 1 | 1 | 12 | 26 |
| S1 | APPROACH | 41 | 1 | 2.10 | 2 | 15 | 13 | 8 | 4 | 1 |
| S1 | ENGAGE | 40 | 2 | 2.58 | 3 | 9 | 9 | 14 | 6 | 2 |
| S1 | YIELD | 41 | 1 | 2.85 | 3 | 9 | 5 | 14 | 9 | 4 |
| S2 | CONTINUE | 42 | 0 | 2.86 | 3 | 7 | 7 | 16 | 9 | 3 |
| S2 | APPROACH | 42 | 0 | 3.55 | 4 | 6 | 6 | 5 | 9 | 16 |
| S2 | ENGAGE | 41 | 1 | 3.73 | 4 | 2 | 2 | 10 | 18 | 9 |
| S2 | YIELD | 39 | 3 | 2.05 | 2 | 16 | 9 | 11 | 2 | 1 |
| S3 | CONTINUE | 42 | 0 | 1.48 | 1 | 33 | 4 | 1 | 2 | 2 |
| S3 | APPROACH | 42 | 0 | 3.02 | 3 | 4 | 9 | 15 | 10 | 4 |
| S3 | ENGAGE | 42 | 0 | 3.83 | 4 | 3 | 2 | 7 | 17 | 13 |
| S3 | YIELD | 42 | 0 | 3.45 | 4 | 5 | 6 | 9 | 9 | 13 |
| S4 | CONTINUE | 42 | 0 | 1.36 | 1 | 36 | 2 | 1 | 1 | 2 |
| S4 | APPROACH | 41 | 1 | 2.17 | 2 | 16 | 9 | 11 | 3 | 2 |
| S4 | ENGAGE | 41 | 1 | 2.46 | 2 | 13 | 8 | 9 | 10 | 1 |
| S4 | YIELD | 42 | 0 | 4.36 | 5 | 2 | 1 | 1 | 14 | 24 |
| S5 | CONTINUE | 42 | 0 | 3.86 | 4 | 2 | 4 | 5 | 18 | 13 |
| S5 | APPROACH | 42 | 0 | 1.76 | 1 | 23 | 12 | 3 | 2 | 2 |
| S5 | ENGAGE | 42 | 0 | 1.83 | 1 | 22 | 11 | 5 | 2 | 2 |
| S5 | YIELD | 42 | 0 | 3.00 | 3 | 8 | 10 | 8 | 6 | 10 |
| S6 | CONTINUE | 42 | 0 | 4.12 | 4 | 2 | 1 | 5 | 16 | 18 |
| S6 | APPROACH | 42 | 0 | 2.12 | 2 | 16 | 12 | 8 | 5 | 1 |
| S6 | ENGAGE | 42 | 0 | 2.79 | 3 | 11 | 5 | 13 | 8 | 5 |
| S6 | YIELD | 41 | 1 | 2.29 | 2 | 11 | 14 | 9 | 7 | 0 |
| S7 | CONTINUE | 42 | 0 | 2.60 | 3 | 10 | 10 | 13 | 5 | 4 |
| S7 | APPROACH | 42 | 0 | 3.64 | 4 | 6 | 3 | 5 | 14 | 14 |
| S7 | ENGAGE | 41 | 1 | 3.61 | 4 | 3 | 0 | 14 | 17 | 7 |
| S7 | YIELD | 41 | 1 | 1.73 | 2 | 19 | 14 | 8 | 0 | 0 |
| S8 | CONTINUE | 42 | 0 | 2.52 | 2 | 12 | 10 | 8 | 10 | 2 |
| S8 | APPROACH | 41 | 1 | 3.29 | 4 | 5 | 6 | 7 | 18 | 5 |
| S8 | ENGAGE | 42 | 0 | 4.05 | 4 | 2 | 2 | 5 | 16 | 17 |
| S8 | YIELD | 42 | 0 | 2.10 | 2 | 18 | 11 | 7 | 3 | 3 |
| S9 | CONTINUE | 42 | 0 | 4.02 | 5 | 4 | 4 | 1 | 11 | 22 |
| S9 | APPROACH | 41 | 1 | 2.22 | 2 | 14 | 11 | 9 | 7 | 0 |
| S9 | ENGAGE | 42 | 0 | 2.93 | 3 | 9 | 5 | 13 | 10 | 5 |
| S9 | YIELD | 42 | 0 | 1.83 | 2 | 19 | 15 | 4 | 4 | 0 |
