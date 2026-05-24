"""Minimal runtime state store used to build snapshots."""

from typing import Any, Dict

from .envelopes import EventEnvelope, Snapshot


class RuntimeSnapshot(Snapshot):
    def __init__(self, *args: Any, focus: Dict[str, Any] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "focus", dict(focus or {}))

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        if self.focus:
            data["focus"] = {
                device_id: dict(focus)
                for device_id, focus in self.focus.items()
            }
        return data


class StateStore:
    """Authoritative in-memory state container for early domain scaffolding."""

    def __init__(self) -> None:
        self.agents: Dict[str, Any] = {}
        self.sessions: Dict[str, Any] = {}
        self.runs: Dict[str, Any] = {}
        self.devices: Dict[str, Any] = {}
        self.profiles: Dict[str, Any] = {}
        self.notifications: Dict[str, Any] = {}
        self.permissions: Dict[str, Any] = {}
        self.focus: Dict[str, Any] = {}

    def apply_event(self, event: EventEnvelope) -> None:
        """Apply event payloads that have a direct snapshot projection."""
        if event.type == "notification.created":
            notification = dict(event.payload)
            notification_id = notification.get("notification_id")
            if not notification_id:
                notification_id = event.event_id
                notification["notification_id"] = notification_id
            self.notifications[str(notification_id)] = notification
        elif event.type == "agent.session.created":
            session = dict(event.payload)
            session_id = session.get("session_id")
            if session_id:
                self.sessions[str(session_id)] = session
        elif event.type == "agent.session.state_changed":
            session_id = event.payload.get("session_id")
            if session_id:
                current = dict(self.sessions.get(str(session_id), {}))
                current.update(event.payload)
                self.sessions[str(session_id)] = current
        elif event.type == "agent.permission.requested":
            permission = dict(event.payload)
            request_id = permission.get("request_id")
            if request_id:
                self.permissions[str(request_id)] = permission
        elif event.type == "agent.permission.resolved":
            request_id = event.payload.get("request_id")
            if request_id:
                self.permissions.pop(str(request_id), None)
        elif event.type == "agent.focus.changed":
            focus = dict(event.payload)
            device_id = focus.get("device_id")
            if not device_id and event.target:
                device_id = event.target.get("device_id")
            if device_id:
                focus["device_id"] = str(device_id)
                self.focus[str(device_id)] = focus

    def snapshot(self, last_event_seq: int) -> Snapshot:
        return RuntimeSnapshot(
            last_event_seq=last_event_seq,
            agents=dict(self.agents),
            sessions=dict(self.sessions),
            runs=dict(self.runs),
            devices=dict(self.devices),
            profiles=dict(self.profiles),
            notifications=list(self.notifications.values()),
            permissions=list(self.permissions.values()),
            focus=dict(self.focus),
        )
