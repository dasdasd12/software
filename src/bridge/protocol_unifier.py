"""
Protocol Unifier — Bridge Server

Converts Codex JSON-RPC 2.0 events and Claude NDJSON events into the
unified JSON Lines format consumed by the CH32H417 device.

Codex  sources: codex exec --json  (JSON-RPC 2.0 envelope-omitted)
Claude sources: claude --output-format stream-json  (NDJSON)
"""

import json
import time
from typing import Any, Dict, Optional, Callable

from session_manager import AgentType, AgentState


class ProtocolUnifier:
    """Converts native Agent events to unified device events."""

    def __init__(self, max_delta_size: int = 2048):
        self._max_delta_size = max_delta_size

    # ------------------------------------------------------------------ #
    #  Codex → Unified
    # ------------------------------------------------------------------ #

    def codex_to_unified(self, raw_line: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Parse a single line from `codex exec --json` and emit unified event(s)."""
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            return None

        event_type = obj.get("type", "")
        # Codex uses nested structure under the type name or common keys like 'item', 'turn', 'thread'
        # Try type-named key first, then common fallbacks
        payload = obj.get(event_type, {}) if event_type else obj
        if not payload and event_type.startswith("item."):
            payload = obj.get("item", {})
        elif not payload and event_type.startswith("turn."):
            payload = obj.get("turn", {})
        elif not payload and event_type.startswith("thread."):
            payload = obj.get("thread", {})

        if event_type in ("thread.started", "turn.started", "item.started"):
            return self._mk_task_update(session_id, AgentType.CODEX, AgentState.SUBMITTED)

        if event_type == "item.agent_message":
            delta = payload.get("content", "") or payload.get("text", "")
            return self._mk_delta(session_id, AgentType.CODEX, delta)

        if event_type == "item.completed":
            # Intermediate completion within a turn
            return self._mk_task_update(session_id, AgentType.CODEX, AgentState.WORKING)

        if event_type == "turn.completed":
            # Entire turn finished
            return self._mk_task_completed(session_id, AgentType.CODEX, summary="Turn completed")

        if event_type == "thread.completed":
            return self._mk_task_completed(session_id, AgentType.CODEX, summary="Thread completed")

        # Codex tool confirmation (may vary by version; fallback)
        if event_type in ("tool.confirmation", "tool.confirm"):
            # Try common key fallbacks for tool events
            tool_payload = payload if payload else obj.get("tool", {})
            return self._mk_permission_request(
                session_id=session_id,
                agent=AgentType.CODEX,
                request_id=tool_payload.get("id", f"req_{int(time.time())}"),
                tool=tool_payload.get("tool", "unknown"),
                description=tool_payload.get("description", "Codex requests tool access."),
            )

        return None

    # ------------------------------------------------------------------ #
    #  Claude → Unified
    # ------------------------------------------------------------------ #

    def claude_to_unified(self, raw_line: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Parse a single line from `claude --output-format stream-json` and emit unified event."""
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            return None

        msg_type = obj.get("type", "")

        if msg_type == "assistant":
            return self._claude_assistant_to_unified(obj, session_id)

        if msg_type == "result":
            if obj.get("is_error") or obj.get("subtype") not in {"success", None}:
                return self._mk_task_failed(
                    session_id=session_id,
                    agent=AgentType.CLAUDE,
                    error_code=str(obj.get("api_error_status") or obj.get("subtype") or "ERROR"),
                    error_message=obj.get("result") or "Claude encountered an error.",
                )
            return self._mk_task_completed(
                session_id=session_id,
                agent=AgentType.CLAUDE,
                summary=obj.get("result", "Claude turn completed."),
            )

        # Claude stream-json format (simplified; actual format depends on version)
        if msg_type == "message_start":
            return self._mk_task_update(session_id, AgentType.CLAUDE, AgentState.SUBMITTED)

        if msg_type == "content_block_delta":
            delta = obj.get("delta", {})
            text = delta.get("text", "") if isinstance(delta, dict) else str(delta)
            return self._mk_delta(session_id, AgentType.CLAUDE, text)

        if msg_type == "message_stop" or msg_type == "message_end":
            return self._mk_task_completed(session_id, AgentType.CLAUDE, summary="Message completed")

        # Claude control request: can_use_tool
        if msg_type == "control_request":
            subtype = obj.get("subtype", "")
            if subtype == "can_use_tool":
                data = obj.get("data", {})
                return self._mk_permission_request(
                    session_id=session_id,
                    agent=AgentType.CLAUDE,
                    request_id=obj.get("request_id", f"req_{int(time.time())}"),
                    tool=data.get("tool", "unknown"),
                    description=data.get("description", "Claude requests tool access."),
                )

        # Claude error events
        if msg_type == "error":
            return self._mk_task_failed(
                session_id=session_id,
                agent=AgentType.CLAUDE,
                error_code=obj.get("code", "UNKNOWN"),
                error_message=obj.get("message", "Claude encountered an error."),
            )

        return None

    def _claude_assistant_to_unified(self, obj: Dict[str, Any], session_id: str) -> Optional[Dict[str, Any]]:
        message = obj.get("message", {})
        content = message.get("content", []) if isinstance(message, dict) else []
        if not isinstance(content, list):
            return None

        text_parts = []
        saw_tool_use = False
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "text" and item.get("text"):
                text_parts.append(str(item["text"]))
            elif item_type == "tool_use":
                saw_tool_use = True

        if text_parts:
            return self._mk_delta(session_id, AgentType.CLAUDE, "\n".join(text_parts))
        if saw_tool_use:
            return self._mk_task_update(session_id, AgentType.CLAUDE, AgentState.EXECUTING)
        return None

    # ------------------------------------------------------------------ #
    #  Unified → JSON string helpers
    # ------------------------------------------------------------------ #

    def encode_device_message(self, msg: Dict[str, Any]) -> str:
        """Serialize unified event dict to JSON Lines string."""
        msg["timestamp"] = int(time.time())
        return json.dumps(msg, ensure_ascii=False)

    def decode_device_message(self, raw_line: str) -> Optional[Dict[str, Any]]:
        """Parse a JSON line from device into dict."""
        try:
            return json.loads(raw_line)
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------ #
    #  Internal builders
    # ------------------------------------------------------------------ #

    def _mk_task_update(self, session_id: str, agent: AgentType, state: AgentState) -> Dict[str, Any]:
        return {
            "type": "task_update",
            "session_id": session_id,
            "agent": agent.value,
            "state": state.value,
        }

    def _mk_delta(self, session_id: str, agent: AgentType, delta: str) -> Dict[str, Any]:
        if len(delta) > self._max_delta_size:
            delta = delta[: self._max_delta_size - 3] + "..."
        return {
            "type": "agent_message_delta",
            "session_id": session_id,
            "agent": agent.value,
            "delta": delta,
        }

    def _mk_task_completed(self, session_id: str, agent: AgentType, summary: str) -> Dict[str, Any]:
        return {
            "type": "task_completed",
            "session_id": session_id,
            "agent": agent.value,
            "summary": summary,
        }

    def _mk_task_failed(self, session_id: str, agent: AgentType,
                        error_code: str, error_message: str) -> Dict[str, Any]:
        return {
            "type": "task_failed",
            "session_id": session_id,
            "agent": agent.value,
            "error_code": error_code,
            "error_message": error_message,
        }

    def _mk_permission_request(self, session_id: str, agent: AgentType,
                               request_id: str, tool: str, description: str) -> Dict[str, Any]:
        return {
            "type": "permission_request",
            "request_id": request_id,
            "session_id": session_id,
            "agent": agent.value,
            "tool": tool,
            "description": description,
            "timeout_sec": 30,
        }
