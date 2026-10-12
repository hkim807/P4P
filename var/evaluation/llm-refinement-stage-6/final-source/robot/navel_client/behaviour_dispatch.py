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
    uid = context.local_target_uid
    if uid is None:
        uid = people[0]["uid"] if len(people) == 1 else None
    result = context.approach_result = await approach_human(runtime, uid)
    if result.status != "APPROACHED_VERIFIED":
        raise ApproachNotVerified(result.status)
    await runtime.settle()
    # Use the approach's existing target, which may have acquired a new SDK UID.
    uid = runtime.target["uid"]
    if context.trial_head is not None:
        await context.trial_head.look_at_person(uid)
    else:
        command = context.robot.look_at_person(uid, 1.0)
        if inspect.isawaitable(command):
            await context.own_task(command)
    await context.own_task(context.robot.say("Hi! Do you need any help?"))


async def engage_person(context):
    # Navel returns an asyncio Task; awaiting say waits for speech to finish.
    await context.own_task(context.robot.say("Hello! Do you need any guidance in the lab?"))


async def yield_space(context):
    runtime = context.approach
    if runtime is None:
        raise RuntimeError("YIELD requires local odometry state")
    stage = "BASE_STOP"

    async def movement(name, command):
        nonlocal stage
        stage = name
        logger.info("yield_stage=%s", stage)
        await runtime.motion(command, timeout=context.timeout_s,
                             check_people=name in {"RETURN", "ADVANCE"})

    try:
        await runtime.wait_ready(require_perception=False)
        await runtime.settle()
        await movement("ESCAPE", lambda: context.robot.move_and_rotate_base(
            -0.90, 100.0, speed=0.25, acceleration=0.35))
        stage = "PASS_SPEECH"
        logger.info("yield_stage=%s", stage)
        await context.own_task(context.robot.say("Please go ahead."))
        stage = "WAIT"
        logger.info("yield_stage=%s timed_wait_s=3", stage)
        await asyncio.sleep(3.0)
        await movement("RETURN", lambda: context.robot.move_and_rotate_base(
            0.85, -92.5, speed=0.12, acceleration=0.15))
        await movement("ADVANCE", lambda: context.robot.move_base(
            0.15, speed=0.25, acceleration=0.35))
        stage = "COMPLETE_SPEECH"
        logger.info("yield_stage=%s", stage)
        await context.own_task(context.robot.say("Yield complete."))
    except (Exception, asyncio.CancelledError) as exc:
        logger.warning("yield_failed stage=%s error=%s", stage, str(exc) or type(exc).__name__)
        raise


HANDLERS = {"CONTINUE": continue_route, "APPROACH": approach_person,
            "ENGAGE": engage_person, "YIELD": yield_space}


