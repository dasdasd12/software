import asyncio
import json
from pathlib import Path
import sys

import pytest


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
BRIDGE_DIR = SRC_DIR / "bridge"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(BRIDGE_DIR))

from core import CommandEnvelope, CommandSource  # noqa: E402
from agents.runtime import AgentLifecycleError  # noqa: E402
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


class FakeProxy:
    def __init__(self):
        self.responses = []

    async def handle_permission_response(self, session_id, request_id, approved):
        self.responses.append((session_id, request_id, approved))
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


class FailedCodexAppServerProxy:
    def __init__(self):
        self.responses = []

    async def handle_permission_response(self, session_id, request_id, approved):
        self.responses.append((session_id, request_id, approved))
        return {
            "accepted": True,
            "forwarded": False,
            "evidence": {
                "adapter": "codex_app_server",
                "reason": "native_request_not_registered",
            },
        }


class SuccessfulCodexAppServerProxy:
    async def handle_permission_response(self, session_id, request_id, approved):
        return {
            "accepted": True,
            "forwarded": True,
            "evidence": {
                "adapter": "codex_app_server",
                "native_channel": "item/commandExecution/requestApproval",
                "jsonrpc_id": 0,
                "thread_id": "thread_1",
                "turn_id": "turn_1",
                "item_id": "item_1",
                "decision": "accept" if approved else "decline",
                "decision_delivered": True,
                "response_written": True,
            },
        }


def make_server(persistence_path=None):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
    }
    if persistence_path is not None:
        config["persistence"] = {
            "enabled": True,
            "app_store_path": str(persistence_path),
        }
    return BridgeServer(config)


def track_permission(server, session, request_id, *, risk_level="medium", native=None):
    payload = {
        "type": "permission_request",
        "request_id": request_id,
        "session_id": session.session_id,
        "agent": session.agent.value,
        "risk_level": risk_level,
        "tool": "shell",
        "description": "Run command",
        "timeout_sec": 30,
    }
    if native is not None:
        payload["native"] = native
    server._on_agent_event(server.unifier.encode_device_message(payload))


def structured_permission_message(permission_id, approved, *, session_id=None, command_id="cmd_permission"):
    target = {"permission_id": permission_id}
    if session_id is not None:
        target["session_id"] = session_id
    return {
        "type": "command",
        "command": {
            "command_id": command_id,
            "type": "agent.permission.respond",
            "source": {"kind": "desktop-ui", "client_id": "desktop"},
            "target": target,
            "payload": {"approved": approved},
        },
    }


def read_payload(queue):
    return json.loads(queue.get_nowait())


def assert_permission_ack_shape(ack, request_id, session_id, approved, *, forwarded=False):
    assert set(ack) == {
        "type",
        "request_id",
        "session_id",
        "approved",
        "forwarded",
        "evidence",
        "timestamp",
    }
    assert ack["type"] == "permission_ack"
    assert ack["request_id"] == request_id
    assert ack["session_id"] == session_id
    assert ack["approved"] is approved
    assert ack["forwarded"] is forwarded
    assert isinstance(ack["evidence"], dict)
    assert isinstance(ack["timestamp"], int)


def test_structured_permission_respond_uses_target_permission_id_and_optional_session_id():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    first = server.session_mgr.create(AgentType.CODEX)
    second = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, first, "req_shared")
    track_permission(server, second, "req_shared")
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(
        structured_permission_message(
            "req_shared",
            True,
            session_id=second.session_id,
            command_id="cmd_permission_target",
        ),
        queue,
    ))

    ack = read_payload(queue)
    assert_permission_ack_shape(ack, "req_shared", second.session_id, True)
    assert proxy.responses == [(second.session_id, "req_shared", True)]
    assert "req_shared" in server.pending_permissions
    assert f"{second.session_id}:req_shared" not in server.pending_permissions


def test_desktop_structured_permission_approval_returns_legacy_permission_ack_shape():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_desktop")
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(
        structured_permission_message("req_desktop", True),
        queue,
    ))

    ack = read_payload(queue)
    assert_permission_ack_shape(ack, "req_desktop", session.session_id, True)
    assert ack["evidence"] == {
        "adapter": "fake",
        "session_id": session.session_id,
        "request_id": "req_desktop",
        "approved": True,
    }
    assert "req_desktop" not in server.pending_permissions
    assert proxy.responses == [(session.session_id, "req_desktop", True)]
    assert server.session_mgr.get(session.session_id).state == AgentState.WORKING
    assert server.runtime.event_bus.events_after(0)[-1].type == "agent.permission.resolved"


def test_device_structured_permission_can_approve_low_risk_with_low_risk_capability():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_low", risk_level="low")
    queue = CaptureQueue()
    server.register_client_identity(
        queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )

    asyncio.run(server._cmd_structured_command(
        structured_permission_message("req_low", True, command_id="cmd_low"),
        queue,
    ))

    ack = read_payload(queue)
    assert_permission_ack_shape(ack, "req_low", session.session_id, True)
    assert "req_low" not in server.pending_permissions
    assert proxy.responses == [(session.session_id, "req_low", True)]


