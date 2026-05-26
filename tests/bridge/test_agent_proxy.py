from pathlib import Path
import sys
import asyncio
from types import SimpleNamespace

import pytest


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(BRIDGE_DIR))
sys.path.insert(0, str(SRC_DIR))

import agent_proxy as agent_proxy_module  # noqa: E402
from agent_proxy import AgentProxy  # noqa: E402
from agents import ClaudeAgentSdkPermissionAdapter, ClaudeSdkPermissionBridge, CodexAppServerPermissionAdapter  # noqa: E402
from protocol_unifier import ProtocolUnifier  # noqa: E402
from session_manager import AgentState, AgentType, SessionManager  # noqa: E402


def make_proxy(agent_type, args=None, workspace=None):
    return AgentProxy(
        agent_type=agent_type,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="agent.exe",
        args=args or [],
        workspace=workspace,
    )


def test_claude_stream_json_command_includes_verbose():
    proxy = make_proxy(AgentType.CLAUDE)

    cmd = proxy._build_claude_cmd("sess_1", "hello")

    assert cmd == [
        "agent.exe",
        "-p",
        "hello",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def test_claude_command_dedupes_user_output_format_and_verbose_args():
    proxy = make_proxy(AgentType.CLAUDE, args=["--output-format", "json", "--verbose", "--model", "sonnet"])

    cmd = proxy._build_claude_cmd("sess_1", "hello")

    assert cmd.count("--output-format") == 1
    assert cmd.count("--verbose") == 1
    assert "--model" in cmd
    assert "sonnet" in cmd


def test_launch_creates_process_with_stdin_pipe_and_workspace_cwd(monkeypatch, tmpdir):
    workspace = Path(str(tmpdir))
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(
            pid=123,
            stdout=asyncio.StreamReader(),
            stderr=asyncio.StreamReader(),
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    proxy = make_proxy(AgentType.CODEX, workspace=workspace)
    monkeypatch.setattr(proxy, "is_available", lambda: True)
    session = proxy._sm.create(AgentType.CODEX)

    asyncio.run(proxy.launch(session.session_id, "hello"))

    assert calls[0][1]["stdin"] == asyncio.subprocess.PIPE
    assert calls[0][1]["cwd"] == str(workspace.resolve())


def test_codex_app_server_command_uses_stdio_listener():
    proxy = AgentProxy(
        agent_type=AgentType.CODEX,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="codex.exe",
        mode="app_server",
    )

    cmd = proxy._build_codex_cmd("sess_1", "hello")

    assert cmd == ["codex.exe", "app-server", "--listen", "stdio://"]


def test_codex_app_server_uses_native_permission_adapter():
    proxy = AgentProxy(
        agent_type=AgentType.CODEX,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="codex.exe",
        mode="app_server",
    )

    assert isinstance(proxy._permission_adapter, CodexAppServerPermissionAdapter)


def test_codex_app_server_status_object_maps_to_working():
    proxy = AgentProxy(
        agent_type=AgentType.CODEX,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="codex.exe",
        mode="app_server",
    )

    event = proxy._codex_app_server_notification_to_event(
        "sess_1",
        {
            "method": "thread/status/changed",
            "params": {
                "threadId": "thread_1",
                "status": {"type": "active", "activeFlags": []},
            },
        },
    )

    assert event["type"] == "task_update"
    assert event["state"] == AgentState.WORKING.value


def test_codex_app_server_retry_error_is_not_terminal_failure():
    proxy = AgentProxy(
        agent_type=AgentType.CODEX,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="codex.exe",
        mode="app_server",
    )

    event = proxy._codex_app_server_notification_to_event(
        "sess_1",
        {
            "method": "error",
            "params": {
                "error": {"message": "Reconnecting... 2/5"},
                "willRetry": True,
            },
        },
    )

    assert event["type"] == "task_update"
    assert event["state"] == AgentState.WORKING.value


def test_codex_app_server_task_stops_after_turn_completed(monkeypatch, tmpdir):
    workspace = Path(str(tmpdir))
    terminated = []
    cwd_calls = []
    monkeypatch.setattr(agent_proxy_module.sys, "platform", "linux")

    class FakeClient:
        def __init__(self, reader, writer, on_server_request=None, on_notification=None):
            self._on_notification = on_notification

        async def read_loop(self):
            await asyncio.Future()

        async def initialize(self):
            return {"ok": True}

        async def start_thread(self, cwd):
            cwd_calls.append(("thread", cwd))
            return {"thread": {"id": "thread_1"}}

        async def start_turn(self, thread_id, prompt, cwd):
            cwd_calls.append(("turn", cwd))
            self._on_notification({
                "method": "turn/completed",
                "params": {"threadId": thread_id, "turnId": "turn_1"},
            })
            return {"turn": {"id": "turn_1"}}

    class FakeProc:
        class Stderr:
            async def readline(self):
                return b""

        stdout = object()
        stderr = Stderr()
        stdin = object()
        returncode = None

        def terminate(self):
            terminated.append("terminate")
            self.returncode = 0

        async def wait(self):
            return 0

    monkeypatch.setattr(agent_proxy_module, "CodexAppServerClient", FakeClient)
    proxy = AgentProxy(
        agent_type=AgentType.CODEX,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="codex.exe",
        mode="app_server",
        workspace=workspace,
    )
    session = proxy._sm.create(AgentType.CODEX)

    async def run():
        task = asyncio.create_task(proxy._read_codex_app_server(session.session_id, FakeProc(), "hello"))
        proxy._read_tasks[session.session_id] = task
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run())

    assert terminated == ["terminate"]
    assert cwd_calls == [
        ("thread", str(workspace.resolve())),
        ("turn", str(workspace.resolve())),
    ]
    assert session.session_id not in proxy._processes
    assert session.session_id not in proxy._read_tasks
    assert proxy._sm.get(session.session_id).state == AgentState.COMPLETED


def test_unsupported_permission_forwarding_reports_evidence():
    proxy = make_proxy(AgentType.CODEX)

    result = asyncio.run(proxy.handle_permission_response("sess_1", "req_1", True))

    assert result == {
        "accepted": True,
        "forwarded": False,
        "evidence": {
            "adapter": "unsupported",
            "reason": "native_permission_channel_unavailable",
            "session_id": "sess_1",
            "request_id": "req_1",
            "approved": True,
        },
    }


def test_claude_permission_bridge_forwards_control_response():
    writes = []
    bridge = ClaudeSdkPermissionBridge(native_writer=writes.append)
    bridge.register_control_request(
        "sess_1",
        {
            "type": "control_request",
            "subtype": "can_use_tool",
            "request_id": "req_1",
            "data": {"tool": "Bash"},
        },
    )

    result = asyncio.run(bridge.forward_permission_response("sess_1", "req_1", False))

    assert result["accepted"] is True
    assert result["forwarded"] is True
    assert result["evidence"]["adapter"] == "claude_sdk_permission_bridge"
    assert result["evidence"]["native_response"] == {
        "type": "control_response",
        "request_id": "req_1",
        "response": {"approved": False},
    }
    assert writes == [result["evidence"]["native_response"]]


def test_claude_permission_bridge_without_native_writer_does_not_claim_forwarded():
    bridge = ClaudeSdkPermissionBridge()
    bridge.register_control_request(
        "sess_1",
        {
            "type": "control_request",
            "subtype": "can_use_tool",
            "request_id": "req_1",
            "data": {"tool": "Bash"},
        },
    )

    result = asyncio.run(bridge.forward_permission_response("sess_1", "req_1", True))

    assert result["accepted"] is True
    assert result["forwarded"] is False
    assert result["evidence"]["reason"] == "native_permission_channel_unavailable"


def test_claude_agent_sdk_permission_adapter_resolves_callback():
    pytest.importorskip("claude_agent_sdk")
    events = []

    class Context:
        tool_use_id = "tool_1"
        description = "https://example.com"
        title = "Fetch example"

    adapter = ClaudeAgentSdkPermissionAdapter(events.append, timeout_sec=5)

    async def run():
        callback_task = asyncio.create_task(
            adapter.can_use_tool(
                "sess_1",
                "WebFetch",
                {"url": "https://example.com"},
                Context(),
            )
        )
        for _ in range(10):
            if events:
                break
            await asyncio.sleep(0)

        assert events[0]["type"] == "permission_request"
        assert events[0]["request_id"] == "tool_1"
        assert events[0]["native"]["channel"] == "can_use_tool"

        forward_result = await adapter.forward_permission_response("sess_1", "tool_1", True)
        permission_result = await callback_task
        return forward_result, permission_result

    result, permission = asyncio.run(run())

    assert result["accepted"] is True
    assert result["forwarded"] is True
    assert result["evidence"]["adapter"] == "claude_agent_sdk"
    assert result["evidence"]["native_channel"] == "claude_agent_sdk.can_use_tool"
    assert permission.__class__.__name__ == "PermissionResultAllow"


def test_claude_agent_sdk_options_use_workspace(monkeypatch, tmpdir):
    workspace = Path(str(tmpdir))
    captured = {}

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ResultMessage:
        is_error = False
        result = "done"

    async def query(prompt, options):
        captured["options"] = options
        yield ResultMessage()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=ClaudeAgentOptions, query=query),
    )
    proxy = AgentProxy(
        agent_type=AgentType.CLAUDE,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="claude.exe",
        mode="agent_sdk",
        workspace=workspace,
    )
    session = proxy._sm.create(AgentType.CLAUDE)

    async def run():
        await proxy._run_claude_agent_sdk(session.session_id, "hello", asyncio.Event())

    asyncio.run(run())

    assert captured["options"].kwargs["cwd"] == workspace.resolve()
