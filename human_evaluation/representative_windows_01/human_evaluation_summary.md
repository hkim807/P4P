# Human Evaluation: Representative-Window Policy Comparison

## Method and scope

This revised exploratory evaluation compares the existing rule-based and LLM policies using sensor-selected three-second windows from 72 in-lab recordings: nine pseudonymous people × eight retained scenarios. The researcher confirms that participants performed the intended scenarios correctly. That confirmation is researcher-provided information; the selected windows were not verified against corresponding video. Missing detections, unreliable gaze, fragmented tracking and insufficient readiness remain part of the evaluated pipeline performance. No recording is excluded for these conditions.

Each recording was processed chronologically from its beginning through its frozen endpoint T, inclusive, using the existing SDK adapter, tracking, SocialState estimation and DecisionState construction. Earlier observations initialise tracking and hysteresis; endpoint gaze and distance-trend evidence follow the configured trailing three-second window. Original timestamps and gaps are preserved, with integer microseconds used for endpoint matching. No observation after T enters the input, and no policy decision is run or latched before T. Preprocessing runs without real-time sleeps. The endpoint state is constructed once and the identical frozen input is reused for rules and all three LLM repetitions. Scenario IDs, filenames, targets and selection reasons are excluded from policy inputs.

This is fixed-state offline evaluation. Shared state-based readiness applies: an unready endpoint produces baseline CONTINUE with `DEFAULT_CONTINUATION` and its specific cause. A valid policy output is `POLICY_DECISION`. Inference or evaluation failures are `ERROR`, are not target matches, and are never converted into successful continuation. LLM calls are allowed to finish within the configured inference timeout; live SingleTrial locking, route-start waiting and post-recording freshness expiry do not apply. Each recording is one evaluation unit. LLM run 1 is the primary comparison; runs 2 and 3 assess repeatability, without selecting the best repetition or counting extra participants.

## Dataset and scenario targets

All 72 required recording identities are represented, nine per retained scenario. Sources follow `var/recordings/person-{person_number}-S{original_scenario_number}-sdk.jsonl`. Original S5 is excluded; original filenames are unchanged.

| New | Original | Intended scenario | Evaluation target | Survey modal action |
| --- | --- | --- | --- | --- |
| S1 | S1 | A person walks past beside the robot without looking at it or obstructing its route. | CONTINUE | CONTINUE |
| S2 | S2 | A person stands beside the route, within approachable distance, and continuously looks at the robot. | APPROACH | APPROACH |
| S3 | S3 | A person is on the route, already within interaction range, and continuously looks at the robot. | ENGAGE | YIELD |
| S4 | S4 | A person walks towards the robot along its route without looking at it. | YIELD | YIELD |
| S5 | S6 | A person walks past beside the robot, briefly glances at it, and then looks ahead again. | CONTINUE | CONTINUE |
| S6 | S7 | A person stands beside the route and initially looks intermittently at the robot. | APPROACH | APPROACH |
| S7 | S8 | A person walks away from the robot while continuously looking at it. | ENGAGE | ENGAGE |
| S8 | S9 | A person stands beside the route, continuously looks at the robot, and gestures for it to pass. | CONTINUE | CONTINUE |

The primary metric is **agreement with evaluation targets**. New S3 deliberately uses the project-defined ENGAGE target, although YIELD was the original survey modal action. The resulting metric is therefore not presented as agreement with the survey mode or objective accuracy. The full-dataset denominator is 72 and each scenario denominator is 9; errors remain non-matches in those denominators.

## Evaluated implementation and settings

| Item | Frozen value |
| --- | --- |
| Evaluated commit | fc26cd860d366c7b23fba39f31a5c2f199e8a8e5 |
| Rule version | social-rules-v7 |
| Prompt identifier | decision-state-llm-v16 |
| System prompt SHA-256 | ec56596714835d28af2562dfb8ae753474e0b10d67287704e4e53e2dc26dfd73 |
| model | qwen2.5:7b |
| temperature | 0.0 |
| seed | 42 |
| num_ctx | 8192 |
| num_predict | 192 |
| timeout_seconds | 120.0 |
| base_url | http://127.0.0.1:11434 |
| Model digest | 845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e |
| Ollama version | 0.35.1 |
| Temporal summary mode | NONE |
| Model quantisation | Q4_K_M |
| Temporal evidence | window 3.0 s; minimum span 1.0 s and 5 samples; gaze coverage 0.6 s and fraction 0.5; maximum gap 0.25 s |
| Gaze entry/exit | near 0.93/0.9; beyond 2.75 m: 0.97/0.95 |
| Sustained gaze fraction | entry/exit 0.8/0.65; minimum trailing run 0.0 s |
| Distance bands | too close 0.6 m; interaction maximum 2.0 m; approachable maximum 6.0 m; hysteresis 0.0 m |
| Corridor and yielding | half-width 0.4 m; hysteresis 0.05 m; fixed head True; closing-yield maximum 3.5 m |
| Distance trend | deadband 0.1 m/s; maximum speed 3.0 m/s; jump allowance 0.05 m; maximum fit residual 0.1 m |
| Other attention settings | recurring attention False; no-person explicit readiness null |
| Tracking | history 3.0 s; missing grace 0.75 s; nose reassociation maximum 0.45 m/0.25 s |

