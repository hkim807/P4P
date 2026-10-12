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

With a person present, readiness requires exactly one currently observed person, valid current distance
and usable NONE/INTERMITTENT/SUSTAINED gaze with existing coverage and category
dwell, plus valid processing and freshness. No person, multiple people, invalid
distance or UNKNOWN gaze keep observing. An optional `no_person_continue_after_s`
permits CONTINUE after that duration of contiguous empty observations and at
least `min_samples`; its default is disabled. Source gaps and detected people reset
the empty interval. No stationary-window, human-motion,
identity-lock or distance-trend prerequisite is added to general readiness.
Explicit path conflict can resolve before distance/gaze warmup, and PASS can
resolve with valid current distance before gaze warmup. Proximity alone cannot.

Eligible `social-rules-v7` observations use this order:

| Evidence | Action |
| --- | --- |
| Configured continuous empty observation interval completed | CONTINUE |
| Explicit upstream path CONFLICT | YIELD |
| Measured PASS invitation | CONTINUE |
| Current nose classified IN_WAY, face detected, distance within `config.yield_closing_max_distance_m`, valid DECREASING trend, valid NONE gaze, latest looking false and latest gaze score ≤`evidence.looking_exit_threshold` (fallback `config.looking_exit`) | YIELD |
| Same closing/low-gaze evidence with nose classified TO_SIDE | CONTINUE |
| Latest looking false while historical SUSTAINED category is held | CONTINUE |
| Sustained or qualifying recurring attention in TOO_CLOSE or INTERACTION_RANGE | ENGAGE |
| Reliable increasing relative separation outside conversation range | CONTINUE |
| Sustained or qualifying recurring attention in APPROACHABLE | APPROACH |
| Other eligible cases, including NONE, incidental INTERMITTENT or FAR | CONTINUE |

The nose corridor gates v5's closing/low-gaze trigger. `path_position` must be
`IN_WAY`; `TO_SIDE`, `BEHIND` and `UNKNOWN` cannot activate that trigger.
See [nose-based path position](path-position.md) for the fixed-head assumption,
coordinate mapping, thresholds and hysteresis. An explicit upstream
`path_relation: CONFLICT` still takes priority over the inferred corridor.
Unknown position does not establish a clear route and does not prevent normal
attention-based decisions. Older raw captures without nose geometry therefore
cannot activate the corridor trigger; replay a full SDK capture to recover it.

The closing/low-gaze trigger replaced v3's distance-only TOO_CLOSE trigger.
TOO_CLOSE remains a distance category: attentive nearby people support ENGAGE,
and eligible non-attentive people support CONTINUE unless the new closing trigger
applies. A stationary person at 0.4 m does not cause YIELD solely through proximity.
The exact current distance cutoff is `config.yield_closing_max_distance_m`,
independent of distance-zone hysteresis. The development config sets it to 3 m,
including exactly 3 m; null removes this distance limit while retaining every
other required cue. Low gaze uses the effective distance-aware exit threshold,
falling back to `config.looking_exit` (0.90 in
`config/temporal-development-frozen.json`); a score at or above `looking_enter`
(0.93 in that config) establishes looking, while the band between those
thresholds retains the previous per-sample state. NONE requires established low attention over the temporal
window, not one low sample. DECREASING requires a valid fitted slope more negative
than `-config.distance_deadband_mps` (currently -0.1 m/s), with valid current
distance, sufficient span/samples and acceptable residual.

The SDK adapter sets `face_detected: true` only for a finite positive-size `face`
bounding box. Missing/invalid boxes supply no detection evidence. Face evidence
and `evidence.latest_gaze_overlap` clear immediately on missing observations.
At 10 Hz with a current IN_WAY position, uninterrupted low gaze and smooth closing, the development config's 2 s
minimum span plus 0.1 s category dwell allows a first YIELD at approximately
2.1 s. Gaps, missing readings and failed fits can delay or prevent it. The
APPROACHABLE boundary is 6 m in that config, independent of the 3 m yield cutoff.
The class defaults and `config/temporal-state.json` remain a separate earlier
configuration; each SocialState embeds the effective parameters used for it.
Relative closing describes decreasing separation regardless of which participant
is moving; human motion is not estimated and closing does not establish a path conflict.
No ego-motion compensation or physical handler is added.

Recurring attention qualifies only when `config.allow_recurring_attention` is
true. The [final-scenario preset](final-scenario-tuning.md) disables it, uses a
2 m interaction boundary, and requires stronger gaze scores beyond 2.75 m.
Gaze overlap 0.5 is a usable low-gaze measurement under the default and current
presets, based on robot tests while looking to the side. Missing gaze remains
unavailable. `gaze_unavailable_score` defaults to null; an explicit numeric
override can exclude a sentinel for a different source.

The LLM prompt is `social-state-llm-v14`. It asks for an independent social
judgment using the shared SocialState, readiness gate and four action meanings.
It describes social objectives and evidence limits without reproducing the
rule policy's conditions, numeric cutoffs or action precedence. The complete
state and effective config still reach both policies. No rule correction or
fallback is applied to model output. The explanation is requested as one sentence
of at most 25 words; that is prompt guidance, not a new output-validation rule.
The v14 prompt explains the shared descriptive `observation_labels` added in
estimator v6; these are evidence aliases, not engagement intent or rule advice.
See the [current state example](../var/evaluation/llm-prompt-v14/scenario-4-prompt.md)
and the historical [v13 prompt comparison](../var/evaluation/llm-prompt-v13/README.md).
Target locks release on `RELATIVE_SEPARATION_INCREASING` outside conversation
range, whether the robot is moving, stationary, or its velocity is unavailable.
Historical reports and freeze manifests remain under their recorded versions.
`config/live-study-freeze-yield-low-gaze.json` predates the current code and prompt;
create and verify a new freeze before collecting a new study.
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
