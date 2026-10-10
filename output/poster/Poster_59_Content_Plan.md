# Project 59 poster content plan

**Working title:** Social navigation with the Navel robot  
**Students:** Eddie Kim and Jeruh Kim  
**Supervisor:** Ho Seok Ahn  
**Contact:** hkim807@aucklanduni.ac.nz

The strongest story at this stage is: **We built a decision pipeline that uses social cues over time. An initial survey establishes scenario-dependent human preferences, and development replay reveals gaps in cue availability and timely decision delivery. Human evaluation of the completed system is still to come.**

The poster should present the initial survey and pipeline comparison as two separate sources of evidence. The survey is a human reference collected from videos. It is not yet a study of people interacting with, or rating the behaviours executed by, the completed system.

## Recommended layout and content

| Position | Section | What to include | Suggested words |
| --- | --- | --- | ---: |
| Top | Title and identity | Short title, Project 59, Navel photograph. Students, supervisor, affiliation and contact can sit in the footer. | 20–35 |
| Upper left | Background | A guide robot must distinguish interest in interaction from ordinary passing. Gaze, distance and the continuity of observations affect this decision. | 25–35 |
| Upper right | Objective | Build a temporal social-state representation and compare a rule policy with an LLM using the same structured input. | 20–30 |
| Below background | Research question | How do rules and a language model respond to the same temporal social cues? Keep a broader LLM/VLM research question for the discussion if visual conditions are not yet measured. | 15–25 |
| Middle | Methods | Editable flow: Navel sensors → person histories → temporal social state → rule policy or LLM → validated action and reason. Identify the four action options and keep physical execution separate from policy output. | 35–50 |
| Main results, left | Initial survey | 37 respondents, nine videos, preferred next action and 1–5 appropriateness ratings. Show a stacked bar chart of preferred actions for all nine scenarios. | 30–45 plus chart |
| Main results, right | Pipeline output comparison | Same three initial eligible social states. Show S2, S3 and S9 outputs and elapsed time from recording start. State that six scenarios produced no action. Show live-client acceptance separately. | 40–60 plus table |
| Below results | Interpretation and limitations | The survey contains both clear and divided preferences. Detection and temporal evidence limit replay coverage. Resolved LLM output does not guarantee timely acceptance. Survey-end scoring awaits video alignment. | 35–50 |
| Bottom | Conclusion and next work | State the engineering contribution and preliminary findings. Plan aligned replay, repeated trials and human ratings of actual behaviour. | 30–45 |
| Footer | References and acknowledgements | Compact source references, university and department identities, participant acknowledgement, supervisor, contact and sponsors if applicable. | 25–40 |

Aim for **300–500 words including chart labels, table entries and footer text**. Give the results the most space. Use captions to explain each graphic without requiring a presenter beside it.

## Initial survey: evidence to include

The response workbook has **37 respondents** and a preferred-action response for every scenario. The chart denominator is 37 for every bar. “Unable to judge” is a separate response, not a robot action. Optional appropriateness ratings have between 34 and 37 valid responses per action, so any rating chart must show the applicable denominator.

| Scenario | Continue | Approach | Engage | Yield | Unable to judge | Modal preference |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| S1 | 30 | 1 | 1 | 5 | 0 | Continue, 30/37 (81.1%) |
| S2 | 6 | 18 | 11 | 2 | 0 | Approach, 18/37 (48.6%) |
| S3 | 1 | 5 | 12 | 18 | 1 | Yield, 18/37 (48.6%) |
| S4 | 1 | 2 | 0 | 34 | 0 | Yield, 34/37 (91.9%) |
| S5 | 21 | 1 | 2 | 13 | 0 | Continue, 21/37 (56.8%) |
| S6 | 26 | 1 | 9 | 0 | 1 | Continue, 26/37 (70.3%) |
| S7 | 5 | 19 | 13 | 0 | 0 | Approach, 19/37 (51.4%) |
| S8 | 3 | 8 | 23 | 3 | 0 | Engage, 23/37 (62.2%) |
| S9 | 26 | 4 | 7 | 0 | 0 | Continue, 26/37 (70.3%) |

Use two examples beside the chart: **S4 has a clear preference for Yield (34/37)**. **S3 is divided between Yield (18/37) and Engage (12/37)**. Avoid treating every modal action as an unambiguous correct label. In S3, Engage also has the highest mean appropriateness rating (3.78/5), while Yield is the modal preference. The two survey measures answer different questions.

If you later replace the chart with selected examples, keep S4 for agreement, S3 for ambiguity, and S2 for the Approach/Engage boundary. Do not cherry-pick only the clearest scenarios.

## Pipeline comparison: evidence to include

Use the latest **first-action development replay** in `docs/results/first-action-live-output`, rather than combining numbers from older snapshots or endpoint experiments. The experiment replays nine SDK captures through the production receiver and temporal pipeline. Rules and the LLM receive identical initial eligible structured social states. The LLM is **qwen2.5:7b**, with temperature 0 and seed 42. The model is warm before encounter timing begins.

