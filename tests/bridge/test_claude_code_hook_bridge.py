import asyncio
import importlib.util
import io
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


async def wait_for_payload(queue, payload_type, timeout=1.0):
    deadline = asyncio.get_event_loop().time() + timeout
    seen = 0
    while asyncio.get_event_loop().time() < deadline:
        for raw in list(queue.items)[seen:]:
            payload = json.loads(raw)
            if payload.get("type") == payload_type:
                return payload
        seen = len(queue.items)
        await asyncio.sleep(0.01)
    return None


def test_hook_script_builds_fail_closed_permission_response():
    module = load_hook_module()

    response = module.permission_denied_response("bridge unavailable")

    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PermissionRequest"
    assert output["decision"]["behavior"] == "deny"
    assert output["decision"]["interrupt"] is True


def test_hook_script_fails_closed_for_pretooluse_bridge_error(monkeypatch, capsys):
    module = load_hook_module()

    async def fail_bridge(args, hook_input):
        raise RuntimeError("Local API unavailable")

    monkeypatch.setattr(module, "run_hook", fail_bridge)
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "Continue?", "options": ["yes", "no"]}]},
    })))

    assert module.main(["--session-id", "sess_native"]) == 0

    raw = capsys.readouterr().out.strip()
    response = json.loads(raw)
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    assert "Local API hook bridge failed closed" in output["permissionDecisionReason"]


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


def test_claude_ask_user_question_hook_round_trips_to_interaction_response():
    server = make_server()
    hook_queue = CaptureQueue()
    desktop_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    server.connected_clients.add(desktop_queue)
    register_desktop(server, desktop_queue)
    session = create_native_claude_session(server)
    register_hook(server, hook_queue, session.session_id)

    question_text = "Pick the next step"
    hook = {
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{
                "question": question_text,
                "options": ["Continue", "Stop"],
            }],
        },
    }

    async def run():
        hook_task = asyncio.create_task(server._cmd_claude_hook_event({
            "type": "claude_hook_event",
            "session_id": session.session_id,
            "hook": hook,
        }, hook_queue))

        request = await wait_for_payload(desktop_queue, "interaction_request")
        assert request is not None
        assert request["session_id"] == session.session_id
        assert request["agent"] == "claude"
        assert request["interaction_type"] == "ask_user_question"
        assert request["tool_name"] == "AskUserQuestion"
        assert request["questions"] == hook["tool_input"]["questions"]
        assert request["native"]["adapter"] == "claude_code_hook"
        assert request["native"]["native_channel"] == "PreToolUse"
        server._sync_runtime_state()
        snapshot = server.runtime.snapshot().to_dict()
        assert snapshot["interactions"][0]["request_id"] == request["request_id"]
        assert snapshot["interactions"][0]["questions"] == hook["tool_input"]["questions"]

        await server._handle_local_api_message({
            "type": "interaction_response",
            "session_id": session.session_id,
            "request_id": request["request_id"],
            "approved": True,
            "answers": {question_text: "Continue"},
        }, desktop_queue)
        await hook_task
        return request

    request = asyncio.run(run())

    hook_result = [json.loads(raw) for raw in hook_queue.items if json.loads(raw).get("type") == "claude_hook_result"][-1]
    hook_response = hook_result["hook_response"]["hookSpecificOutput"]
    assert hook_response["hookEventName"] == "PreToolUse"
    assert hook_response["permissionDecision"] == "allow"
    assert hook_response["updatedInput"]["questions"] == hook["tool_input"]["questions"]
    assert hook_response["updatedInput"]["answers"] == {question_text: "Continue"}

    ack = [json.loads(raw) for raw in desktop_queue.items if json.loads(raw).get("type") == "interaction_ack"][-1]
    assert ack["request_id"] == request["request_id"]
    assert ack["forwarded"] is True
    assert ack["evidence"]["adapter"] == "claude_code_hook"
    assert ack["evidence"]["native_channel"] == "PreToolUse"
    assert ack["evidence"]["response_written"] is True
    server._sync_runtime_state()
    assert server.runtime.snapshot().to_dict()["interactions"] == []


