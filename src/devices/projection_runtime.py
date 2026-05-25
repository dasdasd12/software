"""Runtime projection of core snapshots and events to virtual device frames."""

from typing import Any, Callable, Dict, List, Optional

from core import EventEnvelope, Snapshot
from keyboard import NotificationQueue, PermissionRequest, ScreenFocus

from .device_transport import DeviceFrame
from .projection import project_device_snapshot_frames
from .protocol_codec import DeviceProtocolCodec
from .slot_mapper import DeviceSlotMapper

ActiveProfileSummaryProvider = Callable[[str], Optional[Any]]


class DeviceProjectionRuntime:
    """Owns per-device projection state used by simulated device sessions."""

    def __init__(
        self,
        device_id: str,
        codec: Optional[DeviceProtocolCodec] = None,
        slot_mapper: Optional[DeviceSlotMapper] = None,
        focus: Optional[ScreenFocus] = None,
        notifications: Optional[NotificationQueue] = None,
        active_profile: Optional[Any] = None,
        active_profile_provider: Optional[ActiveProfileSummaryProvider] = None,
    ) -> None:
        self.device_id = device_id
        self.codec = codec or DeviceProtocolCodec()
        self.slot_mapper = slot_mapper or DeviceSlotMapper(device_id=device_id)
        self.focus = focus or ScreenFocus(device_id=device_id)
        self.notifications = notifications or NotificationQueue()
        self._active_profile = active_profile
        self._active_profile_provider = active_profile_provider

    def snapshot_frames(
        self,
        snapshot: Optional[Snapshot] = None,
        *,
        active_profile: Optional[Any] = None,
    ) -> List[DeviceFrame]:
        return project_device_snapshot_frames(
            codec=self.codec,
            device_id=self.device_id,
            snapshot=snapshot or Snapshot(last_event_seq=0),
            mapper=self.slot_mapper,
            focus=self.focus,
            notifications=self.notifications,
            active_profile=self._profile_summary(active_profile),
        )

    def event_frames(self, event: EventEnvelope) -> List[DeviceFrame]:
        if event.type != "agent.permission.requested":
            return []

        permission = self._permission_from_event(event)
        self.notifications.enqueue_permission(permission)
        slot_id = self.slot_mapper.assign_permission(permission.permission_id)
        return [
            self.codec.encode_message(
                frame_type="PERMISSION_REQUEST_PUSH",
                payload={
                    "permission_slot_id": slot_id,
                    "priority": permission.priority,
                    "risk": permission.risk,
                },
                device_id=self.device_id,
                generation=self.slot_mapper.generation,
            )
        ]

    def _profile_summary(self, override: Optional[Any]) -> Optional[Dict[str, Any]]:
        candidate = override
        if candidate is None and self._active_profile_provider is not None:
            candidate = self._active_profile_provider(self.device_id)
        if candidate is None:
            candidate = self._active_profile
        if candidate is None:
            return None
        if isinstance(candidate, dict):
            return dict(candidate)
        summary: Dict[str, Any] = {}
        for field in ("id", "name", "version"):
            value = getattr(candidate, field, None)
            if value is not None:
                summary[field] = value
        return summary or None

    @staticmethod
    def _permission_from_event(event: EventEnvelope) -> PermissionRequest:
        payload = event.payload
        permission_id = (
            payload.get("permission_id")
            or payload.get("request_id")
            or payload.get("native_permission_id")
            or event.event_id
        )
        return PermissionRequest(
            permission_id=str(permission_id),
            priority=int(payload.get("priority", 0)),
            instance_id=_str_or_none(payload.get("instance_id")),
            session_id=_str_or_none(payload.get("session_id")),
            run_id=_str_or_none(payload.get("run_id")),
            risk=str(payload.get("risk", "unknown")),
            status=str(payload.get("status", "pending")),
        )


def _str_or_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None
