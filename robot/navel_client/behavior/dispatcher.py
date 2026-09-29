"""Async type-based dispatch; partial implementation coverage is intentional."""
from robot.navel_client.behavior.commands import COMMAND_TYPES


class BehaviorDispatchError(RuntimeError):
    pass


class BehaviorDispatcher:
    def __init__(self, handlers):
        self._handlers = dict(handlers)
        extra = set(handlers) - COMMAND_TYPES
        if extra:
            raise BehaviorDispatchError(f'Unknown command registrations: {extra}')

    def supports(self, command):
        return type(command) in self._handlers

    async def dispatch(self, command, **context):
        handler = self._handlers.get(type(command))
        if handler is None:
            raise BehaviorDispatchError(f'UNSUPPORTED_ACTION: {type(command).__name__}')
        return await handler.execute(command, **context)
