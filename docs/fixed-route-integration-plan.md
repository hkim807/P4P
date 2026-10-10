# Fixed-route interaction integration plan

Historical plan: the current estimator and policies use relative distance changes
only. Human radial motion and its stationary-window gate have been removed;
the motion-attribution blockers below describe the earlier implementation.

This plan uses the current server pipeline and robot client. The route,
approach, engage, pause, resume, and hardware-stop behaviors will be supplied
separately. The first complete version should stop the route, observe from a
stationary base, then decide. This avoids interpreting a person's motion from
camera-relative range while the robot travels.

## Current behavior that blocks a moving-route trial

1. `app/state/features.py` confirms human radial motion only when both robot
   velocities remain near zero throughout the fitted distance segment.
   `app/policy/rules.py` requires that confirmed motion for APPROACH/ENGAGE.
   On a moving route, the normal result is `DEFER / HUMAN_MOTION_UNKNOWN`.
2. `app/policy/target_lock.py` can acquire a lock as soon as one person is
   visible and the policy has not explicitly classified no attention or away.
   `robot/navel_client/physical_executor.py` pauses the route for LOCKED,
   MISSING, TENTATIVE_RETURN, or AMBIGUOUS. A brief uncertain detection can
   therefore stop the route without an action command.
3. During a physical approach, base motion makes human radial motion unknown.
   The server then changes the effective decision to DEFER. The physical
   executor currently requires every fresh response to repeat the active
   APPROACH/ENGAGE decision, so it cancels a correctly moving approach.
4. The recorded S2 front sonar values are zero. Physical APPROACH preflight
   requires positive front lidar and sonar readings and would reject such a
   frame. Units and zero semantics need robot-side verification.
5. Defaults of 1 s source age, 1 s physical response timeout, and 2 s action
   lease are provisional. A measured approach will likely need a longer,
   bounded lease and an independently enforced local stop condition.

These are separate from the S1–S5 perception issues documented in
[results/robot-scenarios/report.md](results/robot-scenarios/report.md).

## Intended first loop

```text
ROAMING (route owns base)
  -> CANDIDATE (stable person detection)
  -> PAUSING (request route pause; await stopped acknowledgement)
  -> OBSERVING (stationary base/head, fresh evidence window)
  -> APPROACHING (one bounded local action, route remains paused)
  -> OBSERVING (stop and re-estimate at new position)
  -> ENGAGING (one bounded local action)
  -> COOLDOWN -> RESUMING -> ROAMING

Any state -> STOPPED/FAULT on transport loss, target loss, obstacle,
stale command, failed hook, or operator stop. Recovery requires an
acknowledged stop and an explicit route ownership transition.
```

The observing step can go directly to ENGAGING when already within interaction
range. It can return to RESUMING when evidence rules out interaction or the
observation deadline expires. The first version should leave YIELD/route
conflict behavior out of the command set until path geometry is available.

## Work order

### 1. Establish a measured baseline and labels

- Record several repetitions of each S1–S5 condition with one known actor,
  measured 1/2/3/4 m marks, synchronized video or source-time annotations,
  and fresh receiver sessions. Include head following on and off.
- Log route state, pause/resume acknowledgements, head-focus state and any
  available head pose, plus raw SDK UID, distance, gaze, validity/confidence,
  optional position, and range sensor readings. Preserve missing values.
- Determine which SDK track corresponds to the actor in S2; investigate
  2-to-5 m jumps under one UID. Verify whether gaze overlap measures attention
  to the robot and what repeated 0.5 values mean. Establish sonar/lidar units
  and zero semantics on the installed SDK.
- Use the existing traces as a regression set. Report per-trial false action
  proposals, successful positives, time to decision, tracking coverage, and
  identity handoff errors. Do not tune against final command counts alone;
  conservative UNKNOWN can hide bad sensor separation.

**Done when:** the team can assign the actor's UID and measured distance for
each new trial and explain the gaze/range sensor values under positive and
negative conditions.

### 2. Refine perception and temporal evidence

- Calibrate gaze score and validity together. Keep UNKNOWN distinct from NONE.
  Choose thresholds and dwell from labeled positive and negative trials;
  explicitly test eye-only, head-turned, pass-by, and head-follow conditions.
- Investigate a bounded grace for an isolated null gaze value, provided the
  latest evidence is still valid before any new action. Verify that it does
  not produce false positives on negative trials.
