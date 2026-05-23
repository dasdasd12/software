"""Agent provider adapter primitives."""

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


PermissionDecisionEncoder = Callable[
    [str, str, bool, Optional[Dict[str, Any]]],
    Dict[str, Any],
]
NativePermissionWriter = Callable[[Dict[str, Any]], Any]


class UnsupportedPermissionAdapter:
    """Adapter used when a provider has no native permission response path."""

    name = "unsupported"

    async def forward_permission_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
    ) -> Dict[str, Any]:
        return {
            "accepted": True,
            "forwarded": False,
            "evidence": {
                "adapter": self.name,
                "reason": "native_permission_channel_unavailable",
                "session_id": session_id,
                "request_id": request_id,
                "approved": approved,
            },
        }


@dataclass
class _PendingSdkPermission:
    session_id: str
    request_id: str
    tool_name: str
    input_data: Dict[str, Any]
    native_context: Dict[str, Any]
    created_at: float
    future: asyncio.Future
    delivered_future: asyncio.Future


class ClaudeAgentSdkPermissionAdapter:
    """Real Claude Agent SDK permission bridge.

    Claude Code's headless `-p --output-format stream-json` path exposes
    permission denials, but not a reliable reverse channel for dynamic approval
    responses. The Python Agent SDK does expose that channel through the
    `can_use_tool` callback; this adapter turns the callback into a Local API
    permission_request and resolves it when permission_response arrives.
    """

    name = "claude_agent_sdk"

    def __init__(
        self,
        emit_permission_request: Callable[[Dict[str, Any]], None],
        timeout_sec: int = 30,
    ) -> None:
        self._emit_permission_request = emit_permission_request
        self._timeout_sec = timeout_sec
        self._pending: Dict[Tuple[str, str], _PendingSdkPermission] = {}

    async def can_use_tool(
        self,
        session_id: str,
        tool_name: str,
        input_data: Dict[str, Any],
        context: Any,
    ) -> Any:
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        request_id = (
            getattr(context, "tool_use_id", None)
            or f"claude_sdk_{uuid.uuid4().hex[:12]}"
        )
        request_id = str(request_id)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        delivered_future = loop.create_future()
        native_context = self._context_to_dict(context)
        self._pending[(session_id, request_id)] = _PendingSdkPermission(
            session_id=session_id,
            request_id=request_id,
            tool_name=tool_name,
            input_data=dict(input_data),
            native_context=native_context,
            created_at=time.time(),
            future=future,
            delivered_future=delivered_future,
        )

        self._emit_permission_request({
            "type": "permission_request",
            "request_id": request_id,
            "session_id": session_id,
            "agent": "claude",
            "tool": tool_name,
            "description": self._describe_tool_request(tool_name, input_data, native_context),
            "risk_level": self._risk_level(tool_name),
            "timeout_sec": self._timeout_sec,
            "native": {
                "adapter": self.name,
                "channel": "can_use_tool",
                "tool_name": tool_name,
                "input": dict(input_data),
                "context": native_context,
            },
        })

        key = (session_id, request_id)
        try:
            approved = await asyncio.wait_for(future, timeout=self._timeout_sec)
        except asyncio.TimeoutError:
            self._pending.pop(key, None)
            return PermissionResultDeny(
                message=f"Local API permission request {request_id} timed out.",
                interrupt=True,
            )
        except asyncio.CancelledError:
            self._pending.pop(key, None)
            raise

        result = (
            PermissionResultAllow()
            if approved
            else PermissionResultDeny(message="Denied by Local API permission response.")
        )
        if not delivered_future.done():
            delivered_future.set_result(True)
        return result

    async def forward_permission_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
    ) -> Dict[str, Any]:
        key = (session_id, request_id)
        pending = self._pending.get(key)
        if pending is None:
            return {
                "accepted": True,
                "forwarded": False,
                "evidence": {
                    "adapter": self.name,
                    "reason": "native_request_not_registered",
                    "session_id": session_id,
                    "request_id": request_id,
                    "approved": approved,
                },
            }

        if not pending.future.done():
            pending.future.set_result(bool(approved))

        try:
            await asyncio.wait_for(pending.delivered_future, timeout=min(self._timeout_sec, 10))
        except asyncio.TimeoutError:
            return {
                "accepted": True,
                "forwarded": False,
                "evidence": {
                    "adapter": self.name,
                    "reason": "sdk_callback_delivery_timeout",
                    "session_id": session_id,
                    "request_id": request_id,
                    "approved": approved,
                },
            }
        finally:
            self._pending.pop(key, None)

        return {
            "accepted": True,
            "forwarded": True,
            "evidence": {
                "adapter": self.name,
                "native_channel": "claude_agent_sdk.can_use_tool",
                "decision_delivered": True,
                "callback_returned": True,
                "session_id": session_id,
                "request_id": request_id,
                "approved": bool(approved),
                "tool": pending.tool_name,
                "age_ms": int((time.time() - pending.created_at) * 1000),
            },
        }

    @staticmethod
    def _context_to_dict(context: Any) -> Dict[str, Any]:
        fields = (
            "tool_use_id",
            "agent_id",
            "blocked_path",
            "decision_reason",
            "title",
            "display_name",
            "description",
        )
        return {
            field: getattr(context, field, None)
            for field in fields
            if getattr(context, field, None) is not None
        }

    @staticmethod
    def _describe_tool_request(
        tool_name: str,
        input_data: Dict[str, Any],
        native_context: Dict[str, Any],
    ) -> str:
        if native_context.get("description"):
            return str(native_context["description"])
        if tool_name in {"WebFetch", "WebSearch"}:
            return str(input_data.get("url") or input_data.get("query") or "Network access requested.")
        command = input_data.get("command")
        if command:
            return str(command)
        return f"Claude requests permission to use {tool_name}."

    @staticmethod
    def _risk_level(tool_name: str) -> str:
        if tool_name in {"Read", "Glob", "Grep"}:
            return "low"
        if tool_name in {"Bash", "PowerShell", "Write", "Edit", "NotebookEdit"}:
            return "high"
        return "medium"


