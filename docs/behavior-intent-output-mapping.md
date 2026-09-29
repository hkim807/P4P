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
  -> approach handler with shared Navel runtime
  -> supplied bounded approach action (motion only with --execute)
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
is intentional: only APPROACH is implemented. Unknown registrations fail at
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
│   └── approach_human.py
└── handlers/
    ├── __init__.py
    ├── base.py
    └── approach_human.py
```

| Action | Command | Current handler |
| --- | --- | --- |
| `CONTINUE` | `ContinueCommand` | `UNSUPPORTED_ACTION` |
| `MONITOR` | `MonitorCommand` | `UNSUPPORTED_ACTION` |
| `ORIENT` | `OrientCommand` | `UNSUPPORTED_ACTION` |
| `SLOW` | `SlowCommand` | `UNSUPPORTED_ACTION` |
| `YIELD` | `YieldCommand` | `UNSUPPORTED_ACTION` |
| `AVOID` | `AvoidCommand` | `UNSUPPORTED_ACTION` |
| `APPROACH` | `ApproachCommand` | `ApproachHandler` |
| `GREET` | `GreetCommand` | `UNSUPPORTED_ACTION` |
| `GUIDE` | `GuideCommand` | `UNSUPPORTED_ACTION` |
| `WAIT` | `WaitCommand` | `UNSUPPORTED_ACTION` |
| `RESUME` | `ResumeCommand` | `UNSUPPORTED_ACTION` |
| `DISENGAGE` | `DisengageCommand` | `UNSUPPORTED_ACTION` |

`main.py` constructs one runtime/controller inside its single `navel.Robot()`
context. Its sole perception and locomotion collectors feed the shared runtime
and observation adapter. The handler adapts command parameters; the reusable
action preserves the supplied approach curves, limited corrections and explicit
arrival outcomes. The runtime owns shared sensor state, target association and
exclusive movement cleanup. No SDK import or blocking sleep is added to the
behavior package.

The controller validates local observation provenance, intent expiry, target
availability and supported parameters before submitting an owned execution task.
The HTTP response loop does not await movement. Rejections do not overwrite the
active execution. Duplicate decisions, same-target requests and busy-target
requests are not queued. Unsupported actions leave an active approach unchanged.

Execution snapshots expose ACCEPTED, RUNNING, DRY_RUN_COMPLETED, COMPLETED,
CANCELLED or FAILED, plus decision, requested/resolved target, parameters, error
and approach outcome. Existing observation task/controller fields reflect the
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
