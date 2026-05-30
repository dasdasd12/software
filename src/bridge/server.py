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
import hashlib
import json
import logging
import secrets
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
from agents.foreground_cli import ForegroundCliLauncher
from agents.runtime import AgentLifecycleError, AgentRuntime
from app import build_runtime, resolve_workspace
from core import CommandEnvelope, CommandSource
from devices import (
    DeviceProtocolCodec,
    DeviceProjectionRuntime,
    DeviceSlotMapper,
    SimulatedTransport,
    VirtualDeviceCommandAdapter,
    VirtualDeviceSession,
)
from keyboard import Profile, ProfileValidationError, profile_from_dict, validate_profile
from persistence import SQLiteAppStore
from protocol_unifier import ProtocolUnifier
from session_manager import AgentType, AgentState, Session, SessionManager
from local_api.schemas import HelloAck
from security import (
    ApprovalMode,
    ApprovalPolicy,
    ApprovalPolicyEngine,
    CAP_AGENT_LAUNCH,
    CAP_CLAUDE_HOOK,
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


def uuid_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


@dataclass
class PendingPermission:
    request_id: str
    agent: AgentType
    created_at: float
    timeout_sec: int
    session_id: Optional[str] = None
    instance_id: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    tool: str = "unknown"
    description: str = ""
    run_id: Optional[str] = None
    priority: int = 0
    native: Optional[Dict[str, Any]] = None


@dataclass
class PendingClaudeHookDecision:
    request_id: str
    session_id: str
    hook_input: Dict[str, Any]
    created_at: float
    result_future: asyncio.Future
    delivered_future: asyncio.Future


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
        self._ensure_workspace_config(config)
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
        self.security = SecurityConfig.from_dict(config.get("security"))

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
            foreground_cli_launcher=self._build_foreground_cli_launcher(),
            workspace_resolver=self._resolve_launch_workspace,
        )
        self.runtime.configure_agent_commands(self.agent_commands)

        # Local API client state. This is not the product device transport.
        self.connected_clients: Set[asyncio.Queue] = set()
        self.connected_devices = self.connected_clients  # Backward-compatible alias.
        self.client_identities: Dict[asyncio.Queue, ClientIdentity] = {}
        self.approval_policy = ApprovalPolicy(
            policy_id="default",
            mode=ApprovalMode(config.get("approval_policy", {}).get("mode", ApprovalMode.MANUAL.value)),
        )
        self.approval_policy_engine = ApprovalPolicyEngine()
        self.pending_permissions: Dict[str, PendingPermission] = {}
        self._claude_hook_decisions: Dict[str, PendingClaudeHookDecision] = {}
        self._virtual_profiles: Dict[str, Profile] = {}
        self._virtual_sessions: Dict[str, VirtualDeviceSession] = {}
        self._virtual_transports: Dict[str, SimulatedTransport] = {}
        self._virtual_session_owners: Dict[str, asyncio.Queue] = {}
        self._foreground_cli_session_owners: Dict[asyncio.Queue, Set[str]] = {}
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
                workspace=self.cfg.get("workspace", {}).get("resolved"),
            )
            proxy.set_event_callback(self._on_agent_event)
            self.agents[agent_type] = proxy
            status = "available" if proxy.is_available() else "NOT FOUND"
            self.logger.info(f"Agent {agent_key}: {status} ({proxy._executable or 'PATH'})")

    def _build_foreground_cli_launcher(self) -> ForegroundCliLauncher:
        return ForegroundCliLauncher(
            api_url=self._local_api_url(),
            token=self._foreground_cli_token(),
            python_executable=sys.executable,
        )

    def _foreground_cli_token(self) -> Optional[str]:
        if self.security.launch_token:
            return self.security.launch_token
        for grant in self.security.client_grants:
            if (
                grant.token
                and grant.client_kind == "desktop-ui"
                and grant.client_id == "local-agent-cli"
            ):
                return grant.token
        return None

    def _local_api_url(self) -> str:
        srv_cfg = self.cfg.get("server", {})
        host = srv_cfg.get("host", "127.0.0.1")
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
        port = srv_cfg.get("port", 8765)
        return f"ws://{host}:{port}"

    def _resolve_launch_workspace(self, workspace: Optional[str]) -> str:
        return str(resolve_workspace(
            cli_workspace=workspace,
            config_default=str(self.cfg.get("workspace", {}).get("resolved", ".")),
            start=Path.cwd(),
        ))

    def _init_app_store(self, cfg: Dict[str, Any]) -> Optional[SQLiteAppStore]:
        if not cfg.get("enabled", False):
            return None
        db_path = Path(cfg.get("app_store_path") or "data/app.db")
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parents[2] / db_path
        store = SQLiteAppStore.open(db_path)
        self.logger.info(f"SQLite app store opened at {db_path}")
        return store

    @staticmethod
    def _ensure_workspace_config(config: Dict[str, Any]) -> None:
        workspace_cfg = config.setdefault("workspace", {})
        if workspace_cfg.get("resolved"):
            return
        workspace_cfg["resolved"] = str(resolve_workspace(
            config_default=str(workspace_cfg.get("default", ".")),
            start=Path.cwd(),
        ))

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
            await self._cleanup_foreground_cli_sessions_for_queue(client_queue)
            await self._cleanup_virtual_devices_for_queue(client_queue)
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
        elif msg_type == "virtual_device_configure":
            await self._cmd_virtual_device_configure(msg, client_queue)
        elif msg_type == "virtual_input":
            await self._cmd_virtual_input(msg, client_queue)
        elif msg_type == "permission_response":
            await self._cmd_permission_response(msg, client_queue)
        elif msg_type == "claude_hook_event":
            if not await self._require_capability(client_queue, CAP_CLAUDE_HOOK):
                return
            if not await self._require_client_kind(client_queue, ClientKind.AGENT_HOOK):
                return
            await self._cmd_claude_hook_event(msg, client_queue)
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
            hook_identity = self._hook_identity_from_hello(
                token=msg.get("token"),
                client_kind=client_kind,
                client_id=client_id,
                requested_capabilities=requested_capabilities,
                is_loopback_peer=self._is_loopback_peer(peer),
            )
        except ValueError as exc:
            await self._send_error(queue, "AUTH_FAILED", str(exc))
            return
        if hook_identity is not None:
            self.client_identities[queue] = hook_identity
            await queue.put(json.dumps(HelloAck(
                client_kind=hook_identity.kind.value,
                client_id=hook_identity.client_id,
                capabilities=hook_identity.capabilities,
            ).to_dict(), ensure_ascii=False))
            return

        if not self.security.validate_token(msg.get("token"), self._is_loopback_peer(peer)):
            await self._send_error(queue, "AUTH_FAILED", "invalid or missing launch token")
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

    def _hook_identity_from_hello(
        self,
        token: Any,
        client_kind: str,
        client_id: str,
        requested_capabilities: List[Any],
        is_loopback_peer: bool,
    ) -> Optional[ClientIdentity]:
        if client_kind != ClientKind.AGENT_HOOK.value:
            return None
        if not isinstance(token, str) or not token:
            raise ValueError("invalid Claude hook token")
        if not is_loopback_peer:
            raise ValueError("Claude hook token is valid only on loopback clients")
        session_id = self._session_id_from_hook_client_id(client_id)
        expected = self.agent_commands.hook_token_for_session(session_id) if session_id else None
        if not (
            isinstance(expected, str)
            and expected
            and secrets.compare_digest(token, expected)
        ):
            raise ValueError("invalid Claude hook token")
        requested = {cap for cap in requested_capabilities if isinstance(cap, str)}
        capabilities = requested.intersection({CAP_CLAUDE_HOOK})
        return build_client_identity(client_kind, client_id, capabilities)

    @staticmethod
    def _session_id_from_hook_client_id(client_id: str) -> Optional[str]:
        prefix = "claude-code-hook:"
        if not isinstance(client_id, str) or not client_id.startswith(prefix):
            return None
        session_id = client_id[len(prefix):]
        return session_id or None

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

        self._track_foreground_cli_session(command, event, queue)

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

    def _track_foreground_cli_session(
        self,
        command: CommandEnvelope,
        event: Any,
        queue: asyncio.Queue,
    ) -> None:
        session_id = None
        payload = getattr(event, "payload", {})
        if isinstance(payload, dict):
            value = payload.get("session_id")
            if isinstance(value, str) and value:
                session_id = value
        if command.type == "agent.session.close":
            if session_id:
                owned = self._foreground_cli_session_owners.get(queue)
                if owned is not None:
                    owned.discard(session_id)
                    if not owned:
                        self._foreground_cli_session_owners.pop(queue, None)
            return
        if command.type not in {"agent.session.launch_or_resume", "agent.session.register_foreground"}:
            return
        if command.payload.get("launch_surface") != "foreground_cli":
            return
        if not session_id:
            return
        session = self.session_mgr.get(session_id)
        if session is not None and getattr(session, "launch_surface", None) != "foreground_cli":
            return
        self._foreground_cli_session_owners.setdefault(queue, set()).add(session_id)

    async def _cleanup_foreground_cli_sessions_for_queue(self, queue: asyncio.Queue) -> None:
        session_ids = self._foreground_cli_session_owners.pop(queue, set())
        for session_id in list(session_ids):
            session = self.session_mgr.get(session_id)
            if session is None or getattr(session, "launch_surface", None) != "foreground_cli":
                continue
            try:
                await self.agent_commands.close_session(CommandEnvelope(
                    type="agent.session.close",
                    source=CommandSource(kind="desktop-ui", client_id="foreground-cleanup"),
                    target={"session_id": session_id},
                    payload={},
                ))
            except Exception as exc:
                self.logger.warning(f"Foreground CLI session cleanup failed for {session_id}: {exc}")
                continue

    async def _cmd_virtual_device_configure(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        device_id = msg.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            await self._send_error(queue, "INVALID_VIRTUAL_DEVICE", "device_id is required")
            return
        profile_data = msg.get("profile")
        if not isinstance(profile_data, dict):
            await self._send_error(queue, "INVALID_PROFILE", "profile must be an object")
            return

        try:
            profile = profile_from_dict(profile_data)
        except (ProfileValidationError, ValueError) as exc:
            await self._send_error(queue, "INVALID_PROFILE", str(exc))
            return

        try:
            session = self._build_virtual_device_session(device_id, profile=profile, queue=queue)
            validate_profile(profile, device_capabilities=session.transport.get_capabilities())
        except (ProfileValidationError, ValueError) as exc:
            await self._send_error(queue, "INVALID_PROFILE", str(exc))
            return

        self._virtual_profiles[device_id] = profile
        if self.app_store is not None:
            try:
                self.app_store.profiles.upsert(profile)
                self.app_store.settings.set_active_profile_id(profile.id)
            except Exception as exc:
                await self._send_error(queue, "PROFILE_PERSIST_FAILED", str(exc))
                return

        self._sync_runtime_state()
        frames = await session.connect(snapshot=self.runtime.snapshot())
        self._drain_virtual_transport(device_id)
        self._sync_runtime_state()
        await queue.put(json.dumps({
            "type": "virtual_device_configured",
            "device_id": device_id,
            "active_profile_id": profile.id,
            "profile": self._profile_summary(profile),
            "response_frames": [self._device_frame_summary(frame) for frame in frames],
            "timestamp": int(time.time()),
        }, ensure_ascii=False))

    async def _cmd_virtual_input(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        client = self._client_for_queue(queue)
        if (
            client.kind != ClientKind.DEVICE_TRANSPORT
            and not client.has_capability(CAP_AGENT_LAUNCH)
            and not client.has_capability(CAP_PERMISSION_RESPOND)
            and not client.has_capability(CAP_SESSION_LIST)
        ):
            await self._send_error(
                queue,
                "CAPABILITY_DENIED",
                "virtual input requires a device-transport client identity or command capabilities",
            )
            return

        device_id = msg.get("device_id")
        key_id = msg.get("key_id")
        event_type = msg.get("event_type", "press")
        if not isinstance(device_id, str) or not device_id:
            await self._send_error(queue, "INVALID_VIRTUAL_INPUT", "device_id is required")
            return
        if not isinstance(key_id, str) or not key_id:
            await self._send_error(queue, "INVALID_VIRTUAL_INPUT", "key_id is required")
            return
        if not isinstance(event_type, str) or not event_type:
            await self._send_error(queue, "INVALID_VIRTUAL_INPUT", "event_type is required")
            return
        if client.kind == ClientKind.DEVICE_TRANSPORT and client.client_id != device_id:
            await self._send_error(
                queue,
                "DEVICE_ID_MISMATCH",
                "device-transport clients can send virtual input only for their own device_id",
            )
            return

        try:
            session = self._ensure_virtual_device_session(device_id, queue)
        except ValueError as exc:
            await self._send_error(queue, "VIRTUAL_DEVICE_NOT_CONFIGURED", str(exc))
            return

        try:
            payload = {
                "key_id": key_id,
                "event_type": event_type,
                "active_layers": self._string_list(msg.get("active_layers", [])),
                "modifiers": self._string_list(msg.get("modifiers", [])),
            }
        except ValueError as exc:
            await self._send_error(queue, "INVALID_VIRTUAL_INPUT", str(exc))
            return
        for optional_int in ("timestamp", "sequence"):
            if optional_int in msg:
                payload[optional_int] = msg[optional_int]
        generation = int(msg.get("generation", session.slot_mapper.generation))
        frame = session.codec.encode_message(
            frame_type="INPUT_EVENT",
            payload=payload,
            device_id=device_id,
            generation=generation,
        )

        start_seq = self.runtime.event_bus.last_seq
        self._virtual_session_owners[device_id] = queue
        try:
            result = await session.handle_frame(
                frame,
                command_dispatcher=lambda command: self._dispatch_virtual_device_command(command, queue),
            )
        except AgentLifecycleError as exc:
            await self._send_error(queue, exc.code, exc.message)
            self._broadcast_incremental_events(start_seq)
            return
        except ValueError as exc:
            await self._send_error(queue, "INVALID_VIRTUAL_INPUT", str(exc))
            self._broadcast_incremental_events(start_seq)
            return

        self._drain_virtual_transport(device_id)
        self._sync_runtime_state()
        command_result = result.command_result
        await queue.put(json.dumps({
            "type": "virtual_input_ack",
            "device_id": device_id,
            "key_id": key_id,
            "event_type": event_type,
            "input_event": (
                self._input_event_summary(command_result.input_event)
                if command_result and command_result.input_event
                else None
            ),
            "commands": [
                command.to_dict()
                for command in (command_result.commands if command_result else [])
            ],
            "events": [
                event.to_dict()
                for event in (command_result.events if command_result else [])
            ],
            "response_frames": [self._device_frame_summary(frame) for frame in result.response_frames],
            "timestamp": int(time.time()),
        }, ensure_ascii=False))
        self._broadcast_incremental_events(start_seq)

    async def _cmd_agent_launch(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        if not await self._require_capability(queue, CAP_AGENT_LAUNCH):
            return

        agent_str = "claude" if msg.get("agent", "claude") == "claude" else "codex"
        session_id = msg.get("session_id", "new")
        context = msg.get("context", "")
        payload = {"agent": agent_str, "context": context}
        workspace = msg.get("workspace")
        if isinstance(workspace, str) and workspace:
            payload["workspace"] = workspace
        command = self._legacy_agent_command(
            "agent.session.launch_or_resume",
            target={"session_id": session_id},
            payload=payload,
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
        instance_id = msg.get("instance_id")
        if isinstance(instance_id, str) and instance_id:
            target["instance_id"] = instance_id
        run_id = msg.get("run_id")
        if isinstance(run_id, str) and run_id:
            target["run_id"] = run_id
        command = CommandEnvelope(
            type="agent.permission.respond",
            source=self._command_source_for_queue(queue),
            target=target,
            payload={
                "approved": msg.get("approved", False),
                "decision": msg.get("decision"),
            },
        )
        await self._dispatch_permission_command(command, queue)

    async def _cmd_claude_hook_event(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        hook_input = msg.get("hook")
        if not isinstance(hook_input, dict):
            await self._send_error(queue, "INVALID_HOOK_EVENT", "hook must be an object")
            return
        session_id = msg.get("session_id") or hook_input.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            await self._send_error(queue, "INVALID_HOOK_EVENT", "session_id is required")
            return
        if not await self._validate_claude_hook_session(session_id, queue):
            return

        hook_event_name = hook_input.get("hook_event_name")
        if hook_event_name == "PermissionRequest":
            await self._cmd_claude_permission_hook(session_id, hook_input, queue)
            return

        self._emit_claude_hook_observation(session_id, hook_input)
        await queue.put(json.dumps({
            "type": "claude_hook_result",
            "session_id": session_id,
            "hook_event_name": hook_event_name,
            "hook_response": {},
            "timestamp": int(time.time()),
        }, ensure_ascii=False))

    async def _validate_claude_hook_session(self, session_id: str, queue: asyncio.Queue) -> bool:
        client = self._client_for_queue(queue)
        if client.client_id != f"claude-code-hook:{session_id}":
            await self._send_error(queue, "INVALID_HOOK_SESSION", "hook client is not bound to session_id")
            return False
        session = self.session_mgr.get(session_id)
        if session is None:
            await self._send_error(queue, "INVALID_HOOK_SESSION", "session_id is not registered")
            return False
        if session.agent != AgentType.CLAUDE:
            await self._send_error(queue, "INVALID_HOOK_SESSION", "session is not a Claude session")
            return False
        if (
            getattr(session, "launch_surface", None) != "foreground_cli"
            or getattr(session, "control_mode", None) != "native_cli"
        ):
            await self._send_error(queue, "INVALID_HOOK_SESSION", "session is not a native foreground Claude session")
            return False
        return True

    async def _cmd_claude_permission_hook(
        self,
        session_id: str,
        hook_input: Dict[str, Any],
        queue: asyncio.Queue,
    ) -> None:
        loop = asyncio.get_running_loop()
        request_id = "claude_hook_%s" % uuid_hash({
            "session_id": session_id,
            "hook_input": hook_input,
            "time": time.time(),
        })
        result_future = loop.create_future()
        delivered_future = loop.create_future()
        permission_event = self._claude_hook_permission_event(session_id, request_id, hook_input)
        pending_key = self._pending_permission_key(request_id, session_id, None, None)
        self._claude_hook_decisions[pending_key] = PendingClaudeHookDecision(
            request_id=request_id,
            session_id=session_id,
            hook_input=hook_input,
            created_at=time.time(),
            result_future=result_future,
            delivered_future=delivered_future,
        )
        self._on_agent_event(self.unifier.encode_device_message(permission_event))
        try:
            hook_response = await asyncio.wait_for(
                result_future,
                timeout=int(permission_event["timeout_sec"]),
            )
        except asyncio.TimeoutError:
            self.pending_permissions.pop(pending_key, None)
            self._claude_hook_decisions.pop(pending_key, None)
            hook_response = self._claude_hook_permission_response(
                hook_input,
                approved=False,
                decision="deny",
                message=f"Local API permission request {request_id} timed out.",
            )
        await queue.put(json.dumps({
            "type": "claude_hook_result",
            "session_id": session_id,
            "request_id": request_id,
            "hook_event_name": "PermissionRequest",
            "hook_response": hook_response,
            "timestamp": int(time.time()),
        }, ensure_ascii=False))
        if not delivered_future.done():
            delivered_future.set_result(True)

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
        instance_id = target.get("instance_id") or command.payload.get("instance_id")
        if not isinstance(instance_id, str):
            instance_id = None
        run_id = target.get("run_id") or command.payload.get("run_id")
        if not isinstance(run_id, str):
            run_id = None
        try:
            approved = self._parse_approved(command.payload.get("approved", False))
        except ValueError as exc:
            raise AgentLifecycleError("INVALID_PERMISSION_RESPONSE", str(exc)) from exc
        decision = self._permission_decision_from_payload(command.payload, approved)
        if decision == "always_allow":
            approved = True
        elif decision == "deny":
            approved = False

        self.logger.info(f"Permission {request_id}: {'APPROVED' if approved else 'DENIED'}")

        pending_key, pending = self._find_pending_permission(request_id, session_id, instance_id, run_id)
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
        if self._is_claude_hook_permission(pending):
            result = await self._deliver_claude_hook_permission(pending_key, pending, approved, decision)
        elif proxy:
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

        if pending.session_id and not self._is_terminal_session(pending.session_id):
            self.session_mgr.update_state(pending.session_id, AgentState.WORKING)
        self._append_permission_history(client, pending, approved, result)
        self._persist_session(pending.session_id)
        ack = {
            "type": "permission_ack",
            "request_id": pending.request_id,
            "session_id": pending.session_id,
            "approved": approved,
            "forwarded": bool(result.get("forwarded", False)),
            "evidence": result.get("evidence", {}),
        }
        if decision == "always_allow":
            ack["decision"] = decision
        return ack

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
            "sessions": [self._legacy_session_list_record(s) for s in sessions],
            "timestamp": int(time.time()),
        }
        await queue.put(json.dumps(payload, ensure_ascii=False))

    async def _cmd_heartbeat(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        # Optional: track device liveness, respond with server heartbeat
        pass

    def _build_virtual_device_session(
        self,
        device_id: str,
        *,
        profile: Optional[Profile] = None,
        queue: asyncio.Queue,
    ) -> VirtualDeviceSession:
        active_profile = profile or self._active_virtual_profile(device_id)
        device_family = active_profile.target_device_family if active_profile else "simulated"
        codec = DeviceProtocolCodec()
        mapper = DeviceSlotMapper(device_id=device_id)
        transport = SimulatedTransport(
            device_id=device_id,
            device_family=device_family,
            supported_profile_features={"agent_bindings", "layers", "screen", "device"},
            supported_screen_widgets={"permission_list", "session_list", "tool_status"},
            supports_agent_slots=True,
            supports_config_sync=True,
        )
        adapter = VirtualDeviceCommandAdapter(
            active_profile_provider=self._active_virtual_profile,
            router=self.runtime.command_router,
            codec=codec,
            slot_mapper=mapper,
        )
        projection_runtime = DeviceProjectionRuntime(
            device_id=device_id,
            codec=codec,
            slot_mapper=mapper,
            active_profile_provider=lambda current_device_id: self._profile_summary(
                self._active_virtual_profile(current_device_id)
            ),
        )
        session = VirtualDeviceSession(
            device_id=device_id,
            transport=transport,
            codec=codec,
            slot_mapper=mapper,
            command_adapter=adapter,
            projection_runtime=projection_runtime,
            device_manager=self.runtime.device_manager,
        )
        self._virtual_sessions[device_id] = session
        self._virtual_transports[device_id] = transport
        self._virtual_session_owners[device_id] = queue
        return session

    def _ensure_virtual_device_session(
        self,
        device_id: str,
        queue: asyncio.Queue,
    ) -> VirtualDeviceSession:
        session = self._virtual_sessions.get(device_id)
        if session is not None:
            return session
        if self._active_virtual_profile(device_id) is None:
            raise ValueError(f"virtual device is not configured: {device_id}")
        return self._build_virtual_device_session(device_id, queue=queue)

    async def _dispatch_virtual_device_command(
        self,
        command: CommandEnvelope,
        queue: asyncio.Queue,
    ):
        client = self._client_for_queue(queue)
        required_capability = self._capability_for_command(command.type)
        if required_capability and not client.has_capability(required_capability):
            raise AgentLifecycleError(
                "CAPABILITY_DENIED",
                f"client lacks capability: {required_capability}",
            )
        command = self._command_from_virtual_input_sender(command, client)

        self._sync_runtime_state()
        context_token = None
        if command.type == "agent.permission.respond":
            context_token = _permission_client_context.set(client)
        try:
            return await self.runtime.command_router.dispatch_async(command)
        finally:
            if context_token is not None:
                _permission_client_context.reset(context_token)

    async def _cleanup_virtual_devices_for_queue(self, queue: asyncio.Queue) -> None:
        device_ids = [
            device_id
            for device_id, owner in self._virtual_session_owners.items()
            if owner is queue
        ]
        for device_id in device_ids:
            transport = self._virtual_transports.pop(device_id, None)
            if transport is not None:
                self._drain_virtual_transport(device_id, transport=transport)
                await transport.close()
            self._virtual_sessions.pop(device_id, None)
            self._virtual_session_owners.pop(device_id, None)
            self.runtime.device_manager.unregister_transport(device_id)
            self.runtime.state_store.devices.pop(device_id, None)

    def _drain_virtual_transport(
        self,
        device_id: str,
        *,
        transport: Optional[SimulatedTransport] = None,
    ) -> None:
        current = transport or self._virtual_transports.get(device_id)
        if current is None:
            return
        clear = getattr(current, "clear_queued_frames", None)
        if clear is not None:
            clear()

    def _active_virtual_profile(self, device_id: str) -> Optional[Profile]:
        profile = self._virtual_profiles.get(device_id)
        if profile is not None:
            return profile
        if self.app_store is None:
            return None
        active_profile_id = self.app_store.settings.get_active_profile_id()
        if not active_profile_id:
            return None
        return self.app_store.profiles.get(active_profile_id)

    def _profiles_snapshot(self) -> Dict[str, Any]:
        active_by_device = {
            device_id: profile.id
            for device_id, profile in self._virtual_profiles.items()
        }
        active_profile = None
        if self.app_store is not None:
            active_profile_id = self.app_store.settings.get_active_profile_id()
            if active_profile_id:
                active_profile = self.app_store.profiles.get(active_profile_id)
        if active_profile is None and self._virtual_profiles:
            active_profile = next(iter(self._virtual_profiles.values()))
        active_profile_id = active_profile.id if active_profile else None
        profiles = {
            profile.id: self._profile_summary(profile)
            for profile in self._virtual_profiles.values()
        }
        if self.app_store is not None:
            for profile in self.app_store.profiles.list():
                profiles[profile.id] = self._profile_summary(profile)
        return {
            "active_profile_id": active_profile_id,
            "active_profile": self._profile_summary(active_profile),
            "active_profile_by_device": active_by_device,
            "profiles": profiles,
        }

    @staticmethod
    def _profile_summary(profile: Optional[Profile]) -> Optional[Dict[str, Any]]:
        if profile is None:
            return None
        return {
            "id": profile.id,
            "name": profile.name,
            "version": profile.version,
            "target_device_family": profile.target_device_family,
        }

    @staticmethod
    def _legacy_session_list_record(session: Session) -> Dict[str, Any]:
        return {
            "session_id": session.session_id,
            "agent": session.agent.value,
            "state": session.state.value,
            "created_at": int(session.created_at),
            "updated_at": int(session.updated_at),
        }

    def _device_frame_summary(self, frame) -> Dict[str, Any]:
        payload = {}
        try:
            payload = DeviceProtocolCodec().decode_message(frame)
        except Exception:
            payload = {"payload_size": len(frame.payload)}
        return {
            "frame_type": frame.frame_type,
            "device_id": frame.device_id,
            "generation": frame.generation,
            "payload": payload,
        }

    @staticmethod
    def _input_event_summary(event) -> Dict[str, Any]:
        return {
            "device_id": event.device_id,
            "key_id": event.key_id,
            "event_type": event.event_type,
            "active_layers": list(event.active_layers),
            "modifiers": list(event.modifiers),
            "timestamp": event.timestamp,
            "sequence": event.sequence,
        }

    @staticmethod
    def _string_list(value: Any) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("active_layers and modifiers must be arrays")
        result = []
        for item in value:
            if not isinstance(item, str) or not item:
                raise ValueError("active_layers and modifiers must contain only strings")
            result.append(item)
        return result

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
        request_id_counts: Dict[str, int] = {}
        for pending in self.pending_permissions.values():
            request_id_counts[pending.request_id] = request_id_counts.get(pending.request_id, 0) + 1
        for pending_key, pending in self.pending_permissions.items():
            permission_record = self._pending_permission_record(pending, sessions, runs)
            self._ensure_permission_run_ancestry(runs, permission_record, sessions)
            if request_id_counts.get(pending.request_id, 0) == 1:
                projection_key = pending.request_id
            else:
                projection_key = self._projected_permission_key(pending_key)
            permissions[projection_key] = permission_record

        store.sessions = sessions
        store.runs = runs
        store.permissions = permissions
        devices = {
            device_id: dict(record)
            for device_id, record in store.devices.items()
        }
        for device_id in list(self.runtime.device_manager.list_records()):
            self.runtime.device_manager.refresh_status(device_id)
        for device_id, record in self.runtime.device_manager.list_records().items():
            current = dict(devices.get(device_id, {}))
            current.update(record.to_dict())
            active_tool_id = store.active_tools.get(device_id)
            if active_tool_id:
                current["active_tool_id"] = active_tool_id
            devices[device_id] = current
        store.devices = devices
        store.profiles = self._profiles_snapshot()

    def _pending_permission_record(
        self,
        pending: PendingPermission,
        sessions: Dict[str, Dict[str, Any]],
        runs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        provider_id = pending.agent.value
        record = {
            "permission_id": pending.request_id,
            "request_id": pending.request_id,
            "provider_id": provider_id,
            "agent": provider_id,
            "timeout_sec": pending.timeout_sec,
            "risk_level": pending.risk_level.value,
            "tool": pending.tool,
            "description": pending.description,
            "priority": pending.priority,
        }
        run_id = self._pending_run_id(pending)
        if run_id:
            record["run_id"] = run_id
        run_record = runs.get(run_id, {}) if run_id else {}
        run_record_for_derivation = (
            run_record
            if not self._permission_parent_conflicts_with_run(pending, run_record)
            else {}
        )
        session_id = self._pending_permission_session_id(pending, run_record_for_derivation)
        if session_id:
            record["session_id"] = session_id
        session_record = sessions.get(session_id, {}) if session_id else {}
        instance_id = self._pending_permission_instance_id(
            pending,
            session_record,
            run_record_for_derivation,
        )
        if instance_id:
            record["instance_id"] = instance_id
        return record

    @staticmethod
    def _permission_parent_conflicts_with_run(
        pending: PendingPermission,
        run_record: Dict[str, Any],
    ) -> bool:
        if not run_record:
            return False
        for field in ("session_id", "instance_id"):
            pending_value = getattr(pending, field)
            run_value = run_record.get(field)
            if pending_value and run_value and pending_value != run_value:
                return True
        return False

    @staticmethod
    def _pending_permission_session_id(
        pending: PendingPermission,
        run_record: Dict[str, Any],
    ) -> Optional[str]:
        if pending.session_id:
            return pending.session_id
        session_id = run_record.get("session_id")
        if isinstance(session_id, str) and session_id:
            return session_id
        return None

    def _pending_permission_instance_id(
        self,
        pending: PendingPermission,
        session_record: Dict[str, Any],
        run_record: Dict[str, Any],
    ) -> Optional[str]:
        if pending.instance_id:
            return pending.instance_id
        instance_id = session_record.get("instance_id")
        if isinstance(instance_id, str) and instance_id:
            return instance_id
        instance_id = run_record.get("instance_id")
        if isinstance(instance_id, str) and instance_id:
            return instance_id
        return None

    def _ensure_permission_run_ancestry(
        self,
        runs: Dict[str, Dict[str, Any]],
        permission_record: Dict[str, Any],
        sessions: Dict[str, Dict[str, Any]],
    ) -> None:
        run_id = permission_record.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return
        existing_run = runs.get(run_id)
        if existing_run:
            run_record = dict(existing_run)
            run_record.setdefault("run_id", run_id)
            for field in ("session_id", "instance_id", "provider_id", "agent"):
                if run_record.get(field):
                    continue
                value = permission_record.get(field)
                if value:
                    run_record[field] = value
            session_id = run_record.get("session_id")
            session_record = sessions.get(session_id, {}) if isinstance(session_id, str) else {}
            run_record.setdefault("state", session_record.get("state", AgentState.WAITING_PERMISSION.value))
            runs[run_id] = run_record
            return

        run_record = {"run_id": run_id}
        for field in ("session_id", "instance_id", "provider_id", "agent"):
            value = permission_record.get(field)
            if value:
                run_record[field] = value
        session_id = run_record.get("session_id")
        session_record = sessions.get(session_id, {}) if isinstance(session_id, str) else {}
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
            if pending.session_id and run_id:
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
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = None
        instance_id = event.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            instance_id = None
        run_id = event.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            run_id = None
        agent = self._agent_from_string(event.get("agent", ""))
        if not request_id or not agent:
            return

        permission_key = self._pending_permission_key(request_id, session_id, instance_id, run_id)
        self.pending_permissions[permission_key] = PendingPermission(
            request_id=request_id,
            session_id=session_id,
            instance_id=instance_id,
            agent=agent,
            created_at=time.time(),
            timeout_sec=int(event.get("timeout_sec", self.cfg["unifier"].get("permission_timeout_sec", 30))),
            risk_level=self._risk_from_event(event.get("risk_level")),
            tool=str(event.get("tool", "unknown")),
            description=str(event.get("description", "")),
            run_id=run_id,
            priority=self._priority_from_event(event.get("priority")),
            native=event.get("native") if isinstance(event.get("native"), dict) else None,
        )
        if session_id:
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

    def _is_terminal_session(self, session_id: Optional[str]) -> bool:
        if not session_id:
            return False
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

    def _persist_session(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
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

    def _emit_claude_hook_observation(self, session_id: str, hook_input: Dict[str, Any]) -> None:
        payload = {
            "type": "agent_hook_event",
            "session_id": session_id,
            "agent": AgentType.CLAUDE.value,
            "hook_event_name": hook_input.get("hook_event_name"),
            "tool": hook_input.get("tool_name"),
            "hook_input": hook_input,
            "timestamp": int(time.time()),
        }
        self._on_agent_event(self.unifier.encode_device_message(payload))

    def _claude_hook_permission_event(
        self,
        session_id: str,
        request_id: str,
        hook_input: Dict[str, Any],
    ) -> Dict[str, Any]:
        tool_name = str(hook_input.get("tool_name") or "unknown")
        tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
        description = self._describe_claude_hook_tool(tool_name, tool_input)
        return {
            "type": "permission_request",
            "request_id": request_id,
            "session_id": session_id,
            "agent": AgentType.CLAUDE.value,
            "tool": tool_name,
            "description": description,
            "risk_level": self._risk_for_claude_hook_tool(tool_name),
            "timeout_sec": int(self.cfg["unifier"].get("permission_timeout_sec", 30)),
            "native": {
                "adapter": "claude_code_hook",
                "native_channel": "PermissionRequest",
                "hook_event_name": "PermissionRequest",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "permission_suggestions": hook_input.get("permission_suggestions", []),
            },
        }

    @staticmethod
    def _describe_claude_hook_tool(tool_name: str, tool_input: Dict[str, Any]) -> str:
        if tool_name == "Bash":
            return str(tool_input.get("description") or tool_input.get("command") or "Claude requests shell access.")
        for key in ("file_path", "url", "query"):
            value = tool_input.get(key)
            if value:
                return f"Claude requests {tool_name}: {value}"
        return f"Claude requests permission to use {tool_name}."

    @staticmethod
    def _risk_for_claude_hook_tool(tool_name: str) -> str:
        if tool_name in {"Bash", "Write, Edit", "Write", "Edit", "MultiEdit"}:
            return RiskLevel.HIGH.value
        if tool_name in {"Read", "Glob", "Grep", "LS"}:
            return RiskLevel.LOW.value
        return RiskLevel.MEDIUM.value

    @staticmethod
    def _is_claude_hook_permission(pending: PendingPermission) -> bool:
        native = pending.native or {}
        return native.get("adapter") == "claude_code_hook"

    async def _deliver_claude_hook_permission(
        self,
        pending_key: str,
        pending: PendingPermission,
        approved: bool,
        decision: str,
    ) -> Dict[str, Any]:
        waiter = self._claude_hook_decisions.get(pending_key)
        if waiter is None:
            return {
                "accepted": True,
                "forwarded": False,
                "evidence": {
                    "adapter": "claude_code_hook",
                    "reason": "hook_waiter_not_registered",
                    "session_id": pending.session_id,
                    "request_id": pending.request_id,
                },
            }
        if not waiter.result_future.done():
            waiter.result_future.set_result(self._claude_hook_permission_response(
                waiter.hook_input,
                approved=approved,
                decision=decision,
            ))
        try:
            await asyncio.wait_for(waiter.delivered_future, timeout=10.0)
        except asyncio.TimeoutError:
            return {
                "accepted": True,
                "forwarded": False,
                "evidence": {
                    "adapter": "claude_code_hook",
                    "reason": "hook_response_delivery_timeout",
                    "session_id": pending.session_id,
                    "request_id": pending.request_id,
                },
            }
        finally:
            self._claude_hook_decisions.pop(pending_key, None)
        return {
            "accepted": True,
            "forwarded": True,
            "evidence": {
                "adapter": "claude_code_hook",
                "native_channel": "PermissionRequest",
                "decision_delivered": True,
                "response_written": True,
                "session_id": pending.session_id,
                "request_id": pending.request_id,
                "decision": decision,
                "age_ms": int((time.time() - waiter.created_at) * 1000),
            },
        }

    @staticmethod
    def _claude_hook_permission_response(
        hook_input: Dict[str, Any],
        approved: bool,
        decision: str,
        message: str = "",
    ) -> Dict[str, Any]:
        hook_decision: Dict[str, Any]
        if approved:
            hook_decision = {"behavior": "allow"}
            if decision == "always_allow":
                suggestions = hook_input.get("permission_suggestions")
                if isinstance(suggestions, list) and suggestions:
                    hook_decision["updatedPermissions"] = suggestions
        else:
            hook_decision = {
                "behavior": "deny",
                "message": message or "Denied by Local API permission response.",
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": hook_decision,
            }
        }

    @staticmethod
    def _permission_decision_from_payload(payload: Dict[str, Any], approved: bool) -> str:
        value = payload.get("decision")
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_")
            if normalized in {"approve", "approved", "allow"}:
                return "approve"
            if normalized in {"always_allow", "allow_always", "approve_always", "dont_ask"}:
                return "always_allow"
            if normalized in {"deny", "denied", "decline"}:
                return "deny"
        return "approve" if approved else "deny"

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
    def _priority_from_event(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return 0

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

    def _pending_permission_key(
        self,
        request_id: str,
        session_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        for key, pending in self.pending_permissions.items():
            if (
                pending.request_id == request_id
                and pending.session_id == session_id
                and pending.instance_id == instance_id
                and self._pending_run_id(pending) == run_id
            ):
                return key
        return "|".join((
            "permission",
            f"request={request_id}",
            f"session={session_id or ''}",
            f"instance={instance_id or ''}",
            f"run={run_id or ''}",
        ))

    @staticmethod
    def _projected_permission_key(pending_key: str) -> str:
        digest = hashlib.sha256(pending_key.encode("utf-8")).hexdigest()[:16]
        return f"permission:{digest}"

    def _find_pending_permission(
        self,
        request_id: str,
        session_id: Any = None,
        instance_id: Any = None,
        run_id: Any = None,
    ) -> Tuple[str, Optional[PendingPermission]]:
        has_run = isinstance(run_id, str) and bool(run_id)
        has_session = isinstance(session_id, str) and bool(session_id)
        has_instance = isinstance(instance_id, str) and bool(instance_id)

        matches = [
            (key, pending)
            for key, pending in self.pending_permissions.items()
            if pending.request_id == request_id
        ]

        if has_run:
            matches = [
                (key, pending)
                for key, pending in matches
                if self._pending_run_id(pending) == run_id
            ]
        elif has_session or has_instance:
            matches = [
                (key, pending)
                for key, pending in matches
                if not self._pending_run_id(pending)
            ]

        if has_session:
            if has_run:
                exact_session_matches = [
                    (key, pending)
                    for key, pending in matches
                    if pending.session_id == session_id
                ]
                matches = exact_session_matches or [
                    (key, pending)
                    for key, pending in matches
                    if pending.session_id is None
                ]
            else:
                matches = [
                    (key, pending)
                    for key, pending in matches
                    if pending.session_id == session_id
                ]
        elif has_instance:
            matches = [
                (key, pending)
                for key, pending in matches
                if pending.session_id is None
            ]

        if has_instance:
            if has_run or has_session:
                exact_instance_matches = [
                    (key, pending)
                    for key, pending in matches
                    if pending.instance_id == instance_id
                ]
                matches = exact_instance_matches or [
                    (key, pending)
                    for key, pending in matches
                    if pending.instance_id is None
                ]
            else:
                matches = [
                    (key, pending)
                    for key, pending in matches
                    if pending.instance_id == instance_id
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

    async def _require_client_kind(self, queue: asyncio.Queue, kind: ClientKind) -> bool:
        client = self._client_for_queue(queue)
        if client.kind == kind:
            return True
        await self._send_error(queue, "CAPABILITY_DENIED", f"client must be {kind.value}")
        return False

    @staticmethod
    def _capability_for_command(command_type: str) -> Optional[str]:
        return {
            "agent.session.launch_or_resume": CAP_AGENT_LAUNCH,
            "agent.session.register_foreground": CAP_AGENT_LAUNCH,
            "agent.cli.launch_foreground": CAP_AGENT_LAUNCH,
            "agent.session.input": CAP_AGENT_LAUNCH,
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

    @staticmethod
    def _command_from_virtual_input_sender(
        command: CommandEnvelope,
        client: ClientIdentity,
    ) -> CommandEnvelope:
        device_id = command.source.device_id
        if device_id is None and client.kind == ClientKind.DEVICE_TRANSPORT:
            device_id = client.client_id
        return CommandEnvelope(
            type=command.type,
            source=CommandSource(
                kind=client.kind.value,
                client_id=client.client_id,
                device_id=device_id,
            ),
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

def _configure_windows_event_loop_policy() -> None:
    if sys.platform != "win32":
        return
    policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_cls is None:
        return
    if isinstance(asyncio.get_event_loop_policy(), policy_cls):
        return
    asyncio.set_event_loop_policy(policy_cls())


def main():
    parser = argparse.ArgumentParser(description="AI Keyboard Local Core Service MVP")
    parser.add_argument("--config", "-c", default="config.yaml", help="Path to YAML config file")
    parser.add_argument("--workspace", default=None, help="Project workspace for Codex/Claude launches")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent / config_path

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    workspace_cfg = config.setdefault("workspace", {})
    workspace_cfg["resolved"] = str(resolve_workspace(
        cli_workspace=args.workspace,
        config_default=str(workspace_cfg.get("default", ".")),
        start=Path.cwd(),
    ))

    server = LocalCoreServiceMVP(config)
    _configure_windows_event_loop_policy()
    asyncio.run(server.start())


BridgeServer = LocalCoreServiceMVP


if __name__ == "__main__":
    main()
