# Four-action rule policy

`app.policy.rules.decide` classifies one eligible SocialState and returns a `PolicyDecision`.
It is stateless and deterministic. The live receiver includes the decision under
`policy_decision` in each successful social response. `python -m app.decide`
applies the same function to a SocialState JSONL file and writes one decision
or observation status per state. Neither path issues a robot command.

The result includes `decision_id`, `source_state_id`, `session_id`,
`policy_version`, `decision`, `reason_code`, and an optional target UID/epoch.
Only `APPROACH` and `ENGAGE` identify a target. The output contract is
[`schemas/v1/policy-decision.schema.json`](../schemas/v1/policy-decision.schema.json).

Final outputs share the strict `FinalDecision` model in
`app.domain.model_decision`: `{"action":"CONTINUE","reason":"Usable gaze evidence does not show sustained attention."}`.
The four actions are:

- `CONTINUE`: complete the remaining fixed route without approaching or initiating interaction.
- `APPROACH`: leave the route, move towards the person and stop at conversation distance.
- `ENGAGE`: stop or remain stationary and initiate interaction with the nearby person.
- `YIELD`: temporarily move aside and backwards to give room to pass, wait briefly,
  return towards the original route, advance a short distance, then end. The wait
  is timed; the return is nominal rather than verified navigation to an exact path.

`normalise_rule_decision` supplies an English reason for every rule action;
DEFER and STOP are rejected in both rule and final contracts. Live and
`app.decide` replay results carry `policy_decision`, `final_decision`, and
`policy_readiness`. An unready observation has status OBSERVING and a reason
code, with both decisions null: the policy was not invoked. Processing errors
and decision timeouts remain statuses.

Readiness requires exactly one currently observed person, valid current distance
and usable NONE/INTERMITTENT/SUSTAINED gaze with existing coverage and category
dwell, plus valid processing and freshness. No person, multiple people, invalid
distance or UNKNOWN gaze keep observing. No stationary-window, human-motion,
identity-lock or distance-trend prerequisite is added to general readiness.
Explicit path conflict can resolve before distance/gaze warmup, and PASS can
resolve with valid current distance before gaze warmup. Proximity alone cannot.

Eligible `social-rules-v4` observations use this order:

| Evidence | Action |
| --- | --- |
| Explicit upstream path CONFLICT | YIELD |
| Measured PASS invitation | CONTINUE |
| Current face detected, distance ≤3 m, valid DECREASING trend, valid NONE gaze, latest looking false and latest gaze score ≤0.87 | YIELD |
| Latest looking false while historical SUSTAINED category is held | CONTINUE |
| Sustained or qualifying recurring attention in TOO_CLOSE or INTERACTION_RANGE | ENGAGE |
| Reliable AWAY motion or reliable increasing separation outside conversation range | CONTINUE |
| Sustained or qualifying recurring attention in APPROACHABLE | APPROACH |
| Other eligible cases, including NONE, incidental INTERMITTENT or FAR | CONTINUE |

The closing/low-gaze trigger replaces v3's distance-only TOO_CLOSE trigger.
TOO_CLOSE remains a distance category: attentive nearby people support ENGAGE,
and eligible non-attentive people support CONTINUE unless the new closing trigger
applies. A stationary person at 0.4 m does not cause YIELD solely through proximity.
The exact current distance cutoff is `config.yield_closing_max_distance_m`,
independent of distance-zone hysteresis, and includes exactly 3 m. Low gaze uses
`config.looking_exit` (currently 0.87); a score at or above `looking_enter`
(currently 0.88) establishes looking, while the narrow band between those
thresholds retains the previous per-sample state. NONE requires established low attention over the temporal
window, not one low sample. DECREASING requires a valid fitted slope more negative
than `-config.distance_deadband_mps` (currently -0.1 m/s), with valid current
distance, sufficient span/samples and acceptable residual.

The SDK adapter sets `face_detected: true` only for a finite positive-size `face`
bounding box. Missing/invalid boxes supply no detection evidence. Face evidence
and `evidence.latest_gaze_overlap` clear immediately on missing observations.
At 10 Hz with uninterrupted low gaze and smooth closing, the configured 1 s
minimum span plus 0.1 s category dwell allows a first YIELD at approximately
1.1 s. Gaps, missing readings and failed fits can delay or prevent it.
Relative closing while the robot moves leaves human motion UNKNOWN; it describes
decreasing separation rather than proven human movement or path conflict.
No ego-motion compensation or physical handler is added.

The LLM prompt remains `social-state-llm-v6`, including its earlier proximity
guidance. It receives the additional measured fields and revised shared readiness,
but this is a rule-policy change, not a matched change to LLM decision guidance.
Historical reports remain under their recorded versions. The active candidate
freeze is `config/live-study-freeze-yield-low-gaze.json`; the original v3 freeze
is retained for historical reproducibility.
The target-lock wire contract is `target-lock-v3`: identity/rebind/cooldown holds
have `execution_status: HOLD`, a `hold_reason` and `effective_decision: null`;
they never invent an action. Target availability and command checks remain.
SingleTrial accepts fresh pure proposals independently of execution holds.
With single-trial execution, CONTINUE finishes the existing route and the other
actions stop it before running their handlers. The first accepted output latches;
the trial does not reconsider CONTINUE if the person starts closing later.

Historical replay under `social-rules-v1` shows why these are provisional decisions:
recording 04 (named no gaze) produces 3 `APPROACH` frames; recording 05 (named
eye-only intermittent gaze) produces 65 `ENGAGE` frames; recording 07 (named
moving away) produces 5 `ENGAGE` frames. The filenames describe intended test
conditions, not verified frame labels. Annotated behavior intervals and sensor
calibration are needed before physical execution.

Try a recorded SocialState trace:

```bash
.venv/bin/python -m app.decide var/temporal-validation/01-velocity-social.jsonl \
  --output var/temporal-validation/01-decisions.jsonl
```

The output is JSONL. Use `jq . var/temporal-validation/01-decisions.jsonl | less`
to inspect it. As with social replay, the output filename must be new.
