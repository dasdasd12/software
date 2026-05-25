import asyncio
import json
from pathlib import Path
import sys

import websockets


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from server import LocalCoreServiceMVP  # noqa: E402
from session_manager import AgentState, AgentType  # noqa: E402


class FakeProxy:
    def __init__(self, service, agent_type):
        self.service = service
        self.agent_type = agent_type
        self.launched = []
        self.interrupted = []
        self.permission_responses = []

    def is_available(self):
        return True

    async def launch(self, session_id, context=""):
        self.launched.append((session_id, context))
        asyncio.create_task(self._emit_events(session_id))
        return self.service.session_mgr.get(session_id)

    async def resume(self, session_id):
        return await self.launch(session_id, "")

    async def send_interrupt(self, session_id):
        self.interrupted.append(session_id)
        return True

    async def handle_permission_response(self, session_id, request_id, approved):
        self.permission_responses.append((session_id, request_id, approved))
        return {
            "accepted": True,
            "forwarded": False,
            "evidence": {
                "adapter": "fake",
                "session_id": session_id,
                "request_id": request_id,
                "approved": approved,
            },
        }

    async def _emit_events(self, session_id):
        await asyncio.sleep(0.01)
        self.service._on_agent_event(self.service.unifier.encode_device_message({
            "type": "agent_message_delta",
            "session_id": session_id,
            "agent": self.agent_type.value,
            "delta": "hello from fake agent",
        }))
        self.service._on_agent_event(self.service.unifier.encode_device_message({
            "type": "task_completed",
            "session_id": session_id,
            "agent": self.agent_type.value,
            "summary": "fake complete",
        }))


def make_service():
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
    }
    return LocalCoreServiceMVP(config)


async def with_local_api(run_client):
    service = make_service()
    service.agents[AgentType.CODEX] = FakeProxy(service, AgentType.CODEX)
    service.agents[AgentType.CLAUDE] = FakeProxy(service, AgentType.CLAUDE)

    ws_server = await websockets.serve(service._handle_local_api_client, "127.0.0.1", 0)
    port = ws_server.sockets[0].getsockname()[1]
    uri = f"ws://127.0.0.1:{port}"
    try:
        return await run_client(service, uri)
    finally:
        ws_server.close()
        await ws_server.wait_closed()


async def recv_json(ws, timeout=1.0):
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


async def wait_until(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for condition")


def test_invalid_json_returns_error():
    async def run_client(service, uri):
        async with websockets.connect(uri) as ws:
            await ws.send("{bad json")
            payload = await recv_json(ws)
            assert payload["type"] == "error"
            assert payload["code"] == "INVALID_JSON"

    asyncio.run(with_local_api(run_client))


def test_unknown_type_returns_error():
    async def run_client(service, uri):
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "not_a_real_message"}))
            payload = await recv_json(ws)
            assert payload["type"] == "error"
            assert payload["code"] == "UNKNOWN_TYPE"

    asyncio.run(with_local_api(run_client))


def test_list_sessions_over_local_api_returns_protocol_fields():
    async def run_client(service, uri):
        service.session_mgr.create(AgentType.CODEX)
        service.session_mgr.create(AgentType.CLAUDE)
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "list_sessions", "agent": "all"}))
            payload = await recv_json(ws)
            assert payload["type"] == "session_list"
            assert len(payload["sessions"]) == 2
            for session in payload["sessions"]:
                assert set(session.keys()) == {"session_id", "agent", "state", "created_at", "updated_at"}

    asyncio.run(with_local_api(run_client))


def test_structured_snapshot_command_returns_snapshot_with_runtime_state():
    async def run_client(service, uri):
        session = service.session_mgr.create(AgentType.CODEX)
        service._on_agent_event(service.unifier.encode_device_message({
            "type": "permission_request",
            "request_id": "req_snapshot",
            "session_id": session.session_id,
            "agent": "codex",
            "tool": "shell",
            "description": "Run command",
            "timeout_sec": 30,
        }))

        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_snapshot",
                    "type": "system.snapshot.request",
                    "source": {"kind": "test-client", "client_id": "pytest"},
                    "payload": {},
                },
            }))
            payload = await recv_json(ws)

            assert payload["type"] == "snapshot"
            assert payload["command_id"] == "cmd_snapshot"
            snapshot = payload["snapshot"]
            assert session.session_id in snapshot["sessions"]
            assert "codex-default" in snapshot["agents"]
            assert snapshot["agents"]["codex-default"] == {
                "instance_id": "codex-default",
                "provider_id": "codex",
                "agent": "codex",
                "label": "Codex",
                "status": "available",
            }
            assert snapshot["sessions"][session.session_id]["agent"] == "codex"
            assert snapshot["sessions"][session.session_id]["provider_id"] == "codex"
            assert snapshot["sessions"][session.session_id]["instance_id"] == "codex-default"
            active_run_id = snapshot["sessions"][session.session_id]["active_run_id"]
            assert active_run_id in snapshot["runs"]
            assert snapshot["runs"][active_run_id] == {
                "run_id": active_run_id,
                "session_id": session.session_id,
                "instance_id": "codex-default",
                "provider_id": "codex",
                "agent": "codex",
                "state": "WAITING_PERMISSION",
            }
            assert snapshot["permissions"][0]["request_id"] == "req_snapshot"
            assert snapshot["permissions"][0]["session_id"] == session.session_id
            assert snapshot["permissions"][0]["agent"] == "codex"
            assert snapshot["permissions"][0]["timeout_sec"] == 30
            assert set(snapshot.keys()) >= {
                "agents", "sessions", "runs", "permissions", "devices", "profiles", "notifications"
            }

    asyncio.run(with_local_api(run_client))


