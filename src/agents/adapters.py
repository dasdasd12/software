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
NativePermissionWriter = Callable[..., Any]


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
class _PendingCodexPermission:
    session_id: str
    request_id: str
    native_id: Any
    native_channel: str
    thread_id: Optional[str]
    turn_id: Optional[str]
    item_id: Optional[str]
    command: Optional[str]
    cwd: Optional[str]
    created_at: float


class CodexAppServerPermissionAdapter:
    """Unit adapter for Codex app-server JSON-RPC approval requests.

    The Local API request id is the JSON-RPC id coerced to a string. Codex may
    include an approvalId in params, but the app-server response must be sent to
    the JSON-RPC id, so using it as the local key keeps routing deterministic.
    """

    name = "codex_app_server"

    _DECISIONS = {
        "item/commandExecution/requestApproval": (("accept", "decline"), "shell"),
        "item/fileChange/requestApproval": (("accept", "decline"), "file_change"),
        "item/permissions/requestApproval": (("accept", "decline"), "permission"),
        "execCommandApproval": (("approved", "denied"), "shell"),
        "applyPatchApproval": (("approved", "denied"), "file_change"),
    }

    def __init__(
        self,
        emit_permission_request: Callable[[Dict[str, Any]], Any],
        native_writer: Optional[NativePermissionWriter] = None,
    ) -> None:
        self._emit_permission_request = emit_permission_request
        self._native_writer = native_writer
        self._pending: Dict[Tuple[str, str], _PendingCodexPermission] = {}

    def register_server_request(
        self,
        session_id: str,
        native_request: Dict[str, Any],
    ) -> bool:
        event = self._register_pending_request(session_id, native_request)
        if event is None:
            return False
        result = self._emit_permission_request(event)
        if inspect.isawaitable(result):
            raise RuntimeError(
                "Async permission emitters must use handle_native_request()."
            )
        return True

    async def handle_native_request(
        self,
        session_id: str,
        native_request: Dict[str, Any],
    ) -> bool:
        event = self._register_pending_request(session_id, native_request)
        if event is None:
            return False
        result = self._emit_permission_request(event)
        if inspect.isawaitable(result):
            await result
        return True

    def _register_pending_request(
        self,
        session_id: str,
        native_request: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        method = native_request.get("method")
        if method not in self._DECISIONS or "id" not in native_request:
            return None

        params = native_request.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        native_id = native_request["id"]
        request_id = str(native_id)
        command = self._string_field(params, "command", "cmd", "commandLine")
        cwd = self._string_field(params, "cwd", "workdir", "currentWorkingDirectory")
        pending = _PendingCodexPermission(
            session_id=session_id,
            request_id=request_id,
            native_id=native_id,
            native_channel=str(method),
            thread_id=self._string_field(
                params,
                "thread_id",
                "threadId",
                "conversationId",
            ),
            turn_id=self._string_field(params, "turn_id", "turnId"),
            item_id=self._string_field(params, "item_id", "itemId", "callId"),
            command=command,
            cwd=cwd,
            created_at=time.time(),
        )
        self._pending[(session_id, request_id)] = pending

        _decisions, tool = self._DECISIONS[str(method)]
        event = {
            "type": "permission_request",
            "request_id": request_id,
            "session_id": session_id,
            "agent": "codex",
            "tool": tool,
            "description": self._description(tool, command, params),
            "risk_level": "high",
            "native": {
                "adapter": self.name,
                "channel": str(method),
                "jsonrpc_id": native_id,
                "approval_id": self._field(params, "approval_id", "approvalId"),
                "thread_id": pending.thread_id,
                "turn_id": pending.turn_id,
                "item_id": pending.item_id,
                "command": pending.command,
                "cwd": pending.cwd,
            },
        }
        return event

    async def forward_permission_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
    ) -> Dict[str, Any]:
        return await self._deliver_permission_decision(session_id, request_id, approved)

    async def expire_permission_request(
        self,
        session_id: str,
        request_id: str,
    ) -> Dict[str, Any]:
        return await self._deliver_permission_decision(
            session_id,
            request_id,
            False,
            expired=True,
        )

    async def _deliver_permission_decision(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        expired: bool = False,
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

        if self._native_writer is None:
            if expired:
                self._pending.pop(key, None)
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

        decision = self._decision(pending.native_channel, approved)
        native_response = {"id": pending.native_id, "result": {"decision": decision}}
        try:
            result = self._call_native_writer(native_response, session_id)
            if inspect.isawaitable(result):
                await result
        finally:
            if expired:
                self._pending.pop(key, None)
        if not expired:
            self._pending.pop(key, None)

        return {
            "accepted": True,
            "forwarded": True,
            "evidence": {
                "adapter": self.name,
                "native_channel": pending.native_channel,
                "jsonrpc_id": pending.native_id,
                "thread_id": pending.thread_id,
                "turn_id": pending.turn_id,
                "item_id": pending.item_id,
                "command": pending.command,
                "cwd": pending.cwd,
                "decision": decision,
                "decision_delivered": True,
                "response_written": True,
                "expired": expired,
                "session_id": session_id,
                "request_id": request_id,
                "approved": bool(approved),
                "age_ms": int((time.time() - pending.created_at) * 1000),
            },
        }

    def _call_native_writer(self, native_response: Dict[str, Any], session_id: str) -> Any:
        try:
            signature = inspect.signature(self._native_writer)
            signature.bind(native_response, session_id)
        except (TypeError, ValueError):
            return self._native_writer(native_response)
        return self._native_writer(native_response, session_id)

    @classmethod
    def _decision(cls, native_channel: str, approved: bool) -> str:
        allow, deny = cls._DECISIONS[native_channel][0]
        return allow if approved else deny

    @staticmethod
    def _field(params: Dict[str, Any], *names: str) -> Any:
        for name in names:
            if name in params:
                return params[name]
        return None

    @classmethod
    def _string_field(cls, params: Dict[str, Any], *names: str) -> Optional[str]:
        value = cls._field(params, *names)
        if value is None:
            return None
        if isinstance(value, list):
            return " ".join(str(part) for part in value)
        return str(value)

    @classmethod
    def _description(
        cls,
        tool: str,
        command: Optional[str],
        params: Dict[str, Any],
    ) -> str:
        if command:
            return command
        path = cls._string_field(params, "path", "file", "filePath")
        if tool == "file_change" and path:
            return path
        return "Codex requests permission."


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
