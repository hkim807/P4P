"""One SingleTrial behaviour on the existing SDK connection."""

import asyncio
from dataclasses import asdict, dataclass, field
import inspect
import logging
import math

from robot.navel_client.decision_dispatch import DECISIONS
from robot.navel_client.approach import ApproachNotVerified, ApproachRuntime, approach_human

logger = logging.getLogger(__name__)


class BehaviourNotImplemented(RuntimeError):
    pass


async def continue_route(context):
    if context.route is None or context.route.task is None or context.route.stopped:
        raise RuntimeError("CONTINUE requires the retained baseline route task")
    await context.route.task


async def approach_person(context):
    runtime = context.approach
    if runtime is None:
        raise RuntimeError("APPROACH requires local sensor state")
    await runtime.wait_ready()
    await runtime.settle()
    observation = context.current_observation()
    people = observation.get("people", []) if observation else []
    uid = people[0]["uid"] if len(people) == 1 else None
    result = context.approach_result = await approach_human(runtime, uid)
    if result.status != "APPROACHED_VERIFIED":
        raise ApproachNotVerified(result.status)
    await context.own_task(context.robot.say("Approach complete!"))


async def engage_person(context):
    # Navel returns an asyncio Task; awaiting say waits for speech to finish.
    await context.own_task(context.robot.say("Hello! Do you need any guidance in the lab?"))


async def yield_space(context):
    raise BehaviourNotImplemented("YIELD")


HANDLERS = {"CONTINUE": continue_route, "APPROACH": approach_person,
            "ENGAGE": engage_person, "YIELD": yield_space}
_UNIMPLEMENTED = frozenset({yield_space})


@dataclass
class BehaviourContext:
    robot: object
    decision: object
    source: object
    current_observation: object
    route: object = None
    head: object = None
    approach: object = None
    approach_result: object = None
    _tasks: set = field(default_factory=set, init=False, repr=False)
    _closing: bool = field(default=False, init=False, repr=False)

    def own_task(self, awaitable):
        """Register SDK tasks so cancellation settles before local cleanup."""
        task = asyncio.ensure_future(awaitable)
        self._tasks.add(task)
        if self._closing:
            task.cancel()
            raise RuntimeError("behaviour is closing")
        return task


