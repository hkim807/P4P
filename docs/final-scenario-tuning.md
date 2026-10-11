# Final-scenario first-action tuning

This preset fits the nine user-labelled motion-trimmed SDK recordings. It is a
development regression set, not held-out evidence of generalisation. Expected
actions and recording identifiers stay in the evaluation contract; the policy
uses only current and past observations. The recordings are unchanged.

Select the preset explicitly:

```bash
python3 -m scripts.replay_first_action \
  --settings config/policy-comparison-final.json \
  --output var/evaluation/final-scenario-tuning/new-run \
  --warm-model
```

Use a new output directory for each run. Local Ollama must serve `qwen2.5:7b`;
`--policies rules` runs only the deterministic policy without Ollama. The runner
reconstructs perception and locomotion through the SDK adapter, feeds production
HTTP endpoints at recorded speed, keeps observations flowing during model
inference, and stops at the first validated output. `SingleTrial` independently
checks whether that output is still acceptable. No robot transport or physical
executor is instantiated. Each scenario starts with a fresh pipeline.

For a receiver using the same settings, add these flags to your server command:

```bash
python3 -m app.server \
  --social-output var/final-scenario-live-social.jsonl \
  --tracking-config config/person-tracking-final-scenarios.json \
  --temporal-config config/temporal-final-scenarios.json
```

The server output must also be a new file. This command runs the rule receiver;
the replay command above enables and configures the actual LLM comparison.

## Effective settings and reasoning

| Setting | Tuned value | Purpose |
| --- | --- | --- |
| Interaction boundary | ≤2 m ENGAGE; >2 m through 6 m APPROACH, when attentive | Implements the requested conversation distance while retaining the existing maximum approach range |
| Distance zone hysteresis | 0 m | Makes the 2 m split exact, at the cost of possible threshold chatter |
| Near gaze enter / exit | 0.93 / 0.90 | Separates attention within the SDK's compressed high score range |
| Gaze beyond 2.75 m | 0.97 / 0.95 | Requires stronger scores where gaze is less reliable |
| Unavailable gaze | Exactly 0.5 becomes unknown temporal evidence | Does not turn missing gaze into low gaze; raw scores remain intact |
| Window / minimum span | 3 s / 1 s | Accumulates temporal evidence before the short recordings end |
| Gaze evidence | ≥5 samples, ≥0.6 s coverage, ≥50% window coverage | Tolerates some missing detections without inventing attention |
| Sustained gaze fraction | Enter 80%, retain 65%; currently looking required | Distinguishes sustained from intermittent attention |
| Additional category dwell / trailing-run minimum | 0 s / 0 s | Uses the evidence window and gaze fraction; does not demand an extra unbroken run after a missing sample |
| Recurring intermittent attention | Disabled as an interaction trigger | Scenario 7 continues despite repeated looks |
| Nose corridor half-width / hysteresis | 0.40 m / 0.05 m | Distinguishes passing beside the robot from closing in front |
| Closing yield range | ≤3.5 m | Allows the first usable in-path observation in scenario 4 to yield |
| Empty scene | ≥1 s contiguous observations and ≥5 samples | Allows CONTINUE in scenario 5; brief detector losses reset or delay readiness |
| Single-person nose reassociation | ≤0.45 m displacement within ≤0.25 s | Retains evidence through adjacent SDK UID changes in scenarios 1 and 4 |

The path classifier uses `g_nose` in CAM_HEAD with the fixed-head assumption and
the same planar offset/yaw geometry used by the approach code. It exposes
`IN_WAY`, `TO_SIDE`, `BEHIND`, or `UNKNOWN`, together with signed lateral position;
the side's sign is available without needing separate LEFT/RIGHT actions. The
preset uses zero offsets/yaw. This corridor is a proxy for route occupancy;
robot body geometry, predicted trajectories and head movement are not modelled.

Nose reassociation is enabled only in this tracking preset. It requires exactly
one retained track and one current person, adjacent detections, and usable
nonzero CAM_HEAD points. It retains the epoch and emits an explicit unverified
association event; it cannot verify physical identity.

The policy applies explicit conflict and pass measurements first when supplied.
The SDK recordings do not supply gesture recognition. Corridor-based YIELD
requires IN_WAY, a detected face, valid decreasing separation, distance within
3.5 m, and valid low gaze under the current effective threshold. Position alone
does not cause YIELD. Increasing separation outside conversation range prevents
APPROACH; attentive people already in conversation range can still ENGAGE.

## Versions and evaluation

The versions are `uid-tracking-v3`, `temporal-social-v5`, `social-rules-v7`, and
`social-state-llm-v12`. The LLM receives the same SocialState and effective
parameters, with matching English guidance. Its result is neither replaced nor
corrected by the rules. Model settings are `qwen2.5:7b`, temperature 0, seed 42,
8192 context tokens, and 192 output tokens. Trial timeout remains 30 s, model
source age limit 10 s, and latest observation age limit 1 s.

See the [recorded results](../var/evaluation/final-scenario-tuning/README.md)
for actions, source times, response times and live acceptance. A model output
can be valid JSON yet too late to execute. Completion after the trial timeout
belongs in the audit, not in the accepted-action count. Replay source offsets
are measured from the first perception frame; the report also computes times
from the first locomotion record in each motion-trimmed file.

Scenario 6's first CONTINUE precedes its later glance. Scenario 8's first
CONTINUE uses an initial empty interval before the human first appears. Those
first actions therefore do not demonstrate recognition of the later glance or
of walking away while looking. Scenario 7 resolves from INTERMITTENT gaze, not
from its short detection dropout. Gesture recognition remains unavailable in
scenario 9; its APPROACH follows sustained gaze and distance.

Independent boundary tests and all nine first-action regressions are in
`tests/test_final_scenario_tuning.py`. Historical configurations retain their
own parameters. Default temporal processing now interprets gaze 0.5 as unknown;
set `gaze_unavailable_score` to null for a source where 0.5 is a real measurement.
