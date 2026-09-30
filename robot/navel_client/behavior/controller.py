"""Nonblocking admission and ownership of one observable async execution."""
import asyncio
import logging
from collections import OrderedDict
from robot.navel_client.behavior.commands import ApproachCommand
from robot.navel_client.behavior.dispatcher import BehaviorDispatcher
from robot.navel_client.behavior.execution_state import BehaviorExecutionState, BehaviorExecutionStatus as Lifecycle
from robot.navel_client.behavior.handlers.approach_human import validate_parameters
from robot.navel_client.behavior.intent import NavelAction, NavelBehaviorIntent, NavelIntentParseError
from robot.navel_client.behavior.mapper import BehaviorIntentMapper
from robot.navel_client.behavior.registry import build_handler_registry
from robot.navel_client.behavior.results import BehaviorHandlingResult, BehaviorHandlingStatus
from robot.navel_client.navel_runtime import Runtime


class BehaviorController:
    def __init__(self, robot=None, mapper=None, dispatcher=None, execution_state=None,
                 logger=None, *, runtime=None, dedup_limit=256):
        if dedup_limit < 1:
            raise ValueError('Dedup history must be positive')
        self.runtime = runtime if runtime is not None else Runtime(robot)
        self._mapper = mapper or BehaviorIntentMapper()
        self._dispatcher = dispatcher or BehaviorDispatcher(build_handler_registry(self.runtime))
        self._execution_state = execution_state or BehaviorExecutionState()
        self._logger = logger or logging.getLogger('robot.navel_client.behavior')
        self._seen = OrderedDict()
        self._dedup_limit = dedup_limit
        self.active_task = None
        self.last_admission = None

    @property
    def execution_state(self):
        return self._execution_state.snapshot

    def _admit(self, status, intent=None, error=None):
        result = BehaviorHandlingResult(status, error=error,
            decision_id=intent.decision_id if intent else None,
            requested_target=intent.target_human_id if intent else None)
        self.last_admission = result
        self._logger.info('BEHAVIOR_ADMISSION %s', result)
        return result

    def handle_response(self, payload):
        if not isinstance(payload, dict):
            return self._admit(BehaviorHandlingStatus.INVALID_INTENT, error='Expected response object')
        if payload.get('behavior_intent') is None:
            return self._admit(BehaviorHandlingStatus.NO_INTENT,
                error='server response has no behavior_intent field' if 'behavior_intent' not in payload else None)
        try:
            intent = NavelBehaviorIntent.from_payload(payload['behavior_intent'])
        except NavelIntentParseError as exc:
            return self._admit(BehaviorHandlingStatus.INVALID_INTENT, error=str(exc))
        return self.handle(intent)

    def handle(self, intent):
        S = BehaviorHandlingStatus
        if intent.decision_id in self._seen:
            return self._admit(S.DUPLICATE, intent)
        self._seen[intent.decision_id] = None
        if len(self._seen) > self._dedup_limit:
            self._seen.popitem(last=False)
        try:
            command = self._mapper.map(intent)
        except Exception as exc:
            return self._admit(S.INVALID_INTENT, intent, str(exc))
        if not self._dispatcher.supports(command):
            return self._admit(S.UNSUPPORTED_ACTION, intent, 'Unimplemented action; active execution is unchanged')
        if self.runtime.stop_failure or self.runtime.shutdown.is_set():
            return self._admit(S.FAILED, intent, self.runtime.stop_failure or 'Shutting down')
        if self.active_task is not None and not self.active_task.done():
            if self.active_task.cancelling() or self.runtime.stop.is_set():
                return self._admit(S.BUSY, intent, 'Cancellation cleanup is still running')
            same_action = intent.action == self.execution_state.action
            same = same_action and intent.target_human_id == self.execution_state.target_human_id
            if same_action and isinstance(command, ApproachCommand) and not same and self.runtime.target is not None:
                try:
                    context, _ = self.runtime.intent_context(intent)
                    target = self.runtime.resolve(context, intent.target_human_id)
                    same = target['uid'] == self.runtime.target['uid']
                except (ValueError, RuntimeError, TimeoutError):
                    pass
            return self._admit(S.ALREADY_RUNNING if same else S.BUSY, intent)
        try:
            if isinstance(command, ApproachCommand):
                validate_parameters(command)
            context, deadline = self.runtime.intent_context(intent)
        except TimeoutError as exc:
            return self._admit(S.EXPIRED, intent, str(exc))
        except (ValueError, RuntimeError) as exc:
            return self._admit(S.INVALID_INTENT, intent, str(exc))
        try:
            seed = self.runtime.resolve(context, intent.target_human_id) if isinstance(command, ApproachCommand) else None
        except (ValueError, RuntimeError) as exc:
            return self._admit(S.TARGET_UNAVAILABLE, intent, str(exc))
        self._execution_state.accept(intent)
        self.active_task = asyncio.create_task(self._execute(command, seed, deadline, intent.decision_id))
        return self._admit(S.ACCEPTED, intent)

    async def _execute(self, command, seed, deadline, decision_id):
        self._execution_state.update(Lifecycle.RUNNING, resolved_target=str(seed['uid']) if seed else None)
        try:
            result = await self._dispatcher.dispatch(command, seed=seed,
                                                     deadline=deadline, decision_id=decision_id)
        except asyncio.CancelledError:
            self._execution_state.update(Lifecycle.CANCELLED)
        except Exception as exc:
            self._execution_state.update(Lifecycle.FAILED, latest_error=str(exc),
                resolved_target=str(self.runtime.target['uid']) if self.runtime.target else str(seed['uid']) if seed else None)
            self._logger.exception('BEHAVIOR_FAILED decision=%s', decision_id)
        else:
            outcome = result.yield_result
            status = Lifecycle.DRY_RUN_COMPLETED if result.dry_run else Lifecycle.COMPLETED
            if outcome and outcome.status == 'CANCELLED':
                status = Lifecycle.CANCELLED
            elif outcome and outcome.status in {'FAILED', 'SPEECH_FAILED'}:
                status = Lifecycle.FAILED
            self._execution_state.update(status, result=result, resolved_target=result.resolved_target,
                                         latest_error=outcome.error if outcome else None)
        self._logger.info('BEHAVIOR_EXECUTION %s', self.execution_state)
        return self.execution_state

    async def cancel_active(self):
        """Explicit cancellation; await sender cancellation and stop confirmation."""
        task = self.active_task
        if task is not None and not task.done():
            self.runtime.stop.set()
            if not task.cancelling():
                task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Cancellation before the task's first instruction needs no motion cleanup.
                if task.cancelled():
                    self._execution_state.update(Lifecycle.CANCELLED)
                else:
                    raise
        return self.execution_state

    async def shutdown(self):
        await self.cancel_active()
        self.runtime.shutdown.set()

    def robot_context(self):
        state = self.execution_state
        if self.runtime.stop_failure or (state and state.status == Lifecycle.FAILED):
            return {'task': 'ERROR', 'controller_status': 'FAULT'}
        if state and state.status in (Lifecycle.ACCEPTED, Lifecycle.RUNNING):
            return {'task': ('YIELDING' if state.action == NavelAction.YIELD else 'APPROACHING') if self.runtime.cfg.execute else 'IDLE',
                    'controller_status': 'ACTIVE' if self.runtime.cfg.execute else 'STOPPED'}
        if state and state.status == Lifecycle.COMPLETED:
            return {'task': 'COMPLETE', 'controller_status': 'STOPPED'}
        return {'task': 'IDLE', 'controller_status': 'STOPPED'}
