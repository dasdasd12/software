import asyncio
import json
from pathlib import Path
import sys

import pytest
import websockets


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from server import BridgeServer  # noqa: E402
from session_manager import AgentType  # noqa: E402


class FakeProxy:
    def __init__(self):
        self.responses = []

    async def handle_permission_response(self, session_id, request_id, approved):
        self.responses.append((session_id, request_id, approved))
        return {"accepted": True, "forwarded": False}


class CaptureQueue:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)

    def get_nowait(self):
        return self.items.pop(0)


def make_server(security):
    return BridgeServer({
        "server": {"host": "127.0.0.1", "port": 0},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
        "security": security,
    })


def find_pending_permission(server, request_id, session_id=None, instance_id=None, run_id=None):
    _key, pending = server._find_pending_permission(request_id, session_id, instance_id, run_id)
    return pending


async def serve(server):
    ws_server = await websockets.serve(server._handle_local_api_client, "127.0.0.1", 0)
    port = ws_server.sockets[0].getsockname()[1]
    return ws_server, f"ws://127.0.0.1:{port}"


async def recv_json(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))


def test_hello_with_valid_launch_token_returns_ack_and_identity():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "tok_123",
                    "client_kind": "browser-dev-ui",
                    "client_id": "dev-ui",
                    "capabilities": ["session:list"],
                }))
                ack = await recv_json(ws)
                assert ack["type"] == "hello_ack"
                assert ack["client_kind"] == "browser-dev-ui"
                assert ack["client_id"] == "dev-ui"
                assert ack["capabilities"] == ["session:list"]
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_hello_intersects_requested_capabilities_with_server_grant():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
            "client_capabilities": {
                "browser-dev-ui": ["session:list"],
            },
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "tok_123",
                    "client_kind": "browser-dev-ui",
                    "client_id": "dev-ui",
                    "capabilities": ["session:list", "permission:respond"],
                }))
                ack = await recv_json(ws)
                assert ack["type"] == "hello_ack"
                assert ack["capabilities"] == ["session:list"]
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_token_bound_client_grant_rejects_spoofed_client_kind():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "fallback",
            "allow_loopback_without_token": False,
            "clients": [{
                "token": "tok_device",
                "client_kind": "device-transport",
                "client_id": "keyboard-1",
                "capabilities": ["permission:respond:low_risk"],
            }],
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "tok_device",
                    "client_kind": "desktop-ui",
                    "client_id": "keyboard-1",
                    "capabilities": ["permission:respond"],
                }))
                error = await recv_json(ws)
                assert error["type"] == "error"
                assert error["code"] == "AUTH_FAILED"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_token_bound_client_grant_allows_matching_identity_only():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "fallback",
            "allow_loopback_without_token": False,
            "clients": [{
                "token": "tok_device",
                "client_kind": "device-transport",
                "client_id": "keyboard-1",
                "capabilities": ["permission:respond:low_risk"],
            }],
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "tok_device",
                    "client_kind": "device-transport",
                    "client_id": "keyboard-1",
                    "capabilities": ["permission:respond", "permission:respond:low_risk"],
                }))
                ack = await recv_json(ws)
                assert ack["type"] == "hello_ack"
                assert ack["capabilities"] == ["permission:respond:low_risk"]
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_hello_accepts_dedicated_claude_hook_token_only_for_hook_capability():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
        })
        server.agent_commands._foreground_hook_tokens_by_session_id["sess_1"] = "hook-token"
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "hook-token",
                    "client_kind": "agent-hook",
                    "client_id": "claude-code-hook:sess_1",
                    "capabilities": ["claude:hook", "permission:respond"],
                }))
                ack = await recv_json(ws)
                assert ack["type"] == "hello_ack"
                assert ack["client_kind"] == "agent-hook"
                assert ack["capabilities"] == ["claude:hook"]
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_hello_accepts_dedicated_codex_hook_token_only_for_hook_capability():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
        })
        server.agent_commands._foreground_hook_tokens_by_session_id["sess_1"] = "hook-token"
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "hook-token",
                    "client_kind": "agent-hook",
                    "client_id": "codex-cli-proxy:sess_1",
                    "capabilities": ["codex:hook", "permission:respond"],
                }))
                ack = await recv_json(ws)
                assert ack["type"] == "hello_ack"
                assert ack["client_kind"] == "agent-hook"
                assert ack["capabilities"] == ["codex:hook"]
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_hello_rejects_hook_token_for_desktop_identity():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
        })
        server.agent_commands._foreground_hook_tokens_by_session_id["sess_1"] = "hook-token"
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "hook-token",
                    "client_kind": "desktop-ui",
                    "client_id": "local-agent-cli",
                    "capabilities": ["permission:respond"],
                }))
                error = await recv_json(ws)
                assert error["type"] == "error"
                assert error["code"] == "AUTH_FAILED"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_auth_enabled_rejects_command_before_hello():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"type": "list_sessions"}))
                error = await recv_json(ws)
                assert error["type"] == "error"
                assert error["code"] == "AUTH_REQUIRED"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_auth_enabled_rejects_bad_launch_token():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "wrong",
                    "client_kind": "browser-dev-ui",
                    "client_id": "dev-ui",
                    "capabilities": [],
                }))
                error = await recv_json(ws)
                assert error["type"] == "error"
                assert error["code"] == "AUTH_FAILED"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_structured_command_requires_server_granted_capability():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
            "client_capabilities": {"browser-dev-ui": []},
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "tok_123",
                    "client_kind": "browser-dev-ui",
                    "client_id": "dev-ui",
                    "capabilities": ["session:list"],
                }))
                ack = await recv_json(ws)
                assert ack["capabilities"] == []

                await ws.send(json.dumps({
                    "type": "command",
                    "command": {
                        "command_id": "cmd_snapshot",
                        "type": "system.snapshot.request",
                        "source": {"kind": "desktop-ui", "client_id": "spoofed"},
                        "payload": {},
                    },
                }))
                error = await recv_json(ws)
                assert error["type"] == "error"
                assert error["code"] == "CAPABILITY_DENIED"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_notification_create_requires_mutating_capability():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
            "client_capabilities": {"browser-dev-ui": ["session:list"]},
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "token": "tok_123",
                    "client_kind": "browser-dev-ui",
                    "client_id": "dev-ui",
                    "capabilities": ["session:list", "notification:create"],
                }))
                ack = await recv_json(ws)
                assert ack["capabilities"] == ["session:list"]

                await ws.send(json.dumps({
                    "type": "command",
                    "command": {
                        "command_id": "cmd_note",
                        "type": "notification.create",
                        "source": {"kind": "browser-dev-ui", "client_id": "dev-ui"},
                        "payload": {"notification_id": "note_1", "message": "hi"},
                    },
                }))
                error = await recv_json(ws)
                assert error["type"] == "error"
                assert error["code"] == "CAPABILITY_DENIED"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_loopback_can_bypass_launch_token_when_explicitly_configured():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": True,
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "client_kind": "test-client",
                    "client_id": "pytest",
                    "capabilities": [],
                }))
                ack = await recv_json(ws)
                assert ack["type"] == "hello_ack"
                assert ack["client_kind"] == "test-client"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_invalid_websocket_origin_is_rejected_when_configured():
    async def run():
        server = make_server({
            "auth_enabled": True,
            "launch_token": "tok_123",
            "allow_loopback_without_token": False,
            "allowed_origins": ["http://localhost:3000"],
        })
        ws_server, uri = await serve(server)
        try:
            async with websockets.connect(uri, origin="https://evil.example") as ws:
                with pytest.raises(Exception):
                    await ws.recv()
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(run())


