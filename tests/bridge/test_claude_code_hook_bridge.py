import asyncio
import importlib.util
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
BRIDGE_DIR = ROOT_DIR / "src" / "bridge"
HOOK_SCRIPT = ROOT_DIR / "scripts" / "claude-code-hook.py"
sys.path.insert(0, str(BRIDGE_DIR))

from server import BridgeServer  # noqa: E402


class CaptureQueue:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)

    def put_nowait(self, item):
        self.items.append(item)

    def pop_json(self):
        return json.loads(self.items.pop(0))


def load_hook_module():
    spec = importlib.util.spec_from_file_location("claude_code_hook", HOOK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_server():
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
    }
    return BridgeServer(config)


def register_desktop(server, queue):
    server.register_client_identity(
        queue,
        "desktop-ui",
        "pytest-desktop",
        {"agent:launch", "permission:respond", "session:list"},
    )


def register_hook(server, queue, session_id="sess_test"):
    server.register_client_identity(
        queue,
        "agent-hook",
        "claude-code-hook:%s" % session_id,
        {"claude:hook"},
    )


def create_native_claude_session(server):
    session = server.session_mgr.create(server._agent_from_string("claude"))
    session.launch_surface = "foreground_cli"
    session.control_mode = "native_cli"
    return session


def test_hook_script_builds_fail_closed_permission_response():
    module = load_hook_module()

    response = module.permission_denied_response("bridge unavailable")

    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PermissionRequest"
    assert output["decision"]["behavior"] == "deny"
    assert output["decision"]["interrupt"] is True


def test_claude_permission_hook_round_trips_to_permission_response():
    server = make_server()
    hook_queue = CaptureQueue()
    desktop_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    server.connected_clients.add(desktop_queue)
    register_desktop(server, desktop_queue)
    session = create_native_claude_session(server)
    register_hook(server, hook_queue, session.session_id)

    async def run():
        hook_task = asyncio.create_task(server._cmd_claude_hook_event({
            "type": "claude_hook_event",
            "session_id": session.session_id,
            "hook": {
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "tool_input": {"command": "python -c \"print('ok')\""},
                "permission_suggestions": [{
                    "type": "addRules",
                    "rules": [{"toolName": "Bash", "ruleContent": "python -c \"print('ok')\""}],
                    "behavior": "allow",
                    "destination": "localSettings",
                }],
            },
        }, hook_queue))

        request = None
        for _ in range(10):
            await asyncio.sleep(0)
            for raw in list(desktop_queue.items):
                payload = json.loads(raw)
                if payload.get("type") == "permission_request":
                    request = payload
                    break
            if request:
                break
        assert request is not None

        await server._cmd_permission_response({
            "type": "permission_response",
            "request_id": request["request_id"],
            "session_id": session.session_id,
            "approved": True,
            "decision": "always_allow",
        }, desktop_queue)
        await hook_task
        return request

    request = asyncio.run(run())

    hook_result = [json.loads(raw) for raw in hook_queue.items if json.loads(raw).get("type") == "claude_hook_result"][-1]
    hook_response = hook_result["hook_response"]["hookSpecificOutput"]
    assert hook_response["hookEventName"] == "PermissionRequest"
    assert hook_response["decision"]["behavior"] == "allow"
    assert hook_response["decision"]["updatedPermissions"]

    ack = [json.loads(raw) for raw in desktop_queue.items if json.loads(raw).get("type") == "permission_ack"][-1]
    assert ack["request_id"] == request["request_id"]
    assert ack["forwarded"] is True
    assert ack["evidence"]["adapter"] == "claude_code_hook"


def test_claude_hook_event_requires_hook_capability():
    server = make_server()
    hook_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    register_desktop(server, hook_queue)
    session = create_native_claude_session(server)

    asyncio.run(server._handle_local_api_message({
        "type": "claude_hook_event",
        "session_id": session.session_id,
        "hook": {"hook_event_name": "Stop"},
    }, hook_queue))

    payload = hook_queue.pop_json()
    assert payload["type"] == "error"
    assert payload["code"] == "CAPABILITY_DENIED"


def test_claude_hook_event_rejects_non_native_session():
    server = make_server()
    hook_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    session = server.session_mgr.create(server._agent_from_string("claude"))
    register_hook(server, hook_queue, session.session_id)

    asyncio.run(server._cmd_claude_hook_event({
        "type": "claude_hook_event",
        "session_id": session.session_id,
        "hook": {"hook_event_name": "Stop"},
    }, hook_queue))

    payload = hook_queue.pop_json()
    assert payload["type"] == "error"
    assert payload["code"] == "INVALID_HOOK_SESSION"


def test_claude_hook_event_requires_agent_hook_identity_even_with_capability():
    server = make_server()
    hook_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    server.register_client_identity(
        hook_queue,
        "desktop-ui",
        "desktop-with-hook-cap",
        {"claude:hook"},
    )
    session = create_native_claude_session(server)

    asyncio.run(server._handle_local_api_message({
        "type": "claude_hook_event",
        "session_id": session.session_id,
        "hook": {"hook_event_name": "Stop"},
    }, hook_queue))

    payload = hook_queue.pop_json()
    assert payload["type"] == "error"
    assert payload["code"] == "CAPABILITY_DENIED"


def test_claude_hook_event_rejects_session_mismatch():
    server = make_server()
    hook_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    session = create_native_claude_session(server)
    register_hook(server, hook_queue, "sess_other")

    asyncio.run(server._cmd_claude_hook_event({
        "type": "claude_hook_event",
        "session_id": session.session_id,
        "hook": {"hook_event_name": "Stop"},
    }, hook_queue))

    payload = hook_queue.pop_json()
    assert payload["type"] == "error"
    assert payload["code"] == "INVALID_HOOK_SESSION"
