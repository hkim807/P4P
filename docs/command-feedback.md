# Correlated command and simulated feedback

The app server can return `robot_command` alongside the pure policy decision and
target lock. It issues `APPROACH` or `ENGAGE` only when the effective decision is
`LOCKED` to one currently observed, nonzero UID and track epoch. The command has
the source state and frame, logical lock ID, target UID and epoch, expiry in robot
monotonic microseconds, and an execution lease. Defaults are in
`config/commands.json`: source age 1 second, lease 2 seconds, and 256 commands per
session.

A command ID stays stable across observation retries. `APPROACH` is issued once
per lock and bound track; `ENGAGE` is issued once per logical lock. An expired or
simulated command is not reissued. No command is issued while the target is
missing, tentative, ambiguous, or cooling down. This contract does not turn a
policy result into a physical movement or greeting.

The robot's `--command-dry-run` option checks the response against the exact
observation, its current effective decision, lock, target, robot clock, source
frame, expiry, and lease. It records one fake action per command ID and POSTs
`RECEIVED` then `SIMULATED` to `/api/v1/execution-events`. It never calls a Navel
action method. Duplicate commands replay identical events; duplicate events
are acknowledged without another trace line. `SIMULATED` is diagnostic feedback,
not physical completion, and does not release the target lock.

## Run with the robot when ready

On the computer, choose new output filenames for each run:

```bash
python3 -m app.server --host 0.0.0.0 --port 6060 \
  --output var/recordings/pilot-command-01.jsonl \
  --social-output var/recordings/pilot-command-01.social.jsonl \
  --lock-output var/recordings/pilot-command-01.lock.jsonl \
  --command-output var/recordings/pilot-command-01.command.jsonl \
  --execution-output var/recordings/pilot-command-01.execution.jsonl
```

On Navel:

```bash
python3 -m robot.navel_client.main --server http://COMPUTER_LAN_IP:6060 \
  --command-dry-run
```

Add `--head-focus` only for the separate head tracking trial; that flag makes
real head calls. `--command-dry-run` and `--decision-dry-run` are mutually
exclusive. If the observation response is lost, a fresh observation can carry
the same command while its lock and expiry remain valid. The client can then
process its first copy as a retry. Feedback is correlated by command, session,
lock, source state, event ID, and sequence.

For offline inspection, run:

```bash
python3 -m app.command var/recordings/pilot-command-01.jsonl \
  --output var/recordings/pilot-command-01.replay-command.jsonl
```

The replay emits one row per source frame, including `null` when no command is
eligible. It has no execution feedback, so it shows proposal and retry behavior
only. The live command and execution traces show which commands reached the
fake executor. Restart both server and robot client together for a new session;
the in-memory command ledger is not persisted across restarts.

The opt-in [physical executor](physical-executor.md) now adds route and stop
hooks, action scripts, cancellation, a response watchdog, and real outcome
events. Its script contract and hardware validation steps are described there.
