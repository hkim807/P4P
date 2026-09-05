# Social Navigation Decision Pipeline Implementation Plan

## Purpose

This plan describes how to extend the current Ollama connectivity baseline into a robot-independent social-navigation decision pipeline. The target application is a guide robot operating in an unmanned laboratory, museum, or similar human-shared indoor environment.

The central research question is whether a large language model (LLM) or a vision-language model (VLM) produces safer and more socially appropriate high-level decisions when given controlled, comparable inputs. The pipeline must support repeatable offline experiments before it is integrated with the Navel robot.

The recommended design is a supervisory decision system:

1. Perception produces normalized observations without inferring social intent.
2. Temporal state estimation derives motion, attention, interaction, and uncertainty over time.
3. An interchangeable rule-based, LLM, or VLM policy proposes a high-level behaviour.
4. Deterministic validation and the robot's local navigation stack retain authority over physical safety.

## Target Architecture

```text
Robot-specific adapter
        |
        v
Canonical ObservationFrame ---------------- RGB frame references
        |                                         |
        v                                         |
Temporal buffer and tracking                     |
        |                                         |
        v                                         |
Feature derivation and state classification      |
        |                                         |
        +-----------------+-----------------------+
                          v
                   Canonical SocialState
                          |
             +------------+------------+
             v            v            v
         Rule policy   LLM policy   VLM policy
             +------------+------------+
                          v
                    BehaviorIntent
                          |
                          v
           Schema and semantic validation
                          |
                          v
          Safety shield and execution adapter
                          |
                          v
                 Navel or another robot
```

The LLM or VLM should run at a supervisory frequency determined experimentally, probably event-triggered or around 1-2 Hz initially. It must not run the motor-control loop. Navel perception and locomotion packets arrive roughly every 0.1 seconds, while `base_vel` must be refreshed every 10 ms. Therefore, the model should choose behaviours, goals, and social preferences while a deterministic local controller handles collision avoidance, braking, acceleration, and actuator commands [1].

This separation follows the peer-reviewed architecture of Ruo et al., which uses an LLM to choose navigation modes and social constraints while a Control Barrier Function layer enforces them [2]. VLM-Social-Nav similarly uses a VLM to supply a social cost to a conventional planner rather than directly controlling actuators [3]. More recent CVPR work separates a semantic VLM component from a specialized action generator [4].

## 1. Define Robot-Independent Contracts

Create three versioned schemas before implementing Navel-specific processing. Python implementations can use Pydantic models that emit JSON Schema, allowing the same contracts to be used by HTTP clients, replay tools, and future ROS adapters.

### 1.1 ObservationFrame

`ObservationFrame` is the normalized output of every robot adapter. It should contain:

- Schema version and observation ID.
- Monotonic pipeline timestamp and original source timestamps.
- Coordinate-frame identifier and transform provenance.
- Robot pose, linear and angular velocity.
- Current navigation task, goal, and controller status.
- Free-space or range observations.
- Per-human track ID, position, detection confidence, and sensor provenance.
- Optional head pose, gaze, body pose, facial expression, speech activity, and group observations.
- Image references with camera identity, calibration, dimensions, and timestamps.
- Explicit missing values, observation age, and uncertainty.
- A capability manifest declaring which optional fields the source robot can provide.

Use SI units and one documented robot-centric convention, such as X forward, Y left, and Z up. Store RGB images separately and reference them by immutable IDs or paths rather than embedding large base64 payloads in the state JSON.

Keep raw and derived information distinct. For example, `position_robot_m`, `gaze_overlap`, and `id_score` are observations; `closing_speed_mps`, `gaze_pattern`, and `path_conflict_probability` are derived features.

Navel can populate much of this contract. Its documented `Person` structure includes IDs, face landmarks, head orientation, gaze, `gaze_overlap`, facial-expression values, distance, and coordinate-specific 3-D points. Locomotion data includes odometry and range values [5]. Its head and chest camera APIs can return timestamped HWC `uint8` RGB frames [6].

