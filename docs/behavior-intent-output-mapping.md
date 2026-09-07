# Navel BehaviorIntent output mapping

The Navel client consumes the server's validated policy result through a
dry-run-only behavior boundary:

```text
POST /api/v1/observations response
  -> BehaviorIntent schema parsing
  -> BehaviorController
  -> BehaviorIntentMapper
  -> action-specific immutable RobotBehaviorCommand
  -> BehaviorDispatcher
  -> action-specific handler with injected Navel robot instance
  -> structured dry-run result and terminal log
  -> future Navel SDK call (not implemented)
```

The server's `LLMPolicyBridge` validates policy-output semantics before the
intent enters the response. The client parses that response using the shared
canonical `BehaviorIntent`; it does not define a second transport contract.
The mapper narrows generic intent preferences into only the fields relevant to
a concrete Navel command. Social distance remains a desired stand-off distance
and is never interpreted as travel distance.

The dispatcher uses exact command types, so handlers can be replaced one at a
time when robot integration is implemented. Construction fails if its registry
does not cover the complete command set. The mapper has a corresponding
Action/command coverage check.

The implementation layout is:

```text
robot/navel_client/behavior/
├── __init__.py
├── commands.py
├── controller.py
├── dispatcher.py
├── execution_state.py
├── mapper.py
├── registry.py
├── results.py
└── handlers/
    ├── __init__.py
    ├── base.py
    ├── continue_route.py
    ├── monitor.py
    ├── orient.py
    ├── slow.py
    ├── yield_behavior.py
    ├── avoid.py
    ├── approach.py
    ├── greet.py
    ├── guide.py
    ├── wait.py
    ├── resume.py
    └── disengage.py
```

| Action | Command | Current handler |
| --- | --- | --- |
| `CONTINUE` | `ContinueCommand` | `ContinueHandler` |
| `MONITOR` | `MonitorCommand` | `MonitorHandler` |
| `ORIENT` | `OrientCommand` | `OrientHandler` |
| `SLOW` | `SlowCommand` | `SlowHandler` |
| `YIELD` | `YieldCommand` | `YieldHandler` |
| `AVOID` | `AvoidCommand` | `AvoidHandler` |
| `APPROACH` | `ApproachCommand` | `ApproachHandler` |
| `GREET` | `GreetCommand` | `GreetHandler` |
| `GUIDE` | `GuideCommand` | `GuideHandler` |
| `WAIT` | `WaitCommand` | `WaitHandler` |
| `RESUME` | `ResumeCommand` | `ResumeHandler` |
| `DISENGAGE` | `DisengageCommand` | `DisengageHandler` |

Every current handler only logs supplied parameters and returns a typed result
with `dry_run=True`. The behavior package does not import the Navel SDK, sleep,
move, rotate, speak, or change navigation state. Missing and schema-invalid
intents are rejected before mapping; mapping, dispatch, and handler failures are
logged without selecting a fallback action.

`main.py` constructs the controller inside the existing `navel.Robot()` context.
The registry passes that same raw robot instance to all 12 handlers. Handlers
store it for later direct SDK calls, without a wrapper, facade, or additional
service layer. They do not access it during the current dry run.

The controller also exposes an immutable `execution_state` snapshot containing
the latest action, lifecycle status, decision ID, target human ID, update time,
and error. Status progresses through `ACCEPTED`, `DISPATCHED`, and
`DRY_RUN_COMPLETED`, or ends at `FAILED`. A later observation-layer change can
read this snapshot for `RobotContext` feedback; observation and server schemas
are deliberately unchanged here.

Real execution remains future work. Each handler will eventually directly use
the injected Navel SDK robot instance. Target freshness, controller-state and
runtime safety validation, collision and free-space checking, and motion
feasibility must be added before any physical handler is enabled.