def test_claude_exit_plan_mode_hook_approve_echoes_plan_input():
    server = make_server()
    hook_queue = CaptureQueue()
    desktop_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    server.connected_clients.add(desktop_queue)
    register_desktop(server, desktop_queue)
    session = create_native_claude_session(server)
    register_hook(server, hook_queue, session.session_id)
    hook = {
        "hook_event_name": "PreToolUse",
        "tool_name": "ExitPlanMode",
        "tool_input": {
            "plan": "1. inspect\n2. implement",
            "planFilePath": "C:/repo/PLAN.md",
            "allowedPrompts": ["Implement this plan"],
        },
    }

    async def run():
        hook_task = asyncio.create_task(server._cmd_claude_hook_event({
            "type": "claude_hook_event",
            "session_id": session.session_id,
            "hook": hook,
        }, hook_queue))
        request = await wait_for_payload(desktop_queue, "interaction_request")
        assert request is not None
        assert request["interaction_type"] == "exit_plan_mode"
        assert request["plan"] == hook["tool_input"]["plan"]
        assert request["allowedPrompts"] == hook["tool_input"]["allowedPrompts"]
        await server._handle_local_api_message({
            "type": "interaction_response",
            "session_id": session.session_id,
            "request_id": request["request_id"],
            "approved": True,
        }, desktop_queue)
        await hook_task

    asyncio.run(run())

    hook_result = [json.loads(raw) for raw in hook_queue.items if json.loads(raw).get("type") == "claude_hook_result"][-1]
    hook_response = hook_result["hook_response"]["hookSpecificOutput"]
    assert hook_response["hookEventName"] == "PreToolUse"
    assert hook_response["permissionDecision"] == "allow"
    assert hook_response["updatedInput"]["plan"] == hook["tool_input"]["plan"]
    assert hook_response["updatedInput"]["planFilePath"] == hook["tool_input"]["planFilePath"]
    assert hook_response["updatedInput"]["allowedPrompts"] == hook["tool_input"]["allowedPrompts"]
    assert hook_response["updatedInput"]["approved"] is True


def test_claude_exit_plan_mode_hook_deny_returns_denial_reason():
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
                "hook_event_name": "PreToolUse",
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "unsafe plan"},
            },
        }, hook_queue))
        request = await wait_for_payload(desktop_queue, "interaction_request")
        assert request is not None
        await server._handle_local_api_message({
            "type": "interaction_response",
            "session_id": session.session_id,
            "request_id": request["request_id"],
            "approved": False,
            "reason": "Need a safer plan",
        }, desktop_queue)
        await hook_task

    asyncio.run(run())

    hook_result = [json.loads(raw) for raw in hook_queue.items if json.loads(raw).get("type") == "claude_hook_result"][-1]
    hook_response = hook_result["hook_response"]["hookSpecificOutput"]
    assert hook_response["hookEventName"] == "PreToolUse"
    assert hook_response["permissionDecision"] == "deny"
    assert hook_response["permissionDecisionReason"] == "Need a safer plan"


def test_claude_pretooluse_interaction_times_out_fail_closed():
    server = make_server()
    server.cfg["unifier"]["permission_timeout_sec"] = 0
    hook_queue = CaptureQueue()
    desktop_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    server.connected_clients.add(desktop_queue)
    register_desktop(server, desktop_queue)
    session = create_native_claude_session(server)
    register_hook(server, hook_queue, session.session_id)

    asyncio.run(server._cmd_claude_hook_event({
        "type": "claude_hook_event",
        "session_id": session.session_id,
        "hook": {
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "Continue?", "options": ["yes", "no"]}]},
        },
    }, hook_queue))

    request = [json.loads(raw) for raw in desktop_queue.items if json.loads(raw).get("type") == "interaction_request"][-1]
    assert request["timeout_sec"] == 0
    hook_result = [json.loads(raw) for raw in hook_queue.items if json.loads(raw).get("type") == "claude_hook_result"][-1]
    hook_response = hook_result["hook_response"]["hookSpecificOutput"]
    assert hook_response["hookEventName"] == "PreToolUse"
    assert hook_response["permissionDecision"] == "deny"
    assert request["request_id"] not in server._claude_hook_interactions
    ack = [json.loads(raw) for raw in desktop_queue.items if json.loads(raw).get("type") == "interaction_ack"][-1]
    assert ack["request_id"] == request["request_id"]
    assert ack["approved"] is False
    assert ack["forwarded"] is True
    assert ack["evidence"]["adapter"] == "claude_code_hook"
    assert ack["evidence"]["native_channel"] == "PreToolUse"
    assert ack["evidence"]["decision_delivered"] is True
    assert ack["evidence"]["queued_to_hook_client"] is True
    assert ack["evidence"]["response_written"] is True