def test_structured_command_publishes_event_message_to_connected_clients():
    async def run_client(service, uri):
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_note",
                    "type": "notification.create",
                    "source": {"kind": "test-client", "client_id": "pytest"},
                    "payload": {
                        "notification_id": "note_ws",
                        "level": "info",
                        "message": "Ready",
                    },
                },
            }))
            payload = await recv_json(ws)

            assert payload["type"] == "event"
            event = payload["event"]
            assert event["type"] == "notification.created"
            assert event["seq"] == 1
            assert event["payload"] == {
                "notification_id": "note_ws",
                "level": "info",
                "message": "Ready",
            }

    asyncio.run(with_local_api(run_client))


def test_non_permission_structured_command_unresolved_target_returns_error_not_event():
    async def run_client(service, uri):
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_unresolved_interrupt",
                    "type": "agent.run.interrupt",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": "focused_run",
                    "payload": {},
                },
            }))
            payload = await recv_json(ws)

            assert payload["type"] == "error"
            assert payload["code"] == "UNRESOLVED_TARGET"
            assert "focused run" in payload["message"]
            try:
                extra = await recv_json(ws, timeout=0.1)
            except asyncio.TimeoutError:
                extra = None
            assert extra is None

    asyncio.run(with_local_api(run_client))


def test_structured_interrupt_resolves_focused_run_from_synced_session_state():
    async def run_client(service, uri):
        session = service.session_mgr.create(AgentType.CODEX)
        service.session_mgr.update_state(session.session_id, AgentState.SUBMITTED)

        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_focus_session",
                    "type": "agent.focus.set",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": {"device_id": "legacy-local-api"},
                    "payload": {
                        "mode": "session",
                        "session_id": session.session_id,
                    },
                },
            }))
            focus_event = await recv_json(ws)
            assert focus_event["type"] == "event"
            assert focus_event["event"]["type"] == "agent.focus.changed"

            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_interrupt_focused_run",
                    "type": "agent.run.interrupt",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": "focused_run",
                    "payload": {},
                },
            }))
            payload = await recv_json(ws)

            assert payload["type"] == "event"
            assert payload["event"]["type"] == "agent.run.interrupted"
            assert payload["event"]["payload"]["session_id"] == session.session_id
            assert service.agents[AgentType.CODEX].interrupted == [session.session_id]

    asyncio.run(with_local_api(run_client))


def test_structured_interrupt_broadcasts_focused_run_fallback_event():
    async def run_client(service, uri):
        session = service.session_mgr.create(AgentType.CODEX)
        service.session_mgr.update_state(session.session_id, AgentState.SUBMITTED)

        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_focus_missing_run",
                    "type": "agent.focus.set",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": {"device_id": "legacy-local-api"},
                    "payload": {
                        "mode": "run",
                        "instance_id": "codex-default",
                        "session_id": session.session_id,
                        "run_id": "run_missing",
                    },
                },
            }))
            initial_focus_event = await recv_json(ws)
            assert initial_focus_event["type"] == "event"
            assert initial_focus_event["event"]["type"] == "agent.focus.changed"

            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_interrupt_missing_focused_run",
                    "type": "agent.run.interrupt",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": "focused_run",
                    "payload": {},
                },
            }))
            received = [await recv_json(ws), await recv_json(ws)]
            event_types = [payload["event"]["type"] for payload in received]

            assert event_types == ["agent.focus.changed", "agent.run.interrupted"]
            assert event_types.count("agent.focus.changed") == 1
            assert event_types.count("agent.run.interrupted") == 1
            try:
                extra = await recv_json(ws, timeout=0.1)
            except asyncio.TimeoutError:
                extra = None
            assert extra is None

            focus_event = received[0]["event"]
            assert focus_event["payload"]["mode"] == "session"
            assert focus_event["payload"]["target"] == {
                "instance_id": "codex-default",
                "session_id": session.session_id,
                "run_id": None,
            }
            assert received[1]["event"]["payload"]["session_id"] == session.session_id
            assert service.agents[AgentType.CODEX].interrupted == [session.session_id]

            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_snapshot_after_fallback",
                    "type": "system.snapshot.request",
                    "source": {"kind": "test-client", "client_id": "pytest"},
                    "payload": {},
                },
            }))
            snapshot = await recv_json(ws)
            assert snapshot["type"] == "snapshot"
            assert snapshot["snapshot"]["focus"]["legacy-local-api"] == focus_event["payload"]

    asyncio.run(with_local_api(run_client))


