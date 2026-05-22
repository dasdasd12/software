"""Architecture-level command, event, and snapshot envelopes.

These models mirror docs/architecture/event_command_model.md. They are
transport-neutral and intentionally avoid WebSocket or firmware-specific
semantics.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class CommandSource:
    kind: str
    client_id: Optional[str] = None
    device_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"kind": self.kind}
        if self.client_id:
            payload["client_id"] = self.client_id
        if self.device_id:
            payload["device_id"] = self.device_id
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommandSource":
        kind = data.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ValueError("command source kind is required")
        return cls(
            kind=kind,
            client_id=data.get("client_id"),
            device_id=data.get("device_id"),
        )


@dataclass(frozen=True)
class CommandEnvelope:
    type: str
    source: CommandSource
    payload: Dict[str, Any] = field(default_factory=dict)
    target: Optional[Dict[str, Any]] = None
    command_id: str = field(default_factory=lambda: _new_id("cmd"))
    timestamp: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "command_id": self.command_id,
            "type": self.type,
            "source": self.source.to_dict(),
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }
        if self.target is not None:
            data["target"] = dict(self.target)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommandEnvelope":
        command_id = data.get("command_id")
        command_type = data.get("type")
        payload = data.get("payload", {})
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("command_id is required")
        if not isinstance(command_type, str) or not command_type:
            raise ValueError("command type is required")
        if not isinstance(payload, dict):
            raise ValueError("command payload must be an object")
        return cls(
            command_id=command_id,
            type=command_type,
            target=data.get("target"),
            source=CommandSource.from_dict(data.get("source", {})),
            payload=dict(payload),
            timestamp=int(data.get("timestamp", int(time.time()))),
        )


@dataclass(frozen=True)
class EventEnvelope:
    type: str
    seq: int
    payload: Dict[str, Any] = field(default_factory=dict)
    target: Optional[Dict[str, Any]] = None
    event_id: str = field(default_factory=lambda: _new_id("evt"))
    timestamp: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "event_id": self.event_id,
            "seq": self.seq,
            "type": self.type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }
        if self.target is not None:
            data["target"] = dict(self.target)
        return data


@dataclass(frozen=True)
class Snapshot:
    last_event_seq: int
    agents: Dict[str, Any] = field(default_factory=dict)
    sessions: Dict[str, Any] = field(default_factory=dict)
    runs: Dict[str, Any] = field(default_factory=dict)
    devices: Dict[str, Any] = field(default_factory=dict)
    profiles: Dict[str, Any] = field(default_factory=dict)
    notifications: list = field(default_factory=list)
    permissions: list = field(default_factory=list)
    snapshot_id: str = field(default_factory=lambda: _new_id("snap"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "last_event_seq": self.last_event_seq,
            "agents": dict(self.agents),
            "sessions": dict(self.sessions),
            "runs": dict(self.runs),
            "devices": dict(self.devices),
            "profiles": dict(self.profiles),
            "notifications": list(self.notifications),
            "permissions": list(self.permissions),
        }
