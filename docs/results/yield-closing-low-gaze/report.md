# Rules v4: closing distance with low gaze

On `feature/end-to-end-pipeline-refinement`, v4 replaces the distance-only
TOO_CLOSE YIELD trigger with the user-requested measured combination within 3 m.
An explicit path conflict still takes priority; a measured PASS invitation comes
next, followed by closing with low gaze. The robot's physical YIELD handler is
unchanged. The LLM prompt remains v6 with its earlier proximity guidance; no new
LLM inference or matched prompt comparison was performed for this amendment.

The new trigger requires exactly one current person, valid current distance
≤3 m, current positive face-box evidence, a valid DECREASING relative distance
trend, valid NONE gaze, current looking false and latest gaze overlap ≤0.7.
Distance slope must be more negative than -0.1 m/s and satisfy the existing
sample/span, continuity, jump and fit-quality checks. Low gaze is established
temporally; missing gaze is unavailable evidence. The cutoff uses current metres,
including 3 m, regardless of retained distance-zone hysteresis. Relative closing
does not identify human motion while the robot moves or establish a path conflict.

The SDK adapter now passes positive finite face bounding boxes into raw
`face_detected`; tracking and estimation retain that field only as current
evidence. Current gaze overlap is also included in temporal evidence. Missing
tracks clear both. The public schemas and robot decision validator accept v4.
Proximity alone no longer bypasses gaze readiness. Attentive TOO_CLOSE people
support ENGAGE, and eligible non-attentive nearby people support CONTINUE unless
the new closing trigger applies.

All 564 tests passed, including closing/low-gaze behavior, current face/gaze
missingness, multiple detections, valid/invalid trends, exact cutoff and zone
hysteresis, implausible jumps, removal of proximity yielding, priority, and
robot-client acceptance. The final focused adapter/rule suite passed 19 tests.

The controlled first-action run used the production Flask observation receiver
and SingleTrial with a virtual source clock: one person at 10 Hz, distance
2.5 − 0.02×frame m, gaze 0.1, detected face, moving robot at 0.1 m/s. It produced
YIELD at **1.1 s**, after 12 frames, and the client accepted it. The generator
fails if another frame is read, verifying termination at the first resolved
action. This checks causal policy timing, not network or physical execution
latency. Audit traces are in `synthetic-first-action/`.

All nine original SDK recordings were replayed causally through production
preprocessing and rules. The 730 frames have **zero action/status/reason changes**
against the v3 native baseline. All 229 currently observed person rows have
positive face boxes. Only 14 have current gaze ≤0.7, and none has valid established
NONE gaze. Consequently no recorded frame exercises the new YIELD combination.

| Scenario | First resolved action | Source offset |
| --- | --- | --- |
| S1 | No action | — |
| S2 | ENGAGE | 8.269501 s |
| S3 | ENGAGE | 1.186746 s |
| S4 | No action | — |
| S5 | No action | — |
| S6 | No action | — |
| S7 | No action | — |
| S8 | No action | — |
| S9 | APPROACH | 1.211434 s |

The recording replay is unpaced computation over original source timestamps.
Full-frame audits support diagnosis; a real single trial stops at its first
accepted action. If that action is CONTINUE, later closing does not revise it.
These results do not identify trimmed survey endpoints or measure hardware behavior.

`manifest.json` records source/configuration/schema hashes and validation
provenance. `comparison.json` contains the v3/v4 frame comparison. `cases.json`
reruns the 23 historical representative probes from the branch comparison;
the new trigger itself is exercised by the controlled tests and trial above.
Recreate recording artifacts at a new output path using:

```bash
.venv/bin/python docs/results/rule-branch-comparison/runner.py \
  --repo "$PWD" --source-root "$PWD" \
  --contract config/evaluation-contract.json \
  --temporal config/temporal-development-frozen.json \
  --cases docs/results/rule-branch-comparison/inputs.json \
  --output /tmp/p4p-yield-v4-new
.venv/bin/python -m evaluation.freeze \
  --verify config/live-study-freeze-yield-low-gaze.json
```

The new freeze preserves model identity from the prior run and records that LLM
guidance was unchanged. The original `config/live-study-freeze.json` remains the
historical v3 freeze and is expected to differ after this authorized rule change.
