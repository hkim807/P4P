"""Navel observation transport and dry-run behavior command architecture."""

from robot.navel_client.adapter import NavelAdapterConfig, NavelObservationAdapter
from robot.navel_client.transport import ObservationTransport, TransportError

__all__ = [
    "NavelAdapterConfig",
    "NavelObservationAdapter",
    "BehaviorController",
    "ObservationTransport",
    "TransportError",
]


def __getattr__(name: str):
    if name == "BehaviorController":
        from robot.navel_client.behavior import BehaviorController

        globals()[name] = BehaviorController
        return BehaviorController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