The actual implementation supplies prompt `decision-state-llm-v16`; the comparison configuration retains the older `social-state-llm-v14` annotation, which is not the prompt used. Model, prompt and parameters remained fixed for this evaluation. Production code, configuration and source recordings were not modified. No physical robot was connected or controlled, and no commit or push was performed.

## Frozen evaluation windows

All times below are seconds relative to the first valid perception receipt in the corresponding recording. Each interval is [T−3 s, T], inclusive. The 71 explicit endpoints and one fallback rule were fixed before this execution and were not reselected for cleaner evidence, readiness or agreement.

| Person | Original | New | Start (s) | End T (s) | Selection |
| --- | --- | --- | --- | --- | --- |
| person-1 | S1 | S1 | 31.080932 | 34.080932 | FROZEN_SENSOR_SELECTED |
| person-1 | S2 | S2 | 0.003388 | 3.003388 | FROZEN_SENSOR_SELECTED |
| person-1 | S3 | S3 | 0.074163 | 3.074163 | FROZEN_SENSOR_SELECTED |
| person-1 | S4 | S4 | 17.003951 | 20.003951 | FROZEN_SENSOR_SELECTED |
| person-1 | S6 | S5 | 4.071606 | 7.071606 | FROZEN_SENSOR_SELECTED |
| person-1 | S7 | S6 | 0.084136 | 3.084136 | FROZEN_SENSOR_SELECTED |
| person-1 | S8 | S7 | 29.272568 | 32.272568 | FIRST_DETECTION_PLUS_3S_FALLBACK |
| person-1 | S9 | S8 | 0.079778 | 3.079778 | FROZEN_SENSOR_SELECTED |
| person-2 | S1 | S1 | 2.187125 | 5.187125 | FROZEN_SENSOR_SELECTED |
| person-2 | S2 | S2 | 0.012721 | 3.012721 | FROZEN_SENSOR_SELECTED |
| person-2 | S3 | S3 | 0.125398 | 3.125398 | FROZEN_SENSOR_SELECTED |
| person-2 | S4 | S4 | 5.972264 | 8.972264 | FROZEN_SENSOR_SELECTED |
| person-2 | S6 | S5 | 4.103791 | 7.103791 | FROZEN_SENSOR_SELECTED |
| person-2 | S7 | S6 | 0.009264 | 3.009264 | FROZEN_SENSOR_SELECTED |
| person-2 | S8 | S7 | 6.675492 | 9.675492 | FROZEN_SENSOR_SELECTED |
| person-2 | S9 | S8 | 0.065885 | 3.065885 | FROZEN_SENSOR_SELECTED |
| person-3 | S1 | S1 | 11.004131 | 14.004131 | FROZEN_SENSOR_SELECTED |
| person-3 | S2 | S2 | 0.001883 | 3.001883 | FROZEN_SENSOR_SELECTED |
| person-3 | S3 | S3 | 0.061370 | 3.061370 | FROZEN_SENSOR_SELECTED |
| person-3 | S4 | S4 | 7.989333 | 10.989333 | FROZEN_SENSOR_SELECTED |
| person-3 | S6 | S5 | 2.013392 | 5.013392 | FROZEN_SENSOR_SELECTED |
| person-3 | S7 | S6 | 0.108629 | 3.108629 | FROZEN_SENSOR_SELECTED |
| person-3 | S8 | S7 | 7.298055 | 10.298055 | FROZEN_SENSOR_SELECTED |
| person-3 | S9 | S8 | 0.070107 | 3.070107 | FROZEN_SENSOR_SELECTED |
| person-4 | S1 | S1 | 5.457352 | 8.457352 | FROZEN_SENSOR_SELECTED |
| person-4 | S2 | S2 | 0.078464 | 3.078464 | FROZEN_SENSOR_SELECTED |
| person-4 | S3 | S3 | 0.121728 | 3.121728 | FROZEN_SENSOR_SELECTED |
| person-4 | S4 | S4 | 3.013729 | 6.013729 | FROZEN_SENSOR_SELECTED |
| person-4 | S6 | S5 | 5.000068 | 8.000068 | FROZEN_SENSOR_SELECTED |
| person-4 | S7 | S6 | 0.072830 | 3.072830 | FROZEN_SENSOR_SELECTED |
| person-4 | S8 | S7 | 4.807304 | 7.807304 | FROZEN_SENSOR_SELECTED |
| person-4 | S9 | S8 | 0.019412 | 3.019412 | FROZEN_SENSOR_SELECTED |
| person-5 | S1 | S1 | 22.180810 | 25.180810 | FROZEN_SENSOR_SELECTED |
| person-5 | S2 | S2 | 0.064577 | 3.064577 | FROZEN_SENSOR_SELECTED |
| person-5 | S3 | S3 | 0.085264 | 3.085264 | FROZEN_SENSOR_SELECTED |
| person-5 | S4 | S4 | 5.959945 | 8.959945 | FROZEN_SENSOR_SELECTED |
| person-5 | S6 | S5 | 6.309210 | 9.309210 | FROZEN_SENSOR_SELECTED |
| person-5 | S7 | S6 | 0.008894 | 3.008894 | FROZEN_SENSOR_SELECTED |
| person-5 | S8 | S7 | 10.774885 | 13.774885 | FROZEN_SENSOR_SELECTED |
| person-5 | S9 | S8 | 0.026615 | 3.026615 | FROZEN_SENSOR_SELECTED |
| person-6 | S1 | S1 | 13.013780 | 16.013780 | FROZEN_SENSOR_SELECTED |
| person-6 | S2 | S2 | 0.119089 | 3.119089 | FROZEN_SENSOR_SELECTED |
| person-6 | S3 | S3 | 0.082790 | 3.082790 | FROZEN_SENSOR_SELECTED |
| person-6 | S4 | S4 | 9.965217 | 12.965217 | FROZEN_SENSOR_SELECTED |
| person-6 | S6 | S5 | 3.991380 | 6.991380 | FROZEN_SENSOR_SELECTED |
| person-6 | S7 | S6 | 0.064781 | 3.064781 | FROZEN_SENSOR_SELECTED |
| person-6 | S8 | S7 | 7.138609 | 10.138609 | FROZEN_SENSOR_SELECTED |
| person-6 | S9 | S8 | 0.073817 | 3.073817 | FROZEN_SENSOR_SELECTED |
| person-7 | S1 | S1 | 4.066528 | 7.066528 | FROZEN_SENSOR_SELECTED |
| person-7 | S2 | S2 | 0.075406 | 3.075406 | FROZEN_SENSOR_SELECTED |
| person-7 | S3 | S3 | 0.106056 | 3.106056 | FROZEN_SENSOR_SELECTED |
| person-7 | S4 | S4 | 6.996833 | 9.996833 | FROZEN_SENSOR_SELECTED |
| person-7 | S6 | S5 | 6.563529 | 9.563529 | FROZEN_SENSOR_SELECTED |
| person-7 | S7 | S6 | 0.017433 | 3.017433 | FROZEN_SENSOR_SELECTED |
| person-7 | S8 | S7 | 6.930452 | 9.930452 | FROZEN_SENSOR_SELECTED |
| person-7 | S9 | S8 | 0.107258 | 3.107258 | FROZEN_SENSOR_SELECTED |
| person-8 | S1 | S1 | 6.015556 | 9.015556 | FROZEN_SENSOR_SELECTED |
| person-8 | S2 | S2 | 0.101656 | 3.101656 | FROZEN_SENSOR_SELECTED |
| person-8 | S3 | S3 | 0.126574 | 3.126574 | FROZEN_SENSOR_SELECTED |
| person-8 | S4 | S4 | 11.968102 | 14.968102 | FROZEN_SENSOR_SELECTED |
| person-8 | S6 | S5 | 2.005079 | 5.005079 | FROZEN_SENSOR_SELECTED |
| person-8 | S7 | S6 | 0.108725 | 3.108725 | FROZEN_SENSOR_SELECTED |
| person-8 | S8 | S7 | 6.457898 | 9.457898 | FROZEN_SENSOR_SELECTED |
| person-8 | S9 | S8 | 0.064458 | 3.064458 | FROZEN_SENSOR_SELECTED |
| person-9 | S1 | S1 | 2.814015 | 5.814015 | FROZEN_SENSOR_SELECTED |
| person-9 | S2 | S2 | 0.072574 | 3.072574 | FROZEN_SENSOR_SELECTED |
| person-9 | S3 | S3 | 0.007824 | 3.007824 | FROZEN_SENSOR_SELECTED |
| person-9 | S4 | S4 | 2.302291 | 5.302291 | FROZEN_SENSOR_SELECTED |
| person-9 | S6 | S5 | 5.085582 | 8.085582 | FROZEN_SENSOR_SELECTED |
| person-9 | S7 | S6 | 0.092675 | 3.092675 | FROZEN_SENSOR_SELECTED |
| person-9 | S8 | S7 | 7.692809 | 10.692809 | FROZEN_SENSOR_SELECTED |
| person-9 | S9 | S8 | 0.003833 | 3.003833 | FROZEN_SENSOR_SELECTED |

