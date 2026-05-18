#!/usr/bin/env python3
"""
Bridge Server — WebSocket Main Service

Accepts device connections over WebSocket, dispatches unified events,
and proxies to Codex / Claude processes via AgentProxy.

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
from pathlib import Path
from typing import Any, Dict, Optional, Set

import yaml

from agent_proxy import AgentProxy
from protocol_unifier import ProtocolUnifier
from session_manager import AgentType, AgentState, SessionManager


class BridgeServer:
    """WebSocket server bridging CH32H417 devices to AI agents."""

    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        self._setup_logging()

        # Sub-components
        self.session_mgr = SessionManager(
            max_sessions=config["session"]["cache_size"],
            persist_dir=config["session"].get("persist_dir"),
            cleanup_after_hours=config["session"]["cleanup_after_hours"],
        )
        self.unifier = ProtocolUnifier(
            max_delta_size=config["unifier"]["max_delta_size"],
        )

        # Agent proxies
        self.agents: Dict[AgentType, AgentProxy] = {}
        self._init_agents()

        # Connection state
        self.connected_devices: Set[asyncio.Queue] = set()
        self._server = None
        self._shutdown_event = asyncio.Event()

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
        self.logger = logging.getLogger("BridgeServer")

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

    # ------------------------------------------------------------------ #
    #  WebSocket handlers
    # ------------------------------------------------------------------ #

    async def _handle_device(self, websocket) -> None:
        """Handle a single device WebSocket connection."""
        device_queue = asyncio.Queue()
        self.connected_devices.add(device_queue)
        peer = websocket.remote_address
        self.logger.info(f"Device connected: {peer}")

        try:
            # Start a task to forward messages from queue to websocket
            send_task = asyncio.create_task(self._device_sender(websocket, device_queue))

            async for raw_message in websocket:
                try:
                    msg = json.loads(raw_message)
                except json.JSONDecodeError:
                    self.logger.warning(f"Invalid JSON from {peer}: {raw_message[:200]}")
                    await self._send_error(device_queue, "INVALID_JSON", "Message is not valid JSON")
                    continue

                await self._handle_device_message(msg, device_queue)

            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                pass

        except Exception as exc:
            self.logger.warning(f"Device {peer} error: {exc}")
        finally:
            self.connected_devices.discard(device_queue)
            self.logger.info(f"Device disconnected: {peer}")

    async def _device_sender(self, websocket, queue: asyncio.Queue) -> None:
        """Coroutine that pulls from queue and sends to websocket."""
        while True:
            msg = await queue.get()
            if msg is None:
                break
            try:
                await websocket.send(msg)
            except Exception:
                break

    async def _handle_device_message(self, msg: Dict[str, Any], device_queue: asyncio.Queue) -> None:
        """Process a single message from device."""
        msg_type = msg.get("type", "")
        self.logger.debug(f"Device msg: {msg_type}")

        if msg_type == "agent_launch":
            await self._cmd_agent_launch(msg, device_queue)
        elif msg_type == "permission_response":
            await self._cmd_permission_response(msg, device_queue)
        elif msg_type == "interrupt":
            await self._cmd_interrupt(msg, device_queue)
        elif msg_type == "list_sessions":
            await self._cmd_list_sessions(msg, device_queue)
        elif msg_type == "heartbeat":
            await self._cmd_heartbeat(msg, device_queue)
        else:
            await self._send_error(device_queue, "UNKNOWN_TYPE", f"Unknown message type: {msg_type}")

    # ------------------------------------------------------------------ #
    #  Device commands
    # ------------------------------------------------------------------ #

    async def _cmd_agent_launch(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        agent_str = msg.get("agent", "claude")
        session_id = msg.get("session_id", "new")
        context = msg.get("context", "")

        agent_type = AgentType.CLAUDE if agent_str == "claude" else AgentType.CODEX
        proxy = self.agents.get(agent_type)

        if not proxy:
            await self._send_error(queue, "AGENT_NOT_FOUND", f"{agent_str} is not configured")
            return
        if not proxy.is_available():
            await self._send_error(queue, "AGENT_UNAVAILABLE", f"{agent_str} executable not found")
            return

        if session_id == "new":
            sess = self.session_mgr.create(agent_type)
            session_id = sess.session_id
            try:
                await proxy.launch(session_id, context)
            except Exception as exc:
                self.logger.error(f"Launch failed: {exc}")
                await self._send_error(queue, "LAUNCH_FAILED", str(exc))
                return
        else:
            sess = self.session_mgr.get(session_id)
            if not sess:
                await self._send_error(queue, "SESSION_NOT_FOUND", f"Session {session_id} does not exist")
                return
            await proxy.resume(session_id)

        # Acknowledge launch
        ack = self.unifier.encode_device_message({
            "type": "task_update",
            "session_id": session_id,
            "agent": agent_type.value,
            "state": AgentState.SUBMITTED.value,
        })
        await queue.put(ack)

    async def _cmd_permission_response(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        request_id = msg.get("request_id", "")
        approved = msg.get("approved", False)
        self.logger.info(f"Permission {request_id}: {'APPROVED' if approved else 'DENIED'}")
        # Forward to the appropriate agent proxy
        # In a full implementation, map request_id -> session_id -> proxy
        # For MVP, broadcast to all proxies or store pending request map
        await self._send_error(queue, "NOT_IMPLEMENTED", "Direct permission forwarding requires request tracking")

    async def _cmd_interrupt(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
        session_id = msg.get("session_id", "")
        sess = self.session_mgr.get(session_id)
        if not sess:
            await self._send_error(queue, "SESSION_NOT_FOUND", f"Session {session_id} not found")
            return

        proxy = self.agents.get(sess.agent)
        if proxy:
            await proxy.send_interrupt(session_id)
            self.session_mgr.update_state(session_id, AgentState.CANCELLED)

    async def _cmd_list_sessions(self, msg: Dict[str, Any], queue: asyncio.Queue) -> None:
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

    def _on_agent_event(self, json_line: str) -> None:
        """Called by AgentProxy whenever a unified event is produced."""
        # Broadcast to all connected devices
        for queue in list(self.connected_devices):
            try:
                queue.put_nowait(json_line)
            except Exception:
                pass

    async def _send_error(self, queue: asyncio.Queue, code: str, message: str) -> None:
        payload = {
            "type": "error",
            "code": code,
            "message": message,
            "timestamp": int(time.time()),
        }
        await queue.put(json.dumps(payload, ensure_ascii=False))

    # ------------------------------------------------------------------ #
    #  Server lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        srv_cfg = self.cfg["server"]
        host = srv_cfg.get("host", "0.0.0.0")
        port = srv_cfg.get("port", 8765)

        # Import websockets here to allow graceful degradation if not installed
        try:
            import websockets
        except ImportError:
            self.logger.error("Package 'websockets' is required. Install: pip install websockets")
            sys.exit(1)

        self._server = await websockets.serve(self._handle_device, host, port)
        self.logger.info(f"Bridge Server listening on ws://{host}:{port}")

        # Graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

        await self._shutdown_event.wait()
        self.logger.info("Shutting down...")
        self._server.close()
        await self._server.wait_closed()

        # Cleanup agent processes
        for proxy in self.agents.values():
            for session_id in list(proxy._processes.keys()):
                await proxy.terminate(session_id)

    def _request_shutdown(self) -> None:
        self._shutdown_event.set()


# ------------------------------------------------------------------ #
#  CLI entrypoint
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(description="CH32H417 AI Terminal — Bridge Server")
    parser.add_argument("--config", "-c", default="config.yaml", help="Path to YAML config file")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent / config_path

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    server = BridgeServer(config)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
