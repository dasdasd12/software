"""Device transport, protocol, and manager scaffolding."""

from .device_transport import (
    DeviceCapabilities,
    DeviceFrame,
    DeviceStatus,
    DeviceTransport,
    DeviceTransportError,
    SimulatedTransport,
)
from .config_sync import (
    ConfigSyncResult,
    ConfigSyncSimulator,
    DeviceConfigSyncService,
    build_profile_sync_frames,
)
from .command_adapter import VirtualDeviceCommandAdapter, VirtualDeviceCommandResult
from .manager import DeviceManager, DeviceRecord
from .projection import project_device_snapshot_frames, project_slot_snapshot_frames
from .projection_runtime import DeviceProjectionRuntime
from .protocol_codec import DeviceProtocolCodec
from .session import VirtualDeviceSession, VirtualDeviceSessionResult
from .slot_mapper import DeviceSlotMapper, SlotSnapshot
from .virtual_input import decode_input_event, error_frame_for_exception

__all__ = [
    "DeviceCapabilities",
    "ConfigSyncResult",
    "ConfigSyncSimulator",
    "DeviceConfigSyncService",
    "DeviceFrame",
    "DeviceManager",
    "DeviceProtocolCodec",
    "DeviceRecord",
    "DeviceSlotMapper",
    "DeviceStatus",
    "DeviceTransport",
    "DeviceTransportError",
    "DeviceProjectionRuntime",
    "SimulatedTransport",
    "SlotSnapshot",
    "VirtualDeviceCommandAdapter",
    "VirtualDeviceCommandResult",
    "VirtualDeviceSession",
    "VirtualDeviceSessionResult",
    "build_profile_sync_frames",
    "decode_input_event",
    "error_frame_for_exception",
    "project_device_snapshot_frames",
    "project_slot_snapshot_frames",
]