### 1.2 SocialState

`SocialState` is the output of temporal estimation. It should be compact enough to pass to a model and should not be a transcription of every sensor packet.

It should contain:

- **Motion:** velocity, acceleration, heading, closing speed, and distance trend.
- **Prediction:** closest-approach distance and time, path-conflict probability, and predicted trajectories with uncertainty.
- **Attention:** current gaze, gaze ratios over multiple windows, sustained/intermittent/glance classification, and head/body orientation.
- **Social structure:** group membership, F-formation region, local crowd density, and pedestrian flow.
- **Interaction state:** attending, available, speaking, gesturing, or disengaging, preferably as probabilities with supporting evidence.
- **Track quality:** track age, time since seen, predicted-only status, covariance, and contributing sensors.
- **Robot context:** guiding state, destination, previous behaviour, and execution result.

The typed state should be canonical. A deterministic prompt renderer may generate a concise natural-language summary, but natural language should not replace the structured state. This keeps experiment inputs reproducible and prevents minor wording changes from silently changing their meaning.

### 1.3 BehaviorIntent

Every policy must return the same `BehaviorIntent` schema. A starting form is:

```json
{
  "schema_version": "1.0",
  "observation_id": "scenario-042:t-1800",
  "action": "YIELD",
  "target_human_id": 17,
  "preferences": {
    "target_speed_mps": 0.2,
    "preferred_social_distance_m": 1.0,
    "passing_side": "RIGHT",
    "hold_duration_s": 1.0
  },
  "valid_for_ms": 750,
  "reason_codes": [
    "HIGH_PATH_CONFLICT",
    "HUMAN_APPROACHING"
  ]
}
```

The initial action vocabulary should preserve the outcomes identified in the project research:

```text
CONTINUE
MONITOR
ORIENT
SLOW
YIELD
AVOID
APPROACH
GREET
GUIDE
WAIT
RESUME
DISENGAGE
```

Each action needs a precise operational definition, compatible and required parameters, preconditions, completion conditions, and fallback behaviour.

Hard braking distances, collision thresholds, and actuator limits must not be model-selected fields. They belong to the safety controller. A model-reported confidence value may be recorded for analysis, but it must not influence safety unless it has been independently calibrated.

## 2. Build Temporal Social-State Estimation

Begin with transparent deterministic estimators before introducing learned classifiers:

1. Time-align perception, locomotion, odometry, and camera data.
2. Transform observations into the canonical coordinate frame.
3. Maintain a ring buffer for each tracked person.
4. Smooth position and velocity with a constant-velocity Kalman filter.
5. Compensate human motion estimates for robot ego-motion.
6. Propagate uncertainty through short occlusions.
7. Calculate closest point of approach and time to closest approach.
8. Calculate path-intersection and collision-risk features.
9. Derive multi-window attention and motion features.
10. Apply hysteresis or state machines to prevent frame-level label flicker.

Suggested initial aggregation windows are 0.5, 2, and 5 seconds. The five-second window proposed in the planning documents is an experimental starting point, not a literature-established optimum. Window length, frame sampling, model invocation interval, decision validity, and model latency must be varied separately.

Maintain the cue hierarchy established in the project research:

- **Critical:** position/proxemics, motion, trajectory conflict, tracking, occlusion, and uncertainty.
- **High:** head/body orientation, gaze, group formation, and local crowd flow.
- **Contextual:** gestures, turn-taking, engagement, and facial affect.

Avoid collapsing all evidence into a single intent label too early. Retain probabilities and evidence, such as `interaction_readiness_probability`, `path_conflict_probability`, and `attention_state`, so the decision layer can distinguish ambiguity from confidently observed behaviour.

Recent HRI research reinforces the need to evaluate motion prediction through downstream navigation and human outcomes. In an 80-participant, two-platform study, average trajectory error alone did not reliably predict robot-navigation performance or human impressions [7].

