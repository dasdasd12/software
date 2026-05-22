"""Device manager scaffold for transport-independent device sessions."""

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .device_transport import DeviceCapabilities, DeviceStatus, DeviceTransport


@dataclass
class DeviceRecord:
    device_id: str
    capabilities: DeviceCapabilities
    status: DeviceStatus
    connected_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, object]:
        return {
            "device_id": self.device_id,
            "transport_kind": self.capabilities.transport_kind,
            "protocol_version": self.capabilities.protocol_version,
            "max_payload_size": self.capabilities.max_payload_size,
            "supported_message_types": sorted(self.capabilities.supported_message_types),
            "is_open": self.status.is_open,
            "queued_frames": self.status.queued_frames,
            "connected_at": self.connected_at,
            "updated_at": self.updated_at,
        }


class DeviceManager:
    """Tracks connected device transports and their capability snapshots."""

    def __init__(self) -> None:
        self._transports: Dict[str, DeviceTransport] = {}
        self._records: Dict[str, DeviceRecord] = {}

    def register_transport(self, transport: DeviceTransport) -> DeviceRecord:
        capabilities = transport.get_capabilities()
        status = transport.get_status()
        record = DeviceRecord(
            device_id=capabilities.device_id,
            capabilities=capabilities,
            status=status,
        )
        self._transports[capabilities.device_id] = transport
        self._records[capabilities.device_id] = record
        return record

    def refresh_status(self, device_id: str) -> Optional[DeviceRecord]:
        transport = self._transports.get(device_id)
        record = self._records.get(device_id)
        if not transport or not record:
            return None
        record.status = transport.get_status()
        record.updated_at = int(time.time())
        return record

    def get(self, device_id: str) -> Optional[DeviceRecord]:
        return self._records.get(device_id)

    def list_records(self) -> Dict[str, DeviceRecord]:
        return dict(self._records)
