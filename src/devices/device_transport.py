"""Transport-independent device frame contracts.

This module deliberately avoids WebSocket and local API JSON semantics. It is
the first software-side boundary for future USB Vendor HID, CDC, BLE GATT, and
dongle transports.
"""

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Set

try:
    from typing import Protocol
except ImportError:  # pragma: no cover - Python < 3.8 compatibility
    from typing_extensions import Protocol


DEFAULT_PROTOCOL_VERSION = 1


class DeviceTransportError(ValueError):
    """Structured transport error suitable for tests and diagnostics."""

    def __init__(
        self,
        code: str,
        message: str,
        transport_kind: str,
        device_id: str,
        frame_type: Optional[str] = None,
        recoverable: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.transport_kind = transport_kind
        self.device_id = device_id
        self.frame_type = frame_type
        self.recoverable = recoverable
        self.details = dict(details or {})

    def to_dict(self):
        data = {
            "code": self.code,
            "message": self.message,
            "transport_kind": self.transport_kind,
            "device_id": self.device_id,
            "frame_type": self.frame_type,
            "recoverable": self.recoverable,
        }
        data.update(self.details)
        return data


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
    hardware_revision: str = "simulated"
    firmware_version: str = "simulated"
    device_family: str = "simulated"
    supported_profile_features: Set[str] = field(default_factory=set)
    supported_screen_widgets: Set[str] = field(default_factory=set)
    supports_agent_slots: bool = False
    supports_config_sync: bool = False
    supports_firmware_update: bool = False


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
        hardware_revision: str = "simulated",
        firmware_version: str = "simulated",
        device_family: str = "simulated",
        supported_profile_features: Optional[Iterable[str]] = None,
        supported_screen_widgets: Optional[Iterable[str]] = None,
        supports_agent_slots: bool = False,
        supports_config_sync: bool = False,
        supports_firmware_update: bool = False,
    ):
        self._device_id = device_id
        self._protocol_version = protocol_version
        self._max_payload_size = max_payload_size
        self._supported_message_types = set(supported_message_types or {
            "HELLO_REQ",
            "HELLO_RESP",
            "INPUT_EVENT",
            "ACK_RESP",
            "CAPABILITIES_RESP",
            "SLOT_MAP_BEGIN",
            "SLOT_MAP_ITEM",
            "SLOT_MAP_END",
            "DEVICE_SNAPSHOT_BEGIN",
            "DEVICE_SNAPSHOT_END",
            "SCREEN_FOCUS_SET",
            "NOTIFICATION_PUSH",
            "HEARTBEAT",
            "PERMISSION_REQUEST_PUSH",
            "PERMISSION_RESPONSE_CMD",
            "PROFILE_SYNC_BEGIN",
            "PROFILE_SYNC_CHUNK",
            "PROFILE_SYNC_END",
            "PROFILE_SUMMARY_SET",
            "ERROR_RESP",
        })
        self._hardware_revision = hardware_revision
        self._firmware_version = firmware_version
        self._device_family = device_family
        self._supported_profile_features = set(supported_profile_features or set())
        self._supported_screen_widgets = set(supported_screen_widgets or set())
        self._supports_agent_slots = supports_agent_slots
        self._supports_config_sync = supports_config_sync
        self._supports_firmware_update = supports_firmware_update
        self._queue: Optional[asyncio.Queue[DeviceFrame]] = None
        self._is_open = False
        self.active_synced_profile_id: Optional[str] = None
        self.active_synced_profile_version: Optional[int] = None
        self.active_synced_profile_checksum: Optional[str] = None

    async def open(self) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue()
        self._is_open = True

    async def close(self) -> None:
        self._is_open = False

    def clear_queued_frames(self) -> int:
        if self._queue is None:
            return 0
        cleared = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return cleared
            cleared += 1

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
        if self._queue is None:
            self._queue = asyncio.Queue()
        await self._queue.put(frame)

    async def read_frame(self) -> DeviceFrame:
        self._ensure_open()
        if self._queue is None:
            self._queue = asyncio.Queue()
        return await self._queue.get()

    def get_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            device_id=self._device_id,
            transport_kind="simulated",
            protocol_version=self._protocol_version,
            max_payload_size=self._max_payload_size,
            supported_message_types=set(self._supported_message_types),
            hardware_revision=self._hardware_revision,
            firmware_version=self._firmware_version,
            device_family=self._device_family,
            supported_profile_features=set(self._supported_profile_features),
            supported_screen_widgets=set(self._supported_screen_widgets),
            supports_agent_slots=self._supports_agent_slots,
            supports_config_sync=self._supports_config_sync,
            supports_firmware_update=self._supports_firmware_update,
        )

    def get_status(self) -> DeviceStatus:
        return DeviceStatus(
            device_id=self._device_id,
            transport_kind="simulated",
            is_open=self._is_open,
            queued_frames=self._queue.qsize() if self._queue is not None else 0,
        )

    def mark_profile_synced(self, profile_id: str, version: int, checksum: str) -> None:
        self.active_synced_profile_id = profile_id
        self.active_synced_profile_version = version
        self.active_synced_profile_checksum = checksum

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
