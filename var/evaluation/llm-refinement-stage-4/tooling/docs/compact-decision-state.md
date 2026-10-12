# Compact shared decision state — stage 3

This page records the stage 3 representation. Stage 4's current label contract
is documented in [shared measurement descriptions](/Users/eddiekim/Documents/Projects/P4P/docs/semantic-decision-state.md).

`SocialState` remains the full diagnostic/replay record. `DecisionState` is a
deterministic projection used by both the v7 rules and the LLM. The estimator,
tracking, thresholds, readiness conditions and rule precedence are unchanged.

## Shared evidence

The compact object contains:

- `schema_version`, `observation_readiness`, `readiness_reason` and
  `no_person_observed_s`.
- `robot.motion_state`.
- Currently observed `people`, each with `visibility`, `latest_distance_m`,
  `gaze_state`, `distance_zone`, `relative_distance_trend`, `path_relation`,
  `pass_gesture`, `face_detected` and `path_position`.
- Each person's existing `observation_labels`: version, attention over time,
  current gaze, proximity, corridor position and relative separation.
- Each person's reduced `evidence`: `gaze_valid`, `distance_trend_valid`,
  `latest_distance_valid`, `latest_gaze_looking`, `latest_gaze_overlap`,
  `looking_exit_threshold`, `looking_bouts`, `looking_time_s`, `gaze_fraction`
  and `sustained_gaze_s`.

No new semantic labels or action recommendations are introduced in stage 3.
Current validity is explicit, and existing UNKNOWN/null meanings remain.
Numeric values are not rounded: exact threshold behaviour is preserved.

The effective gaze exit threshold remains because the rule's closing/low-gaze
condition distinguishes a genuinely low score from a hysteresis-band score
that retained `latest_gaze_looking: false`. Current-looking labels alone cannot
replace that comparison without changing behaviour. Recurring and sustained
attention operands also remain, including for non-final configurations.

## Outside the decision payload

The full SocialState still retains configuration, version/provenance, tracking
history/lifecycle metadata, missing tracks, raw velocities, head/nose geometry,
fit statistics, validity explanations and changes. These remain available to
recordings, tracing, target locks, execution checks and offline diagnostics.

The rule policy uses seven constants in a separate cached `RuleParameters`
object: looking exit fallback, maximum closing yield distance, recurring
attention enablement, minimum recurring bouts/time/fraction and minimum
sustained run. They are policy settings, not extra social observations. Cache
entries are keyed by the complete immutable effective TemporalConfig and
bounded to 32, so custom configurations retain their original behaviour.

`DecisionContext` separately carries state/session IDs, robot timestamp and
UID/epoch mappings for output correlation and targeting. These identifiers do
not enter the LLM text. The common projection retains observed-person order,
so target metadata matches the original rule output exactly.

## Interfaces and audit

`prepare_decision_input` validates a detached SocialState, recomputing its
authoritative readiness/labels through existing validators, and builds the
shared evidence, settings and context. Existing `decide(SocialState or dict)`
and `rule_readiness` callers are supported by this adapter. The production
social pipeline calls the rule core using `DecisionInput`.

The observation response exposes `decision_state` and its SHA-256 alongside
the unchanged full `social_state`. Live and offline LLM audits retain both
`social_state_json` (full diagnostic record) and `decision_state_json` (actual
model payload). The model worker freezes both before asynchronous inference.
Tests compare the rule input, server decision state and actual model message.

The system instructions and user-message prefix are byte-for-byte unchanged
from the frozen v14 baseline. Prompt metadata is versioned
`social-state-llm-v14-compact-v1` to distinguish the new representation. The
state schema is saved in `schemas/v1/decision-state.schema.json`.

## Verification and measurement

The original rule implementation is loaded from the stage 1/2 freeze rather
than rewritten as a test oracle. Every one of the nine recordings' 574 frames
is compared for readiness, complete PolicyDecision, reasons, target IDs/epochs
and source/decision IDs. The eligible count remains 402. Independent boundary
cases cover threshold equality/adjacency, null/fallback measurements, empty
scene timing/sample boundaries, recurring attention, sustained-run length,
path/pass precedence, missing/multiple people and stale/failed states.

The stage 3 evaluator reuses the frozen first and later timestamps, allowed
actions, model digest/runtime, generation settings and acceptance limits. It
runs three warm repetitions in S1–S9 order. Actual request hashes are compared
against model audit inputs and the compact input used by production rules.
Later diagnostics run after all operational trials and are excluded from the
primary score. No physical robot execution is instantiated.

Run from the repository root with local Ollama serving the installed baseline
model, using a new output directory:

```sh
.venv/bin/python -B -m scripts.evaluate_compact_state \
  --output var/evaluation/compact-state-new
```

`--prepare-only` verifies all-frame rule equivalence and saves the projected
snapshots without contacting Ollama. The original stage 1/2 freeze remains
unchanged; its current-worktree verification is expected to reject a changed
implementation, while the stage 3 tool verifies the frozen copies themselves.

Results: [stage 3 comparison](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/llm-refinement-stage-3/REPORT.md).