## 3. Implement Interchangeable Decision Policies

All policies should implement one interface:

```python
class DecisionPolicy(Protocol):
    def decide(
        self,
        state: SocialState,
        frames: list[ImageFrame] | None = None,
    ) -> BehaviorIntent: ...
```

### 3.1 Deterministic Rule Baseline

Implement a conservative rule policy before evaluating generative models. Example rules include:

- High path conflict and low predicted clearance produce `YIELD`.
- A stale or highly uncertain nearby track produces `SLOW` or `WAIT`.
- A stationary person showing sustained attention may permit `ORIENT` or `APPROACH`.
- A conversational group produces `AVOID` if the planned path crosses its interaction space.
- No relevant human interaction returns `CONTINUE` or `MONITOR`.

This baseline answers whether either generative model adds value, rather than merely which model performs better.

### 3.2 LLM Policy

The LLM request should contain:

- Fixed, versioned system instructions.
- Exact action definitions.
- Field, unit, confidence, and missing-data conventions.
- The compact `SocialState`.
- Operational bounds and robot capability constraints.
- A required JSON Schema response.

Use temperature zero for the main evaluation. Ollama supports JSON-schema-constrained output, but the pipeline must still validate semantics and numerical values independently [8].

### 3.3 VLM Policy

The VLM should receive:

- The identical `SocialState` used by the LLM.
- The same task, action definitions, bounds, and output schema.
- A timestamp-aligned current RGB frame.
- Initially, four sampled frames over the preceding two seconds.
- Optional track-ID overlays only when the same IDs appear in the structured state.

The visual channel is intended to contribute information that is difficult to represent through existing detectors, including body posture, gestures, group layout, exhibit context, and unusual social configurations. The VLM should not be required to reconstruct metric distance or velocity already available from tracking and range sensing.

An appropriate initial model pair is `qwen2.5:7b-instruct` and `qwen2.5vl:7b`, using the same quantization class where possible [9][10]. Pin exact model digests, prompts, inference parameters, and Ollama versions for reproducibility.

## 4. Validate Decisions Before Execution

Validation should occur in layers:

1. **Syntax:** valid JSON and matching schema version.
2. **Domain:** known action, existing target ID, finite values, and allowed ranges.
3. **Action semantics:** required parameters and action preconditions are satisfied.
4. **State freshness:** the referenced observation is still current and the situation has not materially changed during inference.
5. **Robot capability:** the selected robot supports the requested behaviour.
6. **Safety:** local clearance, braking, speed, and collision rules permit execution.

Invalid, timed-out, or stale decisions should produce an explicit conservative fallback such as `SLOW`, `WAIT`, or continuation of the last validated safe behaviour. Never silently coerce a substantially invalid decision into an executable command.

The execution adapter should convert `BehaviorIntent` into robot-specific calls. On Navel, high-level operations such as approaching or navigating to a pose should be preferred where suitable. If velocity control is required, it must run in a separate deterministic process at the frequency required by the SDK, with a watchdog that stops the robot on missed updates.

## 5. Design a Controlled LLM-VLM Comparison

Use the following primary experimental conditions:

| Condition | Input and policy | Purpose |
| --- | --- | --- |
| R0 | `SocialState` to deterministic rules | Non-generative baseline |
| L1 | `SocialState` to Qwen2.5 LLM | Main LLM condition |
| V0 | `SocialState` to Qwen2.5-VL without images | Controls for model-family differences |
| V1 | `SocialState` plus aligned RGB frames to Qwen2.5-VL | Main VLM condition |

The V0 condition is important because the text and vision variants are not identical models. Comparing only L1 and V1 would conflate the effect of vision with differences in model architecture and training.

Add targeted ablations after the primary comparison:

- Current state only versus temporal features.
- Geometry and motion only versus the complete social state.
- VLM visual-only input as a diagnostic condition.
- One frame versus four or eight sampled frames.
- Navel-native perception versus external or fused perception.
- Fixed few-shot examples versus retrieved examples, if examples are later introduced.

