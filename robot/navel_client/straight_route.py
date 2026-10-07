"""One SDK straight-drive sender, with explicit ordered shutdown."""

import asyncio
import logging
import math

logger = logging.getLogger(__name__)


class StraightRoute:
    def __init__(self, robot, distance=10.0, speed=0.1, acceleration=0.2):
        for value, maximum in ((distance, None), (speed, 1.6), (acceleration, 1.2)):
            if not math.isfinite(value) or value <= 0 or (maximum and value > maximum):
                raise ValueError("invalid route distance, speed or acceleration")
        self.robot = robot
        self.distance, self.speed, self.acceleration = distance, speed, acceleration
        self.task = None
        self.stopped = False

    def start(self):
        if self.task is not None or self.stopped:
            raise RuntimeError("baseline route cannot restart")
        logger.info("route_trial real_base_motion=true distance_request_m=%s speed_m_s=%s acceleration_m_s2=%s",
                    self.distance, self.speed, self.acceleration)
        # SDK 0.15.3 returns a task; ensure_future also accepts a coroutine mock.
        self.task = asyncio.ensure_future(self.robot.move_base(
            self.distance, speed=self.speed, acceleration=self.acceleration))
        return self.task

    async def stop(self):
        if self.stopped:
            return
        self.stopped = True
        if self.task is not None:
            self.task.cancel()
            done, _ = await asyncio.wait([self.task], timeout=2.0)
            if not done:
                # Sending zero while this sender survives can be overwritten.
                raise RuntimeError("movement sender did not settle; physical stop unconfirmed")
            await asyncio.gather(self.task, return_exceptions=True)
        self.robot.base_vel(0.0, 0.0)
        logger.info("route_trial sender_settled=true zero_velocity_sent=true physical_stop_verified=false")
