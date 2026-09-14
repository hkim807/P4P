# LLM social-navigation policy: prompt and output design

## Scope

The LLM is a slow, high-level policy component. It receives one temporally
estimated `SocialState` when the event scheduler requests a decision and returns
one short-lived `BehaviorIntent`. It is not a collision-avoidance controller and
its output is not an actuator command. A later deterministic validation and
execution layer must still check freshness, stopping distance, free space,
capabilities, and local collision constraints.

The first deployment context is a guide robot roaming a fixed route in an
unmanned museum or laboratory. The policy may maintain the route, monitor,
orient, slow, yield, avoid, approach, greet, guide, wait, resume, or disengage.

## Research translated into design decisions

### Keep the language model supervisory and bound its outputs

Ruo et al. place an LLM at the high level of a two-layer social-navigation
architecture. Their LLM selects a navigation mode, maximum speed, and social
zone distance; a deterministic control-barrier-function controller handles
low-level motion. Their prompt constrains speed to 0.1-0.8 m/s and social
distance to 1.0-1.4 m, supplies defaults for uncertain state, and uses function
calling. In 100 manually checked requests, their reported accuracies were 87%
for navigation mode, 76% for speed, and 85% for distance (82% mean). This is
useful evidence for constrained high-level output, but also shows why an LLM
decision cannot itself be treated as a safety guarantee.

Implementation consequences:

- `SYSTEM_PROMPT` says the LLM proposes one high-level behavior and that another
  controller owns motion safety.
- Speed and social-distance preferences initially use the same published
  0.8 m/s ceiling and 1.0-1.4 m range. These are experimental defaults, not
  universal human-comfort constants, and should later become deployment
  configuration.
- The prompt never encodes age, disability, appearance, or identity heuristics.
  It acts on measured motion, attention, engagement, geometry, and uncertainty.

