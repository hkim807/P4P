# Navel BehaviorIntent output mapping

The Navel client consumes the server's validated policy result through an
asynchronous behavior boundary (dry-run by default):

```text
POST /api/v1/observations response
  -> standard-library NavelBehaviorIntent parsing
  -> BehaviorController
  -> BehaviorIntentMapper
  -> action-specific immutable RobotBehaviorCommand
  -> BehaviorDispatcher
  -> ApproachHandler or YieldHandler with shared Navel runtime
  -> reusable bounded action (actuation only with --execute)
  -> explicit execution result and terminal log
```

The server's `LLMPolicyBridge` and canonical Pydantic `BehaviorIntent` validate
policy-output semantics before the intent enters the response. The client maps
the unchanged JSON wire values into a lightweight immutable
`NavelBehaviorIntent`. Its parser uses only the Python standard library and
performs defensive type, structure, finite-number, supported-action, and
mapper-required-value checks. The mapper narrows generic intent preferences
into only the fields relevant to a concrete Navel command. Social distance
remains a desired stand-off distance and is never interpreted as travel
distance.

The dispatcher uses exact command types and awaits handlers. Partial registration
is intentional: APPROACH and YIELD are implemented. Unknown registrations fail at
construction; recognized unimplemented actions return UNSUPPORTED_ACTION at
admission. The mapper retains complete Action/command coverage.

The implementation layout is:

```text
robot/navel_client/behavior/
├── __init__.py
├── commands.py
├── controller.py
├── dispatcher.py
├── execution_state.py
├── intent.py
├── mapper.py
├── registry.py
├── results.py
├── actions/
│   ├── __init__.py
│   ├── approach_human.py
│   └── yield_to_person.py
└── handlers/
    ├── __init__.py
    ├── base.py
    ├── approach_human.py
    └── yield_to_person.py
```

| Action | Command | Current handler |
| --- | --- | --- |
| `CONTINUE` | `ContinueCommand` | `UNSUPPORTED_ACTION` |
| `APPROACH` | `ApproachCommand` | `ApproachHandler` |
| `ENGAGE` | `EngageCommand` | `UNSUPPORTED_ACTION` |
| `YIELD` | `YieldCommand` | `YieldHandler` |

`main.py` constructs one runtime/controller inside its single `navel.Robot()`
context. Its sole perception and locomotion collectors feed the shared runtime
and observation adapter. The handler adapts command parameters; the reusable
action preserves the supplied approach curves, limited corrections and explicit
arrival outcomes. The runtime owns shared sensor state, target association and
exclusive movement cleanup. No SDK import or blocking sleep is added to the
behavior package.

The controller validates local observation provenance, intent expiry, APPROACH target
availability and supported parameters before submitting an owned execution task.
The HTTP response loop does not await movement. Rejections do not overwrite the
active execution. Duplicate decisions, same-action/same-target requests and busy
requests are not queued. Unsupported actions leave an active execution unchanged. Different actions
always return BUSY while one is running, including the same target ID.

Execution snapshots expose ACCEPTED, RUNNING, DRY_RUN_COMPLETED, COMPLETED,
CANCELLED or FAILED, plus decision, requested/resolved target, parameters, error
and action-specific outcome. YIELD never resolves a target or produces an
APPROACH result; its observation task is YIELDING. Existing observation task/controller fields reflect the
actual lifecycle; detailed arrival outcomes remain local because the observation
schema has no result field. Completion alone does not mean verified arrival.