| Scenario | Rule first output | Time from replay start | LLM first output | Time from replay start | Accepted by live client, rules / LLM |
| --- | --- | ---: | --- | ---: | --- |
| S2 | Engage | 8.28 s | Engage | 12.39 s | Yes / No |
| S3 | Engage | 1.19 s | Engage | 6.50 s | Yes / No |
| S9 | Approach | 1.22 s | Continue | 8.90 s | Yes / No |

Both policies produce outputs in **3/9 scenarios**, and agree on the selected action in **2/3 eligible scenarios**. This is policy-to-policy agreement on three initial decision inputs, not human-alignment accuracy. Do not report it as a robust 67% performance result.

The remaining six scenarios produce no action. S1, S4, S5 and S7 have no detected people in their SDK captures. S6 and S8 fail to provide sufficient temporal gaze evidence. **No action is not Continue**, and these cases must remain in the coverage denominator.

All three rule outputs pass the live-client acceptance checks in this run. All three LLM outputs fail acceptance: S2 has stale current observations, S3 is no longer gaze-ready when inference finishes, and S9 has no visible person when the output arrives. Report this distinction alongside the policy outputs. The acceptance outcome and timings can change with machine load and inference timing.

The displayed times are **elapsed seconds from replay start**, not model inference latency. Do not relabel them as latency. No physical robot actions were executed in this replay. It is an SDK/structured-state comparison, not a VLM or hybrid-input experiment.

## Claims to make, and claims to postpone

**Supported now**

- A temporal social-state and decision-output pipeline is implemented.
- The initial survey captures scenario-specific preferred actions and appropriateness ratings.
- Development replay exposes missing detections, insufficient cue history and delayed decision delivery.
- Rules and an LLM can agree on an action while differing in whether the live client accepts their outputs.

**Reserve for later evaluation**

- Which policy is more socially appropriate, safer or preferred in actual interaction.
- Human-alignment accuracy at survey-video endings. The survey clips were trimmed and the offsets are unavailable, so the replay decisions cannot yet be scored against the corresponding survey endpoints.
- LLM versus VLM or combined-input performance. The selected replay contains no evaluated visual-model condition.
- Generalisation, statistical superiority or repeatability from one development run.

## Human evaluation to add later

First align each survey video to its sensor recording. Freeze the cue settings and policy prompts, then run repeated trials on the same decision points. Preserve cue coverage, missing outputs, valid outputs and accepted decisions as separate measures.

For the later human evaluation, ask participants to rate actual robot behaviours for social appropriateness, comfort, predictability and perceived safety. Compare policy variants under equivalent conditions. Predefine the sample, measures and analysis before collecting that study. Use its findings to replace part of the current limitations area, rather than adding a dense extra section.

## Design and submission

The draft follows the exemplar's large title, paired background/objective panels, horizontal methods flow, grouped results and closing summary. Rounded **Rubik** headings and **Noto Sans** body text replace the exemplar's fonts. The colours echo the supplied official Navel photograph: cream/white body, charcoal cap and blue eyes. Dark blue is the main accent, with pale blue panels and a light background. These are design choices, not a claim about Navel's official brand typography.

The supplied department guide specifies **A1 portrait (594 × 841 mm), PDF, less than 50 MB**, with project number, title, student names, supervisors, sponsors if applicable, university logo, department logo and references. Name the final submission **Poster_59.pdf** and have both students submit the same approved version individually if the current instructions retain that requirement.

The guide's deadline is from **2024**. Do not reuse it as a 2026 deadline. Confirm the current Canvas submission details. The draft has the university logo extracted from the exemplar and a department identity area. **The official department logo still needs to replace the clearly labelled draft field**. No sponsor has been identified, so add sponsor information only if applicable.

The main poster title is 90 pt, main section headings are 60 pt and narrative body text is 36 pt. Figure and source text use a smaller supporting size where necessary. Check the final poster at actual size and from the intended viewing distance before submission.

## Sources

- Design and required submission elements: `/Users/eddiekim/Downloads/Poster Design Guide.pdf`, especially pages 4–7.
- Design principles: `/Users/eddiekim/Downloads/Speech_Nilushika2023.pdf`.
- Layout reference and university logo: `/Users/eddiekim/Downloads/Poster_77 (1).pdf`.
- Survey: `/Users/eddiekim/Downloads/Human Evaluation of Robot Behaviour (Responses).xlsx`, sheet `Form responses 1`. All nine preference distributions and all 36 rating means were checked against `config/human-reference.json`.
- Pipeline results: `docs/results/first-action-live-output/report.md`, `results.csv` and `verification.json`.
- Alignment limits and separate endpoint diagnostics: `docs/pipeline-refinement.md` and `docs/results/pipeline-refinement/comparison.md`.
- Navel photograph: [official Navel product page](https://navelrobotics.com/en/navel-the-new-empathy-robot-for-good-care/), photograph Navel0157.

The attached guides supply design and submission constraints. Their historical deadlines and the exemplar's claims, participant results and acknowledgements do not apply to Project 59.
