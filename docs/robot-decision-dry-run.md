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
and matching SocialState, fresh within `--max-decision-age`. Rules/LLM also need
`latest_distance_valid` and either `gaze_valid` or `distance_trend_valid`; these
reuse the estimator's evidence minima, without requiring its full rolling window
or stationary robot motion. VLM needs only the current single-person observation.
`DEFER`/null keeps observing. A result freezes at `DECIDED`, disables further
acceptance, and keeps perception running until interruption or an explicit
execution/completion/failure transition. No placeholder action is called in this
mode; accepting a pure proposal does not authorise a command. Ctrl-C fails the
trial with `INTERRUPTED` and uses existing cleanup.
LLM/VLM selection has an acceptance entry point but no result delivery yet, so
those CLI selections currently time out. Existing server audit processing can
continue; this mode never starts a route or claims a physical stop.

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
- The current policy never emits `YIELD` because no route-conflict cue exists.
  A placeholder handler is present for future use.
- A changed PC session is rejected until the client restarts. A later command
  protocol should negotiate a new session explicitly.

Offline tests cover parsing, response matching, duplicate suppression,
target/decision changes, a missing decision, transport loss, watchdog expiry,
and a Flask receiver-to-robot-placeholder round trip. They do not establish
behavior on Navel hardware; run the procedure above to confirm live SDK and
network timing.
