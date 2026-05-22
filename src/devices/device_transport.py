"""Transport-independent device frame contracts.

This module deliberately avoids WebSocket and local API JSON semantics. It is
the first software-side boundary for future USB Vendor HID, CDC, BLE GATT, and
dongle transports.
"""

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, Set


DEFAULT_PROTOCOL_VERSION = 1


class DeviceTransportError(RuntimeError):
    """Structured transport error suitable for tests and diagnostics."""

    def __init__(
        self,
        code: str,
        message: str,
        transport_kind: str,
        device_id: str,
        frame_type: Optional[str] = None,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.transport_kind = transport_kind
        self.device_id = device_id
        self.frame_type = frame_type
        self.recoverable = recoverable

    def to_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "transport_kind": self.transport_kind,
            "device_id": self.device_id,
            "frame_type": self.frame_type,
            "recoverable": self.recoverable,
        }


@dataclass(frozen=True)
class DeviceFrame:
    frame_type: str
    payload: bytes = b""
    protocol_version: int = DEFAULT_PROTOCOL_VERSION
    generation: int = 0
    device_id: str = "simulated-device"

    def encode(self) -> bytes:
        envelope = {
            "frame_type": self.frame_type,
            "payload_b64": base64.b64encode(self.payload).decode("ascii"),
            "protocol_version": self.protocol_version,
            "generation": self.generation,
            "device_id": self.device_id,
        }
        return json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    @classmethod
    def decode(cls, raw: bytes) -> "DeviceFrame":
        envelope = json.loads(raw.decode("utf-8"))
        return cls(
            frame_type=envelope["frame_type"],
            payload=base64.b64decode(envelope.get("payload_b64", "")),
            protocol_version=int(envelope.get("protocol_version", DEFAULT_PROTOCOL_VERSION)),
            generation=int(envelope.get("generation", 0)),
            device_id=envelope.get("device_id", "unknown-device"),
        )


@dataclass(frozen=True)
class DeviceCapabilities:
    device_id: str
    transport_kind: str
    protocol_version: int
    max_payload_size: int
    supported_message_types: Set[str]


@dataclass(frozen=True)
class DeviceStatus:
    device_id: str
    transport_kind: str
    is_open: bool
    queued_frames: int


class DeviceTransport(Protocol):
    async def open(self) -> None:
        ...

    async def close(self) -> None:
        ...

    async def send_frame(self, frame: DeviceFrame) -> None:
        ...

    async def read_frame(self) -> DeviceFrame:
        ...

    def get_capabilities(self) -> DeviceCapabilities:
        ...

    def get_status(self) -> DeviceStatus:
        ...


class SimulatedTransport:
    """In-memory transport for device-frame tests and future core projection tests."""

    def __init__(
        self,
        device_id: str = "simulated-device",
        protocol_version: int = DEFAULT_PROTOCOL_VERSION,
        max_payload_size: int = 1024,
        supported_message_types: Optional[Iterable[str]] = None,
    ):
        self._device_id = device_id
        self._protocol_version = protocol_version
        self._max_payload_size = max_payload_size
        self._supported_message_types = set(supported_message_types or {
            "HELLO_REQ",
            "HEARTBEAT",
            "PERMISSION_REQUEST_PUSH",
            "PERMISSION_RESPONSE_CMD",
            "ERROR_RESP",
        })
        self._queue: asyncio.Queue[DeviceFrame] = asyncio.Queue()
        self._is_open = False

    async def open(self) -> None:
        self._is_open = True

    async def close(self) -> None:
        self._is_open = False

    async def send_frame(self, frame: DeviceFrame) -> None:
        self._ensure_open(frame.frame_type)
        if len(frame.payload) > self._max_payload_size:
            raise DeviceTransportError(
                code="PAYLOAD_TOO_LARGE",
                message=f"Payload size {len(frame.payload)} exceeds max {self._max_payload_size}",
                transport_kind="simulated",
                device_id=self._device_id,
                frame_type=frame.frame_type,
                recoverable=True,
            )
        await self._queue.put(frame)

    async def read_frame(self) -> DeviceFrame:
        self._ensure_open()
        return await self._queue.get()

    def get_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            device_id=self._device_id,
            transport_kind="simulated",
            protocol_version=self._protocol_version,
            max_payload_size=self._max_payload_size,
            supported_message_types=set(self._supported_message_types),
        )

    def get_status(self) -> DeviceStatus:
        return DeviceStatus(
            device_id=self._device_id,
            transport_kind="simulated",
            is_open=self._is_open,
            queued_frames=self._queue.qsize(),
        )

    def _ensure_open(self, frame_type: Optional[str] = None) -> None:
        if self._is_open:
            return
        raise DeviceTransportError(
            code="TRANSPORT_CLOSED",
            message="Transport is closed",
            transport_kind="simulated",
            device_id=self._device_id,
            frame_type=frame_type,
            recoverable=True,
        )
