"""Only implemented behaviours are registered; vocabulary stays in the mapper."""
from robot.navel_client.behavior.commands import ApproachCommand, YieldCommand
from robot.navel_client.behavior.handlers import ApproachHandler, YieldHandler


def build_handler_registry(runtime):
    return {ApproachCommand: ApproachHandler(runtime), YieldCommand: YieldHandler(runtime)}