def test_test_client_without_permission_capability_cannot_approve_real_permission():
    server = make_server({"auth_enabled": False})
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_test",
        "session_id": session.session_id,
        "agent": "codex",
        "risk_level": "low",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()
    server.register_client_identity(queue, "test-client", "pytest", set())

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_test",
        "approved": True,
    }, queue))

    error = json.loads(queue.get_nowait())
    assert error["type"] == "error"
    assert error["code"] == "CAPABILITY_DENIED"
    assert find_pending_permission(server, "req_test", session.session_id) is not None
    assert proxy.responses == []


def test_device_transport_requires_desktop_confirm_for_high_risk_approval():
    server = make_server({"auth_enabled": False})
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_high",
        "session_id": session.session_id,
        "agent": "codex",
        "risk_level": "high",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()
    server.register_client_identity(
        queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond", "permission:respond:low_risk"},
    )

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_high",
        "approved": True,
    }, queue))

    error = json.loads(queue.get_nowait())
    assert error["type"] == "error"
    assert error["code"] == "REQUIRE_DESKTOP_CONFIRM"
    assert find_pending_permission(server, "req_high", session.session_id) is not None
    assert proxy.responses == []


def test_device_transport_can_approve_low_risk_permission_with_low_risk_capability():
    server = make_server({"auth_enabled": False})
    proxy = FakeProxy()
    server.agents[AgentType.CODEX] = proxy
    session = server.session_mgr.create(AgentType.CODEX)
    server._on_agent_event(server.unifier.encode_device_message({
        "type": "permission_request",
        "request_id": "req_low",
        "session_id": session.session_id,
        "agent": "codex",
        "risk_level": "low",
        "timeout_sec": 30,
    }))
    queue = CaptureQueue()
    server.register_client_identity(
        queue,
        "device-transport",
        "keyboard-1",
        {"permission:respond:low_risk"},
    )

    asyncio.run(server._cmd_permission_response({
        "type": "permission_response",
        "request_id": "req_low",
        "approved": True,
    }, queue))

    ack = json.loads(queue.get_nowait())
    assert ack["type"] == "permission_ack"
    assert ack["request_id"] == "req_low"
    assert find_pending_permission(server, "req_low", session.session_id) is None
    assert proxy.responses == [(session.session_id, "req_low", True)]
