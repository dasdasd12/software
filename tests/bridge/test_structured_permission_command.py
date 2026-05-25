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
from keyboard import ScreenFocus  # noqa: E402
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


def pending_for(server, request_id, *, session_id=None, instance_id=None, run_id=None):
    _key, pending = server._find_pending_permission(request_id, session_id, instance_id, run_id)
    return pending


def pending_count(server, request_id, *, session_id=None, instance_id=None, run_id=None):
    return sum(
        1
        for pending in server.pending_permissions.values()
        if (
            pending.request_id == request_id
            and (session_id is None or pending.session_id == session_id)
            and (instance_id is None or pending.instance_id == instance_id)
            and (run_id is None or pending.run_id == run_id)
        )
    )


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


def structured_focused_permission_message(approved, *, command_id="cmd_focused_permission"):
    return {
        "type": "command",
        "command": {
            "command_id": command_id,
            "type": "agent.permission.respond",
            "source": {"kind": "keyboard-device", "client_id": "keyboard-1"},
            "target": "focused_permission",
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
    assert pending_for(server, "req_shared", session_id=first.session_id) is not None
    assert pending_for(server, "req_shared", session_id=second.session_id) is None


def test_focused_permission_uses_request_id_when_duplicate_session_scoped_ids_are_projected():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    first = server.session_mgr.create(AgentType.CODEX)
    second = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, first, "req_shared", risk_level="low")
    track_permission(server, second, "req_shared", risk_level="low")
    queue = CaptureQueue()
    server.register_client_identity(
        queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )
    server.runtime.keyboard_runtime.focus_manager.set_focus(ScreenFocus(
        device_id="keyboard-1",
        mode="session",
        session_id=second.session_id,
    ))

    server._sync_runtime_state()
    projected_permissions = list(server.runtime.state_store.permissions.values())
    assert any(
        permission["permission_id"] == "req_shared"
        and permission.get("session_id") == second.session_id
        for permission in projected_permissions
    )
    assert all(
        "req_shared" not in key
        and first.session_id not in key
        and second.session_id not in key
        for key in server.runtime.state_store.permissions
    )
    snapshot = server.runtime.snapshot().to_dict()
    assert all(
        permission.get("permission_id") == "req_shared"
        for permission in snapshot["permissions"]
    )

    asyncio.run(server._cmd_structured_command(
        structured_focused_permission_message(
            True,
            command_id="cmd_focused_duplicate_session",
        ),
        queue,
    ))

    ack = read_payload(queue)
    assert_permission_ack_shape(ack, "req_shared", second.session_id, True)
    assert proxy.responses == [(second.session_id, "req_shared", True)]
    assert pending_for(server, "req_shared", session_id=first.session_id) is not None
    assert pending_for(server, "req_shared", session_id=second.session_id) is None


def test_focused_permission_reuses_same_session_scope_after_other_duplicate_resolves():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    first = server.session_mgr.create(AgentType.CODEX)
    second = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, first, "req_reused", risk_level="low")
    track_permission(server, second, "req_reused", risk_level="low")

    first_queue = CaptureQueue()
    asyncio.run(server._cmd_structured_command(
        structured_permission_message(
            "req_reused",
            True,
            session_id=first.session_id,
            command_id="cmd_resolve_first_duplicate",
        ),
        first_queue,
    ))

    track_permission(server, second, "req_reused", risk_level="low")

    assert pending_count(server, "req_reused", session_id=second.session_id) == 1
    assert pending_count(server, "req_reused") == 1

    second_queue = CaptureQueue()
    server.register_client_identity(
        second_queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )
    server.runtime.keyboard_runtime.focus_manager.set_focus(ScreenFocus(
        device_id="keyboard-1",
        mode="session",
        session_id=second.session_id,
    ))

    asyncio.run(server._cmd_structured_command(
        structured_focused_permission_message(
            True,
            command_id="cmd_focused_reused_duplicate",
        ),
        second_queue,
    ))

    ack = read_payload(second_queue)
    assert_permission_ack_shape(ack, "req_reused", second.session_id, True)
    assert pending_for(server, "req_reused", session_id=second.session_id) is None
    assert proxy.responses == [
        (first.session_id, "req_reused", True),
        (second.session_id, "req_reused", True),
    ]


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
    assert pending_for(server, "req_desktop", session_id=session.session_id) is None
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
    assert pending_for(server, "req_low", session_id=session.session_id) is None
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
    assert pending_for(server, "req_same_client", session_id=session.session_id) is not None
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
    assert pending_for(server, "req_direct", session_id=session.session_id) is not None
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
    assert pending_for(server, "req_current_queue", session_id=session.session_id) is None
    assert proxy.responses == [(session.session_id, "req_current_queue", True)]


def test_focused_permission_structured_command_sees_pending_permission_on_first_dispatch():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    track_permission(server, session, "req_focused_first", risk_level="low")
    queue = CaptureQueue()
    server.register_client_identity(
        queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )
    server.runtime.keyboard_runtime.focus_manager.set_focus(ScreenFocus(
        device_id="keyboard-1",
        mode="session",
        session_id=session.session_id,
    ))

    asyncio.run(server._cmd_structured_command(
        structured_focused_permission_message(
            True,
            command_id="cmd_focused_first",
        ),
        queue,
    ))

    ack = read_payload(queue)
    assert_permission_ack_shape(ack, "req_focused_first", session.session_id, True)
    assert pending_for(server, "req_focused_first", session_id=session.session_id) is None
    assert proxy.responses == [(session.session_id, "req_focused_first", True)]


def test_unresolved_focused_permission_structured_command_returns_error_envelope():
    server = make_server()
    queue = CaptureQueue()
    server.register_client_identity(
        queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )
    server.runtime.keyboard_runtime.focus_manager.set_focus(ScreenFocus(
        device_id="keyboard-1",
        mode="session",
        session_id="sess_missing",
    ))

    asyncio.run(server._cmd_structured_command(
        structured_focused_permission_message(
            True,
            command_id="cmd_focused_missing",
        ),
        queue,
    ))

    error = read_payload(queue)
    assert error["type"] == "error"
    assert error["code"] == "UNRESOLVED_TARGET"
    assert isinstance(error["message"], str)
    assert isinstance(error["timestamp"], int)


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
    assert pending_for(server, "req_high", session_id=session.session_id) is not None
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
    assert pending_for(server, "req_forward_fail", session_id=session.session_id) is not None
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
