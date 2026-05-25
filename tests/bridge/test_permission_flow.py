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
            "evidence": {
                "adapter": "fake",
                "session_id": session_id,
                "request_id": request_id,
                "approved": approved,
            },
        }


class FailedClaudeProxy:
    async def handle_permission_response(self, session_id, request_id, approved):
        return {
            "accepted": True,
            "forwarded": False,
            "evidence": {
                "adapter": "claude_sdk_permission_bridge",
                "reason": "native_request_not_registered",
            },
        }


class FailedCodexAppServerProxy:
    async def handle_permission_response(self, session_id, request_id, approved):
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
                "command": "python -c \"print('codex approval smoke')\"",
                "cwd": "C:/repo",
                "decision": "accept" if approved else "decline",
                "decision_delivered": True,
                "response_written": True,
            },
        }


class ExpiringCodexAppServerProxy(SuccessfulCodexAppServerProxy):
    def __init__(self):
        self.expired = []

    async def expire_permission_request(self, session_id, request_id):
        self.expired.append((session_id, request_id))
        return await self.handle_permission_response(session_id, request_id, False)


class CompletingProxy(FakeProxy):
    def __init__(self, server):
        super().__init__()
        self.server = server

    async def handle_permission_response(self, session_id, request_id, approved):
        result = await super().handle_permission_response(session_id, request_id, approved)
        self.server.session_mgr.update_state(session_id, AgentState.COMPLETED)
        return result


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


def make_server_with_persistence(db_path):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
        "persistence": {"enabled": True, "app_store_path": str(db_path)},
    }
    return BridgeServer(config)


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

    pending = pending_for(server, "req_1", session_id=session.session_id)
    assert pending is not None
    assert pending.session_id == session.session_id
    assert pending.agent == AgentType.CODEX
    assert server.session_mgr.get(session.session_id).state == AgentState.WAITING_PERMISSION


def test_repeated_session_scoped_request_replaces_pending_after_other_scope_resolves():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    first = server.session_mgr.create(AgentType.CODEX)
    second = server.session_mgr.create(AgentType.CODEX)

    for session in (first, second):
        server._on_agent_event(server.unifier.encode_device_message({
            "type": "permission_request",
            "request_id": "req_shared",
            "session_id": session.session_id,
            "agent": "codex",
            "timeout_sec": 30,
        }))

    first_queue = CaptureQueue()
    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_shared",
        "session_id": first.session_id,
        "approved": True,
    }, first_queue))

    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_shared",
        "session_id": second.session_id,
        "agent": "codex",
        "timeout_sec": 30,
    }))

    assert pending_count(server, "req_shared", session_id=second.session_id) == 1
    pending = pending_for(server, "req_shared", session_id=second.session_id)
    assert pending is not None
    assert pending.session_id == second.session_id

    second_queue = CaptureQueue()
    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_shared",
        "session_id": second.session_id,
        "approved": True,
    }, second_queue))

    ack = json.loads(second_queue.get_nowait())
    assert ack["type"] == "permission_ack"
    assert ack["request_id"] == "req_shared"
    assert ack["session_id"] == second.session_id
    assert proxy.responses == [
        (first.session_id, "req_shared", True),
        (second.session_id, "req_shared", True),
    ]


def test_same_request_id_with_different_run_parent_scope_can_coexist_and_disambiguate():
    server = make_server()
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    first = server.session_mgr.create(AgentType.CODEX)
    second = server.session_mgr.create(AgentType.CODEX)

    for session in (first, second):
        server._on_agent_event(server.unifier.encode_device_message({
            "type": "permission_request",
            "request_id": "req_same_run",
            "session_id": session.session_id,
            "run_id": "run_shared",
            "agent": "codex",
            "timeout_sec": 30,
        }))

    assert pending_count(server, "req_same_run") == 2
    assert pending_for(
        server,
        "req_same_run",
        session_id=first.session_id,
        run_id="run_shared",
    ) is not None
    assert pending_for(
        server,
        "req_same_run",
        session_id=second.session_id,
        run_id="run_shared",
    ) is not None
    assert pending_for(server, "req_same_run") is None
    assert pending_for(server, "req_same_run", run_id="run_shared") is None

    queue = CaptureQueue()
    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_same_run",
        "session_id": second.session_id,
        "run_id": "run_shared",
        "approved": True,
    }, queue))

    ack = json.loads(queue.get_nowait())
    assert ack["type"] == "permission_ack"
    assert ack["request_id"] == "req_same_run"
    assert ack["session_id"] == second.session_id
    assert pending_count(server, "req_same_run") == 1
    assert pending_for(
        server,
        "req_same_run",
        session_id=first.session_id,
        run_id="run_shared",
    ) is not None
    assert proxy.responses == [(second.session_id, "req_same_run", True)]


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
    assert ack["evidence"] == {
        "adapter": "fake",
        "session_id": session.session_id,
        "request_id": "req_2",
        "approved": True,
    }
    assert pending_for(server, "req_2", session_id=session.session_id) is None
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


def test_permission_response_rejects_ambiguous_string_bool():
    server = make_server()
    session = server.session_mgr.create(AgentType.CODEX)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_bool",
        "session_id": session.session_id,
        "agent": "codex",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_bool",
        "approved": "not-a-bool",
    }, queue))

    error = json.loads(queue.get_nowait())
    assert error["type"] == "error"
    assert error["code"] == "INVALID_PERMISSION_RESPONSE"
    assert pending_for(server, "req_bool", session_id=session.session_id) is not None


