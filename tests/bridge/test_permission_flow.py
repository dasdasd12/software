import asyncio
import json
from pathlib import Path
import sys


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from server import BridgeServer  # noqa: E402
from session_manager import AgentState, AgentType  # noqa: E402


class FakeProxy:
    def __init__(self):
        self.responses = []

    async def handle_permission_response(self, session_id, request_id, approved):
        self.responses.append((session_id, request_id, approved))
        return {
            "accepted": True,
            "forwarded": False,
            "session_id": session_id,
            "request_id": request_id,
            "approved": approved,
        }


class CaptureQueue:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)

    def get_nowait(self):
        return self.items.pop(0)


def make_server():
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
    }
    return BridgeServer(config)


def test_permission_request_event_registers_pending_request():
    server = make_server()
    session = server.session_mgr.create(AgentType.CODEX)
    event = server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_1",
        "session_id": session.session_id,
        "agent": "codex",
        "tool": "shell",
        "description": "Run command",
        "timeout_sec": 30,
    })

    server._on_agent_event(event)

    pending = server.pending_permissions["req_1"]
    assert pending.session_id == session.session_id
    assert pending.agent == AgentType.CODEX
    assert server.session_mgr.get(session.session_id).state == AgentState.WAITING_PERMISSION


def test_known_permission_response_returns_ack_and_clears_pending():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_2",
        "session_id": session.session_id,
        "agent": "codex",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_2",
        "approved": True,
    }, queue))

    ack = json.loads(queue.get_nowait())
    assert ack["type"] == "permission_ack"
    assert ack["request_id"] == "req_2"
    assert ack["session_id"] == session.session_id
    assert ack["approved"] is True
    assert ack["forwarded"] is False
    assert "req_2" not in server.pending_permissions
    assert proxy.responses == [(session.session_id, "req_2", True)]
    assert server.session_mgr.get(session.session_id).state == AgentState.WORKING


def test_unknown_permission_response_returns_request_not_found():
    server = make_server()
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "missing",
        "approved": False,
    }, queue))

    error = json.loads(queue.get_nowait())
    assert error["type"] == "error"
    assert error["code"] == "REQUEST_NOT_FOUND"


def test_expired_permission_request_is_pruned():
    server = make_server()
    session = server.session_mgr.create(AgentType.CLAUDE)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_old",
        "session_id": session.session_id,
        "agent": "claude",
        "timeout_sec": 1,
    }))
    server.pending_permissions["req_old"].created_at -= 10

    server._prune_expired_permissions()

    assert "req_old" not in server.pending_permissions
