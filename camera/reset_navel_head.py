#!/usr/bin/env python3
"""Point the Navel robot's head straight ahead.

Run this on the robot after stopping any process that is still issuing
``look_at_person`` or head-overlay commands; otherwise that process may
immediately move the head again.
"""

from __future__ import annotations

import time

import navel


def clear_head_overlay(robot) -> str:
    """Clear offsets from the robot's current direction of focus."""
    if hasattr(robot, "head_overlay_degrees"):
        robot.head_overlay_degrees(0.0, 0.0, 0.0)
        return "head_overlay_degrees"

    if hasattr(robot, "head_overlay") and hasattr(navel, "Bryan"):
        robot.head_overlay(navel.Bryan(0.0, 0.0, 0.0))
        return "head_overlay"

    raise RuntimeError("Installed Navel SDK has no supported head-overlay method")


def point_head_forward(robot) -> str:
    """Replace person focus with a target two metres straight ahead."""
    if not hasattr(robot, "look_at_cart"):
        raise RuntimeError("Installed Navel SDK has no look_at_cart method")
    if not hasattr(navel, "CartSys3d") or not hasattr(navel, "CoordSystem"):
        raise RuntimeError("Installed Navel SDK lacks Cartesian target types")

    target = navel.CartSys3d(navel.CoordSystem.HEAD_STRAIGHT, 2.0, 0.0, 0.0)
    robot.look_at_cart(target, 1.0)
    return "look_at_cart(HEAD_STRAIGHT, x=2m, y=0, z=0, head=1.0)"


def main() -> None:
    with navel.Robot() as robot:
        overlay_method = clear_head_overlay(robot)
        focus_method = point_head_forward(robot)
        # Keep the connection alive while the head moves to the target.
        time.sleep(2.0)
    print(f"Forward head command sent using {focus_method}; overlay cleared using {overlay_method}.")


if __name__ == "__main__":
    main()