For **person-1/original S8 (new S7)**, the fallback finds the first valid perception frame containing a detected person at offset d, then takes the first valid perception timestamp at or after d+3 s. The resulting endpoint is **32.272568 s**, with window **[29.272568, 32.272568] s**. It is explicitly a fallback because a representative encounter segment could not be identified reliably; this recording remains included. The first detection occurred at d = 29.202573 s; d+3 s is 32.202573 s. The endpoint contains 0 currently observed people.

## Overall results

| Pipeline | Rows | Completed | Errors | Target matches/all 72 | Matches/completed only |
| --- | --- | --- | --- | --- | --- |
| Rule-based | 72 | 72 | 0 | 37/72 (51.4%) | 37/72 (51.4%) |
| LLM run 1 (primary) | 72 | 72 | 0 | 30/72 (41.7%) | 30/72 (41.7%) |
| LLM run 2 | 72 | 72 | 0 | 30/72 (41.7%) | 30/72 (41.7%) |
| LLM run 3 | 72 | 72 | 0 | 30/72 (41.7%) | 30/72 (41.7%) |

Rule-based target agreement was 37/72 (51.4%); primary LLM agreement was 30/72 (41.7%). The two pipelines chose the same final action on 58/72 (80.6%) of all recordings (58/72 (80.6%) among paired completed outcomes). Default CONTINUE is included in these counts.

