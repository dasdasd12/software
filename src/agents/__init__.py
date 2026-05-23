"""Agent identity and registry models."""

from .identity import (
    AgentInstance,
    AgentProvider,
    AgentRef,
    AgentRun,
    AgentSession,
    RunState,
)
from .adapters import (
    ClaudeAgentSdkPermissionAdapter,
    ClaudeSdkPermissionBridge,
    UnsupportedPermissionAdapter,
)
from .registry import AgentRegistry

__all__ = [
    "AgentInstance",
    "AgentProvider",
    "AgentRef",
    "AgentRegistry",
    "AgentRun",
    "AgentSession",
    "ClaudeAgentSdkPermissionAdapter",
    "ClaudeSdkPermissionBridge",
    "RunState",
    "UnsupportedPermissionAdapter",
]
