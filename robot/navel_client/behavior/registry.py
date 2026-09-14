"""Construction of all behavior handlers with one shared Navel robot instance."""

from __future__ import annotations

import logging
from typing import Any

from robot.navel_client.behavior.commands import (
    ApproachCommand,
    AvoidCommand,
    ContinueCommand,
    DisengageCommand,
    GreetCommand,
    GuideCommand,
    MonitorCommand,
    OrientCommand,
    ResumeCommand,
    RobotBehaviorCommand,
    SlowCommand,
    WaitCommand,
    YieldCommand,
)
from robot.navel_client.behavior.handlers import (
    ApproachHandler,
    AvoidHandler,
    BehaviorHandler,
    ContinueHandler,
    DisengageHandler,
    GreetHandler,
    GuideHandler,
    MonitorHandler,
    OrientHandler,
    ResumeHandler,
    SlowHandler,
    WaitHandler,
    YieldHandler,
)


HandlerRegistry = dict[type[RobotBehaviorCommand], BehaviorHandler[Any]]


def build_handler_registry(
    robot: Any,
    logger: logging.Logger | None = None,
) -> HandlerRegistry:
    """Build every handler with the exact same raw SDK robot object."""
    return {
        ContinueCommand: ContinueHandler(robot, logger),
        MonitorCommand: MonitorHandler(robot, logger),
        OrientCommand: OrientHandler(robot, logger),
        SlowCommand: SlowHandler(robot, logger),
        YieldCommand: YieldHandler(robot, logger),
        AvoidCommand: AvoidHandler(robot, logger),
        ApproachCommand: ApproachHandler(robot, logger),
        GreetCommand: GreetHandler(robot, logger),
        GuideCommand: GuideHandler(robot, logger),
        WaitCommand: WaitHandler(robot, logger),
        ResumeCommand: ResumeHandler(robot, logger),
        DisengageCommand: DisengageHandler(robot, logger),
    }