| Pipeline | CONTINUE | APPROACH | YIELD | ENGAGE | Policy decisions | Default continuation |
| --- | --- | --- | --- | --- | --- | --- |
| Rule-based | 52 | 9 | 2 | 9 | 51 | 21 |
| LLM run 1 (primary) | 46 | 8 | 0 | 18 | 51 | 21 |
| LLM run 2 | 46 | 8 | 0 | 18 | 51 | 21 |
| LLM run 3 | 46 | 8 | 0 | 18 | 51 | 21 |

| Pipeline | Target matches/explicit decisions | Target matches/default outcomes |
| --- | --- | --- |
| Rule-based | 28/51 (54.9%) | 9/21 (42.9%) |
| LLM run 1 (primary) | 21/51 (41.2%) | 9/21 (42.9%) |
| LLM run 2 | 21/51 (41.2%) | 9/21 (42.9%) |
| LLM run 3 | 21/51 (41.2%) | 9/21 (42.9%) |

The origin-specific percentages describe their subsets and do not replace the primary denominator of 72. A default target match means the required baseline action agreed with the target; it does not mean the action policy made that decision.

| Pipeline | Default-continuation cause | Count |
| --- | --- | --- |
| Rule-based | DISTANCE_UNKNOWN | 1 |
| Rule-based | INSUFFICIENT_GAZE_EVIDENCE | 14 |
| Rule-based | MULTIPLE_VISIBLE_PEOPLE | 1 |
| Rule-based | NO_VISIBLE_PERSON | 5 |
| LLM run 1 (primary) | DISTANCE_UNKNOWN | 1 |
| LLM run 1 (primary) | INSUFFICIENT_GAZE_EVIDENCE | 14 |
| LLM run 1 (primary) | MULTIPLE_VISIBLE_PEOPLE | 1 |
| LLM run 1 (primary) | NO_VISIBLE_PERSON | 5 |
| LLM run 2 | DISTANCE_UNKNOWN | 1 |
| LLM run 2 | INSUFFICIENT_GAZE_EVIDENCE | 14 |
| LLM run 2 | MULTIPLE_VISIBLE_PEOPLE | 1 |
| LLM run 2 | NO_VISIBLE_PERSON | 5 |
| LLM run 3 | DISTANCE_UNKNOWN | 1 |
| LLM run 3 | INSUFFICIENT_GAZE_EVIDENCE | 14 |
| LLM run 3 | MULTIPLE_VISIBLE_PEOPLE | 1 |
| LLM run 3 | NO_VISIBLE_PERSON | 5 |