def test_structured_launch_resolves_active_agent_to_synced_default_instance():
    async def run_client(service, uri):
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_focus_codex",
                    "type": "agent.focus.set",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": {"device_id": "legacy-local-api"},
                    "payload": {
                        "mode": "instance",
                        "instance_id": "codex-default",
                    },
                },
            }))
            focus_event = await recv_json(ws)
            assert focus_event["type"] == "event"
            assert focus_event["event"]["type"] == "agent.focus.changed"

            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_launch_active_agent",
                    "type": "agent.session.launch_or_resume",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": "active_agent",
                    "payload": {"context": "from active agent"},
                },
            }))
            payload = await recv_json(ws)

            assert payload["type"] == "event"
            assert payload["event"]["type"] == "agent.session.created"
            assert service.agents[AgentType.CODEX].launched == [
                (payload["event"]["payload"]["session_id"], "from active agent")
            ]

    asyncio.run(with_local_api(run_client))


def test_structured_launch_resolves_active_agent_from_session_focus_only():
    async def run_client(service, uri):
        session = service.session_mgr.create(AgentType.CODEX)

        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_focus_session_for_launch",
                    "type": "agent.focus.set",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": {"device_id": "legacy-local-api"},
                    "payload": {
                        "mode": "session",
                        "session_id": session.session_id,
                    },
                },
            }))
            focus_event = await recv_json(ws)
            assert focus_event["type"] == "event"
            assert focus_event["event"]["type"] == "agent.focus.changed"
            assert focus_event["event"]["payload"]["target"]["instance_id"] == "codex-default"

            await ws.send(json.dumps({
                "type": "command",
                "command": {
                    "command_id": "cmd_launch_active_agent_from_session",
                    "type": "agent.session.launch_or_resume",
                    "source": {
                        "kind": "keyboard-device",
                        "client_id": "kbd_01",
                        "device_id": "kbd_01",
                    },
                    "target": "active_agent",
                    "payload": {"context": "from focused session agent"},
                },
            }))
            payload = await recv_json(ws)

            assert payload["type"] == "event"
            assert payload["event"]["type"] == "agent.session.created"
            assert payload["event"]["payload"]["agent"] == "codex"
            assert service.agents[AgentType.CODEX].launched == [
                (payload["event"]["payload"]["session_id"], "from focused session agent")
            ]

    asyncio.run(with_local_api(run_client))


def test_agent_launch_uses_fake_proxy_and_broadcasts_events():
    async def run_client(service, uri):
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "agent_launch",
                "agent": "codex",
                "session_id": "new",
                "context": "hello",
            }))
            received = [await recv_json(ws), await recv_json(ws), await recv_json(ws)]
            by_type = {payload["type"]: payload for payload in received}

            assert by_type["task_update"]["state"] == "SUBMITTED"
            assert by_type["agent_message_delta"]["delta"] == "hello from fake agent"
            assert by_type["task_completed"]["summary"] == "fake complete"

    asyncio.run(with_local_api(run_client))


def test_permission_response_round_trip_over_local_api():
    async def run_client(service, uri):
        session = service.session_mgr.create(AgentType.CODEX)
        async with websockets.connect(uri) as ws:
            service._on_agent_event(service.unifier.encode_device_message({
                "type": "permission_request",
                "request_id": "req_ws",
                "session_id": session.session_id,
                "agent": "codex",
                "tool": "shell",
                "description": "Run command",
                "timeout_sec": 30,
            }))
            request = await recv_json(ws)
            assert request["type"] == "permission_request"
            assert request["request_id"] == "req_ws"

            await ws.send(json.dumps({
                "type": "permission_response",
                "request_id": "req_ws",
                "approved": True,
            }))
            ack = await recv_json(ws)
            assert ack["type"] == "permission_ack"
            assert ack["request_id"] == "req_ws"
            assert ack["session_id"] == session.session_id
            assert ack["approved"] is True
            assert ack["evidence"]["adapter"] == "fake"
            assert "req_ws" not in service.pending_permissions
            assert service.agents[AgentType.CODEX].permission_responses == [
                (session.session_id, "req_ws", True)
            ]

    asyncio.run(with_local_api(run_client))


def test_local_api_client_disconnect_cleans_sender_queue():
    async def run_client(service, uri):
        async with websockets.connect(uri):
            await wait_until(lambda: len(service.connected_clients) == 1)
        await wait_until(lambda: len(service.connected_clients) == 0)

    asyncio.run(with_local_api(run_client))
