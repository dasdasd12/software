import asyncio
import json
from pathlib import Path
import sys


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from server import BridgeServer  # noqa: E402
from session_manager import AgentState, AgentType  # noqa: E402


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


def read_session_list(queue):
    payload = json.loads(queue.get_nowait())
    assert payload["type"] == "session_list"
    return payload["sessions"]


def test_list_sessions_returns_protocol_fields_for_all_sessions():
    server = make_server()
    codex = server.session_mgr.create(AgentType.CODEX)
    claude = server.session_mgr.create(AgentType.CLAUDE)
    server.session_mgr.update_state(codex.session_id, AgentState.WORKING)
    server.session_mgr.update_state(claude.session_id, AgentState.COMPLETED)
    queue = CaptureQueue()

    asyncio.run(server._cmd_list_sessions({"type": "list_sessions", "agent": "all"}, queue))

    sessions = read_session_list(queue)
    assert len(sessions) == 2
    for session in sessions:
        assert set(session.keys()) == {"session_id", "agent", "state", "created_at", "updated_at"}
        assert isinstance(session["session_id"], str)
        assert session["agent"] in {"codex", "claude"}
        assert isinstance(session["state"], str)
        assert isinstance(session["created_at"], int)
        assert isinstance(session["updated_at"], int)


def test_list_sessions_filters_by_agent():
    server = make_server()
    codex = server.session_mgr.create(AgentType.CODEX)
    server.session_mgr.create(AgentType.CLAUDE)
    queue = CaptureQueue()

    asyncio.run(server._cmd_list_sessions({"type": "list_sessions", "agent": "codex"}, queue))

    sessions = read_session_list(queue)
    assert [session["session_id"] for session in sessions] == [codex.session_id]
    assert sessions[0]["agent"] == "codex"


def test_list_sessions_treats_unknown_filter_as_all():
    server = make_server()
    server.session_mgr.create(AgentType.CODEX)
    server.session_mgr.create(AgentType.CLAUDE)
    queue = CaptureQueue()

    asyncio.run(server._cmd_list_sessions({"type": "list_sessions", "agent": "unknown"}, queue))

    assert len(read_session_list(queue)) == 2