## Per-scenario agreement

| New | Original | Target | Rule/target | LLM1/target | LLM2/target | LLM3/target | Rule/LLM1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | S1 | CONTINUE | 9/9 (100.0%) | 7/9 (77.8%) | 7/9 (77.8%) | 7/9 (77.8%) | 7/9 (77.8%) |
| S2 | S2 | APPROACH | 3/9 (33.3%) | 3/9 (33.3%) | 3/9 (33.3%) | 3/9 (33.3%) | 7/9 (77.8%) |
| S3 | S3 | ENGAGE | 7/9 (77.8%) | 7/9 (77.8%) | 7/9 (77.8%) | 7/9 (77.8%) | 9/9 (100.0%) |
| S4 | S4 | YIELD | 2/9 (22.2%) | 0/9 (0.0%) | 0/9 (0.0%) | 0/9 (0.0%) | 4/9 (44.4%) |
| S5 | S6 | CONTINUE | 9/9 (100.0%) | 6/9 (66.7%) | 6/9 (66.7%) | 6/9 (66.7%) | 6/9 (66.7%) |
| S6 | S7 | APPROACH | 3/9 (33.3%) | 2/9 (22.2%) | 2/9 (22.2%) | 2/9 (22.2%) | 8/9 (88.9%) |
| S7 | S8 | ENGAGE | 0/9 (0.0%) | 0/9 (0.0%) | 0/9 (0.0%) | 0/9 (0.0%) | 9/9 (100.0%) |
| S8 | S9 | CONTINUE | 4/9 (44.4%) | 5/9 (55.6%) | 5/9 (55.6%) | 5/9 (55.6%) | 8/9 (88.9%) |

Every cell uses nine recordings, including defaults and errors. Rule/LLM1 measures final-action agreement between pipelines, independently of target agreement.

| Scenario | Pipeline | CONTINUE | APPROACH | YIELD | ENGAGE | Policy | Default | Error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | Rule-based | 9 | 0 | 0 | 0 | 4 | 5 | 0 |
| S1 | LLM run 1 (primary) | 7 | 1 | 0 | 1 | 4 | 5 | 0 |
| S1 | LLM run 2 | 7 | 1 | 0 | 1 | 4 | 5 | 0 |
| S1 | LLM run 3 | 7 | 1 | 0 | 1 | 4 | 5 | 0 |
| S2 | Rule-based | 6 | 3 | 0 | 0 | 7 | 2 | 0 |
| S2 | LLM run 1 (primary) | 4 | 3 | 0 | 2 | 7 | 2 | 0 |
| S2 | LLM run 2 | 4 | 3 | 0 | 2 | 7 | 2 | 0 |
| S2 | LLM run 3 | 4 | 3 | 0 | 2 | 7 | 2 | 0 |
| S3 | Rule-based | 2 | 0 | 0 | 7 | 8 | 1 | 0 |
| S3 | LLM run 1 (primary) | 2 | 0 | 0 | 7 | 8 | 1 | 0 |
| S3 | LLM run 2 | 2 | 0 | 0 | 7 | 8 | 1 | 0 |
| S3 | LLM run 3 | 2 | 0 | 0 | 7 | 8 | 1 | 0 |
| S4 | Rule-based | 7 | 0 | 2 | 0 | 6 | 3 | 0 |
| S4 | LLM run 1 (primary) | 6 | 0 | 0 | 3 | 6 | 3 | 0 |
| S4 | LLM run 2 | 6 | 0 | 0 | 3 | 6 | 3 | 0 |
| S4 | LLM run 3 | 6 | 0 | 0 | 3 | 6 | 3 | 0 |
| S5 | Rule-based | 9 | 0 | 0 | 0 | 7 | 2 | 0 |
| S5 | LLM run 1 (primary) | 6 | 0 | 0 | 3 | 7 | 2 | 0 |
| S5 | LLM run 2 | 6 | 0 | 0 | 3 | 7 | 2 | 0 |
| S5 | LLM run 3 | 6 | 0 | 0 | 3 | 7 | 2 | 0 |
| S6 | Rule-based | 6 | 3 | 0 | 0 | 7 | 2 | 0 |
| S6 | LLM run 1 (primary) | 7 | 2 | 0 | 0 | 7 | 2 | 0 |
| S6 | LLM run 2 | 7 | 2 | 0 | 0 | 7 | 2 | 0 |
| S6 | LLM run 3 | 7 | 2 | 0 | 0 | 7 | 2 | 0 |
| S7 | Rule-based | 9 | 0 | 0 | 0 | 5 | 4 | 0 |
| S7 | LLM run 1 (primary) | 9 | 0 | 0 | 0 | 5 | 4 | 0 |
| S7 | LLM run 2 | 9 | 0 | 0 | 0 | 5 | 4 | 0 |
| S7 | LLM run 3 | 9 | 0 | 0 | 0 | 5 | 4 | 0 |
| S8 | Rule-based | 4 | 3 | 0 | 2 | 7 | 2 | 0 |
| S8 | LLM run 1 (primary) | 5 | 2 | 0 | 2 | 7 | 2 | 0 |
| S8 | LLM run 2 | 5 | 2 | 0 | 2 | 7 | 2 | 0 |
| S8 | LLM run 3 | 5 | 2 | 0 | 2 | 7 | 2 | 0 |