def test_structured_agent_interaction_respond_round_trips_to_pretooluse_hook():
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
                "hook_event_name": "PreToolUse",
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": [{"question": "Pick", "options": ["A", "B"]}]},
            },
        }, hook_queue))
        request = await wait_for_payload(desktop_queue, "interaction_request")
        assert request is not None

        await server._handle_local_api_message({
            "type": "command",
            "command": {
                "command_id": "cmd_interaction_respond",
                "type": "agent.interaction.respond",
                "source": {"kind": "desktop-ui", "client_id": "pytest-desktop"},
                "target": {
                    "session_id": session.session_id,
                    "request_id": request["request_id"],
                },
                "payload": {
                    "approved": True,
                    "answers": {"Pick": "A"},
                },
            },
        }, desktop_queue)
        await hook_task
        return request

    request = asyncio.run(run())

    hook_result = [json.loads(raw) for raw in hook_queue.items if json.loads(raw).get("type") == "claude_hook_result"][-1]
    hook_response = hook_result["hook_response"]["hookSpecificOutput"]
    assert hook_response["permissionDecision"] == "allow"
    assert hook_response["updatedInput"]["answers"] == {"Pick": "A"}
    ack = [json.loads(raw) for raw in desktop_queue.items if json.loads(raw).get("type") == "interaction_ack"][-1]
    assert ack["request_id"] == request["request_id"]
    assert ack["forwarded"] is True


def test_structured_agent_interaction_respond_requires_permission_capability():
    server = make_server()
    hook_queue = CaptureQueue()
    no_cap_queue = CaptureQueue()
    desktop_queue = CaptureQueue()
    server.connected_clients.add(hook_queue)
    server.connected_clients.add(no_cap_queue)
    server.connected_clients.add(desktop_queue)
    register_desktop(server, desktop_queue)
    server.register_client_identity(no_cap_queue, "test-client", "readonly", {"session:list"})
    session = create_native_claude_session(server)
    register_hook(server, hook_queue, session.session_id)

    async def run():
        hook_task = asyncio.create_task(server._cmd_claude_hook_event({
            "type": "claude_hook_event",
            "session_id": session.session_id,
            "hook": {
                "hook_event_name": "PreToolUse",
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": [{"question": "Pick", "options": ["A", "B"]}]},
            },
        }, hook_queue))
        request = await wait_for_payload(desktop_queue, "interaction_request")
        assert request is not None

        await server._handle_local_api_message({
            "type": "command",
            "command": {
                "command_id": "cmd_interaction_respond_denied",
                "type": "agent.interaction.respond",
                "source": {"kind": "test-client", "client_id": "readonly"},
                "target": {
                    "session_id": session.session_id,
                    "request_id": request["request_id"],
                },
                "payload": {
                    "approved": True,
                    "answers": {"Pick": "A"},
                },
            },
        }, no_cap_queue)
        hook_task.cancel()
        try:
            await hook_task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())

    errors = [json.loads(raw) for raw in no_cap_queue.items if json.loads(raw).get("type") == "error"]
    assert errors
    error = errors[-1]
    assert error["type"] == "error"
    assert error["code"] == "CAPABILITY_DENIED"


def test_interaction_response_unknown_request_does_not_ack_forwarded():
    server = make_server()
    desktop_queue = CaptureQueue()
    server.connected_clients.add(desktop_queue)
    register_desktop(server, desktop_queue)

    asyncio.run(server._handle_local_api_message({
        "type": "interaction_response",
        "session_id": "sess_missing",
        "request_id": "missing",
        "approved": True,
        "answers": {},
    }, desktop_queue))

    payloads = [json.loads(raw) for raw in desktop_queue.items]
    assert payloads[-1]["type"] == "error"
    assert payloads[-1]["code"] == "REQUEST_NOT_FOUND"
    assert not any(
        payload.get("type") == "interaction_ack" and payload.get("forwarded") is True
        for payload in payloads
    )


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