See [the README](../README.md#navel-observation-to-policy-pipeline) for exact
commands, coordinate and clock assumptions, parameter limits, cancellation and
stop-failure policy. The integration has hardware-free coverage; its physical
validation remains outstanding. Obstacle avoidance is not implemented.

## Runtime dependencies

`requirements.txt` belongs to the application server and retains Flask,
Pydantic, and the model-client dependencies. The Navel client does not import
those packages. `requirements-navel.txt` documents that the client needs no
additional PyPI packages beyond the Navel SDK already installed by the robot
environment, so it runs with the robot's system `python3` without a virtual
environment or internet installation.

## Reusable YIELD and pipeline invocation

The only canonical actions are CONTINUE / APPROACH / ENGAGE / YIELD. Old
behaviour action names are rejected, with no aliases. Robot tasks, controller
states, reason codes and human motion classifications remain separate concepts.
Recorded observation/ground-truth JSONL files are unchanged; historical decisions
must never be relabelled as new actions.

```python
from robot.navel_client.behavior.actions.yield_to_person import yield_to_person
result = await yield_to_person(runtime)
```

The runtime must already have its single shared perception and locomotion
collectors running. The reusable action leases it exclusively and checks fresh
sensors and a stopped base. In the pipeline, a server `BehaviorIntent` with
`action="YIELD"`, optional `target_human_id`, and `preferences={}` maps to
`YieldCommand`, then `YieldHandler` calls this function with the admission
deadline. Observation IDs and source timestamps must match a locally retained
observation. No face, nose acquisition, median sampling or conflict decision
occurs inside YIELD.

The exact execution sequence is:

1. Acquire exclusive ownership and confirm execution may start.
2. Say **"conflict person detected"** and await completion (default timeout 5 s).
3. Recheck admission expiry and current sensor freshness before movement.
4. `rotate_base(angle=100.0, speed=30.0, acceleration=35.0)` (degrees, seconds).
5. Await SDK completion, cancel/await its sender, then confirm stopped using
   new odometry for at least 0.25 s after zero-velocity commands.
6. `move_base(distance=-0.60, speed=0.25, acceleration=0.35)` (metres, seconds),
   relative to the heading **after** rotation; await completion and confirm stop.
7. Say **"yield complete"**, await completion, and return an explicit result.

Remain stationary at the escape position with the resulting heading. There is
no initial route, head centring, second reader, face tracking, five-second return
wait, forward return, turn back, route resumption or terminal prompt. Local
`--invert-yield-turn-direction` changes the turn to -100 degrees. Pipeline
preferences cannot choose a side, speed, distance or hold time.

Speech coroutines, Tasks and Futures are owned, bounded and observed. Initial
speech failure prevents movement. Movement failure/cancellation suppresses the
completion announcement. Final speech failure records FAILED/SPEECH_FAILED but
retains `movement_completed=True` and `completed_phase=ESCAPE_STOPPED`.
Cancellation cancels and awaits speech and motion senders before stop cleanup;
readers remain alive. A stop-confirmation failure latches the existing lockout.
Results retain the last completed phase, requested/applied parameters, speech
errors and cancellation/failure. SDK completion plus fresh stopped odometry is
the reported verification; it does **not** prove measured travel or angle.

Motion timeouts include nominal travel, acceleration allowance and a four-second
margin; they are bounds, not duration sleeps used to assume completion. Fresh
odometry and perception and the existing 0.50 m local person-stop guard remain
in force. Negative velocity is handled by the shared absolute stop thresholds.
No additional displacement-based completion claim is made.

Dry-run emits no speech, movement or zero commands. Example commands (not run
as part of mock verification):

```bash
python3 -m robot.navel_client.main --server http://192.168.1.100:6060
python3 -m robot.navel_client.main --server http://192.168.1.100:6060 --execute
```

`--speech-timeout` defaults to 5 seconds. Intent validity is 250–15,000 ms from
the source observation, including HTTP/LLM latency and initial speech, with a
configurable local `--max-admission-age-ms` cap (default 15,000). Never renew on
receipt. Once the first movement starts within validity, the bounded manoeuvre
may finish after expiry. Busy requests are not queued or preempted. Deduplication
is bounded to the latest 256 decisions; expiry independently rejects old requests.

Mocks cover contracts, exact SDK calls, cancellation and pipeline concurrency.
Integrated hardware testing, turn-sign/calibration, actual SDK completion and
speech semantics, clearance, and real LLM latency remain unvalidated.
