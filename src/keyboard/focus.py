"""Per-device screen focus and permission notification resolution."""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional


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

    def all_focus(self) -> Dict[str, ScreenFocus]:
        return dict(self._focus_by_device)

    def next_session(self, device_id: str, sessions: Iterable[Any]) -> Optional[ScreenFocus]:
        ordered_sessions = [self._session_record(item) for item in sessions]
        ordered_sessions = [item for item in ordered_sessions if item.get("session_id")]
        if not ordered_sessions:
            return None

        session_ids = [str(item["session_id"]) for item in ordered_sessions]
        current_id = self.get_focus(device_id).session_id
        try:
            current_index = session_ids.index(current_id) if current_id else -1
        except ValueError:
            current_index = -1

        next_session = ordered_sessions[(current_index + 1) % len(ordered_sessions)]
        return self.set_focus(ScreenFocus(
            device_id=device_id,
            mode="session",
            instance_id=self._str_or_none(next_session.get("instance_id")),
            session_id=str(next_session["session_id"]),
        ))

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

    @staticmethod
    def _session_record(value: Any) -> Dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        return {
            "session_id": getattr(value, "session_id", None),
            "instance_id": getattr(value, "instance_id", None),
        }

    @staticmethod
    def _str_or_none(value: Any) -> Optional[str]:
        return value if isinstance(value, str) and value else None


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
        if not pending:
            return None
        pending = sorted(pending, key=lambda item: item.priority, reverse=True)

        has_focus_scope = False
        for field in ("run_id", "session_id", "instance_id"):
            if not self._focus_value(focus, field):
                continue
            has_focus_scope = True
            for permission in pending:
                if self._permission_matches_focus_scope(permission, focus, field):
                    return permission
        if has_focus_scope:
            return None

        for permission in pending:
            if self._is_global_permission(permission):
                return permission
        return None

    @staticmethod
    def _focus_value(focus: ScreenFocus, field: str) -> Optional[str]:
        value = getattr(focus, field, None)
        if isinstance(value, str) and value:
            return value
        return None

    def _permission_matches_focus_scope(
        self,
        permission: PermissionRequest,
        focus: ScreenFocus,
        focus_field: str,
    ) -> bool:
        if getattr(permission, focus_field) != self._focus_value(focus, focus_field):
            return False
        if focus_field == "run_id":
            return (
                self._permission_parent_matches_focus(permission, focus, "session_id")
                and self._permission_parent_matches_focus(permission, focus, "instance_id")
            )
        if focus_field == "session_id":
            return (
                not permission.run_id
                and self._permission_parent_matches_focus(permission, focus, "instance_id")
            )
        if focus_field == "instance_id":
            return not permission.session_id and not permission.run_id
        return True

    def _permission_parent_matches_focus(
        self,
        permission: PermissionRequest,
        focus: ScreenFocus,
        parent_field: str,
    ) -> bool:
        permission_value = getattr(permission, parent_field)
        if not permission_value:
            return True
        return permission_value == self._focus_value(focus, parent_field)

    @staticmethod
    def _is_global_permission(permission: PermissionRequest) -> bool:
        return not permission.instance_id and not permission.session_id and not permission.run_id