class BehaviourDispatcher:
    def __init__(self, trial, robot, *, route=None, head=None, timeout_s=120.0, handlers=None):
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 3600:
            raise ValueError("behaviour timeout must be positive and at most 3600 seconds")
        self.handlers = {**HANDLERS, **(handlers or {})}
        if set(self.handlers) != DECISIONS or any(
                not inspect.iscoroutinefunction(handler) for handler in self.handlers.values()):
            raise ValueError("behaviour handlers must map the four actions to async functions")
        self.trial, self.timeout_s = trial, timeout_s
        self.context = BehaviourContext(robot, None, None, lambda: trial.current_observation, route, head)
        if self.handlers["APPROACH"] is approach_person:
            self.context.approach = ApproachRuntime(self.context)
        self._dispatched = self._cleaned = False
        self._handler_task = None
        self._cleanup_lock = asyncio.Lock()

    def preflight(self):
        if all(handler in _UNIMPLEMENTED for handler in self.handlers.values()):
            raise BehaviourNotImplemented("BEHAVIOUR_NOT_IMPLEMENTED: no execution handlers available")
        if not callable(getattr(self.context.robot, "base_vel", None)):
            raise ValueError("execution requires SDK robot.base_vel for local stopping")
        if self.handlers["CONTINUE"] is continue_route:
            if self.context.route is None:
                raise ValueError("CONTINUE execution requires --route-trial")
            if not callable(getattr(self.context.robot, "move_base", None)):
                raise ValueError("CONTINUE execution requires SDK robot.move_base")
        if self.handlers["ENGAGE"] is engage_person and not callable(getattr(self.context.robot, "say", None)):
            raise ValueError("ENGAGE execution requires SDK robot.say")
        if self.handlers["APPROACH"] is approach_person:
            for method in ("move_and_rotate_base", "rotate_base", "say"):
                if not callable(getattr(self.context.robot, method, None)):
                    raise ValueError(f"APPROACH execution requires SDK robot.{method}")

    async def dispatch(self):
        if self._dispatched or self.trial.phase != "DECIDED":
            return False
        # Claim before the first await; concurrent callbacks cannot dispatch twice.
        self._dispatched = True
        context = self.context
        context.decision, context.source = self.trial.decision, self.trial.source
        action = context.decision["action"]
        handler = self.handlers[action]
        succeeded = False
        try:
            if handler in _UNIMPLEMENTED:
                raise BehaviourNotImplemented(action)
            if action != "CONTINUE":
                if context.head is not None:
                    context.head.suspend()
                if context.route is not None:
                    await context.route.stop()
                if context.head is not None:
                    await context.head.suspend_and_settle()
            if action in {"APPROACH", "ENGAGE"}:
                observation = context.current_observation()
                if (observation is None or len(observation.get("people", [])) != 1
                        or not 0 <= self.trial.monotonic_us() - observation["timestamp"] <= self.trial.max_age_us):
                    self.trial.fail("CURRENT_PERSON_UNAVAILABLE")
                    return False
            async def run_handler():
                if not self.trial.start_execution():
                    return False
                logger.info("single_trial phase=EXECUTING action=%s", action)
                await handler(context)
                return True

            task = context.own_task(run_handler())
            self._handler_task = task
            done, _ = await asyncio.wait([task], timeout=self.timeout_s)
            if not done:
                self.trial.fail("BEHAVIOUR_TIMEOUT")
            else:
                succeeded = task.result()
        except BehaviourNotImplemented:
            self.trial.fail("BEHAVIOUR_NOT_IMPLEMENTED")
        except ApproachNotVerified as exc:
            self.trial.fail(str(exc))
        except asyncio.CancelledError:
            self.trial.fail("BEHAVIOUR_CANCELLED")
            if asyncio.current_task().cancelling():
                raise
        except Exception:
            self.trial.fail("BEHAVIOUR_FAILED")
            logger.exception("single_trial behaviour_failed action=%s", action)
        finally:
            if context.approach_result is not None:
                self.trial.approach_result = asdict(context.approach_result)
            await self.close()
        if succeeded:
            self.trial.complete_execution()
        return self.trial.phase == "COMPLETED"

    async def close(self):
        """Settle every owned sender before local zero; never wait for HTTP."""
        async with self._cleanup_lock:
            if self._cleaned:
                return
            context = self.context
            context._closing = True
            if context.head is not None:
                context.head.suspend()
            # Cancel the handler first: its awaited SDK task may be cancelled
            # by propagation. A second cancel could interrupt sender settling.
            if self._handler_task is not None and not self._handler_task.done():
                if not self._handler_task.cancelling():
                    self._handler_task.cancel()
            for task in context._tasks:
                if not task.done() and not getattr(task, "cancelling", lambda: 0)():
                    task.cancel()
            if context.route is not None and context.route.task is not None:
                task = context.route.task
                if not task.done() and not getattr(task, "cancelling", lambda: 0)():
                    task.cancel()
            try:
                if context._tasks:
                    # APPROACH's cancellation includes up to 2 s sender settling
                    # plus the reference's 3 s measured stop confirmation.
                    cleanup_timeout = 6.0 if context.approach is not None and context.approach.motion_active else 2.0
                    await asyncio.wait(context._tasks, timeout=cleanup_timeout)
                    if any(not task.done() for task in context._tasks):
                        raise RuntimeError("behaviour sender did not settle; stop unconfirmed")
                    await asyncio.gather(*context._tasks, return_exceptions=True)
                if context.route is not None and not context.route.stopped:
                    await context.route.stop()
                else:
                    context.robot.base_vel(0.0, 0.0)
                if context.head is not None:
                    await context.head.suspend_and_settle()
            except Exception:
                self.trial.fail("LOCAL_CLEANUP_FAILED")
                logger.exception("single_trial cleanup_failed physical_stop_verified=false")
                raise
            self._cleaned = True
            logger.info("single_trial cleanup_complete physical_stop_verified=false")
