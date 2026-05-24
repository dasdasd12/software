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
from session_manager import AgentState, AgentType  # noqa: E402


class CaptureQueue:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)

    def put_nowait(self, item):
        self.items.append(item)

    def get_nowait(self):
        return self.items.pop(0)


class FakeController:
    def __init__(self, service, agent_type):
        self.service = service
        self.agent_type = agent_type
        self.launches = []
        self.resumes = []
        self.interrupts = []
        self.terminations = []

    def is_available(self):
        return True

    async def launch(self, session_id, context=""):
        self.launches.append((session_id, context))
        return self.service.session_mgr.get(session_id)

    async def resume(self, session_id):
        self.resumes.append(session_id)
        return self.service.session_mgr.get(session_id)

    async def send_interrupt(self, session_id):
        self.interrupts.append(session_id)
        return True

    async def terminate(self, session_id):
        self.terminations.append(session_id)
        return True


def make_server():
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
    }
    server = BridgeServer(config)
    server.agents[AgentType.CODEX] = FakeController(server, AgentType.CODEX)
    server.agents[AgentType.CLAUDE] = FakeController(server, AgentType.CLAUDE)
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


def test_structured_launch_or_resume_new_session_creates_session_and_calls_launch():
    server = make_server()
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.launch_or_resume",
        target={"session_id": "new"},
        payload={"agent": "codex", "context": "hello"},
        command_id="cmd_launch_new",
    ), queue))

    event = read_event(queue)
    session_id = event["payload"]["session_id"]
    assert event["type"] == "agent.session.created"
    assert session_id != "new"
    assert event["payload"]["agent"] == "codex"
    assert server.agents[AgentType.CODEX].launches == [(session_id, "hello")]
    assert server.session_mgr.get(session_id).agent == AgentType.CODEX


def test_structured_launch_or_resume_existing_session_calls_resume():
    server = make_server()
    session = server.session_mgr.create(AgentType.CODEX)
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.launch_or_resume",
        target={"session_id": session.session_id},
        payload={"context": "ignored for resume"},
        command_id="cmd_resume",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.session.state_changed"
    assert event["payload"]["session_id"] == session.session_id
    assert event["payload"]["agent"] == "codex"
    assert server.agents[AgentType.CODEX].resumes == [session.session_id]


def test_structured_interrupt_calls_controller_and_marks_session_cancelled():
    server = make_server()
    session = server.session_mgr.create(AgentType.CODEX)
    server.session_mgr.update_state(session.session_id, AgentState.WORKING)
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.run.interrupt",
        target={"session_id": session.session_id},
        command_id="cmd_interrupt",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.run.interrupted"
    assert event["payload"]["session_id"] == session.session_id
    assert event["payload"]["state"] == AgentState.CANCELLED.value
    assert server.agents[AgentType.CODEX].interrupts == [session.session_id]
    assert server.session_mgr.get(session.session_id).state == AgentState.CANCELLED


def test_structured_close_calls_controller_and_marks_session_cancelled_with_event():
    server = make_server()
    session = server.session_mgr.create(AgentType.CODEX)
    server.session_mgr.update_state(session.session_id, AgentState.WORKING)
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.close",
        target={"session_id": session.session_id},
        command_id="cmd_close",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.session.closed"
    assert event["payload"]["session_id"] == session.session_id
    assert event["payload"]["state"] == AgentState.CANCELLED.value
    assert event["payload"]["closed"] is True
    assert server.agents[AgentType.CODEX].terminations == [session.session_id]
    assert server.session_mgr.get(session.session_id).state == AgentState.CANCELLED


def test_legacy_launch_and_interrupt_share_structured_lifecycle_path_without_response_shape_change():
    server = make_server()
    queue = CaptureQueue()

    asyncio.run(server._cmd_agent_launch({
        "type": "agent_launch",
        "agent": "codex",
        "session_id": "new",
        "context": "legacy hello",
    }, queue))

    ack = json.loads(queue.get_nowait())
    assert ack["type"] == "task_update"
    session_id = ack["session_id"]
    assert ack["agent"] == "codex"
    assert ack["state"] == AgentState.SUBMITTED.value
    assert server.agents[AgentType.CODEX].launches == [(session_id, "legacy hello")]
    assert queue.items == []

    launch_events = server.runtime.event_bus.events_after(0)
    assert [event.type for event in launch_events] == ["agent.session.created"]
    assert launch_events[0].payload["session_id"] == session_id

    asyncio.run(server._cmd_interrupt({
        "type": "interrupt",
        "session_id": session_id,
    }, queue))

    assert server.agents[AgentType.CODEX].interrupts == [session_id]
    assert server.session_mgr.get(session_id).state == AgentState.CANCELLED
    events = server.runtime.event_bus.events_after(0)
    assert [event.type for event in events] == [
        "agent.session.created",
        "agent.run.interrupted",
    ]
    assert events[-1].payload["session_id"] == session_id
    assert queue.items == []