## Person-by-scenario outcomes

Each cell is **rule/LLM1/LLM2/LLM3**. C = CONTINUE, A = APPROACH, Y = YIELD, E = ENGAGE; superscript ᵈ marks default continuation; ERR marks an unavailable outcome. All columns use new scenario IDs.

| Person | S1 | S2 | S3 | S4 | S5 | S6 | S7 | S8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| person-1 | C/C/C/C | C/E/E/E | E/E/E/E | C/E/E/E | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/C/C/C | Cᵈ/Cᵈ/Cᵈ/Cᵈ | E/E/E/E |
| person-2 | Cᵈ/Cᵈ/Cᵈ/Cᵈ | A/A/A/A | C/C/C/C | C/C/C/C | C/E/E/E | A/C/C/C | C/C/C/C | A/A/A/A |
| person-3 | C/A/A/A | Cᵈ/Cᵈ/Cᵈ/Cᵈ | E/E/E/E | C/E/E/E | Cᵈ/Cᵈ/Cᵈ/Cᵈ | A/A/A/A | C/C/C/C | A/C/C/C |
| person-4 | Cᵈ/Cᵈ/Cᵈ/Cᵈ | A/A/A/A | E/E/E/E | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/E/E/E | C/C/C/C | C/C/C/C | E/E/E/E |
| person-5 | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/E/E/E | E/E/E/E | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/C/C/C | C/C/C/C | C/C/C/C | Cᵈ/Cᵈ/Cᵈ/Cᵈ |
| person-6 | C/C/C/C | A/A/A/A | E/E/E/E | Y/C/C/C | C/C/C/C | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/C/C/C | A/A/A/A |
| person-7 | C/E/E/E | C/C/C/C | E/E/E/E | Y/C/C/C | C/C/C/C | A/A/A/A | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/C/C/C |
| person-8 | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/C/C/C | E/E/E/E | C/E/E/E | C/C/C/C | C/C/C/C | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/C/C/C |
| person-9 | Cᵈ/Cᵈ/Cᵈ/Cᵈ | Cᵈ/Cᵈ/Cᵈ/Cᵈ | Cᵈ/Cᵈ/Cᵈ/Cᵈ | Cᵈ/Cᵈ/Cᵈ/Cᵈ | C/E/E/E | Cᵈ/Cᵈ/Cᵈ/Cᵈ | Cᵈ/Cᵈ/Cᵈ/Cᵈ | Cᵈ/Cᵈ/Cᵈ/Cᵈ |

## LLM repeatability and inference duration

| Scenario | Complete triples | Same action/complete triples | Same action/all recordings | Same action and origin/complete triples | Incomplete triples |
| --- | --- | --- | --- | --- | --- |
| Overall | 72 | 72/72 (100.0%) | 72/72 (100.0%) | 72/72 (100.0%) | 0 |
| S1 | 9 | 9/9 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 0 |
| S2 | 9 | 9/9 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 0 |
| S3 | 9 | 9/9 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 0 |
| S4 | 9 | 9/9 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 0 |
| S5 | 9 | 9/9 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 0 |
| S6 | 9 | 9/9 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 0 |
| S7 | 9 | 9/9 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 0 |
| S8 | 9 | 9/9 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 0 |