def test_same_client_id_unprivileged_queue_cannot_borrow_permission_capability():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_same_client", risk_level="low")
    privileged_queue = CaptureQueue()
    unprivileged_queue = CaptureQueue()
    server.register_client_identity(
        privileged_queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )
    server.register_client_identity(
        unprivileged_queue,
        "device-transport",
        "keyboard-1",
        set(),
    )

    asyncio.run(server._cmd_structured_command(
        structured_permission_message(
            "req_same_client",
            True,
            command_id="cmd_same_client_unprivileged",
        ),
        unprivileged_queue,
    ))

    error = read_payload(unprivileged_queue)
    assert error["type"] == "error"
    assert error["code"] == "CAPABILITY_DENIED"
    assert "req_same_client" in server.pending_permissions
    assert proxy.responses == []


def test_direct_structured_permission_dispatch_with_unregistered_source_fails_closed():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_direct", risk_level="high")
    command = CommandEnvelope(
        type="agent.permission.respond",
        source=CommandSource(kind="unknown-client", client_id="spoofed"),
        target={
            "permission_id": "req_direct",
            "session_id": session.session_id,
        },
        payload={"approved": True},
        command_id="cmd_direct_unregistered",
    )

    with pytest.raises(AgentLifecycleError) as exc_info:
        asyncio.run(server.runtime.command_router.dispatch_async(command))

    assert exc_info.value.code in {"AUTH_REQUIRED", "CAPABILITY_DENIED"}
    assert "req_direct" in server.pending_permissions
    assert proxy.responses == []


def test_handler_path_uses_current_queue_identity_for_structured_permission_approval():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_current_queue", risk_level="low")
    queue = CaptureQueue()
    server.register_client_identity(
        queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )

    asyncio.run(server._cmd_structured_command(
        structured_permission_message(
            "req_current_queue",
            True,
            command_id="cmd_current_queue",
        ),
        queue,
    ))

    ack = read_payload(queue)
    assert_permission_ack_shape(ack, "req_current_queue", session.session_id, True)
    assert "req_current_queue" not in server.pending_permissions
    assert proxy.responses == [(session.session_id, "req_current_queue", True)]


def test_device_structured_permission_cannot_approve_high_risk_permission():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_high", risk_level="high")
    queue = CaptureQueue()
    server.register_client_identity(
        queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )

    asyncio.run(server._cmd_structured_command(
        structured_permission_message("req_high", True, command_id="cmd_high"),
        queue,
    ))

    error = read_payload(queue)
    assert error["type"] == "error"
    assert error["code"] == "REQUIRE_DESKTOP_CONFIRM"
    assert "req_high" in server.pending_permissions
    assert proxy.responses == []


def test_structured_permission_forward_failure_keeps_request_pending():
    server = make_server()
    proxy = FailedCodexAppServerProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_forward_fail", risk_level="high")
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(
        structured_permission_message(
            "req_forward_fail",
            True,
            session_id=session.session_id,
            command_id="cmd_forward_fail",
        ),
        queue,
    ))

    error = read_payload(queue)
    assert error["type"] == "error"
    assert error["code"] == "PERMISSION_FORWARD_FAILED"
    assert "req_forward_fail" in server.pending_permissions
    assert proxy.responses == [(session.session_id, "req_forward_fail", True)]


def test_structured_permission_history_persists_forwarded_evidence(tmpdir):
    server = make_server(Path(str(tmpdir)) / "app.db")
    server.agents[AgentType.CODEX] = SuccessfulCodexAppServerProxy()
    session = server.session_mgr.create(AgentType.CODEX)
    native = {
        "adapter": "codex_app_server",
        "channel": "item/commandExecution/requestApproval",
        "jsonrpc_id": 0,
        "thread_id": "thread_1",
        "turn_id": "turn_1",
        "item_id": "item_1",
    }
    track_permission(server, session, "0", risk_level="high", native=native)
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(
        structured_permission_message(
            "0",
            True,
            session_id=session.session_id,
            command_id="cmd_history",
        ),
        queue,
    ))

    ack = read_payload(queue)
    assert_permission_ack_shape(ack, "0", session.session_id, True, forwarded=True)
    history = server.app_store.permission_history.list(session_id=session.session_id)
    assert history[0]["permission_id"] == "0"
    assert history[0]["decision"] == "approve"
    assert history[0]["source_client"] == "legacy-local-api"
    assert history[0]["forwarded"] is True
    assert history[0]["evidence"]["adapter"] == "codex_app_server"
    assert history[0]["evidence"]["jsonrpc_id"] == 0
    assert history[0]["evidence"]["response_written"] is True
    assert history[0]["native"] == native
    server.app_store.close()


def test_legacy_permission_response_dispatches_same_structured_permission_event():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_legacy", risk_level="low")
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_legacy",
        "session_id": session.session_id,
        "approved": True,
    }, queue))

    ack = read_payload(queue)
    assert_permission_ack_shape(ack, "req_legacy", session.session_id, True)
    events = server.runtime.event_bus.events_after(0)
    assert events[-1].type == "agent.permission.resolved"
    assert events[-1].payload["request_id"] == "req_legacy"