Source: A. Ruo et al., [An LLM-based Architecture for Socially Intelligent Robot
Navigation based on Social Cues](https://doi.org/10.1109/RO-MAN63969.2025.11217843),
IEEE RO-MAN 2025; [open manuscript and data](https://zenodo.org/records/15648910).

### Invoke the model on social events and keep a local planner downstream

VLM-Social-Nav invokes its VLM only when an entity detector finds a significant
social cue or interaction. Its prompt supplies the navigation task, current
action, social-etiquette guidance, and a constrained speed/heading response. The
model modifies a social cost used by a conventional motion planner rather than
directly driving motors. The reported GPT-4V inference time was approximately
three seconds, further motivating event-driven rather than sensor-rate calls.

Implementation consequences:

- The existing `DecisionScheduler` remains in front of the policy.
- The prompt includes the scheduler triggers and robot task context.
- Intents expire after at most two seconds, matching the current active-scene
  refresh interval. This two-second value is a project timing decision, not a
  proxemic result from the paper.
- No fixed left/right etiquette is assumed. Regional conventions belong in
  explicit deployment configuration; otherwise the model uses `EITHER`.

Source: D. Song et al., [VLM-Social-Nav: Socially Aware Robot Navigation Through
Scoring Using Vision-Language Models](https://doi.org/10.1109/LRA.2024.3511409),
IEEE Robotics and Automation Letters, 2025; [author manuscript](https://arxiv.org/abs/2404.00210).

### Preserve the perception-prediction-action boundary

Social-LLaVA's SNEI dataset contains more than 40,000 human-annotated
visual-question-answer pairs from 2,000 social scenarios, organized around
perception, prediction, reasoning, action, and explanation. Its results support
making the evidence-to-action boundary explicit rather than asking a model to
reconstruct temporal motion from a single observation.

Implementation consequences:

- The temporal estimator, not the LLM, computes motion relation, trends,
  predicted positions, closest approach, attention windows, and uncertainty.
- The LLM receives canonical structured state and selects an action.
- `reason_codes` expose a short, enumerable evidence trace for evaluation. The
  system does not request or store private chain-of-thought.

Source: A. Payandeh et al., [Social-LLaVA: Enhancing Social Robot Navigation
through Human-Language Reasoning](https://doi.org/10.1109/IROS60139.2025.11247618),
IEEE/RSJ IROS 2025; [author manuscript](https://arxiv.org/abs/2501.09024).

### Treat prompt wording as an experimental variable

Two newer prompt studies are informative but are preprints, so they are used as
supplementary evidence. Xiao and Yamasaki report that prompt effects vary across
models and datasets, and that an unsuitable system prompt can perform worse than
no system prompt. MAction-SocialNav reports that explicit causal-reasoning
instructions do not consistently improve action prediction, while a constrained
feasibility-then-social-norms-then-efficiency procedure is more reliable.

Implementation consequences:

- The prompt has a version constant (`llm-social-navigation-v1`) and will be
  logged with every experiment input.
- It specifies a decision priority and observable evidence rules, but asks only
  for the final structured selection rather than a narrative reasoning chain.
- Temperature is zero for the comparison baseline.
- Prompt/model/version changes should create a new experimental condition
  instead of silently replacing results.

Sources: H. Xiao and T. Yamasaki, [Probing Prompt Design for Socially Compliant
Robot Navigation with VLMs](https://arxiv.org/abs/2601.14622), 2026 preprint;
[MAction-SocialNav](https://arxiv.org/abs/2512.21722), 2025 preprint.

## Runtime contract

### Intrinsic action contract

The policy selection should be internally coherent before it can become a
`BehaviorIntent`. The immutable `ACTION_CONTRACTS` table is authoritative for
the requested output and generates the `action_contract` object included in
every prompt. Omitted and explicit `null` preference values are equivalent:
required preferences must be non-null, forbidden preferences must be omitted or
null, and optional preferences may be omitted, null, or valid.

| Action | Target | Required preferences | Optional preferences | Forbidden preferences |
| --- | --- | --- | --- | --- |
| `CONTINUE` | Forbidden | — | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side`, `orientation_target_rad`, `hold_duration_s` |
| `MONITOR` | Forbidden | — | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side`, `orientation_target_rad`, `hold_duration_s` |
| `ORIENT` | Required | — | `orientation_target_rad` | `target_speed_mps`, `preferred_social_distance_m`, `passing_side`, `hold_duration_s` |
| `SLOW` | Optional | `target_speed_mps` | — | `preferred_social_distance_m`, `passing_side`, `orientation_target_rad`, `hold_duration_s` |
| `YIELD` | Optional | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side`, `hold_duration_s` | `orientation_target_rad` |
| `AVOID` | Optional | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side` | `orientation_target_rad`, `hold_duration_s` |
| `APPROACH` | Required | `preferred_social_distance_m` | `target_speed_mps` | `passing_side`, `orientation_target_rad`, `hold_duration_s` |
| `GREET` | Required | — | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side`, `orientation_target_rad`, `hold_duration_s` |
| `GUIDE` | Required | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side` | `orientation_target_rad`, `hold_duration_s` |
| `WAIT` | Forbidden | `hold_duration_s` | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side`, `orientation_target_rad` |
| `RESUME` | Forbidden | — | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side`, `orientation_target_rad`, `hold_duration_s` |
| `DISENGAGE` | Required | — | — | `target_speed_mps`, `preferred_social_distance_m`, `passing_side`, `orientation_target_rad`, `hold_duration_s` |

Optional targets on `SLOW`, `YIELD`, and `AVOID` identify the human associated
with the global navigation response when one track is relevant. This contract
does not decide whether that target remains fresh, whether a speed is safe, or
whether the robot is in a state where `RESUME` is feasible. Those contextual
checks remain responsibilities of the later deterministic validator and
controller.

`LLMPolicyBridge.decide(state, triggers)` performs these steps:

1. Render a deterministic compact request containing the prompt version, task
   context, scheduler triggers, allowed reason codes, non-null `SocialState`
   fields, and the response JSON Schema.
2. Send the same response schema through Ollama's OpenAI-compatible
   `response_format`, at temperature zero.
3. Parse and type-check the response strictly. Markdown, explanatory prose,
   unknown fields, invalid enums, invalid types, unsupported reason codes, NaN,
   infinity, and out-of-range values fail.
4. Normalise only safe omissions: remove preferences forbidden for the selected
   action; default `SLOW.target_speed_mps` to 0.2,
   `APPROACH.preferred_social_distance_m` to 1.2, and `WAIT.hold_duration_s` to
   1.0; and fill a required target only when exactly one currently observed
   human is eligible. Unknown, predicted-only, stale, or ambiguous targets fail.
5. Add `decision_id`, observation/state IDs, schema version, and the state clock
   timestamp in deterministic code, then validate the complete result through
   the public `BehaviorIntent` model.

Ollama recommends both passing a JSON schema to the structured-output API and
including it in the prompt, followed by application-side validation. The bridge
does all three. See [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
and [OpenAI API compatibility](https://docs.ollama.com/api/openai-compatibility).

The public API returns the validated object under `behavior_intent`. Invalid
model output returns HTTP 502 with error code
`invalid_llm_behavior_selection`; provider failures use `llm_request_failed`.
In both cases the observation remains accepted and estimator history is
preserved. No invalid selection may be sent to a robot executor.

## Missing information and conservative behavior

The Navel adapter currently provides human motion, distance when available,
head/gaze information, and facial-expression scores. It does not provide
per-person speech meaning, a confirmed guidance request, body pose, groups, or
images. Absent and `UNKNOWN` fields therefore remain unknown; the prompt forbids
turning absence into negative evidence.

In particular, gaze or speech activity may justify `ORIENT`, `GREET`, or further
monitoring, but cannot establish what a visitor said. A future dialogue or
request-intent field must be added to `SocialState` before the policy can infer a
new destination and confidently begin `GUIDE`. This limitation should be
represented in test labels rather than hidden in prompt assumptions.

## What is still outside this change

- Deterministic semantic and safety validation against the newest state.
- A conservative fallback policy for invalid, stale, or timed-out decisions.
- Mapping `BehaviorIntent` to Navel capabilities and commands.
- Prompt calibration on a labelled scenario suite, comparison against a rule
  policy, and accuracy/calibration reporting by action and social scenario.
- A VLM policy using the same action-selection schema and aligned images.

Until those stages exist, the Navel client remains observation-only and prints
the returned intent for inspection.
