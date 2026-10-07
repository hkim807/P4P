"""One SingleTrial behaviour on the existing SDK connection; no behaviours yet."""

import asyncio
from dataclasses import dataclass, field
import inspect
import logging
import math

from robot.navel_client.decision_dispatch import DECISIONS

logger = logging.getLogger(__name__)


class BehaviourNotImplemented(RuntimeError):
    pass


async def continue_route(context):
    raise BehaviourNotImplemented("CONTINUE")


async def approach_person(context):
    raise BehaviourNotImplemented("APPROACH")


async def engage_person(context):
    raise BehaviourNotImplemented("ENGAGE")


async def yield_space(context):
    raise BehaviourNotImplemented("YIELD")


HANDLERS = {"CONTINUE": continue_route, "APPROACH": approach_person,
            "ENGAGE": engage_person, "YIELD": yield_space}
_UNIMPLEMENTED = frozenset(HANDLERS.values())


@dataclass
class BehaviourContext:
    robot: object
    decision: object
    source: object
    current_observation: object
    route: object = None
    head: object = None
    _tasks: set = field(default_factory=set, init=False, repr=False)
    _closing: bool = field(default=False, init=False, repr=False)

    def own_task(self, awaitable):
        """Register SDK movement/head tasks so cancellation settles before zero."""
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
        self._dispatched = self._cleaned = False
        self._handler_task = None
        self._cleanup_lock = asyncio.Lock()

    def preflight(self):
        if all(handler in _UNIMPLEMENTED for handler in self.handlers.values()):
            raise BehaviourNotImplemented("BEHAVIOUR_NOT_IMPLEMENTED: no execution handlers available")

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
        except asyncio.CancelledError:
            self.trial.fail("BEHAVIOUR_CANCELLED")
            if asyncio.current_task().cancelling():
                raise
        except Exception:
            self.trial.fail("BEHAVIOUR_FAILED")
            logger.exception("single_trial behaviour_failed action=%s", action)
        finally:
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
                    await asyncio.wait(context._tasks, timeout=2.0)
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
