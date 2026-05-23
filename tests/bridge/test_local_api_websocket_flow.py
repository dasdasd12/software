import asyncio
import json
from pathlib import Path
import sys

import websockets


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from server import LocalCoreServiceMVP  # noqa: E402
from session_manager import AgentType  # noqa: E402


class FakeProxy:
    def __init__(self, service, agent_type):
        self.service = service
        self.agent_type = agent_type
        self.launched = []
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
            assert snapshot["sessions"][session.session_id]["agent"] == "codex"
            assert snapshot["permissions"][0]["request_id"] == "req_snapshot"
            assert snapshot["permissions"][0]["session_id"] == session.session_id
            assert snapshot["permissions"][0]["agent"] == "codex"
            assert snapshot["permissions"][0]["timeout_sec"] == 30
            assert set(snapshot.keys()) >= {
                "sessions", "permissions", "devices", "profiles", "notifications"
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