- Change distance trend fitting so one omitted person frame can be bridged
  only within a small time/coverage limit and with a consistent UID or
  independently validated identity. Continue rejecting large range jumps.
  Increasing `max_gap_s` alone does not help: `features.adjacent` also requires
  consecutive frame sequence numbers.
- Separate thresholds for gaze support and distance support if labeled
  trials show different noise patterns. Recheck distance zones after the S2
  actor/range issue is resolved.

**Done when:** the negative trials yield no APPROACH/ENGAGE proposals, the
positive trials produce stable cues, and toward/away evidence survives ordinary
single-frame person dropouts without hiding target changes.

### 3. Add explicit route pause and observation intent

- Give the robot one owner for base motion: route, action, or stopped. Connect
  the supplied route controller through pause, resume, and stop acknowledgements.
  If separate scripts cannot safely share the Navel SDK session, use an IPC
  adapter to a long-lived route/control service rather than competing clients.
- Replace lock-implies-pause with an explicit, bounded PAUSE_AND_OBSERVE
  condition. Require a stable candidate and a spatial trigger; record why a
  pause was requested. A logical identity lock may exist without stopping the
  route. Prevent repeated pauses of the same passing person through cooldown.
- On pause acknowledgement, confirm near-zero base velocity, then begin a
  fresh stationary evidence window. The existing fit can otherwise include
  moving-route samples for up to its two-second window. Require head settling
  or mark camera-dependent cues unknown while the head moves.
- Make timeouts and recovery explicit: if pause fails or observation never
  becomes valid, stop or resume according to a confirmed controller state.

**Done when:** a moving route stops only for intended candidates, confirms a
stationary observation period, and resumes reliably after a no-action outcome.

### 4. Separate action authorization from new action proposals

- Keep the current rule policy for deciding whether to **start** APPROACH or
  ENGAGE after stationary observation. Add an in-flight execution state owned
  by the command/lock lifecycle. While an approach is moving, fresh responses
  should confirm the same session/lock/target, action lease, target visibility,
  and local safety; they need not recompute stationary-human-motion evidence
  or issue another APPROACH decision.
- Cancel on actual authorization loss: target disappearance or ambiguity,
  changed target/lock, obstacle, robot fault, stale responses, elapsed lease,
  or operator stop. Keep stop confirmation before route ownership changes.
- After a completed approach, stop, rebuild stationary evidence at the new
  position, and only then consider ENGAGE. After engagement completion, use
  execution feedback to enter cooldown and resume the route exactly once.
- Add deterministic tests for movement during APPROACH, valid continuation,
  each cancellation cause, feedback ordering, repeated responses, and
  approach-to-engage transition.

**Done when:** a controlled approach can move without self-cancelling merely
because the base is moving, while genuine target or safety loss still stops it.

### 5. Integrate the supplied robot commands

- Use the existing `navel-action-script-v1` JSON request/response contract or
  an equivalent long-lived controller adapter. The supplied commands must
  implement pause route, resume route, bounded approach, engage, and an actual
  hardware stop. Define which process owns the Navel SDK and base motors.
- For approach, the supplied controller should use live target and obstacle
  information throughout movement, maintain a bounded speed/distance/time,
  and report success only after reaching the intended range and stopping.
  The launch observation is only a snapshot.
- Validate front-range availability and physical units; set local obstacle
  limits from the installed robot's sensor contract. Do not infer an obstacle
  threshold from the current native-unit JSON alone.
- Measure route pause latency, response round trip, and real action duration.
  Set source-age, response-timeout, and action-lease values from those
  measurements while retaining a finite lease and independent hardware stop.

**Done when:** controller acknowledgements correspond to observed physical
effects and every tested cancellation invokes a confirmed stop.

### 6. Validate progressively on the real route

1. Replay old and new labeled recordings after each policy/estimator change;
   check for false action proposals and regressions.
2. Run the full client/server loop with a simulated route controller and
   stub action scripts. Exercise pauses, resumes, execution feedback, dropped
   responses, target loss, changed UID, and stop failures.
3. Run the real fixed route with decision/command dry-run and all actions
   logged. Confirm when a stop would be requested and which UID would be used.
4. Enable physical pause/resume and stop, still with stub interaction actions.
   Verify route ownership and emergency stop on the robot.