Repeatability is assessed per recording across all three repetitions, not by treating the repetitions as additional participants. Errors do not become repeated actions. LLM calls were made across the ready recordings in run 1, then run 2, then run 3.

| Run | Measured inference calls | All measured calls (s) | Successful policy calls (s) |
| --- | --- | --- | --- |
| LLM run 1 | 51 | 1.076 [0.895–4.292] (n=51) | 1.076 [0.895–4.292] (n=51) |
| LLM run 2 | 51 | 1.073 [0.898–1.323] (n=51) | 1.073 [0.898–1.323] (n=51) |
| LLM run 3 | 51 | 1.076 [0.894–1.323] (n=51) | 1.076 [0.894–1.323] (n=51) |

Durations are median [minimum–maximum], with n measured calls. The native client measures the full model HTTP request for a frozen endpoint input, including any model loading, queueing and generation; these are not pure token-generation times. They do not measure live delivery, time-to-decision from recording onset or physical robot reaction. Readiness defaults make no model call and have no inference duration. There were 153 measured LLM calls; 21/72 recordings produced readiness defaults in each pipeline and repetition.

Among the 51 recordings with three actual successful model calls, action repeatability was 51/51 (100.0%). The remaining 21 recordings repeated the shared readiness default without model inference. This separates model-output repeatability from deterministic fallback repeatability.

## Findings and observation limitations

At the selected endpoints, 5/72 recordings have no currently observed person, 1/72 have multiple observed people, 64/72 have unknown robot motion, and 33/72 have at least one observed person with an invalid relative-distance trend. These are recording-level sensor-state counts; they do not revise the researcher’s confirmation of the performed scenarios. Missing or fragmented evidence can prevent state readiness even when the intended behaviour occurred.

Within the selected windows, 27/72 recordings contain at least one no-detection interval and 26/72 contain more than one observed tracking epoch. At the endpoint, 15/72 have an observed person with invalid temporal gaze evidence. Multiple epochs describe track continuity in the data, not the number of actual participants. The CSV note `gaze_valid_observations` counts available raw gaze observations; it does not establish valid temporal gaze evidence. In 8/72 endpoint states, an observed person has latest-looking=true while the recent-window gaze category is NONE; these features describe different time scales and are preserved together.

| Unready endpoint cause | Recordings |
| --- | --- |
| DISTANCE_UNKNOWN | 1 |
| INSUFFICIENT_GAZE_EVIDENCE | 14 |
| MULTIPLE_VISIBLE_PEOPLE | 1 |
| NO_VISIBLE_PERSON | 5 |

| Scenario | Example person | Rule outcome | Target | Endpoint evidence/cause |
| --- | --- | --- | --- | --- |
| S2 | person-1 | CONTINUE | APPROACH | distance 1.838 m, gaze NONE, latest looking True, corridor TO_SIDE, separation STABLE |
| S3 | person-2 | CONTINUE | ENGAGE | distance 1.338 m, gaze INTERMITTENT, latest looking False, corridor IN_WAY, separation STABLE |
| S4 | person-1 | CONTINUE | YIELD | distance 1.959 m, gaze NONE, latest looking True, corridor IN_WAY, separation UNKNOWN |
| S6 | person-1 | CONTINUE | APPROACH | distance 2.008 m, gaze NONE, latest looking False, corridor TO_SIDE, separation STABLE |
| S7 | person-1 | CONTINUE | ENGAGE | NO_VISIBLE_PERSON |
| S8 | person-1 | ENGAGE | CONTINUE | distance 1.953 m, gaze SUSTAINED, latest looking True, corridor IN_WAY, separation STABLE |

