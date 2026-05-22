"""Agent identity hierarchy from docs/architecture/agent_control."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RunState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OFFLINE = "offline"


@dataclass(frozen=True)
class AgentRef:
    provider_id: str
    instance_id: str
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    permission_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "provider_id": self.provider_id,
            "instance_id": self.instance_id,
        }
        if self.session_id:
            data["session_id"] = self.session_id
        if self.run_id:
            data["run_id"] = self.run_id
        if self.permission_id:
            data["permission_id"] = self.permission_id
        return data


@dataclass(frozen=True)
class AgentProvider:
    provider_id: str
    display_name: str
    adapter_kind: str = "cli"
    capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "adapter_kind": self.adapter_kind,
            "capabilities": list(self.capabilities),
        }


@dataclass
class AgentInstance:
    instance_id: str
    provider_id: str
    label: str
    role: str
    workspace: str
    executable: str
    args: List[str] = field(default_factory=list)
    status: str = "idle"
    default_policy_id: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "provider_id": self.provider_id,
            "label": self.label,
            "role": self.role,
            "workspace": self.workspace,
            "executable": self.executable,
            "args": list(self.args),
            "status": self.status,
            "default_policy_id": self.default_policy_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class AgentSession:
    session_id: str
    provider_id: str
    instance_id: str
    title: str
    workspace: str
    state: str = "active"
    active_run_id: Optional[str] = None
    policy_id: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def ref(self) -> AgentRef:
        return AgentRef(
            provider_id=self.provider_id,
            instance_id=self.instance_id,
            session_id=self.session_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "provider_id": self.provider_id,
            "instance_id": self.instance_id,
            "title": self.title,
            "workspace": self.workspace,
            "state": self.state,
            "active_run_id": self.active_run_id,
            "policy_id": self.policy_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class AgentRun:
    run_id: str
    provider_id: str
    instance_id: str
    session_id: str
    state: RunState
    prompt_summary: str = ""
    started_at: int = field(default_factory=lambda: int(time.time()))
    ended_at: Optional[int] = None
    last_event_seq: int = 0

    def ref(self) -> AgentRef:
        return AgentRef(
            provider_id=self.provider_id,
            instance_id=self.instance_id,
            session_id=self.session_id,
            run_id=self.run_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "provider_id": self.provider_id,
            "instance_id": self.instance_id,
            "session_id": self.session_id,
            "state": self.state.value,
            "prompt_summary": self.prompt_summary,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "last_event_seq": self.last_event_seq,
        }