def test_claude_forward_failure_keeps_pending_request():
    server = make_server()
    server.agents[AgentType.CLAUDE] = FailedClaudeProxy()
    session = server.session_mgr.create(AgentType.CLAUDE)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_forward_fail",
        "session_id": session.session_id,
        "agent": "claude",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_forward_fail",
        "session_id": session.session_id,
        "approved": True,
    }, queue))

    error = json.loads(queue.get_nowait())
    assert error["type"] == "error"
    assert error["code"] == "PERMISSION_FORWARD_FAILED"
    assert pending_for(server, "req_forward_fail", session_id=session.session_id) is not None


def test_codex_app_server_forward_failure_keeps_pending_request():
    server = make_server()
    server.agents[AgentType.CODEX] = FailedCodexAppServerProxy()
    session = server.session_mgr.create(AgentType.CODEX)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_codex_forward_fail",
        "session_id": session.session_id,
        "agent": "codex",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_codex_forward_fail",
        "session_id": session.session_id,
        "approved": True,
    }, queue))

    error = json.loads(queue.get_nowait())
    assert error["type"] == "error"
    assert error["code"] == "PERMISSION_FORWARD_FAILED"
    assert pending_for(server, "req_codex_forward_fail", session_id=session.session_id) is not None


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
    pending = pending_for(server, "req_old", session_id=session.session_id)
    assert pending is not None
    pending.created_at -= 10

    server._prune_expired_permissions()

    assert pending_for(server, "req_old", session_id=session.session_id) is None


def test_permission_response_persists_session_and_history_to_sqlite(tmpdir):
    db_path = Path(str(tmpdir)) / "app.db"
    server = make_server_with_persistence(db_path)
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_sqlite",
        "session_id": session.session_id,
        "agent": "codex",
        "risk_level": "low",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_sqlite",
        "session_id": session.session_id,
        "approved": "false",
    }, queue))

    assert server.app_store.sessions.get(session.session_id)["state"] == AgentState.WORKING.value
    history = server.app_store.permission_history.list(session_id=session.session_id)
    assert history[0]["permission_id"] == "req_sqlite"
    assert history[0]["decision"] == "deny"
    assert history[0]["forwarded"] is False
    server.app_store.close()


def test_permission_response_does_not_regress_terminal_session_state():
    server = make_server()
    session = server.session_mgr.create(AgentType.CODEX)
    server.agents[AgentType.CODEX] = CompletingProxy(server)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_race",
        "session_id": session.session_id,
        "agent": "codex",
        "risk_level": "low",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_race",
        "session_id": session.session_id,
        "approved": True,
    }, queue))

    assert server.session_mgr.get(session.session_id).state == AgentState.COMPLETED


def test_expired_codex_app_server_permission_is_declined_natively():
    server = make_server()
    proxy = ExpiringCodexAppServerProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_expire_native",
        "session_id": session.session_id,
        "agent": "codex",
        "risk_level": "high",
        "timeout_sec": -1,
        "native": {"adapter": "codex_app_server"},
    }))

    asyncio.run(server._prune_expired_permissions_async())

    assert pending_for(server, "req_expire_native", session_id=session.session_id) is None
    assert proxy.expired == [(session.session_id, "req_expire_native")]


def test_codex_app_server_permission_history_persists_native_evidence(tmpdir):
    db_path = Path(str(tmpdir)) / "app.db"
    server = make_server_with_persistence(db_path)
    server.agents[AgentType.CODEX] = SuccessfulCodexAppServerProxy()
    session = server.session_mgr.create(AgentType.CODEX)
    native = {
        "adapter": "codex_app_server",
        "channel": "item/commandExecution/requestApproval",
        "jsonrpc_id": 0,
        "thread_id": "thread_1",
        "turn_id": "turn_1",
        "item_id": "item_1",
        "command": "python -c \"print('codex approval smoke')\"",
        "cwd": "C:/repo",
    }
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "0",
        "session_id": session.session_id,
        "agent": "codex",
        "risk_level": "high",
        "tool": "shell",
        "description": native["command"],
        "native": native,
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "0",
        "session_id": session.session_id,
        "approved": True,
    }, queue))

    history = server.app_store.permission_history.list(session_id=session.session_id)
    assert history[0]["permission_id"] == "0"
    assert history[0]["decision"] == "approve"
    assert history[0]["forwarded"] is True
    assert history[0]["evidence"]["adapter"] == "codex_app_server"
    assert history[0]["evidence"]["jsonrpc_id"] == 0
    assert history[0]["evidence"]["response_written"] is True
    assert history[0]["native"] == native
    server.app_store.close()


def test_service_restores_sessions_from_sqlite_app_store(tmpdir):
    db_path = Path(str(tmpdir)) / "app.db"
    first = make_server_with_persistence(db_path)
    session = first.session_mgr.create(AgentType.CODEX)
    first._persist_session(session.session_id)
    first.app_store.close()

    restored = make_server_with_persistence(db_path)

    assert restored.session_mgr.get(session.session_id).agent == AgentType.CODEX
    restored.app_store.close()
