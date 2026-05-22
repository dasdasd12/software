"""Minimal runtime state store used to build snapshots."""

from typing import Any, Dict

from .envelopes import Snapshot


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

    def snapshot(self, last_event_seq: int) -> Snapshot:
        return Snapshot(
            last_event_seq=last_event_seq,
            agents=dict(self.agents),
            sessions=dict(self.sessions),
            runs=dict(self.runs),
            devices=dict(self.devices),
            profiles=dict(self.profiles),
            notifications=list(self.notifications.values()),
            permissions=list(self.permissions.values()),
        )
