"""Client identity and capability model for local service commands."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Set


class ClientKind(str, Enum):
    DESKTOP_UI = "desktop-ui"
    BROWSER_DEV_UI = "browser-dev-ui"
    DEVICE_TRANSPORT = "device-transport"
    TEST_CLIENT = "test-client"
    AUTOMATION_CLIENT = "automation-client"


@dataclass(frozen=True)
class ClientIdentity:
    kind: ClientKind
    client_id: str
    capabilities: Set[str] = field(default_factory=set)

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities
