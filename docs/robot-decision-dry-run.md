# Test policy decisions on Navel without executing actions

The robot client reads `target_lock.effective_decision` when the server enables
social processing, while retaining `policy_decision` as the pure rule result.
It routes the effective decision to named placeholder handlers. Enable this with
`--decision-dry-run`. The handlers in
[`decision_dispatch.py`](../robot/navel_client/decision_dispatch.py) only log;
they do not call Navel motion, speech, route, or configuration APIs.

## Run the dry-run

Use the same code revision on the computer and Navel. Start the computer receiver
with a **new** raw path and social trace path:

```bash
.venv/bin/python -m app.server --host 0.0.0.0 --port 6060 \
  --output var/recordings/decision-pilot-01.jsonl \
  --social-output var/temporal-validation/decision-pilot-01.social.jsonl \
  --lock-output var/temporal-validation/decision-pilot-01.lock.jsonl \
  --temporal-config config/temporal-state.json
```

If you use the reverse SSH tunnel, bind the computer receiver to `127.0.0.1`
and follow [the tunnel instructions](runbook.md#connection-through-a-reverse-ssh-tunnel).
On Navel, run the sensor client in its SDK-enabled Python environment:

```bash
python3 -m robot.navel_client.main \
  --server http://COMPUTER_LAN_IP:6060 \
  --decision-dry-run
```

For the tunnel, replace the server URL with `http://127.0.0.1:16060`.
`--print-only` cannot be combined with `--decision-dry-run`, since print-only
does not receive HTTP responses.

Add `--single-trial` to latch one final result from `--single-trial-policy rules`
(the default). `--decision-wait-timeout 30` sets the provisional local monotonic
deadline; expiry fails with `NO_DECISION_TIMEOUT` and exits with status 1.
Acceptance requires exactly one currently observed person in the local frame
and matching SocialState, fresh within `--max-decision-age`. Rules need `latest_distance_valid`, `gaze_valid` and a usable gaze category.
LLM retains its existing gaze-or-distance-trend evidence check; these
reuse the estimator's evidence minima, without requiring its full rolling window
or stationary robot motion. VLM needs the current single-person observation
and a fresh matched head image.
Null decisions keep observing; DEFER is rejected as an action. A result freezes at `DECIDED`, disables further
acceptance, and keeps perception running until interruption or an explicit
execution/completion/failure transition. No placeholder action is called in this
mode; accepting a pure proposal does not authorise a command. Ctrl-C fails the
trial with `INTERRUPTED` and uses existing cleanup.
LLM/VLM results use the [selected live result delivery](live-model-inference.md#selected-singletrial-delivery) path. Existing server audit processing can
continue; without `--route-trial` this mode never starts a route.

`--route-trial` explicitly enables **real base movement**, even though selected
policy behaviours remain in decision dry-run mode. On Navel, with the receiver
running with social processing, use a short SDK distance request:

```bash
python3 -m robot.navel_client.main --server http://192.168.1.100:6060 \
  --decision-dry-run --single-trial --route-trial \
  --route-distance 0.5 --route-speed 0.1 --route-acceleration 0.2
```

For the default 10 m request, explicitly allow more than its nominal 100 seconds
at 0.1 m/s (the decision timeout otherwise stays at 30 seconds):

```bash
python3 -m robot.navel_client.main --server http://192.168.1.100:6060 \
  --decision-dry-run --single-trial --route-trial \
  --route-distance 10 --route-speed 0.1 --route-acceleration 0.2 \
  --decision-wait-timeout 120
```

One shared SDK connection and perception reader serve HTTP and local head
tracking (head contribution 1.0, commands at most every 0.6 seconds). Detection
does not alter the single base movement request; tracking retains a visible UID
and uses the existing bounded loss grace without requiring a policy lock.
No perception frames for 5 seconds ends the route. Distance is an SDK request,
not verified travel; this adds no obstacle avoidance.

In decision dry-run, an accepted decision stops the route and exits at `DECIDED` without
executing the selected behaviour. Route completion first fails with
`ROUTE_FINISHED_WITHOUT_DECISION`. Timeout, transport invalidation, interruption
and task failures also stop it. Cleanup cancels and settles the movement sender
before `base_vel(0.0, 0.0)`, independently of HTTP/head completion. Sender settling
has a two-second bound; failure or a rejected zero command is logged as an
unconfirmed stop. No software cancellation or zero request proves physical
stopping. Hardware verification is still required.

The separate `--single-trial --single-trial-execute` option connects acceptance
to [`BehaviourDispatcher`](../robot/navel_client/behaviour_dispatch.py). It
excludes decision/command dry-run, the older script executor, SDK-only, and
print-only modes. CONTINUE awaits the original route task with baseline head
tracking active; it never starts another movement. ENGAGE cancels/settles/zeros
the route and settles baseline head commands, then says exactly once:
"Hello! Do you need any guidance in the lab?" It owns and awaits `robot.say()`'s
asyncio task. The [SDK documentation](https://doc.navelrobotics.com/getting_started.html#creating-your-own-scripts)
defines awaiting this task as waiting for speech to finish. APPROACH uses the
ported demo's `HEAD_STRAIGHT` g_nose geometry and timestamp-matched odometry
from the existing shared readers. It targets 0.7 m horizontal base-centre-to-nose
distance, with ±0.10 m distance and ±4° heading tolerances. Arc speed is
`min(0.25, radians(70) * length / max(abs(theta), 1e-9))`, acceleration 1.0 m/s²;
heading speed is `min(70, sqrt(abs(angle_degrees) * 60))`, acceleration 60°/s².
The reference acquisition, filtering, geometric UID association, arc monitoring,
one distance correction and up to two final heading corrections are retained.
After measured stopping, only fresh `APPROACHED_VERIFIED` arrival permits the
owned/awaited utterance "Approach complete!". `APPROACHED_UNVERIFIED` and
`OUTSIDE_TOLERANCE` fail without speaking; measurements are retained in
`SingleTrial.approach_result` and local approach logs, separately from policy.
There is no ENGAGE greeting or route resumption after APPROACH. YIELD temporarily
moves aside/backwards, waits, returns towards the original route, advances a short
distance and ends. It rotates +100° (30°/s, 35°/s²), reverses −0.60 m
(0.25 m/s, 0.35 m/s²), stops and says "Please go ahead.". After speech completion
it waits 3 seconds, moves +0.60 m (0.12 m/s, 0.15 m/s²), stops, rotates −100°
(30°/s, 35°/s²), advances only +0.15 m (0.25 m/s, 0.35 m/s²), stops and awaits
"Yielding complete!". Rotation margins are 0.50 s, escape/return margins 0.30 s,
and the short advance margin 0.04 s, following actual SDK task completion and
local measured stopping. The wait is timed, not sensor-confirmed clearance;
return is nominal, not verified navigation to an exact path. YIELD uses local
odometry without UID/nose acquisition and never restarts the cancelled baseline.
Interruption/failure stops locally without forcing the remaining return stages.
Preflight requires `--route-trial`
and the SDK movement, arc, rotation, stopping and speech methods before motion starts.
Dry-run remains at DECIDED and never calls this dispatcher.

Handlers are asynchronous functions receiving the frozen decision/source,
shared robot, `current_observation()` getter, existing `route`/`head` controls,
and `own_task(awaitable)` for SDK tasks. Handlers must register and await their
SDK tasks; they must not launch detached senders. CONTINUE retains
`route.task` and baseline tracking. APPROACH, ENGAGE, and YIELD suspend/settle
baseline head commands and cancel/settle/zero the route before handler startup.
APPROACH/ENGAGE recheck one fresh local person then, without tying it to the
source UID. The perception reader continues throughout execution.

Execution is claimed once. EXECUTING begins inside the selected handler task;
COMPLETED requires handler success and local task/head/base cleanup. Failure,
cancellation, or `--behaviour-timeout` (default 120 seconds, maximum 3600)
produces FAILED, with no restart or resumption. The decision-wait deadline applies
only while observing. Cleanup settles registered SDK senders before zero velocity
and precedes server/model acknowledgements. An unsettled sender is reported as
an unconfirmed stop; physical stopping has not been hardware-verified.

For execution, start the computer receiver with new recording paths:

```bash
.venv/bin/python -m app.server --host 0.0.0.0 --port 6060 \
  --output var/recordings/execute-01.raw.jsonl \
  --social-output var/temporal-validation/execute-01.social.jsonl
```

On Navel, using its SDK-enabled Python and the computer's LAN IP, explicitly
enable the route (this performs real movement and selected behaviour speech):

```bash
python3 -m robot.navel_client.main --server http://COMPUTER_LAN_IP:6060 \
  --single-trial --single-trial-execute --single-trial-policy rules --route-trial \
  --route-distance 0.5 --route-speed 0.1 --route-acceleration 0.2 \
  --behaviour-timeout 120
```

Normal route completion after CONTINUE succeeds; completion before any accepted
decision still fails with `ROUTE_FINISHED_WITHOUT_DECISION`. These handlers have
only been verified with mocks, not real robot motion or speech.

Accepted raw frames still print to stdout. On stderr, a first valid decision
or a changed decision/target produces a line such as:

```text
decision_dry_run={"event":"CALL","handler":"APPROACH","decision_id":"...","source_state_id":"...","target_uid":17,"target_track_epoch":1,"lock_id":"...","reason_code":"SUSTAINED_GAZE_IN_APPROACHABLE_RANGE"}
```

When the decision or target changes, or a response is lost or rejected, the
active placeholder gets a `CANCEL` log before the new placeholder is called.
Repeated frames proposing the same decision and target produce no extra call.
The `DEFER` placeholder logs observation; it does not pause or move the robot.

You can inspect the PC's social trace after the run, or feed it to the decision
replay command:

```bash
.venv/bin/python -m app.decide \
  var/temporal-validation/decision-pilot-01.social.jsonl \
  --output var/temporal-validation/decision-pilot-01.decisions.jsonl
```

The decision trace matches the **pure** decisions returned during the live run.
The lock trace records the effective decisions that reached the robot's
placeholder after local response checks. Stop the client with Ctrl-C;
the active placeholder is cancelled in the log.

## What is checked locally

The dispatcher accepts a decision only when the POST was fully processed and
the response timestamp equals the frame the robot sent. The SocialState and
decision must agree on state ID and session; the policy version and decision ID
must match this client. The original frame must be at most one second old when
the response arrives, measured with the robot's own monotonic clock. An
`APPROACH` or `ENGAGE` target must be currently `OBSERVED` in that SocialState,
with the same UID and track epoch. Older, conflicting, or new-session responses
are rejected. Restart the robot client after restarting the PC receiver so it
can bind to the new session.
When a target lock is present, the client also validates its source state,
selected UID/epoch, lock ID, and effective decision. A missing or unresolved
target produces `DEFER`, even when the pure rule says `CONTINUE` or proposes an
action for a different UID.

An independent local task expires the active placeholder after two seconds
without a valid decision response. `--max-decision-age` and
`--decision-timeout` adjust these development defaults. A transport error or
rejected response also clears the placeholder. These checks protect this
**dry-run state**; they do not physically stop a robot.

## Current limits and next changes

- No real Navel behavior function is called. The five methods in
  `DryRunHandlers` are explicit replacement points, but should only be connected
  to actions after the command and execution layer is built.
- The PC sends **policy decisions, not commands**. There is no command ID,
  execution lease, local capability check, or `STARTED`/`COMPLETED`/`REJECTED`
  feedback route to the PC.
- The logical lock has a missing hold and release cooldown, but no completion
  feedback. The dispatcher suppresses identical decision/target/lock calls
  while active; a later change can still cause another `ENGAGE` call. Calibrate
  gaze and identity continuity before using that decision for speech or motion.
- The watchdog cancels a logging placeholder only. Before physical execution,
  add a robot-local controller that can verify and perform a physical stop,
  arbitrate route motion, recheck current target and obstacle data, and expire
  executable command leases even while HTTP is stalled.
- Rules select `YIELD` for TOO_CLOSE using the existing provisional proximity
  condition; this does not establish a verified route conflict.
- A changed PC session is rejected until the client restarts. A later command
  protocol should negotiate a new session explicitly.

Offline tests cover parsing, response matching, duplicate suppression,
target/decision changes, a missing decision, transport loss, watchdog expiry,
and a Flask receiver-to-robot-placeholder round trip. They do not establish
behavior on Navel hardware; run the procedure above to confirm live SDK and
network timing.
