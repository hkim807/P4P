# Final scenarios 1–9: recording review

The current files were assessed from robot motion onset, excluding the stationary setup period. **Scenario 2 is suitable for the intended sensor-based evaluation.** The others either conflict with the intended cues or lack enough evidence to confirm the complete scenario. These findings describe sensor agreement, not independently verified human behaviour.

The current scenario 1 is 17.14 seconds long. It is a different capture from the 82.24-second file reviewed earlier in the conversation. SHA-256 fingerprints in [audit.json](audit.json) identify exactly which files this report covers.

## Stream continuity

All nine files contain one session each, valid SDK envelopes and ordered receipt timestamps. Both streams are continuous at approximately 10 Hz:

- No per-stream sequence discontinuities, duplicate sequences or nonincreasing receipt timestamps.
- No receipt interval exceeds 0.25 seconds. The largest interval across all files/streams is approximately 0.135 seconds.
- No repeated or backwards SDK perception/odometry source timestamps.
- Empty person arrays are still delivered perception packets. They indicate detection loss, not missing stream frames.

This establishes continuity in the saved files. It cannot exclude loss before the capture writer assigned sequence numbers, or prove that a recording includes the end of the intended trial. No camera frames with a matching final-capture session were found among the recording manifests.

## Scenario alignment after motion begins

The checks use `config/temporal-development-frozen.json`: high overlap ≥0.93, low overlap ≤0.90, interaction distance ≤1.5 m, approach distance up to 6 m. High/low counts below are instantaneous person samples, not temporal categories or time fractions. Samples in the hysteresis band are neither high nor low. Duplicate person entries can contribute more than one sample per packet.

| Scenario | Motion onset in source | Duration after onset | High / usable gaze | Unknown gaze samples | Empty perception packets | Assessment |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1: pass without looking | 6.623 s | 10.514 s | 33 / 63 | 6 | 38 / 105 | Does not cleanly align. Frequent high gaze before late low gaze; closing and loss of detection are compatible with passing but do not establish human motion. |
| 2: stare within approach distance | 2.777 s | 4.707 s | 49 / 49 | 1 | 0 / 48 | Aligns. Distance 2.11–2.56 m, continuous detection, consistently high usable gaze. |
| 3: looking within interaction range | 2.900 s | 4.699 s | 45 / 46 | 1 | 0 / 47 | Partial. Starts at 1.709 m; first sample ≤1.5 m arrives 2.809 s after onset. Looking is supported throughout. |
| 4: pass without looking | 5.680 s | 6.081 s | 22 / 38 | 25 | 1 / 62 | Does not cleanly align. Frequent high gaze and unavailable estimates; closing from roughly 3.6 to 1.2 m, then one empty packet at capture end. |
| 5: walk away without looking | 3.730 s | 6.911 s | 0 / 0 | 0 | 69 / 69 | Unobservable. No detected person after robot motion starts. Neither walking away nor looking away can be evaluated from person cues. |
| 6: pass with brief glance, then look ahead | 23.950 s | 4.674 s | 27 / 45 | 4 | 0 / 47 | Does not cleanly align. Only one usable low-gaze sample, high gaze through much of the encounter, and no reliable return-to-looking-ahead phase before capture ends. |
| 7: stand to side, intermittent looking | 5.380 s | 8.901 s | 36 / 50 | 30 | 10 / 89 | Partial. Ten usable low-gaze samples and high-gaze bouts support intermittency, but unknown estimates and detection gaps interrupt it. Standing to the side of the path is unverified. |
| 8: walk away while continuously looking back | 3.010 s | 7.162 s | 3 / 27 | 18 | 27 / 72 | Partial. After an initial ~2.7 s detection gap, separation grows from ~1.12 to 2.10 m. However, 19 usable gaze samples are low, so continuous looking is not supported by these measurements. |
| 9: stand in front, look and gesture to pass | 20.164 s | 3.458 s | 26 / 35 | 0 | 0 / 35 | Partial. Looking at ~2.0–2.4 m is supported. No gesture/path measurements or matching video confirm the pass invitation or position in the route. |

Suspected SDK defaults (overlap exactly 0.5 together with zero gaze/head vectors) and missing/nonfinite vectors count as unknown for this review. They do not demonstrate that a person looked away. This diagnostic treatment does not change the live adapter or policy.

Scenario 2 has two packets with duplicate entries for the same UID, rather than clear evidence of two different people. Its one unavailable vector is a minor limitation. Scenario 7 has five distinct UIDs and a roughly 0.59-second no-person gap; do not treat missing/default intervals as confirmed look-away bouts. Scenario 3 may be appropriate if an independently chosen interaction boundary includes ~1.7 m, but it does not start within the repository's 1.5 m boundary. Evaluating its first eligible decision may therefore test approach behaviour before it tests engagement.

SDK-only evaluation cannot verify pass gestures in scenario 9: the recordings have no gesture/path fields or images carrying that evidence. An intended gesture cannot be inferred from gaze or assigned as if measured.

## Prepared evaluation copy

Only scenario 2 was approved and trimmed:

- [trimmed/final_scenario_2-sdk.jsonl](trimmed/final_scenario_2-sdk.jsonl)
- Starts at the first recorded movement packet, 2.777035 seconds into the source.
- Contains 48 perception and 48 locomotion packets, spanning 4.706534 seconds.
- Preserves packet payloads, session ID and all timestamps. Per-stream sequences restart at 1 because the SDK replay loader requires it; the original sequence/line mapping and both file hashes are in `audit.json`.
- Successfully loaded through `iter_replay_states(..., 'sdk')`, yielding all 48 perception states. Payload/timestamp equality against the retained source records was checked.

The nine originals were preserved. No additional time was removed to force scenario alignment, and no alternate trim starting when the person first appears or enters range was substituted for the requested robot-motion start.

Motion onset is the first nonzero planar/yaw velocity in the contiguous acceleration ramp leading to three consecutive movement packets above 0.02 m/s or 0.03 rad/s. This retains the small initial ramp in scenarios 1, 6 and 8. SDK telemetry resolves onset only to its sampling interval, approximately 0.1 seconds.

## Reproduce and inspect

Run `python3 var/evaluation/final-scenarios-audit/audit.py` from the repository root. [audit.json](audit.json) contains full/post-motion continuity statistics, source fingerprints, one-second gaze/distance summaries, detection-gap intervals and trim provenance. The script refuses to replace a different existing trimmed output.

For behavioural ground truth, retain the intended labels separately and confirm them with timestamped observation or synchronized video. Trials with sensor disagreement may still be valuable for evaluating sensor failure, but should not be described as verified clean examples of the intended SDK cues.
