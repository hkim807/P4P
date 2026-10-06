"""Loss-visible JSON snapshots of SDK packets, before observation conversion."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import time
from collections.abc import Mapping
from enum import Enum
from typing import Any
from uuid import uuid4

from robot.navel_client.transport import ObservationTransport, TransportError


logger = logging.getLogger(__name__)


def sdk_json(value: Any) -> Any:
    """Serialize the SDK's nested data structs without imposing the v1 policy schema."""
    if isinstance(value, Enum):
        return value.name
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, (list, tuple)):
        return [sdk_json(item) for item in value]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            name = key.name if isinstance(key, Enum) else str(key)
            if name in result:
                raise ValueError(f"duplicate SDK mapping key after conversion: {name}")
            result[name] = sdk_json(item)
        return result
    if dataclasses.is_dataclass(value):
        names = [field.name for field in dataclasses.fields(value)]
    elif hasattr(value, "__dict__"):
        names = [name for name in vars(value) if not name.startswith("_")]
    else:
        names = [name for cls in type(value).__mro__
                 for name in getattr(cls, "__slots__", ()) if not name.startswith("_")]
    if not names:
        raise TypeError(f"unsupported SDK value: {type(value).__name__}")
    return {name: sdk_json(getattr(value, name)) for name in dict.fromkeys(names)}


class SdkCapture:
    """Queue every received SDK packet; fail loudly if transport cannot keep up."""

    def __init__(self, transport: ObservationTransport, *, queue_size: int = 1024) -> None:
        self.transport = transport
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self.session_id = uuid4().hex
        self.sequences = {"perception": 0, "locomotion": 0}

    def record(self, stream: str, packet: Any) -> None:
        if stream not in self.sequences:
            raise ValueError(f"unknown SDK stream: {stream}")
        received_monotonic_us = time.monotonic_ns() // 1000
        received_unix_us = time.time_ns() // 1000
        sequence = self.sequences[stream] + 1
        record = {
            "capture_version": 1,
            "session_id": self.session_id,
            "stream": stream,
            "sequence": sequence,
            "received_monotonic_us": received_monotonic_us,
            "received_unix_us": received_unix_us,
            "packet": sdk_json(packet),
        }
        try:
            self.queue.put_nowait(record)
        except asyncio.QueueFull as error:
            raise RuntimeError("SDK capture queue full; recording would lose packets") from error
        self.sequences[stream] = sequence

    async def send(self) -> None:
        while True:
            record = await self.queue.get()
            try:
                while True:
                    try:
                        response = await asyncio.to_thread(self.transport.send_sdk_packet, record)
                    except TransportError as error:
                        logger.warning("sdk_capture_transport_error=%s; retrying", error)
                        await asyncio.sleep(0.25)
                        continue
                    if (response.status_code == 200 and response.payload.get("accepted") is True
                            and response.payload.get("stream") == record["stream"]
                            and response.payload.get("sequence") == record["sequence"]):
                        break
                    if 500 <= response.status_code < 600:
                        logger.warning("sdk_capture_server_error=%s; retrying", response.status_code)
                        await asyncio.sleep(0.25)
                        continue
                    raise RuntimeError(f"SDK capture rejected: {response.status_code} {response.payload}")
            finally:
                self.queue.task_done()
