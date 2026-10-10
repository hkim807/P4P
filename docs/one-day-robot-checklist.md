# One day with Navel: people, attention and visible social actions

Goal: a repeatable live demonstration where one person is detected, the robot
looks at them, and measured cues produce an appropriate visible action. Use the
rules policy as today's main condition. Budget about eight hours, with most time
spent observing and testing the actual robot. The targets below are proposed
pilot acceptance criteria, not established performance claims.

## 1. Prepare the exact system — 30 minutes

- [ ] Put the same current branch/code on the PC and Navel. The starting version
  is `feature/end-to-end-pipeline-refinement`, commit `e2b136b`.
- [ ] Confirm PC/robot connectivity and the installed SDK. Keep other scripts
  that command the robot's base/head stopped during these tests.
- [ ] Mark a short straight route and person positions at approximately 1.2,
  2.2 and 3 m. Verify the robot's stop control before movement trials.
- [ ] Set up a phone video showing both robot and person. Use verbal trial labels
  and a visible cue at the start/end. This supplies context missing from SDK logs;
  it does not establish precise camera/SDK exposure synchronization.

On the PC, from the repository root, start a rules-only receiver:

```bash
RUN_ID=day1-01
.venv/bin/python -m app.server --host 0.0.0.0 --port 6060 \
  --output "var/recordings/${RUN_ID}-raw.jsonl" \
  --sdk-output "var/recordings/${RUN_ID}-sdk.jsonl" \
  --camera-output-dir "var/recordings/${RUN_ID}-cameras" \
  --social-output "var/recordings/${RUN_ID}-social.jsonl" \
  --tracking-config config/person-tracking.json \
  --temporal-config config/temporal-development-frozen.json
```

Use a new RUN_ID whenever restarting the receiver; outputs are exclusively
created. Restart the PC receiver between isolated decision trials so temporal
history and locks start fresh. No Ollama server is needed for this condition.

## 2. Establish reliable detection and visible head focus — 45 minutes

On Navel, from its repository root, replace PC_IP:

```bash
python3 -m robot.navel_client.main --server http://PC_IP:6060 \
  --sdk-capture --camera-capture --camera-interval 0.2 \
  --head-focus --head-focus-magnitude 1.0
```

This streams observations and issues real head-focus commands, without starting
the base route or executing social actions. Magnitude 1.0 matches the current
route-trial head setting. SDK `look_at_person` follows a UID; physical focus
release must be observed on the installed robot.

- [ ] Enter the view at about 2 m, remain still, move left/right, turn away and
  return. Repeat five times, preferably with a second person too.
- [ ] Check that the intended face has positive `face_detected`, plausible
  `distance_m`, mostly continuous UID and visible head following.
- [ ] Leave the view and confirm detections clear. Observe what the physical
  head does after loss and client shutdown.
- [ ] Address camera framing, light, obstruction and competing controllers
  before changing policy thresholds. Suggested gate: tracking stays usable for
  five seconds in at least four of five attempts.

## 3. Measure gaze on the actual robot — 75 minutes

Capture these labelled conditions for 10–15 seconds each at approximately 1.2
and 2.2 m, with at least two people and two repetitions:

| Condition | Instruction |
| --- | --- |
| Direct attention | Look towards the robot's eyes |
| Eye-only away | Keep the head facing the robot and look to either side |
| Head turned away | Turn head and eyes towards something beside the robot |

- [ ] Compare raw `gaze_overlap` ranges/medians and variation within each period.
- [ ] Inspect full SDK `head_position`, `gaze`, face boxes and gaze-related fields
  alongside the video; these are captured even though not all drive the policy.
- [ ] Repeat a few conditions with physical head following disabled/reset. The
  flag `--no-head-focus` prevents new commands; it does not promise to cancel an
  earlier SDK tracking command. Verify the actual head behavior.
- [ ] Decide whether the scalar distinguishes attention from looking away.
  Scores consistently above 0.9 do not support the current 0.8/0.7 thresholds.

Do not spend this block stretching each recording to 0–1. That cannot create
separation if looking and non-looking periods overlap.

## 4. Make one bounded calibration change — 45 minutes

- [ ] If labelled scores separate, adjust `looking_enter` and `looking_exit` in
  `config/temporal-development-frozen.json`; choose entry higher than exit, with
  enough margin to avoid ordinary sensor noise flipping state.
- [ ] Keep the existing window, minimum span, coverage and dwell initially.
  `none_enter` and `sustained_enter` refer to the fraction of time classified as
  looking, not the raw SDK score; do not change them to raw-score thresholds.
- [ ] Use a repetition/person held out from threshold selection to check both
  direct-looking and looking-away states. Restart the server to load changes.
- [ ] If scalar scores do not separate, time-box investigation of head orientation
  as a head-facing proxy. It requires an adapter/state/policy change and verified
  conventions; it is not already used for gaze classification and cannot prove
  eye contact. If it cannot be verified today, narrow the demo to reliable face
  tracking and action execution, and record the gaze limitation explicitly.
- [ ] Avoid adding a new vision model or per-trial normalization today. Any
  introduced calibration must preserve raw SDK scores and use fixed parameters.

## 5. Verify live decisions with the base stationary — 45 minutes

For each fresh trial, restart the receiver with a new RUN_ID, then run on Navel:

```bash
python3 -m robot.navel_client.main --server http://PC_IP:6060 \
  --sdk-capture --camera-capture --camera-interval 0.2 \
  --head-focus --head-focus-magnitude 1.0 \
  --single-trial --decision-dry-run --single-trial-policy rules
```

This latches/logs a decision and issues head commands, without route movement or
social-action execution. In this stationary rules dry run the collector may
continue after DECIDED; use Ctrl+C after recording the first result.