5. Enable stationary ENGAGE at close range; then bounded APPROACH; then the
   complete approach-to-engage-to-resume loop.

For every live trial, preserve raw, tracking, social, lock, command, execution,
and route-controller traces under one session ID. Acceptance should include no
unintended physical actions on repeated S1/S4 negatives, timely and correctly
targeted S2/S3 positives where applicable, S5 release while walking away, no
duplicate engagement, and confirmed stop/resume behavior after all faults.

## Code ownership map

| Area | Likely files |
| --- | --- |
| Gaze, distance, head/base validity | `app/state/features.py`, `app/state/estimator.py`, `config/temporal-state.json` |
| Candidate/observe/interaction lifecycle | `app/policy/target_lock.py`, `app/policy/rules.py`, `app/social_pipeline.py` |
| In-flight command state and feedback | `app/commands.py`, `robot/navel_client/physical_executor.py` |
| Route and action controller integration | `robot/navel_client/main.py`, `robot/navel_client/script_contract.py`, supplied controller adapter/scripts |
| Scenario and route regression tests | `tests/test_social_integration.py`, `tests/test_physical_executor.py`, new labeled trial fixtures |

The concrete route adapter depends on how the fixed-path controller starts,
pauses, reports stopped state, resumes, and exposes the hardware stop. The
pipeline can be refined and exercised with stubs before that API is available.

## If social decisions must be made during route motion

This is a larger perception change than the stop-and-observe first loop. The
current raw frame contains a scalar person distance, gaze overlap, forward base
speed and yaw rate. Its optional CAM_HEAD Cartesian person position was absent
throughout S1–S5. The latest locomotion packet has no source timestamp in the
frame and can be up to one second old by default. A decreasing range can be
caused by the robot moving toward a stationary person. Turning changes the
person's direction in the camera. Do not remove the stationary-base gate until
those effects can be separated and validated.

Propose a versioned observation-frame extension with these measured inputs:

1. **Perception acquisition time and separate sensor times.** Capture the
   perception receive timestamp immediately after `next_frame()` returns,
   before optional head-focus calls; prefer a documented SDK acquisition time
   when available. Retain odometry and head-pose measurement times, ages, and
   validity. Keep a short odometry history and align/interpolate it to the
   perception time rather than attaching an arbitrarily recent packet. Measure
   source latency and clock behavior on the installed robot.
2. **Person direction.** Require a usable calibrated 2D/3D position or bearing
   with range in an explicitly named camera/body frame, with validity and
   detection quality. The existing optional CAM_HEAD position is a candidate
   only if the installed SDK actually emits it. A bounding box can support a
   bearing only after camera intrinsics and frame orientation are verified.
   Preserve null when person geometry is unavailable.
3. **Robot displacement and camera orientation.** Record planar odometry pose
   or time-aligned position/yaw increments, full available planar twist,
   measured head pan/tilt, and the calibrated camera-to-base transform or its
   version. The current `linear_x`/`angular_z` pair and command to focus the
   head are insufficient to reconstruct the camera pose during a turn.
4. **Controller state and local safety provenance.** Record whether the route
   is moving, pausing, paused, or an action owns the base, with a source time.
   Keep range sensor timestamps/validity and verify units and zero semantics.
   Physical obstacle decisions remain local to the robot controller.

Update `robot/navel_client/main.py` and `adapter.py` to collect these measured
fields; version `app/domain/models.py` and the JSON Schema so older recordings
remain readable; retain aligned samples in `app/state/tracks.py`. Then update
`app/state/features.py` to transform each person position from camera to base
to odometry coordinates at the acquisition time and fit motion in that stable
frame. `app/state/estimator.py` should expose compensated motion and the reason
it is valid or unknown. `app/policy/rules.py` can use it only after tests show
stationary people stay stationary while the robot drives straight and turns.

Keep a separate relative-motion cue for local collision monitoring; a person
whose distance is shrinking because the robot approaches still requires an
obstacle response even when compensated human motion is stationary. Collect
labeled robot-moving/person-stationary, robot-stationary/person-moving,
both-moving, passing, and turning trials. Reject moving-base human-motion
classification when geometry, pose, synchronization, or identity is uncertain.

Moving-route **decision** support does not by itself authorize physical
APPROACH from a moving base. The current physical executor requires a paused,
near-zero-velocity base before launch. That ownership transition and the
in-flight authorization change above remain necessary.
