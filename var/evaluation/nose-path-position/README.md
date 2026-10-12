# Nose position replay check

Replayed the nine motion-trimmed SDK recordings through the updated adapter,
tracker and `temporal-social-v4` estimator using the development configuration:
0.5 m corridor half-width, 0.1 m hysteresis, zero camera offsets/yaw and the
fixed-head assumption enabled. Rules use `social-rules-v6`. Results are in
[replay-summary.json](replay-summary.json).

| Scenario | IN_WAY samples | TO_SIDE samples | Eligible rule proposals |
| --- | ---: | ---: | --- |
| 1 | 0 | 67 | None; all 105 frames NOT_READY |
| 2 | 0 | 48 | 25 APPROACH |
| 3 | 47 | 0 | 16 APPROACH, 8 ENGAGE |
| 4 | 61 | 0 | 9 CONTINUE |
| 5 | 0 | 0 | None; no observed person |
| 6 | 30 | 17 | 7 APPROACH |
| 7 | 0 | 79 | None; all 89 frames NOT_READY |
| 8 | 27 | 18 | 1 CONTINUE |
| 9 | 0 | 35 | 6 APPROACH, 3 CONTINUE |

Position counts include currently observed tracked people only. The adapter
deduplicates repeated UIDs, so these counts differ from the earlier audit's raw
person-instance counts. Retained missing tracks are excluded. Decisions are
per-frame proposals, not physical actions or the single-trial latched result.

The geometry separates scenarios 1 and 4: every observed sample in scenario 1
is TO_SIDE; every observed sample in scenario 4 is IN_WAY. Existing readiness,
gaze and distance evidence still govern whether an action is eligible. This
recorded pair does not yield a CONTINUE/YIELD action pair: scenario 1 has no
eligible decisions and scenario 4 never meets the complete low-gaze/closing
yield trigger. No gaze or identity evidence was changed to force those outputs.

The synthetic production-pipeline test holds closing distance, valid face and
low gaze equal: a lateral nose offset of 0.3 m yields, while 0.65 m continues.
Additional tests cover hysteresis, missing/invalid coordinates, gaps, disabled
fixed-head interpretation, transforms and live/SDK replay parity. See
[tests/test_path_position.py](../../../tests/test_path_position.py) and
[docs/path-position.md](../../../docs/path-position.md).

Validation: 107 focused tests passed. The full suite ran 623 tests and reported
12 errors in two existing cancellation tests because Python 3.10 has no
`asyncio.Task.cancelling()` method. Running those two tests against an isolated,
unmodified HEAD checkout reproduced the same 12 errors. The new position tests,
SDK replay parity checks, public schema checks and policy tests passed.