SALM and NaviWM should be cited cautiously. SALM is described by its project as intended for submission or under review, while NaviWM is currently a preprint [11][12]. They can inform prompt organization and structured spatiotemporal reasoning, but peer-reviewed studies should form the main evidential basis of the project.

## 6. Build the Evaluation Programme

### 6.1 Perception and State-Estimation Evaluation

Validate the inputs before comparing decision models:

- Timestamp drift, observed frequency, dropped-frame rate, and state age.
- Coordinate transform and unit correctness.
- Position and distance MAE/RMSE.
- Velocity error, heading error, jitter, and start/stop detection latency.
- Trajectory ADE/FDE plus closest-approach and path-conflict accuracy.
- Track IDF1/HOTA, ID switches, fragmentation, and occlusion recovery.
- Gaze precision/recall, angular error, valid-estimate rate, and temporal stability.
- Group-membership F1 and interaction-space violation detection.
- Gesture and engagement per-class precision/recall/F1.

Compare raw Navel gaze outputs with engineered temporal features such as gaze ratio and sustained-attention classification. The downstream feature can be useful even if the frame-level estimator remains noisy.

### 6.2 Frozen Offline Policy Benchmark

Build a versioned replay dataset. Each atomic example should contain:

- Raw observations and RGB frames.
- Derived `SocialState`.
- Exact state/frame timestamps.
- Scenario factors and difficulty.
- Acceptable action set.
- Acceptable parameter intervals.
- Expert rationale recorded before inspecting model outputs.
- Exact prompt, model, digest, quantization, and generation settings.

Allow multiple valid actions when a scenario has more than one socially acceptable response. A single exact label may make the benchmark falsely precise.

The scenario suite should cover:

- A stationary person looking elsewhere.
- A stationary person showing sustained attention.
- A person approaching the robot directly.
- A person approaching and then stopping.
- Perpendicular path crossing.
- Parallel or side-by-side walking.
- Repeated glances while walking.
- Head orientation toward the robot with gaze elsewhere.
- A face-to-face conversational group.
- A group opening space for the robot.
- A person emerging from behind another person or object.
- Several people moving in opposing directions.
- A person waving, pointing, or signalling stop.
- A visitor speaking while looking toward the robot.
- An empty room containing posters or displays with human faces.
- Sensor dropout, low light, backlighting, and delayed observations.

Report:

- Schema-valid response rate.
- Action appropriateness.
- Unsafe-action rate.
- Parameter interval compliance and MAE.
- Decision flip rate and minimum dwell time.
- Timeout, stale-decision, and fallback rates.
- End-to-end p50, p95, and p99 latency.
- Input/output token counts.
- GPU memory and energy use where measurable.
- Results by scenario and cue, not only aggregate performance.

Use a written scoring rubric, multiple blinded raters, and inter-rater agreement. Determine participant and scenario counts using a pilot and power analysis. Use paired confidence intervals and paired statistical tests because every policy receives the same replay examples.

### 6.3 Closed-Loop Simulation

Offline replay evaluates isolated decisions but not how decisions change future observations. Add a deterministic simulator or scripted kinematic harness and measure:

- Task completion and collision rates.
- Minimum human distance.
- Duration of proxemic-space violations.
- Time and path efficiency.
- Robot and human delay.
- Acceleration, jerk, and action oscillation.
- Recovery from occlusion and unexpected motion.
- Social compliance and legibility.

The social-navigation evaluation guidelines recommend quantitative metrics, baselines, repeatable scenario cards, and human-centered outcomes rather than a single success percentage [13]. SocNavBench provides an example of multi-metric simulation testing [14]. The Bi3 dataset is also relevant because it combines multiple robot platforms, multimodal motion data, and user impressions [15].

### 6.4 Navel Deployment Ladder

Progress through these gates:

