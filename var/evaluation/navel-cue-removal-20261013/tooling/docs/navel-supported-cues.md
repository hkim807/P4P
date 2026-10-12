# Supported Navel cues — 13 October 2026

Navel does not supply a measured path-relation cue or a pass gesture. The current
pipeline therefore removes `path_relation`, `pass_gesture`,
`measured_path_relation` and `measured_pass_gesture` entirely from current raw,
SocialState and DecisionState contracts. They are absent from emitted raw
frames, tracking samples, estimator output, shared policy input, LLM text and
published schemas. The two unsupported rule branches/reasons and readiness
shortcuts have also been removed.

The measured nose-based `path_position` remains. Its forward-corridor estimate
is supported by Navel geometry and is used by the existing closing/low-gaze
yield rule. This removal does not replace missing cues with assumed clearance,
absence of a gesture or inferred intent.

DecisionState and its description block are version 3; prompt metadata is
`social-state-llm-v14-semantic-v3-navel-cues`. The system instructions and all
remaining descriptions, numeric operands, estimator thresholds and supported
rule conditions are unchanged. The native rule policy remains v7 because its
Navel-supported conditions, outputs, reasons and decision IDs are preserved.
Its former synthetic path/gesture branches are no longer accepted behaviour.

Current person descriptions are attention history, latest gaze, proximity,
corridor position, relative separation and face detection. The robot's motion
description also remains. Both policies consume exactly the same shared object.

## Archived evaluation data

Frozen stage 1–4 artifacts are historical evidence and are not rewritten.
Current live validation rejects the removed fields, even when their value is
UNKNOWN. Offline SocialState readers may remove old UNKNOWN placeholders on a
detached copy through `app.replay.legacy.remove_legacy_unknown_cues`.
The original file and source-provenance record remain unchanged. An archived
CLEAR, CONFLICT or PASS measurement is rejected with instructions to use its
frozen implementation, rather than silently erasing measured evidence.

The rule oracle now loads both the original frozen rules and their original
SocialState model. This avoids accidentally testing old rule code against a new
schema/readiness implementation. Current supported Navel states are compared
with that oracle, whose missing optional fields default to UNKNOWN as before.

The paired-input preparation tool retains the original frozen full-state hash
as `frozen_full_social_state_sha256`. Its current full-state hash represents
exactly the original evidence with the two UNKNOWN placeholders removed. It
checks that no other full-state data changed. First/later timestamps, target
identity and expected actions remain frozen.

## Proximity: observation versus task definition

`latest_distance_m` is a measurement. `distance_zone` and its proximity
description are configured categories; they are not universal facts about the
distance at which someone wants a conversation.

The final evaluation preset uses boundaries of 0.6, 2 and 6 metres, with zero
zone hysteresis. Its categories are distance ≤0.6 m, >0.6–2 m, >2–6 m and >6 m.
The labels currently call these closer than conversation distance, within
conversation distance, beyond conversation distance within social range, and
beyond social range. Other configurations can use different boundaries and
stateful hysteresis.

Those category definitions do not select an action: the LLM independently
judges attention, separation and position. However, the conversation/social
names already provide a task-specific interpretation of distance. The compact
state does not currently include numeric range definitions, although it does
include the exact measured distance. This is a real limitation of the label
contract, not a new finding about human intent.

For a shared task with a fixed interaction range, explain that reference range
clearly to both policies while leaving the social action to each policy. For an
experiment testing the LLM's own distance judgment, use measured metres and
neutral spatial descriptions in the shared state, and let the rule policy apply
its configured boundaries internally. That is a separate representation and
prompt experiment. Proximity and action definitions have not been changed in
this cue-removal step. The physical APPROACH stopping distance is a separate
executor setting, not identical to the estimator's conversation-range boundary.

## Verification

All 574 recorded frames preserve frozen v7 readiness, complete decisions,
reasons, targets and source/session IDs; 402 remain eligible. Independent rule
boundary tests continue to pass. Contract tests check removal end to end,
strict live rejection, lack of readiness bypasses, published schemas, and offline
UNKNOWN migration without altering source data. No new LLM accuracy/latency
claim is made from this cleanup; the recorded stage 4 metrics describe v2.
