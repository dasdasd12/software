"""Device transport, protocol, and manager scaffolding."""

from .device_transport import (
    DeviceCapabilities,
    DeviceFrame,
    DeviceStatus,
    DeviceTransport,
    DeviceTransportError,
    SimulatedTransport,
)
from .manager import DeviceManager, DeviceRecord
from .protocol_codec import DeviceProtocolCodec
from .slot_mapper import DeviceSlotMapper, SlotSnapshot

__all__ = [
    "DeviceCapabilities",
    "DeviceFrame",
    "DeviceManager",
    "DeviceProtocolCodec",
    "DeviceRecord",
    "DeviceSlotMapper",
    "DeviceStatus",
    "DeviceTransport",
    "DeviceTransportError",
    "SimulatedTransport",
    "SlotSnapshot",
]
