"""Device-facing snapshot projection helpers."""

from typing import Any, Dict, List, Optional

from core import Snapshot
from keyboard.focus import NotificationQueue, ScreenFocus

from .device_transport import DeviceFrame
from .protocol_codec import DeviceProtocolCodec
from .slot_mapper import DeviceSlotMapper, SlotSnapshot


def project_slot_snapshot_frames(codec: DeviceProtocolCodec, snapshot: SlotSnapshot) -> List[DeviceFrame]:
    frames = [
        codec.encode_message(
            frame_type="SLOT_MAP_BEGIN",
            payload={"generation": snapshot.generation},
            device_id=snapshot.device_id,
            generation=snapshot.generation,
        )
    ]
    slot_groups = [
        ("agent", snapshot.agent_slots),
        ("session", snapshot.session_slots),
        ("run", snapshot.run_slots),
        ("permission", snapshot.permission_slots),
        ("notification", snapshot.notification_slots),
    ]
    for slot_kind, slots in slot_groups:
        for slot_id in sorted(slots):
            frames.append(codec.encode_message(
                frame_type="SLOT_MAP_ITEM",
                payload={"slot_kind": slot_kind, "slot_id": slot_id, "value": slots[slot_id]},
                device_id=snapshot.device_id,
                generation=snapshot.generation,
            ))
    frames.append(codec.encode_message(
        frame_type="SLOT_MAP_END",
        payload={"generation": snapshot.generation},
        device_id=snapshot.device_id,
        generation=snapshot.generation,
    ))
    return frames


def project_device_snapshot_frames(
    codec: DeviceProtocolCodec,
    device_id: str,
    snapshot: Snapshot,
    mapper: DeviceSlotMapper,
    focus: Optional[ScreenFocus] = None,
    notifications: Optional[NotificationQueue] = None,
    active_profile: Optional[Dict[str, Any]] = None,
) -> List[DeviceFrame]:
    focus = focus or ScreenFocus(device_id=device_id)
    notifications = notifications or NotificationQueue()
    permission = notifications.resolve_focused_permission(focus)
    pending_permissions = list(notifications.pending_permissions())

    _assign_focus_slots(mapper, focus)
    if permission:
        mapper.assign_permission(permission.permission_id)
    for pending in pending_permissions:
        mapper.assign_permission(pending.permission_id)
    for notification in snapshot.notifications:
        notification_id = notification.get("notification_id")
        if notification_id:
            mapper.assign_notification(str(notification_id))

    frames = [
        codec.encode_message(
            frame_type="DEVICE_SNAPSHOT_BEGIN",
            payload={"last_event_seq": snapshot.last_event_seq},
            device_id=device_id,
            generation=mapper.generation,
        )
    ]
    frames.extend(project_slot_snapshot_frames(codec, mapper.snapshot()))

    if active_profile:
        frames.append(codec.encode_message(
            frame_type="PROFILE_SUMMARY_SET",
            payload={"profile": dict(active_profile)},
            device_id=device_id,
            generation=mapper.generation,
        ))

    frames.append(codec.encode_message(
        frame_type="SCREEN_FOCUS_SET",
        payload=_project_focus_payload(mapper, focus, permission.permission_id if permission else None),
        device_id=device_id,
        generation=mapper.generation,
    ))

    for notification in snapshot.notifications:
        notification_id = notification.get("notification_id")
        if notification_id:
            frames.append(codec.encode_message(
                frame_type="NOTIFICATION_PUSH",
                payload={
                    "notification_slot_id": mapper.assign_notification(str(notification_id)),
                    "level": notification.get("level", "info"),
                    "message": notification.get("message", ""),
                },
                device_id=device_id,
                generation=mapper.generation,
            ))

    for pending in pending_permissions:
        frames.append(codec.encode_message(
            frame_type="PERMISSION_REQUEST_PUSH",
            payload={
                "permission_slot_id": mapper.assign_permission(pending.permission_id),
                "priority": pending.priority,
                "risk": pending.risk,
            },
            device_id=device_id,
            generation=mapper.generation,
        ))

    frames.append(codec.encode_message(
        frame_type="DEVICE_SNAPSHOT_END",
        payload={"last_event_seq": snapshot.last_event_seq},
        device_id=device_id,
        generation=mapper.generation,
    ))
    return frames


def _assign_focus_slots(mapper: DeviceSlotMapper, focus: ScreenFocus) -> None:
    if focus.instance_id:
        mapper.assign_agent(focus.instance_id)
    if focus.session_id:
        mapper.assign_session(focus.session_id)
    if focus.run_id:
        mapper.assign_run(focus.run_id)
    if focus.selected_notification_id:
        mapper.assign_notification(focus.selected_notification_id)


def _project_focus_payload(
    mapper: DeviceSlotMapper,
    focus: ScreenFocus,
    permission_id: Optional[str],
) -> Dict[str, Any]:
    return {
        "focus_mode": focus.mode,
        "agent_slot_id": mapper.assign_agent(focus.instance_id) if focus.instance_id else 0,
        "session_slot_id": mapper.assign_session(focus.session_id) if focus.session_id else 0,
        "run_slot_id": mapper.assign_run(focus.run_id) if focus.run_id else 0,
        "notification_slot_id": mapper.assign_notification(focus.selected_notification_id)
        if focus.selected_notification_id else 0,
        "permission_slot_id": mapper.assign_permission(permission_id) if permission_id else 0,
        "generation": mapper.generation,
    }