| Person/scenario | Rule | LLM1 | Rule reason/cause | LLM reason/cause |
| --- | --- | --- | --- | --- |
| person-1/S2 | C | E | Gaze evidence is classified as no attention; continue along the fixed route. | Person within interaction range, looking at the robot. |
| person-1/S4 | C | E | Gaze evidence is classified as no attention; continue along the fixed route. | Person within interaction range, looking at the robot. |
| person-2/S5 | C | E | Gaze evidence is classified as no attention; continue along the fixed route. | Person within interaction range, looking at the robot. |
| person-2/S6 | A | C | The person is looking steadily at the robot and is within approach distance. | Sustained gaze outside conversation distance. |
| person-3/S1 | C | A | Gaze evidence is classified as no attention; continue along the fixed route. | Person within interaction range and looking at the robot. |
| person-3/S4 | C | E | Usable gaze evidence does not show sustained attention; continue along the fixed route. | Intermittent looking and sustained gaze within interaction range |
| person-3/S8 | A | C | The person is looking steadily at the robot and is within approach distance. | Sustained gaze outside interaction range, no immediate spatial need. |
| person-4/S5 | C | E | Usable gaze evidence does not show sustained attention; continue along the fixed route. | Intermittent looking and face detected within interaction range. |
| person-5/S2 | C | E | Separation is increasing outside conversation range; avoid pursuing the person. | Person within interaction range, looking at the robot. |
| person-6/S4 | Y | C | The current nose position lies within the configured forward corridor, separation is decreasing within yield range, and face and low-gaze evidence are valid; give the person room to pass. | Person within interaction range but not looking at the robot. |
| person-7/S1 | C | E | Gaze evidence is classified as no attention; continue along the fixed route. | Person within interaction range, looking at the robot. |
| person-7/S4 | Y | C | The current nose position lies within the configured forward corridor, separation is decreasing within yield range, and face and low-gaze evidence are valid; give the person room to pass. | Person outside interaction range, no sustained looking. |
| person-8/S4 | C | E | Usable gaze evidence does not show sustained attention; continue along the fixed route. | Intermittent looking and high gaze fraction within interaction range |
| person-9/S5 | C | E | Usable gaze evidence does not show sustained attention; continue along the fixed route. | Intermittent looking and recent gaze overlap within interaction range |

The rules matched the target in all nine S1 and all nine S5 recordings, while the primary LLM selected interaction actions in two S1 and three S5 recordings. Both pipelines matched the revised ENGAGE target in seven of nine S3 recordings. In S4, rules returned YIELD for persons 6 and 7, whose endpoint states contain valid decreasing separation, low gaze and IN_WAY corridor position; the LLM returned CONTINUE for both. Neither pipeline matched the ENGAGE target in S7: each produced five explicit and four default CONTINUE outcomes. These are differences on the same frozen evidence, rather than differences in readiness or observation timing.

Some primary LLM interaction outcomes occur with latest-looking=true but recent-window gaze category NONE, including person-3/S1, person-7/S1, person-1/S2, person-5/S2, person-1/S4 and person-2/S5. The saved reasons mention current looking in these cases. This describes an observed association between input features and outputs; it does not establish the model’s internal decision process.

These examples use only the saved endpoint input; they do not inspect later observations. Policy reasons are preserved explanations, not independent measurement evidence. The rule policy does not treat intermittent gaze alone as an approach trigger while recurring-attention behaviour is disabled. Its YIELD branch requires valid decreasing separation within the configured range, low gaze and relevant corridor evidence. Failure to meet these sensor-based conditions is part of the observed policy outcome.

Neither the adapter-derived DecisionState nor the structured LLM input contains a hand-gesture feature or camera imagery. Agreement in new S8 therefore cannot demonstrate recognition of the invitation-to-pass gesture. Relative distance does not identify which participant moved, and gaze classifications do not establish social intent.

## Interpretation and validation

This revised sensor-window comparison is exploratory. Window selection was fixed for this run, but it is not an independently video-verified or randomly sampled evaluation. S3 uses a project-defined target different from the survey mode. Results describe agreement with these targets on these recordings; they do not establish participant satisfaction, successful physical robot behaviour or general performance beyond this dataset. The next evaluation could independently verify sensor/video alignment and examine perception continuity and readiness on a newly frozen dataset. No policies or windows were tuned to these results.

The CSV contains 288 unique recording–policy–repetition rows for 72 recordings: 72 rule-based and 216 LLM rows. All four rows for each recording carry the same selected window and identical frozen DecisionState JSON where input construction succeeded. The report aggregates were recomputed from the CSV, retaining errors as non-matches. Endpoint matching, the no-future-observation boundary, identical policy inputs and unchanged protected source/configuration/recording hashes were verified.

The new evaluation directory contains only [human_evaluation_results.csv](human_evaluation_results.csv) and this summary. The CSV stores the endpoint input, observation notes, fallback causes and any errors; no separate per-trial logs or manifests are delivered. The earlier evaluation is preserved.
