import asyncio
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
BRIDGE_DIR = SRC_DIR / "bridge"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(BRIDGE_DIR))

from server import BridgeServer  # noqa: E402
from session_manager import AgentType  # noqa: E402


class CaptureQueue:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)

    def put_nowait(self, item):
        self.items.append(item)

    def get_nowait(self):
        return self.items.pop(0)


class FakeInputController:
    def __init__(self, service):
        self.service = service
        self.inputs = []
        self.available = True
        self.result = True
        self.input_error = None

    def is_available(self):
        return self.available

    async def send_input(self, session_id, text):
        if self.input_error:
            raise self.input_error
        self.inputs.append((session_id, text))
        return self.result


class FakeNoInputController:
    def is_available(self):
        return True


def make_server_with_input_controller():
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
    }
    server = BridgeServer(config)
    server.agents[AgentType.CLAUDE] = FakeInputController(server)
    return server


def command_message(command_type, *, target=None, payload=None, command_id="cmd_test"):
    data = {
        "type": "command",
        "command": {
            "command_id": command_id,
            "type": command_type,
            "source": {"kind": "test-client", "client_id": "pytest"},
            "payload": payload or {},
        },
    }
    if target is not None:
        data["command"]["target"] = target
    return data


def read_event(queue):
    payload = json.loads(queue.get_nowait())
    assert payload["type"] == "event"
    return payload["event"]


def read_error(queue):
    payload = json.loads(queue.get_nowait())
    assert payload["type"] == "error"
    return payload


def test_structured_session_input_calls_controller_and_emits_event():
    server = make_server_with_input_controller()
    session = server.session_mgr.create(AgentType.CLAUDE)
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.input",
        target={"session_id": session.session_id},
        payload={"text": "run tests"},
        command_id="cmd_input",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.session.input.accepted"
    assert event["payload"]["session_id"] == session.session_id
    assert event["payload"]["accepted"] is True
    assert server.agents[AgentType.CLAUDE].inputs == [(session.session_id, "run tests")]


def test_structured_session_input_rejects_empty_text():
    server = make_server_with_input_controller()
    session = server.session_mgr.create(AgentType.CLAUDE)
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.input",
        target={"session_id": session.session_id},
        payload={"text": ""},
        command_id="cmd_empty_input",
    ), queue))

    error = read_error(queue)
    assert error["code"] == "INVALID_COMMAND"


def test_structured_session_input_returns_unavailable_without_send_input():
    server = make_server_with_input_controller()
    session = server.session_mgr.create(AgentType.CLAUDE)
    server.agents[AgentType.CLAUDE] = FakeNoInputController()
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.input",
        target={"session_id": session.session_id},
        payload={"text": "run tests"},
        command_id="cmd_input_unavailable",
    ), queue))

    error = read_error(queue)
    assert error["code"] == "INPUT_UNAVAILABLE"


def test_structured_session_input_returns_rejected_when_controller_refuses():
    server = make_server_with_input_controller()
    session = server.session_mgr.create(AgentType.CLAUDE)
    server.agents[AgentType.CLAUDE].result = False
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.input",
        target={"session_id": session.session_id},
        payload={"text": "run tests"},
        command_id="cmd_input_rejected",
    ), queue))

    error = read_error(queue)
    assert error["code"] == "INPUT_REJECTED"


def test_structured_session_input_returns_failed_when_controller_raises():
    server = make_server_with_input_controller()
    session = server.session_mgr.create(AgentType.CLAUDE)
    server.agents[AgentType.CLAUDE].input_error = RuntimeError("provider input failed")
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.input",
        target={"session_id": session.session_id},
        payload={"text": "run tests"},
        command_id="cmd_input_failed",
    ), queue))

    error = read_error(queue)
    assert error["code"] == "INPUT_FAILED"
    assert error["message"] == "provider input failed"


def test_structured_session_input_requires_agent_launch_capability():
    server = make_server_with_input_controller()
    session = server.session_mgr.create(AgentType.CLAUDE)
    queue = CaptureQueue()
    server.register_client_identity(queue, "test-client", "pytest", set())

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.input",
        target={"session_id": session.session_id},
        payload={"text": "run tests"},
        command_id="cmd_input_denied",
    ), queue))

    error = read_error(queue)
    assert error["code"] == "CAPABILITY_DENIED"
    assert server.agents[AgentType.CLAUDE].inputs == []
