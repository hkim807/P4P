# Canonical social rule policy (v2)

The authoritative decision table, thresholds, interpretation limits and complete
840-row matrix are documented in [the policy-comparison baseline](policy_comparison_baseline.md#e-transparent-rule-baseline).

`app.policy.rules.decide` consumes the same SocialState as the LLM comparison.
Its canonical `.action` is CONTINUE, YIELD, APPROACH, ENGAGE or null when not ready;
`.status` distinguishes DECIDED and NOT_READY. The existing controller transport
retains `decision="DEFER"` for null-action periods. DEFER is not an experimental
social action. Reasons, source IDs and optional target UID/epoch remain audited.

Compared with v1, intermittent attention and FAR deliberately continue the route,
missing retained tracks defer, moving-base UNKNOWN human motion no longer blocks
otherwise supported interaction, increasing separation discourages pursuit, and
measured path conflict / pass gesture have explicit precedence. These last two
cues are UNKNOWN in the present production estimator; their positive conditions
are covered by synthetic tests, not claimed as working perception.

The [target lock](target-lock.md), [command planner](command-feedback.md) and
[physical executor](physical-executor.md) remain separate. Repeated policy ENGAGE
outputs must not become repeated greetings. The model does not control the robot.

Replay new states with:

```bash
.venv/bin/python -m app.decide path/to/social.jsonl --output path/to/new-decisions.jsonl
.venv/bin/python -m evaluation.run_scenarios --output var/evaluation/rules-new
```

Historical v1 decisions/STOP model labels and original pilot reports are historical
artifacts, not measurements of the v2 rule policy. Rebuild states from raw data
when migrating the estimator version.
