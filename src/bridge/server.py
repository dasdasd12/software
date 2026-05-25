#!/usr/bin/env python3
"""
Local Core Service MVP — WebSocket Local API

Accepts local UI/test/automation clients over WebSocket, dispatches unified
events, and proxies to Codex / Claude processes via AgentProxy.

Usage:
    python server.py --config config.yaml
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agent_proxy import AgentProxy
from agents.commands import AgentCommandService
from agents.runtime import AgentLifecycleError, AgentRuntime
from app import build_runtime
from core import CommandEnvelope, CommandSource
from persistence import SQLiteAppStore
from protocol_unifier import ProtocolUnifier
from session_manager import AgentType, AgentState, Session, SessionManager
from local_api.schemas import HelloAck
from security import (
    ApprovalMode,
    ApprovalPolicy,
    ApprovalPolicyEngine,
    CAP_AGENT_LAUNCH,
    CAP_NOTIFICATION_CREATE,
    CAP_PERMISSION_RESPOND,
    CAP_PERMISSION_RESPOND_LOW_RISK,
    CAP_SESSION_LIST,
    ClientIdentity,
    ClientKind,
    PolicyDecision,
    RiskLevel,
    SecurityConfig,
    build_client_identity,
    default_capabilities_for,
)


_permission_client_context: ContextVar[Optional[ClientIdentity]] = ContextVar(
    "permission_client_context",
    default=None,
)


@dataclass
class PendingPermission:
    request_id: str
    session_id: str
    agent: AgentType
    created_at: float
    timeout_sec: int
    risk_level: RiskLevel = RiskLevel.MEDIUM
    tool: str = "unknown"
    description: str = ""
    run_id: Optional[str] = None
    native: Optional[Dict[str, Any]] = None


class LocalCoreServiceMVP:
    """Local Core Service MVP for local APIs, sessions, permissions, and agents."""

    _COMPAT_ACTIVE_RUN_STATES = {
        AgentState.SUBMITTED,
        AgentState.WORKING,
        AgentState.RUNNING,
        AgentState.THINKING,
        AgentState.EXECUTING,
        AgentState.WAITING_PERMISSION,
        AgentState.WAITING_INPUT,
        AgentState.PAUSED,
    }

    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        self._setup_logging()

        # Sub-components
        self.session_mgr = SessionManager(
            max_sessions=config["session"]["cache_size"],
            persist_dir=config["session"].get("persist_dir") if config["session"].get("persist_to_disk") else None,
            cleanup_after_hours=config["session"]["cleanup_after_hours"],
        )
        self.unifier = ProtocolUnifier(
            max_delta_size=config["unifier"]["max_delta_size"],
        )
        self.runtime = build_runtime()
        self.app_store = self._init_app_store(config.get("persistence", {}))
        self._restore_sessions_from_app_store()

        # Agent proxies
        self.agents: Dict[AgentType, AgentProxy] = {}
        self._init_agents()
        self.agent_runtime = AgentRuntime(
            session_manager=self.session_mgr,
            controllers=self.agents,
            persist_session=self._persist_session,
        )
        self.agent_commands = AgentCommandService(
            self.agent_runtime,
            permission_responder=self._handle_permission_command,
        )
        self.runtime.configure_agent_commands(self.agent_commands)

        # Local API client state. This is not the product device transport.
        self.connected_clients: Set[asyncio.Queue] = set()
        self.connected_devices = self.connected_clients  # Backward-compatible alias.
        self.client_identities: Dict[asyncio.Queue, ClientIdentity] = {}
        self.security = SecurityConfig.from_dict(config.get("security"))
        self.approval_policy = ApprovalPolicy(
            policy_id="default",
            mode=ApprovalMode(config.get("approval_policy", {}).get("mode", ApprovalMode.MANUAL.value)),
        )
        self.approval_policy_engine = ApprovalPolicyEngine()
        self.pending_permissions: Dict[str, PendingPermission] = {}
        self._server = None
        self._shutdown_event: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    def _setup_logging(self) -> None:
        log_cfg = self.cfg.get("logging", {})
        level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
        fmt = log_cfg.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handlers = []

        if log_cfg.get("console", True):
            handlers.append(logging.StreamHandler(sys.stdout))
        if log_cfg.get("file"):
            handlers.append(logging.FileHandler(log_cfg["file"], encoding="utf-8"))

        logging.basicConfig(level=level, format=fmt, handlers=handlers)
        self.logger = logging.getLogger("LocalCoreServiceMVP")

    def _init_agents(self) -> None:
        for agent_key, agent_type in [("claude", AgentType.CLAUDE), ("codex", AgentType.CODEX)]:
            acfg = self.cfg["agents"].get(agent_key, {})
            if not acfg.get("enabled", False):
                continue

            proxy = AgentProxy(
                agent_type=agent_type,
                session_manager=self.session_mgr,
                unifier=self.unifier,
                executable=acfg.get("executable") or None,
                mode=acfg.get("mode", ""),
                args=acfg.get("args", []),
                env=acfg.get("env"),
                api_key=acfg.get("api_key") or None,
                session_timeout_sec=acfg.get("session_timeout_sec", 3600),
            )
            proxy.set_event_callback(self._on_agent_event)
            self.agents[agent_type] = proxy
            status = "available" if proxy.is_available() else "NOT FOUND"
            self.logger.info(f"Agent {agent_key}: {status} ({proxy._executable or 'PATH'})")

    def _init_app_store(self, cfg: Dict[str, Any]) -> Optional[SQLiteAppStore]:
        if not cfg.get("enabled", False):
            return None
        db_path = Path(cfg.get("app_store_path") or "data/app.db")
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parents[2] / db_path
        store = SQLiteAppStore.open(db_path)
        self.logger.info(f"SQLite app store opened at {db_path}")
        return store

    def _restore_sessions_from_app_store(self) -> None:
        if not self.app_store:
            return
        restored = 0
        for item in self.app_store.sessions.list():
            try:
                payload = dict(item)
                payload.setdefault("session_id", payload.get("id"))
                self.session_mgr.restore(Session.from_dict(payload))
                restored += 1
            except Exception as exc:
                self.logger.warning(f"SQLite session restore skipped: {exc}")
        if restored:
            self.logger.info(f"Restored {restored} sessions from SQLite app store")

    # ------------------------------------------------------------------ #
    #  Local API WebSocket handlers
    # ------------------------------------------------------------------ #

    async def _handle_local_api_client(self, websocket) -> None:
        """Handle a single local WebSocket API client connection."""
        if not self._is_websocket_origin_allowed(websocket):
            await websocket.close(code=1008, reason="origin not allowed")
            return

        client_queue = asyncio.Queue()
        send_task: Optional[asyncio.Task] = None
        self.connected_clients.add(client_queue)
        peer = websocket.remote_address
        self.logger.info(f"Local API client connected: {peer}")

        try:
            # Start a task to forward messages from queue to websocket
            send_task = asyncio.create_task(self._local_api_sender(websocket, client_queue))

            async for raw_message in websocket:
                try:
                    msg = json.loads(raw_message)
                except json.JSONDecodeError:
                    self.logger.warning(f"Invalid JSON from {peer}: {raw_message[:200]}")
                    await self._send_error(client_queue, "INVALID_JSON", "Message is not valid JSON")
                    continue

                await self._handle_local_api_message(msg, client_queue, websocket)

        except Exception as exc:
            self.logger.warning(f"Local API client {peer} error: {exc}")
        finally:
            if send_task:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass
            self.connected_clients.discard(client_queue)
            self.client_identities.pop(client_queue, None)
            self.logger.info(f"Local API client disconnected: {peer}")

    async def _local_api_sender(self, websocket, queue: asyncio.Queue) -> None:
        """Coroutine that pulls from queue and sends to websocket."""
        while True:
            msg = await queue.get()
            if msg is None:
                break
            try:
                await websocket.send(msg)
            except Exception:
                break

    async def _handle_local_api_message(
        self,
        msg: Dict[str, Any],
        client_queue: asyncio.Queue,
        websocket: Any = None,
    ) -> None:
        """Process a single message from a local API client."""
        msg_type = msg.get("type", "")
        self.logger.debug(f"Local API msg: {msg_type}")
        await self._prune_expired_permissions_async()

        if msg_type == "hello":
            await self._cmd_hello(msg, client_queue, websocket)
        elif self.security.auth_enabled and client_queue not in self.client_identities:
            await self._send_error(client_queue, "AUTH_REQUIRED", "hello with a valid launch token is required")
        elif msg_type == "agent_launch":
            await self._cmd_agent_launch(msg, client_queue)
        elif msg_type == "command":
            await self._cmd_structured_command(msg, client_queue)
        elif msg_type == "permission_response":
            await self._cmd_permission_response(msg, client_queue)
        elif msg_type == "interrupt":
            await self._cmd_interrupt(msg, client_queue)
        elif msg_type == "list_sessions":
            await self._cmd_list_sessions(msg, client_queue)
        elif msg_type == "heartbeat":
            await self._cmd_heartbeat(msg, client_queue)
        else:
            await self._send_error(client_queue, "UNKNOWN_TYPE", f"Unknown message type: {msg_type}")

    async def _handle_device(self, websocket) -> None:
        """Backward-compatible alias for older tests; use _handle_local_api_client."""
        await self._handle_local_api_client(websocket)

    async def _device_sender(self, websocket, queue: asyncio.Queue) -> None:
        """Backward-compatible alias for older tests; use _local_api_sender."""
        await self._local_api_sender(websocket, queue)

    async def _handle_device_message(self, msg: Dict[str, Any], device_queue: asyncio.Queue) -> None:
        """Backward-compatible alias for older tests; use _handle_local_api_message."""
        await self._handle_local_api_message(msg, device_queue)

    # ------------------------------------------------------------------ #
    #  Local API commands
    # ------------------------------------------------------------------ #

    async def _cmd_hello(
        self,
        msg: Dict[str, Any],
        queue: asyncio.Queue,
        websocket: Any = None,
    ) -> None:
        peer = getattr(websocket, "remote_address", None)
        if not self.security.validate_token(msg.get("token"), self._is_loopback_peer(peer)):
            await self._send_error(queue, "AUTH_FAILED", "invalid or missing launch token")
            return

        client_kind = msg.get("client_kind")
        client_id = msg.get("client_id")
        requested_capabilities = msg.get("capabilities", [])
        if not isinstance(client_kind, str) or not isinstance(client_id, str) or not client_id:
            await self._send_error(queue, "INVALID_HELLO", "client_kind and client_id are required")
            return
        if not isinstance(requested_capabilities, list):
            await self._send_error(queue, "INVALID_HELLO", "capabilities must be a list")
            return

        try:
            granted = self.security.granted_capabilities(
                msg.get("token"),
                client_kind,
                client_id,
                self._is_loopback_peer(peer),
            )
        except ValueError as exc:
            await self._send_error(queue, "AUTH_FAILED", str(exc))
            return

        try:
            identity = build_client_identity(
                client_kind,
                client_id,
                set(requested_capabilities).intersection(granted),
            )
        except ValueError as exc:
            await self._send_error(queue, "INVALID_HELLO", str(exc))
            return

        self.client_identities[queue] = identity
        await queue.put(json.dumps(HelloAck(
            client_kind=identity.kind.value,
            client_id=identity.client_id,
            capabilities=identity.capabilities,
        ).to_dict(), ensure_ascii=False))

    async def _cmd_structured_command(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        try:
            command_data = msg.get("command")
            if not isinstance(command_data, dict):
                raise ValueError("command must be an object")
            command = CommandEnvelope.from_dict(command_data)
        except ValueError as exc:
            await self._send_error(queue, "INVALID_COMMAND", str(exc))
            return

        required_capability = self._capability_for_command(command.type)
        if required_capability and not await self._require_capability(queue, required_capability):
            return
        command = self._command_from_client_identity(command, queue)
        if command.type == "agent.permission.respond":
            await self._dispatch_permission_command(command, queue)
            return

        self._sync_runtime_state()
        start_seq = self.runtime.event_bus.last_seq
        try:
            event = await self.runtime.command_router.dispatch_async(command)
        except KeyError as exc:
            await self._send_error(queue, "UNKNOWN_COMMAND", str(exc))
            self._broadcast_incremental_events(start_seq)
            return
        except AgentLifecycleError as exc:
            await self._send_error(queue, exc.code, exc.message)
            self._broadcast_incremental_events(start_seq)
            return

        if event.type == "command.target.unresolved":
            await self._send_unresolved_target_error(queue, event)
            self._broadcast_incremental_events(start_seq, exclude_event=event)
            return

        if command.type == "system.snapshot.request":
            self._sync_runtime_state()
            payload = {
                "type": "snapshot",
                "command_id": command.command_id,
                "snapshot": self.runtime.snapshot().to_dict(),
                "timestamp": int(time.time()),
            }
            await queue.put(json.dumps(payload, ensure_ascii=False))
            self._broadcast_core_events(self._events_to_broadcast(start_seq, event))
            return

        self._sync_runtime_state()
        self._broadcast_core_events(self._events_to_broadcast(start_seq, event))

    async def _cmd_agent_launch(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        if not await self._require_capability(queue, CAP_AGENT_LAUNCH):
            return

        agent_str = "claude" if msg.get("agent", "claude") == "claude" else "codex"
        session_id = msg.get("session_id", "new")
        context = msg.get("context", "")
        command = self._legacy_agent_command(
            "agent.session.launch_or_resume",
            target={"session_id": session_id},
            payload={"agent": agent_str, "context": context},
        )
        try:
            event = await self.runtime.command_router.dispatch_async(command)
        except AgentLifecycleError as exc:
            self.logger.error(f"Launch failed: {exc.message}" if exc.code == "LAUNCH_FAILED" else exc.message)
            await self._send_error(queue, exc.code, exc.message)
            return

        session_id = event.payload["session_id"]

        # Acknowledge launch
        ack = self.unifier.encode_device_message({
            "type": "task_update",
            "session_id": session_id,
            "agent": event.payload["agent"],
            "state": AgentState.SUBMITTED.value,
        })
        await queue.put(ack)

    async def _cmd_permission_response(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        target: Dict[str, Any] = {"permission_id": msg.get("request_id", "")}
        session_id = msg.get("session_id")
        if isinstance(session_id, str) and session_id:
            target["session_id"] = session_id
        command = CommandEnvelope(
            type="agent.permission.respond",
            source=self._command_source_for_queue(queue),
            target=target,
            payload={"approved": msg.get("approved", False)},
        )
        await self._dispatch_permission_command(command, queue)

    async def _dispatch_permission_command(
        self,
        command: CommandEnvelope,
        queue: asyncio.Queue,
    ) -> None:
        await self._prune_expired_permissions_async()
        self._sync_runtime_state()
        start_seq = self.runtime.event_bus.last_seq
        context_token = _permission_client_context.set(self._client_for_queue(queue))
        try:
            event = await self.runtime.command_router.dispatch_async(command)
        except KeyError as exc:
            await self._send_error(queue, "UNKNOWN_COMMAND", str(exc))
            self._broadcast_incremental_events(start_seq)
            return
        except AgentLifecycleError as exc:
            await self._send_error(queue, exc.code, exc.message)
            self._broadcast_incremental_events(start_seq)
            return
        finally:
            _permission_client_context.reset(context_token)

        if event.type == "command.target.unresolved":
            await self._send_unresolved_target_error(queue, event)
            self._broadcast_incremental_events(start_seq, exclude_event=event)
            return

        self._sync_runtime_state()
        await queue.put(self.unifier.encode_device_message(dict(event.payload)))
        self._broadcast_core_events(self._events_to_broadcast(start_seq, event))

    async def _handle_permission_command(self, command: CommandEnvelope) -> Dict[str, Any]:
        target = command.target
        if target is None:
            target = {}
        if not isinstance(target, dict):
            raise AgentLifecycleError("INVALID_COMMAND", "command target must be an object")

        request_id = (
            target.get("permission_id")
            or target.get("request_id")
            or command.payload.get("permission_id")
            or command.payload.get("request_id")
        )
        if not isinstance(request_id, str) or not request_id:
            raise AgentLifecycleError("INVALID_COMMAND", "target.permission_id is required")
        session_id = target.get("session_id") or command.payload.get("session_id")
        if not isinstance(session_id, str):
            session_id = None
        try:
            approved = self._parse_approved(command.payload.get("approved", False))
        except ValueError as exc:
            raise AgentLifecycleError("INVALID_PERMISSION_RESPONSE", str(exc)) from exc

        self.logger.info(f"Permission {request_id}: {'APPROVED' if approved else 'DENIED'}")

        pending_key, pending = self._find_pending_permission(request_id, session_id)
        if not pending:
            raise AgentLifecycleError("REQUEST_NOT_FOUND", f"Permission request {request_id} not found")

        client = _permission_client_context.get()
        if client is None:
            raise AgentLifecycleError(
                "AUTH_REQUIRED",
                "permission command requires a bound client identity",
            )
        allowed, code, reason = self._can_submit_permission_response_for_client(client, pending, approved)
        if not allowed:
            raise AgentLifecycleError(code, reason)

        proxy = self.agents.get(pending.agent)
        result = {"accepted": True, "forwarded": False, "evidence": {}}
        if proxy:
            try:
                result = await proxy.handle_permission_response(
                    pending.session_id,
                    pending.request_id,
                    approved,
                )
            except Exception as exc:
                self.logger.warning(f"Permission forward failed for {request_id}: {exc}")
                raise AgentLifecycleError("PERMISSION_FORWARD_FAILED", str(exc)) from exc

        if not result.get("accepted", True):
            raise AgentLifecycleError(
                "PERMISSION_REJECTED",
                "permission adapter rejected the response",
            )

        if proxy and not result.get("forwarded", False) and self._requires_native_forwarding(pending.agent, result):
            raise AgentLifecycleError(
                "PERMISSION_FORWARD_FAILED",
                result.get("evidence", {}).get("reason", "permission was not forwarded to provider"),
            )

        self.pending_permissions.pop(pending_key, None)

        if not self._is_terminal_session(pending.session_id):
            self.session_mgr.update_state(pending.session_id, AgentState.WORKING)
        self._append_permission_history(client, pending, approved, result)
        self._persist_session(pending.session_id)
        return {
            "type": "permission_ack",
            "request_id": pending.request_id,
            "session_id": pending.session_id,
            "approved": approved,
            "forwarded": bool(result.get("forwarded", False)),
            "evidence": result.get("evidence", {}),
        }

    async def _cmd_interrupt(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        if not await self._require_capability(queue, CAP_AGENT_LAUNCH):
            return

        session_id = msg.get("session_id", "")
        command = self._legacy_agent_command(
            "agent.run.interrupt",
            target={"session_id": session_id},
        )
        try:
            await self.runtime.command_router.dispatch_async(command)
        except AgentLifecycleError as exc:
            await self._send_error(queue, exc.code, exc.message)

    async def _cmd_list_sessions(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        if not await self._require_capability(queue, CAP_SESSION_LIST):
            return

        agent_str = msg.get("agent", "all")
        agent_type = AgentType.CLAUDE if agent_str == "claude" else (AgentType.CODEX if agent_str == "codex" else None)

        sessions = self.session_mgr.list_by_agent(agent_type) if agent_type else self.session_mgr.list_all()
        payload = {
            "type": "session_list",
            "sessions": [s.to_dict() for s in sessions],
            "timestamp": int(time.time()),
        }
        await queue.put(json.dumps(payload, ensure_ascii=False))

    async def _cmd_heartbeat(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        # Optional: track device liveness, respond with server heartbeat
        pass

    # ------------------------------------------------------------------ #
    #  Agent event forwarder
    # ------------------------------------------------------------------ #

    def _sync_runtime_state(self) -> None:
        self._prune_expired_permissions()
        store = self.runtime.state_store
        store.agents = {
            record["instance_id"]: record
            for record in (
                self._default_instance_record(agent, proxy)
                for agent, proxy in self.agents.items()
            )
        }
        sessions: Dict[str, Dict[str, Any]] = {}
        runs: Dict[str, Dict[str, Any]] = {}
        pending_run_ids = self._pending_permission_run_ids_by_session()
        for session in self.session_mgr.list_all():
            provider_id = session.agent.value
            instance_id = self._default_instance_id(session.agent)
            session_record = dict(session.to_dict())
            session_record.update({
                "provider_id": provider_id,
                "agent": provider_id,
                "instance_id": instance_id,
            })
            run_id = self._compat_active_run_id(session, pending_run_ids)
            if run_id:
                session_record["active_run_id"] = run_id
                runs[run_id] = {
                    "run_id": run_id,
                    "session_id": session.session_id,
                    "instance_id": instance_id,
                    "provider_id": provider_id,
                    "agent": provider_id,
                    "state": session.state.value,
                }
            sessions[session.session_id] = session_record
        permissions: Dict[str, Dict[str, Any]] = {}
        for request_id, pending in self.pending_permissions.items():
            permission_record = self._pending_permission_record(pending, sessions)
            self._ensure_permission_run_ancestry(runs, permission_record, sessions)
            permissions[request_id] = permission_record

        store.sessions = sessions
        store.runs = runs
        store.permissions = permissions
        store.devices = {
            device_id: record.to_dict()
            for device_id, record in self.runtime.device_manager.list_records().items()
        }

    def _pending_permission_record(
        self,
        pending: PendingPermission,
        sessions: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        provider_id = pending.agent.value
        session_record = sessions.get(pending.session_id, {})
        instance_id = self._pending_permission_instance_id(pending, session_record)
        return {
            "request_id": pending.request_id,
            "session_id": pending.session_id,
            "instance_id": instance_id,
            "provider_id": provider_id,
            "agent": provider_id,
            "timeout_sec": pending.timeout_sec,
            "risk_level": pending.risk_level.value,
            "tool": pending.tool,
            "description": pending.description,
            "run_id": self._pending_run_id(pending),
        }

    def _pending_permission_instance_id(
        self,
        pending: PendingPermission,
        session_record: Dict[str, Any],
    ) -> str:
        instance_id = session_record.get("instance_id")
        if isinstance(instance_id, str) and instance_id:
            return instance_id
        return self._default_instance_id(pending.agent)

    def _ensure_permission_run_ancestry(
        self,
        runs: Dict[str, Dict[str, Any]],
        permission_record: Dict[str, Any],
        sessions: Dict[str, Dict[str, Any]],
    ) -> None:
        run_id = permission_record.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return
        session_id = permission_record.get("session_id")
        session_record = sessions.get(session_id, {}) if isinstance(session_id, str) else {}
        run_record = dict(runs.get(run_id, {}))
        run_record.update({
            "run_id": run_id,
            "session_id": permission_record.get("session_id"),
            "instance_id": permission_record.get("instance_id"),
            "provider_id": permission_record.get("provider_id"),
            "agent": permission_record.get("agent"),
        })
        run_record.setdefault("state", session_record.get("state", AgentState.WAITING_PERMISSION.value))
        runs[run_id] = run_record

    def _default_instance_record(self, agent: AgentType, proxy: AgentProxy) -> Dict[str, Any]:
        status = "available"
        is_available = getattr(proxy, "is_available", None)
        if callable(is_available) and not is_available():
            status = "unavailable"
        return {
            "instance_id": self._default_instance_id(agent),
            "provider_id": agent.value,
            "agent": agent.value,
            "label": agent.value.capitalize(),
            "status": status,
        }

    def _default_instance_id(self, agent: AgentType) -> str:
        agent_cfg = self.cfg.get("agents", {}).get(agent.value, {})
        instance_id = agent_cfg.get("instance_id") if isinstance(agent_cfg, dict) else None
        if isinstance(instance_id, str) and instance_id:
            return instance_id
        return f"{agent.value}-default"

    def _pending_permission_run_ids_by_session(self) -> Dict[str, str]:
        run_ids: Dict[str, str] = {}
        for pending in self.pending_permissions.values():
            run_id = self._pending_run_id(pending)
            if run_id:
                run_ids.setdefault(pending.session_id, run_id)
        return run_ids

    def _compat_active_run_id(
        self,
        session: Session,
        pending_run_ids: Dict[str, str],
    ) -> Optional[str]:
        if session.state not in self._COMPAT_ACTIVE_RUN_STATES:
            return None
        return pending_run_ids.get(session.session_id) or f"run_{session.session_id}"

    @staticmethod
    def _pending_run_id(pending: PendingPermission) -> Optional[str]:
        if pending.run_id:
            return pending.run_id
        native = pending.native if isinstance(pending.native, dict) else {}
        for key in ("run_id", "runId"):
            value = native.get(key)
            if isinstance(value, str) and value:
                return value
        params = native.get("params")
        if isinstance(params, dict):
            for key in ("run_id", "runId"):
                value = params.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    def _broadcast_core_event(self, event) -> None:
        payload = json.dumps({
            "type": "event",
            "event": event.to_dict(),
            "timestamp": int(time.time()),
        }, ensure_ascii=False)
        for queue in list(self.connected_clients):
            try:
                queue.put_nowait(payload)
            except Exception:
                pass

    def _broadcast_core_events(self, events) -> None:
        for event in events:
            self._broadcast_core_event(event)

    def _broadcast_incremental_events(self, start_seq: int, exclude_event=None) -> None:
        events = self.runtime.event_bus.events_after(start_seq)
        if exclude_event is not None:
            events = [
                event
                for event in events
                if not self._event_was_published(exclude_event, [event])
            ]
        self._broadcast_core_events(events)

    def _events_to_broadcast(self, start_seq: int, returned_event):
        published_events = self.runtime.event_bus.events_after(start_seq)
        if self._event_was_published(returned_event, published_events):
            return published_events
        return [*published_events, returned_event]

    @staticmethod
    def _event_was_published(returned_event, published_events) -> bool:
        returned_seq = getattr(returned_event, "seq", None)
        if returned_seq:
            return any(getattr(event, "seq", None) == returned_seq for event in published_events)
        returned_id = getattr(returned_event, "event_id", None)
        if returned_id:
            return any(getattr(event, "event_id", None) == returned_id for event in published_events)
        return any(event is returned_event for event in published_events)

    def _on_agent_event(self, json_line: str) -> None:
        """Called by AgentProxy whenever a unified event is produced."""
        self._track_permission_event(json_line)
        self._persist_event_state(json_line)
        # Broadcast to all connected local API clients.
        for queue in list(self.connected_clients):
            try:
                queue.put_nowait(json_line)
            except Exception:
                pass

    def _track_permission_event(self, json_line: str) -> None:
        try:
            event = json.loads(json_line)
        except json.JSONDecodeError:
            return

        if event.get("type") != "permission_request":
            return

        request_id = event.get("request_id", "")
        session_id = event.get("session_id", "")
        agent = self._agent_from_string(event.get("agent", ""))
        if not request_id or not session_id or not agent:
            return

        permission_key = self._pending_permission_key(request_id, session_id)
        self.pending_permissions[permission_key] = PendingPermission(
            request_id=request_id,
            session_id=session_id,
            agent=agent,
            created_at=time.time(),
            timeout_sec=int(event.get("timeout_sec", self.cfg["unifier"].get("permission_timeout_sec", 30))),
            risk_level=self._risk_from_event(event.get("risk_level")),
            tool=str(event.get("tool", "unknown")),
            description=str(event.get("description", "")),
            run_id=event.get("run_id") if isinstance(event.get("run_id"), str) else None,
            native=event.get("native") if isinstance(event.get("native"), dict) else None,
        )
        self.session_mgr.update_state(session_id, AgentState.WAITING_PERMISSION)
        self._persist_session(session_id)

    def _collect_expired_permissions(self) -> List[Tuple[str, PendingPermission]]:
        now = time.time()
        expired = [
            (request_id, pending)
            for request_id, pending in self.pending_permissions.items()
            if now - pending.created_at > pending.timeout_sec
        ]
        for request_id, _pending in expired:
            del self.pending_permissions[request_id]
        return expired

    def _prune_expired_permissions(self) -> None:
        expired = self._collect_expired_permissions()
        for _request_id, pending in expired:
            self._schedule_provider_permission_expiry(pending)

    async def _prune_expired_permissions_async(self) -> None:
        expired = self._collect_expired_permissions()
        for _request_id, pending in expired:
            await self._expire_provider_permission(pending)

    def _schedule_provider_permission_expiry(self, pending: PendingPermission) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._expire_provider_permission(pending))
        task.add_done_callback(self._log_permission_expiry_failure)

    def _log_permission_expiry_failure(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception as exc:
            self.logger.warning(f"Expired permission native deny failed: {exc}")

    async def _expire_provider_permission(self, pending: PendingPermission) -> None:
        proxy = self.agents.get(pending.agent)
        expire = getattr(proxy, "expire_permission_request", None)
        if expire is None:
            return
        result = await expire(pending.session_id, pending.request_id)
        if not result.get("forwarded", False) and self._requires_native_forwarding(pending.agent, result):
            self.logger.warning(
                "Expired permission %s was not forwarded to provider: %s",
                pending.request_id,
                result.get("evidence", {}).get("reason", "unknown"),
            )

    def _is_terminal_session(self, session_id: str) -> bool:
        session = self.session_mgr.get(session_id)
        return bool(session and session.state in {
            AgentState.COMPLETED,
            AgentState.FAILED,
            AgentState.CANCELLED,
            AgentState.ERROR,
            AgentState.TIMEOUT,
        })

    def _persist_event_state(self, json_line: str) -> None:
        if not self.app_store:
            return
        try:
            event = json.loads(json_line)
        except json.JSONDecodeError:
            return
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id:
            self._persist_session(session_id)

    def _persist_session(self, session_id: str) -> None:
        if not self.app_store:
            return
        session = self.session_mgr.get(session_id)
        if not session:
            return
        try:
            payload = session.to_dict()
            payload["id"] = session.session_id
            self.app_store.sessions.upsert(payload)
        except Exception as exc:
            self.logger.warning(f"SQLite session persist failed for {session_id}: {exc}")

    def _append_permission_history(
        self,
        client: ClientIdentity,
        pending: PendingPermission,
        approved: bool,
        result: Dict[str, Any],
    ) -> None:
        if not self.app_store:
            return
        try:
            self.app_store.permission_history.append({
                "permission_id": pending.request_id,
                "session_id": pending.session_id,
                "run_id": pending.run_id,
                "action_type": pending.tool,
                "risk_level": pending.risk_level.value,
                "decision": "approve" if approved else "deny",
                "source_client": client.client_id,
                "timestamp": int(time.time()),
                "summary": pending.description or f"{'Approved' if approved else 'Denied'} {pending.agent.value} permission",
                "forwarded": bool(result.get("forwarded", False)),
                "evidence": result.get("evidence", {}),
                "native": pending.native,
            })
        except Exception as exc:
            self.logger.warning(f"SQLite permission history append failed for {pending.request_id}: {exc}")

    @staticmethod
    def _agent_from_string(value: str) -> Optional[AgentType]:
        try:
            return AgentType(value)
        except ValueError:
            return None

    def register_client_identity(
        self,
        queue: asyncio.Queue,
        client_kind: str,
        client_id: str,
        capabilities: Set[str],
    ) -> ClientIdentity:
        identity = build_client_identity(client_kind, client_id, capabilities)
        self.client_identities[queue] = identity
        return identity

    def _client_for_queue(self, queue: asyncio.Queue) -> ClientIdentity:
        identity = self.client_identities.get(queue)
        if identity:
            return identity
        return ClientIdentity(
            kind=ClientKind.DESKTOP_UI,
            client_id="legacy-local-api",
            capabilities=default_capabilities_for(ClientKind.DESKTOP_UI),
        )

    def _can_submit_permission_response(
        self,
        queue: asyncio.Queue,
        pending: PendingPermission,
        approved: bool,
    ) -> Tuple[bool, str, str]:
        client = self._client_for_queue(queue)
        return self._can_submit_permission_response_for_client(client, pending, approved)

    def _can_submit_permission_response_for_client(
        self,
        client: ClientIdentity,
        pending: PendingPermission,
        approved: bool,
    ) -> Tuple[bool, str, str]:
        policy = self.approval_policy_engine.evaluate(
            self.approval_policy,
            pending.risk_level,
            client,
        )
        if policy.decision == PolicyDecision.DENY:
            return False, "POLICY_DENIED", policy.reason
        if (
            policy.decision == PolicyDecision.REQUIRE_DESKTOP_CONFIRM
            and client.kind not in {ClientKind.DESKTOP_UI, ClientKind.BROWSER_DEV_UI}
        ):
            return False, "REQUIRE_DESKTOP_CONFIRM", policy.reason

        can_respond = client.has_capability(CAP_PERMISSION_RESPOND)
        can_respond_low_risk = (
            client.kind == ClientKind.DEVICE_TRANSPORT
            and approved
            and pending.risk_level == RiskLevel.LOW
            and client.has_capability(CAP_PERMISSION_RESPOND_LOW_RISK)
        )
        if not can_respond and not can_respond_low_risk:
            return False, "CAPABILITY_DENIED", "client cannot respond to permission requests"

        if client.kind == ClientKind.TEST_CLIENT and approved:
            return False, "CAPABILITY_DENIED", "test clients cannot approve real permission requests"

        if client.kind == ClientKind.DEVICE_TRANSPORT and approved and pending.risk_level != RiskLevel.LOW:
            return (
                False,
                "REQUIRE_DESKTOP_CONFIRM",
                "device transport can directly approve only low-risk requests",
            )

        return True, "", ""

    @staticmethod
    def _risk_from_event(value: Any) -> RiskLevel:
        try:
            return RiskLevel(value)
        except (TypeError, ValueError):
            return RiskLevel.MEDIUM

    @staticmethod
    def _parse_approved(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "y", "approve", "approved"}:
                return True
            if lowered in {"0", "false", "no", "n", "deny", "denied"}:
                return False
        raise ValueError("approved must be a boolean or an explicit approve/deny string")

    def _pending_permission_key(self, request_id: str, session_id: str) -> str:
        existing = self.pending_permissions.get(request_id)
        if existing is None or existing.session_id == session_id:
            return request_id
        return f"{session_id}:{request_id}"

    def _find_pending_permission(
        self,
        request_id: str,
        session_id: Any = None,
    ) -> Tuple[str, Optional[PendingPermission]]:
        if isinstance(session_id, str) and session_id:
            matches = [
                (key, pending)
                for key, pending in self.pending_permissions.items()
                if pending.request_id == request_id and pending.session_id == session_id
            ]
        else:
            matches = [
                (key, pending)
                for key, pending in self.pending_permissions.items()
                if pending.request_id == request_id
            ]

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return "", None
        return "", None

    @staticmethod
    def _requires_native_forwarding(agent: AgentType, result: Dict[str, Any]) -> bool:
        adapter = result.get("evidence", {}).get("adapter")
        if agent == AgentType.CODEX:
            return adapter == "codex_app_server"
        if agent != AgentType.CLAUDE:
            return False
        return adapter not in {"fake", "unsupported"}

    async def _require_capability(self, queue: asyncio.Queue, capability: str) -> bool:
        client = self._client_for_queue(queue)
        if client.has_capability(capability):
            return True
        await self._send_error(queue, "CAPABILITY_DENIED", f"client lacks capability: {capability}")
        return False

    @staticmethod
    def _capability_for_command(command_type: str) -> Optional[str]:
        return {
            "agent.session.launch_or_resume": CAP_AGENT_LAUNCH,
            "agent.run.interrupt": CAP_AGENT_LAUNCH,
            "agent.session.close": CAP_AGENT_LAUNCH,
            "system.snapshot.request": CAP_SESSION_LIST,
            "notification.create": CAP_NOTIFICATION_CREATE,
        }.get(command_type)

    def _command_from_client_identity(
        self,
        command: CommandEnvelope,
        queue: asyncio.Queue,
    ) -> CommandEnvelope:
        client = self._client_for_queue(queue)
        return CommandEnvelope(
            type=command.type,
            source=self._command_source_for_queue(queue),
            payload=dict(command.payload),
            target=command.target,
            command_id=command.command_id,
            timestamp=command.timestamp,
        )

    def _command_source_for_queue(self, queue: asyncio.Queue) -> CommandSource:
        client = self._client_for_queue(queue)
        return CommandSource(kind=client.kind.value, client_id=client.client_id)

    @staticmethod
    def _legacy_agent_command(
        command_type: str,
        *,
        target: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> CommandEnvelope:
        return CommandEnvelope(
            type=command_type,
            source=CommandSource(kind="legacy-local-api", client_id="legacy-local-api"),
            payload=dict(payload or {}),
            target=dict(target or {}),
        )

    def _is_websocket_origin_allowed(self, websocket: Any) -> bool:
        origin = None
        headers = getattr(websocket, "request_headers", None)
        if headers is not None:
            origin = headers.get("Origin") or headers.get("origin")
        request = getattr(websocket, "request", None)
        request_headers = getattr(request, "headers", None)
        if origin is None and request_headers is not None:
            origin = request_headers.get("Origin") or request_headers.get("origin")
        return self.security.origin_allowed(origin)

    @staticmethod
    def _is_loopback_peer(peer: Any) -> bool:
        if not peer:
            return False
        host = peer[0] if isinstance(peer, tuple) else str(peer)
        return host in {"127.0.0.1", "::1", "localhost"}

    async def _send_error(self, queue: asyncio.Queue, code: str, message: str) -> None:
        payload = {
            "type": "error",
            "code": code,
            "message": message,
            "timestamp": int(time.time()),
        }
        await queue.put(json.dumps(payload, ensure_ascii=False))

    async def _send_unresolved_target_error(self, queue: asyncio.Queue, event: Any) -> None:
        await self._send_error(
            queue,
            str(event.payload.get("code", "UNRESOLVED_TARGET")),
            str(event.payload.get("message", "unresolved command target")),
        )

    # ------------------------------------------------------------------ #
    #  Server lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._shutdown_event = asyncio.Event()
        srv_cfg = self.cfg["server"]
        host = srv_cfg.get("host", "127.0.0.1")
        port = srv_cfg.get("port", 8765)

        # Import websockets here to allow graceful degradation if not installed
        try:
            import websockets
        except ImportError:
            self.logger.error("Package 'websockets' is required. Install: pip install websockets")
            sys.exit(1)

        self._server = await websockets.serve(self._handle_local_api_client, host, port)
        self.logger.info(f"Local Core Service MVP listening on ws://{host}:{port}")

        # Graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                self.logger.debug("Signal handlers are not supported on this platform")
                break

        await self._shutdown_event.wait()
        self.logger.info("Shutting down...")
        self._server.close()
        await self._server.wait_closed()

        # Cleanup agent processes
        for proxy in self.agents.values():
            session_ids = set(proxy._processes.keys())
            session_ids.update(getattr(proxy, "_sdk_tasks", {}).keys())
            for session_id in list(session_ids):
                await proxy.terminate(session_id)
        if self.app_store:
            self.app_store.close()

    def _request_shutdown(self) -> None:
        if self._shutdown_event:
            self._shutdown_event.set()


# ------------------------------------------------------------------ #
#  CLI entrypoint
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(description="AI Keyboard Local Core Service MVP")
    parser.add_argument("--config", "-c", default="config.yaml", help="Path to YAML config file")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent / config_path

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    server = LocalCoreServiceMVP(config)
    asyncio.run(server.start())


BridgeServer = LocalCoreServiceMVP


if __name__ == "__main__":
    main()