@dataclass
class BehaviourContext:
    robot: object
    decision: object
    source: object
    current_observation: object
    route: object = None
    head: object = None
    trial_head: object = None
    decision_person_uid: int | None = None
    approach: object = None
    approach_result: object = None
    timeout_s: float = 120.0
    local_target_uid: int | None = None
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
    def __init__(self, trial, robot, *, route=None, head=None, trial_head=None,
                 timeout_s=120.0, handlers=None):
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 3600:
            raise ValueError("behaviour timeout must be positive and at most 3600 seconds")
        self.handlers = {**HANDLERS, **(handlers or {})}
        if set(self.handlers) != DECISIONS or any(
                not inspect.iscoroutinefunction(handler) for handler in self.handlers.values()):
            raise ValueError("behaviour handlers must map the four actions to async functions")
        self.trial, self.timeout_s = trial, timeout_s
        self.context = BehaviourContext(robot, None, None, lambda: trial.current_observation,
                                        route, head, trial_head, timeout_s=timeout_s)
        if self.handlers["APPROACH"] is approach_person or self.handlers["YIELD"] is yield_space:
            self.context.approach = ApproachRuntime(self.context)
        self._dispatched = self._cleaned = False
        self._handler_task = None
        self._cleanup_lock = asyncio.Lock()

    def preflight(self, action=None):
        if not callable(getattr(self.context.robot, "base_vel", None)):
            raise ValueError("execution requires SDK robot.base_vel for local stopping")
        if action in (None, "CONTINUE") and self.handlers["CONTINUE"] is continue_route:
            if self.context.route is None:
                raise ValueError("CONTINUE execution requires --route-trial")
            if not callable(getattr(self.context.robot, "move_base", None)):
                raise ValueError("CONTINUE execution requires SDK robot.move_base")
        if action in (None, "ENGAGE") and self.handlers["ENGAGE"] is engage_person and not callable(getattr(self.context.robot, "say", None)):
            raise ValueError("ENGAGE execution requires SDK robot.say")
        if action in (None, "APPROACH") and self.handlers["APPROACH"] is approach_person:
            for method in ("move_and_rotate_base", "rotate_base", "look_at_person", "say"):
                if not callable(getattr(self.context.robot, method, None)):
                    raise ValueError(f"APPROACH execution requires SDK robot.{method}")
        if action in (None, "YIELD") and self.handlers["YIELD"] is yield_space:
            for method in ("move_and_rotate_base", "move_base", "say"):
                if not callable(getattr(self.context.robot, method, None)):
                    raise ValueError(f"YIELD execution requires SDK robot.{method}")
        if self.context.trial_head is not None:
            self.context.trial_head.preflight()

    @staticmethod
    def _person_uid(context: BehaviourContext, *, current_first: bool = False) -> int:
        current_uid = None
        observation = context.current_observation()
        people = observation.get("people", []) if observation else []
        if len(people) == 1:
            candidate = people[0].get("uid")
            current_uid = candidate if type(candidate) is int and candidate >= 0 else None
        candidates = ((current_uid, context.decision_person_uid) if current_first
                      else (context.decision_person_uid, current_uid))
        uid = next((candidate for candidate in candidates if candidate is not None), None)
        if uid is None:
            raise RuntimeError("action head command requires one visible decision person")
        return uid

    async def dispatch(self):
        if self._dispatched or self.trial.phase != "DECIDED":
            return False
        # Claim before the first await; concurrent callbacks cannot dispatch twice.
        self._dispatched = True
        context = self.context
        context.decision, context.source = self.trial.decision, self.trial.source
        context.decision_person_uid = self.trial.decision_person_uid
        return await self._execute(context.decision["action"])

    async def dispatch_local(self, action, *, target_uid=None):
        """Execute an explicit local action without a server decision or route."""
        if action not in {"APPROACH", "YIELD", "ENGAGE"}:
            raise ValueError("local action must be APPROACH, YIELD or ENGAGE")
        if action == "APPROACH" and (type(target_uid) is not int or target_uid < 0):
            raise ValueError("local APPROACH requires a valid acquired target UID")
        if self.context.route is not None:
            raise ValueError("local action excludes a baseline route")
        if self._dispatched or self.trial.phase != "OBSERVING":
            return False
        self._dispatched = True
        self.context.local_target_uid = target_uid
        return await self._execute(action, local=True)

    async def _execute(self, action, *, local=False):
        context = self.context
        handler = self.handlers[action]
        succeeded = False
        try:
            if action != "CONTINUE":
                if context.head is not None:
                    context.head.suspend()
                if context.route is not None:
                    await context.route.stop()
                if context.head is not None:
                    await context.head.suspend_and_settle()
            if not local and action in {"APPROACH", "ENGAGE"}:
                observation = context.current_observation()
                if (observation is None or len(observation.get("people", [])) != 1
                        or not 0 <= self.trial.monotonic_us() - observation["timestamp"] <= self.trial.max_age_us):
                    self.trial.fail("CURRENT_PERSON_UNAVAILABLE")
                    return False
            async def run_handler():
                started = (self.trial.start_local_execution() if local
                           else self.trial.start_execution())
                if not started:
                    return False
                logger.info("%s phase=EXECUTING action=%s", "debug_action" if local else "single_trial", action)
                if local and action == "ENGAGE":
                    await context.approach.wait_ready(require_perception=False)
                    await context.approach.settle()
                trial_head = context.trial_head
                local_uid = None
                if local:
                    observation = context.current_observation()
                    if (observation is not None and
                            0 <= self.trial.monotonic_us() - observation["timestamp"] <= self.trial.max_age_us):
                        try:
                            local_uid = self._person_uid(context, current_first=True)
                        except RuntimeError:
                            pass  # Optional focus must not gate target-free actions.
                if trial_head is not None:
                    if action == "CONTINUE":
                        await trial_head.glance(self._person_uid(context, current_first=True))
                    elif action == "YIELD":
                        if not local or local_uid is not None:
                            await trial_head.look_at_person(local_uid if local else self._person_uid(context, current_first=True))
                    elif action == "APPROACH":
                        await trial_head.neutral()
                    else:  # ENGAGE
                        if not local or local_uid is not None:
                            await trial_head.look_at_person(local_uid if local else self._person_uid(context, current_first=True))
                try:
                    await handler(context)
                    if trial_head is not None and action == "APPROACH" and handler is not approach_person:
                        await trial_head.look_at_person(self._person_uid(context, current_first=True))
                finally:
                    if trial_head is not None and action == "YIELD":
                        await trial_head.neutral()
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
                self._handler_task.cancel()
            elif self._handler_task is None or self._handler_task.done():
                # An active handler owns and awaits its child senders; cancelling
                # those again can interrupt their measured-stop cleanup.
                for task in context._tasks:
                    if task is not self._handler_task and not task.done():
                        task.cancel()
            if context.route is not None and context.route.task is not None:
                task = context.route.task
                if not task.done() and not getattr(task, "cancelling", lambda: 0)():
                    task.cancel()
            try:
                if context._tasks:
                    # Local motion cancellation includes up to 2 s sender settling
                    # plus the reference's 3 s measured stop confirmation.
                    cleanup_timeout = 6.0 if context.approach is not None and context.approach.motion_active else 2.0
                    if context.trial_head is not None:
                        cleanup_timeout += getattr(context.trial_head, "neutral_settle_s", 2.0) + 0.5
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