1. Recorded Navel data with no live connection.
2. Live shadow mode that produces but does not execute decisions.
3. Stationary interaction tests.
4. Low-speed supervised navigation with a safety operator.
5. Controlled participant study.
6. Laboratory or museum pilot after component and system gates pass.

Human-subject studies and stored RGB, gaze, voice, expression, or identity data require an ethics and privacy plan. Store only what the research question requires, separate participant identity from sensor data, define retention periods, and document consent and deletion processes.

## 7. Repository Implementation Sequence

The current repository is a useful connectivity spike, but it implements only free-form text exchange through `/chat`. Extend it in the following order:

1. Add Pydantic domain models and emitted JSON Schemas for the three contracts.
2. Add synthetic and recorded-replay observation adapters.
3. Implement temporal buffers, tracking, uncertainty, and feature derivation.
4. Implement the deterministic rule baseline.
5. Add a production `/v1/decide` endpoint while retaining `/chat` for diagnostics.
6. Add `RulePolicy`, `OllamaTextPolicy`, and `OllamaVisionPolicy` implementations.
7. Add syntax, semantic, staleness, capability, and safety validation.
8. Add JSONL or Parquet experiment logging and deterministic replay tooling.
9. Add a dry-run execution adapter.
10. Add Navel perception and execution adapters after the offline pipeline passes its gates.

Suggested package boundaries are:

```text
app/domain/          Versioned schemas and contracts
app/adapters/        Synthetic, replay, and Navel inputs
app/state/           Buffers, tracking, features, and classification
app/policies/        Rules, Ollama text, and Ollama vision
app/decision/        Prompting, orchestration, and validation
app/execution/       Dry-run and Navel command mappings
app/recording/       Synchronized dataset capture
evaluation/          Scenarios, annotations, metrics, and reports
```

### Proposed API Evolution

```text
GET  /health
POST /v1/observations       Ingest a normalized observation
GET  /v1/state              Inspect the current social state
POST /v1/decide             Run a selected policy on a supplied state
POST /v1/replay             Evaluate a recorded example without execution
POST /v1/execute            Execute only a previously validated intent
POST /chat                  Retained as a development diagnostic
```

Image transfer should use immutable file/blob references or multipart requests rather than adding base64 images to the canonical JSON state. If the Flask gateway becomes a throughput bottleneck, retain the domain contracts and replace only the transport layer.

## 8. Milestones and Acceptance Gates

### Milestone 1: Contracts and Replay Harness

Deliverables:

- Versioned observation, social-state, and decision schemas.
- Synthetic scenario generator.
- Deterministic recording and replay format.
- Action vocabulary and scoring rubric.

Acceptance gate: one recorded or synthetic example can be replayed byte-for-byte without Navel or Ollama.

### Milestone 2: Temporal Social State

Deliverables:

- Time synchronization and coordinate normalization.
- Per-person buffers and baseline tracker.
- Motion, path-conflict, gaze-history, and uncertainty features.
- Unit and scenario tests.

Acceptance gate: every derived value is traceable to timestamped source observations, and stale or missing information remains explicit.

### Milestone 3: Safe Structured LLM Policy

Deliverables:

- Versioned prompt builder.
- Ollama structured-output adapter.
- Rule and LLM policies using the same contract.
- Full validation and fallback path.

Acceptance gate: malformed, out-of-range, timed-out, or stale model output cannot reach the execution adapter.

### Milestone 4: Comparable VLM Policy

Deliverables:

- Timestamp-aligned frame selection.
- VLM adapter with the same prompt and output contract.
- V0 text-only and V1 visual experimental conditions.

Acceptance gate: all policies can run on the same frozen dataset with inputs, outputs, timing, and model metadata logged consistently.

### Milestone 5: Offline and Closed-Loop Evaluation

Deliverables:

- Scenario cards and annotated test set.
- Component, decision, latency, stability, and resource metrics.
- Closed-loop simulator or kinematic harness.
- Statistical analysis with confidence intervals.

Acceptance gate: the comparison can distinguish the value of temporal features, social cues, model family, and visual input.

