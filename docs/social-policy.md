# First rule policy

`app.policy.rules.decide` evaluates one SocialState and returns a `PolicyDecision`.
It is stateless and deterministic. The live receiver includes the decision under
`policy_decision` in each successful social response. `python -m app.decide`
applies the same function to a SocialState JSONL file and writes one decision
per state. Neither path issues a robot command.

The result includes `decision_id`, `source_state_id`, `session_id`,
`policy_version`, `decision`, `reason_code`, and an optional target UID/epoch.
Only `APPROACH` and `ENGAGE` identify a target. The output contract is
[`schemas/v1/policy-decision.schema.json`](../schemas/v1/policy-decision.schema.json).

Final outputs share the strict `FinalDecision` model in
`app.domain.model_decision`: `{"action":"CONTINUE","reason":"No person is visible."}`.
The four actions are:

- `CONTINUE`: complete the remaining fixed route without approaching or initiating interaction.
- `APPROACH`: leave the route, move towards the person and stop at conversation distance.
- `ENGAGE`: stop or remain stationary and initiate interaction with the nearby person.
- `YIELD`: temporarily move aside and backwards to give room to pass, then remain stopped
  there without automatically returning to the route. Manoeuvre parameters remain for later work.

`normalise_rule_decision` supplies an English reason. Live responses add
`final_decision` beside the unchanged `policy_decision`; `app.decide` replay rows
add it beside the existing rule fields. `DEFER` produces `final_decision: null`,
with its reason code and provenance retained in those fields. LLM/VLM envelopes
keep their existing `decision` field with the same action/reason shape. `STOP`
and `DEFER` are rejected as final actions; errors never become fallback actions.
Target-lock and command consumers continue to use their existing metadata.

Rules run in this order:

1. A failed processing status or stale state gives `DEFER`. Callers supply those
   conditions to the pure function. The current live receiver only evaluates
   freshly accepted states; a separate stream-loss watchdog remains to be built.
2. More than one visible person gives `DEFER`. No visible person gives `CONTINUE`.
   Temporarily missing tracks cannot become targets.
3. A person in `TOO_CLOSE`, or without a valid current distance zone, gives
   `DEFER`.
4. Valid `NONE` gaze or valid `AWAY` human radial motion gives `CONTINUE`.
5. `SUSTAINED` gaze with valid `TOWARD` or `STATIONARY` human radial motion gives
   `ENGAGE` in `INTERACTION_RANGE`, or `APPROACH` in `APPROACHABLE`.
6. Other cases give `DEFER`, including `UNKNOWN` human motion, intermittent or
   unknown gaze, and a person in `FAR`.

The policy checks cue validity as well as category names. It never emits `YIELD`:
the current SocialState has no verified route-conflict input. This initial rule
does not lock a target, manage cooldown, or deduplicate repeated decisions.
The [target lock layer](target-lock.md) binds a logical lock to one UID/epoch at
a time and can hand off to a new UID under guarded short-gap evidence. It
overrides unsafe transitions during loss or ambiguity. Completion feedback and
speech deduplication remain separate work. Repeated `ENGAGE` decisions must
not be interpreted as repeated speech commands.

Replay of the seven pilot recordings shows why these are provisional decisions:
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