class ClaudeSdkPermissionBridge:
    """Testable scaffold for Claude native permission forwarding.

    The current bridge does not assume the `claude -p --output-format stream-json`
    stdin contract. Instead, it models a companion/native channel boundary:
    a native control_request is registered, a local permission_response is
    accepted, and the bridge produces the native control_response payload.
    """

    name = "claude_sdk_permission_bridge"

    def __init__(
        self,
        decision_encoder: Optional[PermissionDecisionEncoder] = None,
        native_writer: Optional[NativePermissionWriter] = None,
    ) -> None:
        self._decision_encoder = decision_encoder or self._default_decision_encoder
        self._native_writer = native_writer
        self._pending: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.forwarded_responses = []

    def register_control_request(self, session_id: str, native_request: Dict[str, Any]) -> None:
        request_id = native_request.get("request_id")
        if not request_id:
            return
        self._pending[(session_id, str(request_id))] = dict(native_request)

    async def forward_permission_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
    ) -> Dict[str, Any]:
        key = (session_id, request_id)
        native_request = self._pending.get(key)
        if native_request is None:
            return {
                "accepted": True,
                "forwarded": False,
                "evidence": {
                    "adapter": self.name,
                    "reason": "native_request_not_registered",
                    "session_id": session_id,
                    "request_id": request_id,
                    "approved": approved,
                },
            }

        native_response = self._decision_encoder("claude", request_id, approved, native_request)
        if self._native_writer is None:
            return {
                "accepted": True,
                "forwarded": False,
                "evidence": {
                    "adapter": self.name,
                    "reason": "native_permission_channel_unavailable",
                    "session_id": session_id,
                    "request_id": request_id,
                    "approved": approved,
                    "native_response": native_response,
                },
            }

        result = self._native_writer(native_response)
        if inspect.isawaitable(result):
            await result
        self._pending.pop(key, None)
        self.forwarded_responses.append(native_response)
        return {
            "accepted": True,
            "forwarded": True,
            "evidence": {
                "adapter": self.name,
                "session_id": session_id,
                "request_id": request_id,
                "approved": approved,
                "native_response": native_response,
            },
        }

    @staticmethod
    def _default_decision_encoder(
        agent: str,
        request_id: str,
        approved: bool,
        native_request: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "type": "control_response",
            "request_id": request_id,
            "response": {"approved": bool(approved)},
        }