### Milestone 6: Navel Shadow and Physical Trials

Deliverables:

- Navel observation and execution adapters.
- Shadow-mode dashboard and logs.
- Safety watchdog and operator stop mechanism.
- Ethics-approved controlled evaluation protocol.

Acceptance gate: physical execution occurs only from a fresh, validated intent and remains subordinate to deterministic local safety control.

## 9. Immediate Next Milestone

The first meaningful end-to-end result should be:

> A recorded or synthetic `ObservationFrame` passes through temporal state estimation; the rule, LLM, and VLM policies emit the identical `BehaviorIntent` schema; invalid or stale responses fall back conservatively; and the complete run can be replayed without Navel or Ollama installed on the development computer.

This milestone creates the foundation for both the scientific comparison and eventual Navel integration without coupling the research pipeline to one robot or one model runtime.

## References

1. Navel Robotics. [Communication Classes](https://doc.navelrobotics.com/api/communication.html).
2. Ruo, A., Cacace, J., Dalmau-Moreno, M., Sabattini, L., and Villani, V. [An LLM-based Architecture for Socially Intelligent Robot Navigation based on Social Cues](https://doi.org/10.1109/RO-MAN63969.2025.11217843). IEEE RO-MAN, 2025.
3. Song, D., Liang, J., Payandeh, A., Raj, A. H., Xiao, X., and Manocha, D. [VLM-Social-Nav: Socially Aware Robot Navigation Through Scoring Using Vision-Language Models](https://doi.org/10.1109/LRA.2024.3511409). IEEE Robotics and Automation Letters, 2025.
4. Chen et al. [SocialNav: Training Human-Inspired Foundation Model for Socially-Aware Embodied Navigation](https://openaccess.thecvf.com/content/CVPR2026/papers/Chen_SocialNav_Training_Human-Inspired_Foundation_Model_for_Socially-Aware_Embodied_Navigation_CVPR_2026_paper.pdf). CVPR, 2026.
5. Navel Robotics. [Data Classes and Enums](https://doc.navelrobotics.com/api/data_structs.html).
6. Navel Robotics. [Python Camera Snippets](https://doc.navelrobotics.com/python_snippets.html).
7. Stratton, A., Singamaneni, P. T., Goyal, P., Alami, R., and Mavrogiannis, C. [How Human Motion Prediction Quality Shapes Social Robot Navigation Performance in Constrained Spaces](https://doi.org/10.1145/3757279.3788664). ACM/IEEE HRI, 2026.
8. Ollama. [Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs).
9. Ollama. [Qwen2.5 7B Instruct](https://ollama.com/library/qwen2.5%3A7b-instruct).
10. Ollama. [Qwen2.5-VL Model Tags](https://ollama.com/library/qwen2.5vl/tags).
11. Wang et al. [SALM: Unifying Large Language Model and Deep Reinforcement Learning for Human-in-Loop Interactive Socially-aware Navigation](https://sites.google.com/view/navi-salm/home). Project page and manuscript under review.
12. Wang, W., Ike, O., Choi, S., Hong, S., and Min, B. C. [Deductive Chain-of-Thought Augmented Socially-aware Robot Navigation World Model](https://arxiv.org/abs/2510.23509). arXiv preprint, 2025.
13. Francis, A. et al. [Principles and Guidelines for Evaluating Social Robot Navigation Algorithms](https://doi.org/10.1145/3700599). ACM Transactions on Human-Robot Interaction, 2025.
14. Biswas, A. et al. [SocNavBench: A Grounded Simulation Testing Framework for Evaluating Social Navigation](https://arxiv.org/abs/2103.00047). 2021.
15. Stratton, A., Singamaneni, P. T., Goyal, P., Alami, R., and Mavrogiannis, C. [Bi3: A Biplatform, Bicultural, Biperson Dataset for Social Robot Navigation](https://fluentrobotics.com/bi3dataset/). IEEE ICRA, 2026.
