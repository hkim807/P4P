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
- `YIELD`: temporarily move aside and backwards to give room to pass, then remain stopped
  there without automatically returning to the route. Manoeuvre parameters remain for later work.

`normalise_rule_decision` supplies an English reason for every rule action;
DEFER and STOP are rejected in both rule and final contracts. Live and
`app.decide` replay results carry `policy_decision`, `final_decision`, and
`policy_readiness`. An unready observation has status OBSERVING and a reason
code, with both decisions null: the policy was not invoked. Processing errors
and decision timeouts remain statuses. LLM/VLM outputs and inputs are unchanged.

Readiness requires exactly one currently observed person, valid current distance
and usable NONE/INTERMITTENT/SUSTAINED gaze with existing coverage and category
dwell, plus valid processing and freshness. No person, multiple people, invalid
distance or UNKNOWN gaze keep observing. No stationary-window, human-motion,
identity-lock or distance-trend prerequisite is added.

Eligible `social-rules-v2` observations use this order:

| Evidence | Action |
| --- | --- |
| TOO_CLOSE | YIELD |
| Reliable AWAY motion (valid trend and stationary measurement window) | CONTINUE |
| SUSTAINED gaze in INTERACTION_RANGE | ENGAGE |
| SUSTAINED gaze in APPROACHABLE | APPROACH |
| Other eligible cases, including NONE/INTERMITTENT or FAR | CONTINUE |

TOO_CLOSE uses the existing threshold/hysteresis. Its YIELD rule is a provisional
study assumption to give space, not evidence of crossing or route obstruction.
Relative closing while the robot moves leaves human motion UNKNOWN; gaze-based
decisions still work. No ego-motion compensation or physical handler is added.
The target-lock wire contract is `target-lock-v3`: identity/rebind/cooldown holds
have `execution_status: HOLD`, a `hold_reason` and `effective_decision: null`;
they never invent an action. Target availability and command checks remain.
SingleTrial accepts fresh pure proposals independently of execution holds; route
trials retain temporary stop-on-decision behaviour and finish at DECIDED.

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
