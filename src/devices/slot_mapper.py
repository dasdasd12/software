"""Per-device compact slot mapping for firmware-facing projections."""

from dataclasses import dataclass, field
from typing import Dict, Optional

from .device_transport import DeviceTransportError


@dataclass
class SlotSnapshot:
    device_id: str
    generation: int
    agent_slots: Dict[int, str] = field(default_factory=dict)
    session_slots: Dict[int, str] = field(default_factory=dict)
    run_slots: Dict[int, str] = field(default_factory=dict)
    permission_slots: Dict[int, str] = field(default_factory=dict)
    notification_slots: Dict[int, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "device_id": self.device_id,
            "generation": self.generation,
            "agent_slots": {str(k): v for k, v in self.agent_slots.items()},
            "session_slots": {str(k): v for k, v in self.session_slots.items()},
            "run_slots": {str(k): v for k, v in self.run_slots.items()},
            "permission_slots": {str(k): v for k, v in self.permission_slots.items()},
            "notification_slots": {str(k): v for k, v in self.notification_slots.items()},
        }


class DeviceSlotMapper:
    """Assigns small integer slots for long core IDs."""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self._generation = 0
        self._agent_slots: Dict[int, str] = {}
        self._session_slots: Dict[int, str] = {}
        self._run_slots: Dict[int, str] = {}
        self._permission_slots: Dict[int, str] = {}
        self._notification_slots: Dict[int, str] = {}

    @property
    def generation(self) -> int:
        return self._generation

    def snapshot(self) -> SlotSnapshot:
        return SlotSnapshot(
            device_id=self.device_id,
            generation=self._generation,
            agent_slots=dict(self._agent_slots),
            session_slots=dict(self._session_slots),
            run_slots=dict(self._run_slots),
            permission_slots=dict(self._permission_slots),
            notification_slots=dict(self._notification_slots),
        )

    def assign_agent(self, instance_id: str) -> int:
        return self._assign(self._agent_slots, instance_id)

    def assign_session(self, session_id: str) -> int:
        return self._assign(self._session_slots, session_id)

    def assign_run(self, run_id: str) -> int:
        return self._assign(self._run_slots, run_id)

    def assign_permission(self, permission_id: str) -> int:
        return self._assign(self._permission_slots, permission_id)

    def assign_notification(self, notification_id: str) -> int:
        return self._assign(self._notification_slots, notification_id)

    def resolve_session(self, slot_id: int, generation: int) -> Optional[str]:
        self._require_generation(generation)
        return self._session_slots.get(slot_id)

    def _assign(self, slots: Dict[int, str], value: str) -> int:
        for slot_id, existing in slots.items():
            if existing == value:
                return slot_id
        slot_id = max(slots.keys(), default=0) + 1
        slots[slot_id] = value
        self._generation += 1
        return slot_id

    def _require_generation(self, generation: int) -> None:
        if generation != self._generation:
            raise DeviceTransportError(
                code="UNKNOWN_SLOT_GENERATION",
                message="slot generation mismatch",
                transport_kind="device_slot_mapper",
                device_id=self.device_id,
                recoverable=True,
                details={
                    "expected_generation": self._generation,
                    "received_generation": generation,
                },
            )
