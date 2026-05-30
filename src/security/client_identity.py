"""Client identity and capability model for local service commands."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Set


class ClientKind(str, Enum):
    DESKTOP_UI = "desktop-ui"
    BROWSER_DEV_UI = "browser-dev-ui"
    DEVICE_TRANSPORT = "device-transport"
    TEST_CLIENT = "test-client"
    AUTOMATION_CLIENT = "automation-client"
    AGENT_HOOK = "agent-hook"


CAP_AGENT_LAUNCH = "agent:launch"
CAP_CLAUDE_HOOK = "claude:hook"
CAP_NOTIFICATION_CREATE = "notification:create"
CAP_PERMISSION_RESPOND = "permission:respond"
CAP_PERMISSION_RESPOND_LOW_RISK = "permission:respond:low_risk"
CAP_SESSION_LIST = "session:list"


DEFAULT_CAPABILITIES = {
    ClientKind.DESKTOP_UI: {
        CAP_AGENT_LAUNCH,
        CAP_NOTIFICATION_CREATE,
        CAP_PERMISSION_RESPOND,
        CAP_SESSION_LIST,
    },
    ClientKind.BROWSER_DEV_UI: {
        CAP_AGENT_LAUNCH,
        CAP_NOTIFICATION_CREATE,
        CAP_PERMISSION_RESPOND,
        CAP_SESSION_LIST,
    },
    ClientKind.DEVICE_TRANSPORT: {
        CAP_PERMISSION_RESPOND_LOW_RISK,
    },
    ClientKind.TEST_CLIENT: set(),
    ClientKind.AUTOMATION_CLIENT: set(),
    ClientKind.AGENT_HOOK: set(),
}


@dataclass(frozen=True)
class ClientIdentity:
    kind: ClientKind
    client_id: str
    capabilities: Set[str] = field(default_factory=set)

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


def coerce_client_kind(value: str) -> ClientKind:
    try:
        return ClientKind(value)
    except ValueError as exc:
        raise ValueError(f"unknown client kind: {value}") from exc


def default_capabilities_for(kind: ClientKind) -> Set[str]:
    return set(DEFAULT_CAPABILITIES.get(kind, set()))


def build_client_identity(
    client_kind: str,
    client_id: str,
    capabilities: Iterable[str] = (),
) -> ClientIdentity:
    kind = coerce_client_kind(client_kind)
    requested = {cap for cap in capabilities if isinstance(cap, str) and cap}
    return ClientIdentity(kind=kind, client_id=client_id, capabilities=requested)
