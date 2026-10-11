#!/usr/bin/env python3
"""Navel SDK 0.15.3: perform one hard-coded straight-line drive.

Run this script on the robot with a clear route:

    python3 straight_drive.py

The distance is an SDK timed-motion request, not odometry-verified travel.
This script does not read perception data, track people, move the head, or
implement obstacle avoidance. Ctrl+C or a movement failure cancels the motion
sender and sends zero base velocity.
"""

import asyncio
import logging

import navel


LOG = logging.getLogger("straight_drive")

# Edit these constants to change the one movement request.
DISTANCE_M = 1.0
SPEED_M_S = 0.1
ACCELERATION_M_S2 = 0.2


async def drive(robot):
    LOG.info(
        "Driving straight: %.2f m at %.2f m/s (acceleration %.2f m/s^2)",
        DISTANCE_M,
        SPEED_M_S,
        ACCELERATION_M_S2,
    )
    movement = asyncio.ensure_future(
        robot.move_base(
            DISTANCE_M,
            speed=SPEED_M_S,
            acceleration=ACCELERATION_M_S2,
        )
    )
    try:
        await movement
        LOG.info("Straight-drive SDK task completed")
    finally:
        # Settle the SDK sender before explicitly commanding zero velocity.
        if not movement.done():
            movement.cancel()
        await asyncio.gather(movement, return_exceptions=True)
        robot.base_vel(0.0, 0.0)
        LOG.info("Zero base velocity sent")


async def main():
    async with navel.Robot() as robot:
        await drive(robot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info("Interrupted")
