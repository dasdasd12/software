"""Per-device screen focus and permission notification resolution."""

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class ScreenFocus:
    device_id: str
    mode: str = "global_dashboard"
    instance_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    selected_notification_id: Optional[str] = None
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, object]:
        return {
            "device_id": self.device_id,
            "mode": self.mode,
            "target": {
                "instance_id": self.instance_id,
                "session_id": self.session_id,
                "run_id": self.run_id,
            },
            "selected_notification_id": self.selected_notification_id,
            "updated_at": self.updated_at,
        }


class FocusManager:
    """Stores independent focus state per device."""

    def __init__(self) -> None:
        self._focus_by_device: Dict[str, ScreenFocus] = {}

    def set_focus(self, focus: ScreenFocus) -> ScreenFocus:
        self._focus_by_device[focus.device_id] = focus
        return focus

    def get_focus(self, device_id: str) -> ScreenFocus:
        return self._focus_by_device.get(device_id) or ScreenFocus(device_id=device_id)

    def resolve_focus(
        self,
        device_id: str,
        existing_instances: Iterable[str] = (),
        existing_sessions: Iterable[str] = (),
        existing_runs: Iterable[str] = (),
    ) -> ScreenFocus:
        focus = self.get_focus(device_id)
        instances = set(existing_instances)
        sessions = set(existing_sessions)
        runs = set(existing_runs)

        if focus.run_id and focus.run_id in runs:
            return focus
        if focus.session_id and focus.session_id in sessions:
            return self.set_focus(ScreenFocus(
                device_id=device_id,
                mode="session",
                instance_id=focus.instance_id,
                session_id=focus.session_id,
                selected_notification_id=focus.selected_notification_id,
            ))
        if focus.instance_id and focus.instance_id in instances:
            return self.set_focus(ScreenFocus(
                device_id=device_id,
                mode="instance",
                instance_id=focus.instance_id,
                selected_notification_id=focus.selected_notification_id,
            ))
        return self.set_focus(ScreenFocus(device_id=device_id, mode="global_dashboard"))


@dataclass(frozen=True)
class Notification:
    notification_id: str
    level: str
    message: str
    priority: int = 0
    instance_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    status: str = "pending"


@dataclass(frozen=True)
class PermissionRequest:
    permission_id: str
    priority: int = 0
    instance_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    risk: str = "unknown"
    status: str = "pending"


class NotificationQueue:
    """Simple pending notification and permission queue."""

    def __init__(self) -> None:
        self._notifications: Dict[str, Notification] = {}
        self._permissions: Dict[str, PermissionRequest] = {}
        self._dismissed_permissions = set()

    def enqueue(self, notification: Notification) -> Notification:
        self._notifications[notification.notification_id] = notification
        return notification

    def enqueue_permission(self, permission: PermissionRequest) -> PermissionRequest:
        self._permissions[permission.permission_id] = permission
        self._notifications[permission.permission_id] = Notification(
            notification_id=permission.permission_id,
            level="permission",
            message=f"Permission request {permission.permission_id}",
            priority=permission.priority,
            instance_id=permission.instance_id,
            session_id=permission.session_id,
            run_id=permission.run_id,
        )
        return permission

    def dismiss(self, permission_id: str) -> None:
        self._dismissed_permissions.add(permission_id)

    def pending_notifications(self) -> List[Notification]:
        return [item for item in self._notifications.values() if item.status == "pending"]

    def pending_permissions(self) -> List[PermissionRequest]:
        return [
            item for item in self._permissions.values()
            if item.status == "pending" and item.permission_id not in self._dismissed_permissions
        ]

    def resolve_focused_permission(self, focus: ScreenFocus) -> Optional[PermissionRequest]:
        pending = self.pending_permissions()
        for permission in pending:
            if focus.run_id and permission.run_id == focus.run_id:
                return permission
        for permission in pending:
            if focus.session_id and permission.session_id == focus.session_id:
                return permission
        for permission in pending:
            if focus.instance_id and permission.instance_id == focus.instance_id:
                return permission
        if not pending:
            return None
        return sorted(pending, key=lambda item: item.priority, reverse=True)[0]