| Intended output | Present these conditions from the start |
| --- | --- |
| ENGAGE | One attentive person around 1.2 m |
| APPROACH | One attentive person around 2.2 m |
| CONTINUE | One person with valid low gaze and stable/separating distance |
| YIELD | One detected face already within 3 m, valid low gaze, closing faster than 0.1 m/s |

- [ ] Read the `single_trial phase=DECIDED` line and its action/reason.
- [ ] Also try no person and two people: expect observation hold, not a fabricated
  social decision. A missing gaze reading is not low gaze.
- [ ] Keep each intended cue present for at least two seconds. Clean 10 Hz input
  can resolve around 1.1 s; gaps and UID changes can delay readiness.
- [ ] Start a new trial for a new condition. An accepted CONTINUE latches and
  cannot change to YIELD when somebody starts closing later. Starting a low-gaze
  person outside 3 m can therefore resolve CONTINUE before they enter the cutoff.

## 6. Check the physical route and each action — 60 minutes

First test the baseline route while keeping policy handlers in dry-run mode:

```bash
python3 -m robot.navel_client.main --server http://PC_IP:6060 \
  --sdk-capture --camera-capture --camera-interval 0.2 \
  --single-trial --decision-dry-run --single-trial-policy rules \
  --route-trial --route-distance 1.0 --route-speed 0.05
```

`--route-trial` starts REAL base movement even with `--decision-dry-run`.
It also enables head focus. This mode stops the route when the first decision
resolves and does not execute its physical handler. Observe route direction,
head following, stop behavior and actual clearance.

After that check, use a fresh receiver/trial for each intended action:

```bash
python3 -m robot.navel_client.main --server http://PC_IP:6060 \
  --sdk-capture --camera-capture --camera-interval 0.2 \
  --single-trial --single-trial-execute --single-trial-policy rules \
  --route-trial --route-distance 1.0 --route-speed 0.05
```

- [ ] Confirm every executable trial centres the head before starting the route
  and holds it neutral until the decision.
- [ ] CONTINUE: observe a brief look at the person, return to neutral, and
  completion of the existing short route without changing base motion.
- [ ] ENGAGE: observe route stop, look at the person and greeting, with no
  duplicate greeting.
- [ ] APPROACH: observe route stop, approach and stopping at the configured
  conversation distance while the head remains neutral. After verified
  completion, confirm the robot looks at the person. Verify physical distance
  rather than relying only on the completion message.
- [ ] Once APPROACH is verified, replace “Approach complete!” with a short greeting
  after the successful stop. Remove “Yielding complete!” if it adds no value to
  the person. These are small handler edits, not existing command-line options.
- [ ] YIELD: confirm the initial look at the person, inspect the whole current
  backward arc/wait/return arc/advance maneuver, and confirm the head returns to
  neutral only after completion. The route speed flag does not reduce its movement
  distances/speeds.

The current YIELD combines −0.90 m backward motion with +100° rotation, speaks,
waits three seconds, returns with a combined +0.85 m/−95° arc and advances 0.15 m.
These empirical parameters nominally leave +5° heading difference and do not
guarantee an exact return to the departure pose. It does not detect that the
person has passed. If this is not repeatable in the available space,
prefer implementing/testing a simpler stop, “Please go ahead,” timed wait and
end behavior today. That is a deliberate handler change; no existing flag makes
the current maneuver simpler. Do not spend the day making the return path clever.

## 7. Repeat the full moving encounters — 90 minutes

- [ ] Run five fresh trials per supported condition, with at least two people.
  Use the same short route and configuration. Include natural head tracking,
  since it may change perception compared with the stationary tests.
- [ ] Record trial ID, intended condition, first action, approximate detection-to-
  action delay, actual motion/speech, UID loss, timeout and completion/failure.
- [ ] Classify failures as detection, gaze interpretation, readiness, transport,
  policy selection or physical execution before making another change.
- [ ] Suggested demo gate: at least four of five correct first actions for each
  advertised condition, with repeatable physical execution. Reduce scope if a
  condition fails; do not count scripted/fixed actions as successful live inference.

## 8. Freeze and record the final demonstration — 90 minutes

- [ ] Stop tuning for the last hour. Keep the final PC and robot versions the same.
- [ ] Commit measured calibration/handler changes and save the exact configuration.
  The existing v4 freeze will differ if its tracked code/config is changed.
- [ ] If using the existing active temporal-config path, create and verify a new
  freeze with the available installed-model metadata (no new LLM test is implied):

```bash
.venv/bin/python -m evaluation.freeze \
  --output config/live-study-freeze-robot-day1.json \
  --model-manifest docs/results/pipeline-refinement/after/manifest.json
.venv/bin/python -m evaluation.freeze \
  --verify config/live-study-freeze-robot-day1.json
```

- [ ] Record final, unedited examples of each supported live behavior plus a
  no-person trial. Retain raw observations, full SDK captures, camera images,
  terminal decision/execution logs and the external video.
- [ ] Record limitations plainly: one-person decision scope, first-action latch,
  head-facing proxy if used, timed yielding, and any unsupported condition.

If there is time pressure, prioritize stable face/head following, reliable
ENGAGE and CONTINUE, then APPROACH, then YIELD. A smaller repeatable live demo is
a stronger outcome than four unreliable outputs. Structured LLM/VLM comparison
can follow after the sensor and physical behavior chain works.

Relevant sources: [current rule policy](social-policy.md),
[SDK capture audit](navel-sdk-capture-audit.md),
[head-focus behavior](robot-head-focus.md), and the
[official Navel SDK](https://doc.navelrobotics.com/api/communication.html), which
documents UID-based `look_at_person` and perception frames at roughly 10 Hz.
