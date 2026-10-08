"""Strict server-side validation for RawObservationFrame."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


NonNegativeFloat = Annotated[float, Field(ge=0)]
GazeOverlap = Annotated[float, Field(ge=0, le=1)]


class RawModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class RelativeHeadPosition(RawModel):
    """Cartesian position in the head camera frame, in metres."""

    coordinate_frame: Literal["CAM_HEAD"]
    x: float
    y: float
    z: float


class RawPerson(RawModel):
    uid: Annotated[int, Field(ge=0)]
    distance_m: NonNegativeFloat | None
    gaze_overlap: GazeOverlap | None
    optional_relative_head_position: RelativeHeadPosition | None = None
    # Optional upstream measurements. The SDK adapter does not infer these.
    path_relation: Literal["UNKNOWN", "CLEAR", "CONFLICT"] = "UNKNOWN"
    pass_gesture: Literal["UNKNOWN", "PASS"] = "UNKNOWN"


class RawRobot(RawModel):
    linear_velocity: float | None = Field(description="Signed forward velocity in m/s.")
    angular_velocity: float | None = Field(description="Yaw velocity in rad/s.")


class RawSafety(RawModel):
    lidar: list[NonNegativeFloat | None] | None = Field(
        description="SDK native range values, ordered front then back; null means unavailable."
    )
    sonar: list[NonNegativeFloat | None] | None = Field(
        description="SDK native range values, ordered front-right, front-left, back."
    )


class RawObservationFrame(RawModel):
    timestamp: Annotated[int, Field(ge=0)] = Field(
        description="Robot-host monotonic microseconds at perception collection; not UTC."
    )
    people: list[RawPerson]
    robot: RawRobot
    safety: RawSafety

    @model_validator(mode="after")
    def unique_people(self) -> "RawObservationFrame":
        uids = [person.uid for person in self.people]
        if len(set(uids)) != len(uids):
            raise ValueError("people must have unique uid values")
        return self
