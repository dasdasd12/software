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


def make_codex_app_server_proxy(workspace=None):
    return AgentProxy(
        agent_type=AgentType.CODEX,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="codex.exe",
        mode="app_server",
        workspace=workspace,
    )


def make_claude_sdk_proxy(workspace=None):
    return AgentProxy(
        agent_type=AgentType.CLAUDE,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="claude.exe",
        mode="agent_sdk",
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


def test_launch_failure_includes_exception_class_when_message_is_empty(monkeypatch):
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise NotImplementedError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    proxy = make_proxy(AgentType.CODEX)
    monkeypatch.setattr(proxy, "is_available", lambda: True)
    session = proxy._sm.create(AgentType.CODEX)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(proxy.launch(session.session_id, "hello"))

    assert str(exc_info.value) == "Failed to start codex: NotImplementedError"
    assert proxy._sm.get(session.session_id).state == AgentState.FAILED
    assert session.session_id not in proxy._session_workspaces


def test_codex_app_server_command_uses_stdio_listener():
    proxy = make_codex_app_server_proxy()

    cmd = proxy._build_codex_cmd("sess_1", "hello")

    assert cmd == ["codex.exe", "app-server", "--listen", "stdio://"]


def test_codex_app_server_uses_native_permission_adapter():
    proxy = make_codex_app_server_proxy()

    assert isinstance(proxy._permission_adapter, CodexAppServerPermissionAdapter)


def test_codex_app_server_status_object_maps_to_working():
    proxy = make_codex_app_server_proxy()

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
    proxy = make_codex_app_server_proxy()

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


def test_codex_app_server_keeps_native_channel_after_turn_completed(monkeypatch, tmpdir):
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
        proc = FakeProc()
        proxy._processes[session.session_id] = proc
        task = asyncio.create_task(proxy._read_codex_app_server(session.session_id, proc, "hello"))
        proxy._read_tasks[session.session_id] = task
        for _ in range(20):
            if proxy._sm.get(session.session_id).state == AgentState.COMPLETED:
                break
            await asyncio.sleep(0)
        assert proxy._sm.get(session.session_id).state == AgentState.COMPLETED
        assert task.done() is False
        assert session.session_id in proxy._codex_clients
        assert proxy._codex_thread_ids[session.session_id] == "thread_1"
        assert proxy._session_workspaces[session.session_id] == workspace.resolve()
        assert session.session_id in proxy._read_tasks
        assert await proxy.terminate(session.session_id) is True

    asyncio.run(run())

    assert terminated
    assert cwd_calls == [
        ("thread", str(workspace.resolve())),
        ("turn", str(workspace.resolve())),
    ]
    assert session.session_id not in proxy._processes
    assert session.session_id not in proxy._read_tasks
    assert session.session_id not in proxy._session_workspaces
    assert session.session_id not in proxy._codex_clients
    assert session.session_id not in proxy._codex_thread_ids
    assert proxy._sm.get(session.session_id).state == AgentState.CANCELLED


def test_codex_app_server_send_input_after_completed_turn_reuses_thread_and_workspace(monkeypatch, tmpdir):
    workspace = Path(str(tmpdir))
    turns = []
    monkeypatch.setattr(agent_proxy_module.sys, "platform", "linux")

    class FakeClient:
        def __init__(self, reader, writer, on_server_request=None, on_notification=None):
            self._on_notification = on_notification

        async def read_loop(self):
            await asyncio.Future()

        async def initialize(self):
            return {"ok": True}

        async def start_thread(self, cwd):
            return {"thread": {"id": "thread_1"}}

        async def start_turn(self, thread_id, prompt, cwd):
            turns.append({
                "thread_id": thread_id,
                "prompt": prompt,
                "cwd": cwd,
            })
            self._on_notification({
                "method": "turn/completed",
                "params": {"threadId": thread_id, "turnId": "turn_%d" % len(turns)},
            })
            return {"turn": {"id": "turn_%d" % len(turns)}}

    class FakeProc:
        class Stderr:
            async def readline(self):
                return b""

        stdout = object()
        stderr = Stderr()
        stdin = object()
        returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return 0

    monkeypatch.setattr(agent_proxy_module, "CodexAppServerClient", FakeClient)
    proxy = make_codex_app_server_proxy(workspace=workspace)
    session = proxy._sm.create(AgentType.CODEX)

    async def run():
        proc = FakeProc()
        proxy._processes[session.session_id] = proc
        task = asyncio.create_task(proxy._read_codex_app_server(session.session_id, proc, "hello"))
        proxy._read_tasks[session.session_id] = task
        for _ in range(20):
            if proxy._sm.get(session.session_id).state == AgentState.COMPLETED:
                break
            await asyncio.sleep(0)
        assert proxy._sm.get(session.session_id).state == AgentState.COMPLETED
        accepted = await proxy.send_input(session.session_id, "next prompt")
        for _ in range(20):
            if len(turns) == 2:
                break
            await asyncio.sleep(0)
        await proxy.terminate(session.session_id)
        return accepted

    accepted = asyncio.run(run())

    assert accepted is True
    assert turns == [
        {
            "thread_id": "thread_1",
            "prompt": "hello",
            "cwd": str(workspace.resolve()),
        },
        {
            "thread_id": "thread_1",
            "prompt": "next prompt",
            "cwd": str(workspace.resolve()),
        },
    ]


def test_codex_app_server_send_input_starts_turn_with_existing_thread(tmpdir):
    class FakeCodexClient:
        def __init__(self, proxy):
            self.turns = []
            self._proxy = proxy

        async def start_turn(self, thread_id, prompt, cwd):
            self.turns.append({
                "thread_id": thread_id,
                "prompt": prompt,
                "cwd": cwd,
            })
            self._proxy._handle_codex_notification(session_id, {
                "method": "turn/completed",
                "params": {"threadId": thread_id, "turnId": "turn_1"},
            })

    workspace = Path(str(tmpdir)).resolve()
    proxy = make_codex_app_server_proxy(workspace=workspace)
    session = proxy._sm.create(AgentType.CODEX)
    session_id = session.session_id
    proxy._session_workspaces[session_id] = workspace
    proxy._codex_thread_ids[session_id] = "thread_1"
    proxy._codex_clients[session_id] = FakeCodexClient(proxy)

    async def run():
        proxy._codex_turn_done_events[session_id] = asyncio.Event()
        proxy._codex_turn_done_events[session_id].set()
        accepted = await proxy.send_input(session_id, "next prompt")
        for _ in range(20):
            if proxy._codex_clients[session_id].turns:
                break
            await asyncio.sleep(0)
        task = proxy._codex_input_tasks.pop(session_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return accepted

    accepted = asyncio.run(run())

    assert accepted is True
    assert proxy._sm.get(session_id).state == AgentState.COMPLETED
    assert proxy._codex_clients[session_id].turns == [{
        "thread_id": "thread_1",
        "prompt": "next prompt",
        "cwd": str(workspace),
    }]


def test_codex_app_server_send_input_serializes_follow_up_turns(tmpdir):
    class FakeCodexClient:
        def __init__(self):
            self.turns = []

        async def start_turn(self, thread_id, prompt, cwd):
            self.turns.append({
                "thread_id": thread_id,
                "prompt": prompt,
                "cwd": cwd,
            })

    workspace = Path(str(tmpdir)).resolve()
    proxy = make_codex_app_server_proxy(workspace=workspace)
    session = proxy._sm.create(AgentType.CODEX)
    session_id = session.session_id
    client = FakeCodexClient()
    proxy._session_workspaces[session_id] = workspace
    proxy._codex_thread_ids[session_id] = "thread_1"
    proxy._codex_clients[session_id] = client

    async def run():
        done_event = asyncio.Event()
        done_event.set()
        proxy._codex_turn_done_events[session_id] = done_event
        first = await proxy.send_input(session_id, "first")
        second = await proxy.send_input(session_id, "second")
        for _ in range(20):
            if len(client.turns) == 1:
                break
            await asyncio.sleep(0)
        assert [turn["prompt"] for turn in client.turns] == ["first"]
        done_event.set()
        for _ in range(20):
            if len(client.turns) == 2:
                break
            await asyncio.sleep(0)
        task = proxy._codex_input_tasks.pop(session_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return first, second

    first, second = asyncio.run(run())

    assert first is True
    assert second is True
    assert client.turns == [
        {"thread_id": "thread_1", "prompt": "first", "cwd": str(workspace)},
        {"thread_id": "thread_1", "prompt": "second", "cwd": str(workspace)},
    ]


def test_codex_app_server_send_input_returns_false_without_client_or_thread():
    proxy = make_codex_app_server_proxy()

    assert asyncio.run(proxy.send_input("sess_1", "next prompt")) is False

    proxy._codex_clients["sess_1"] = object()
    assert asyncio.run(proxy.send_input("sess_1", "next prompt")) is False


def test_claude_agent_sdk_send_input_enqueues_prompt():
    proxy = make_claude_sdk_proxy()

    async def run():
        proxy._sdk_prompt_queues["sess_1"] = asyncio.Queue()
        accepted = await proxy.send_input("sess_1", "next prompt")
        queued = await proxy._sdk_prompt_queues["sess_1"].get()
        return accepted, queued

    accepted, queued = asyncio.run(run())
    assert accepted is True
    assert queued == "next prompt"


def test_claude_agent_sdk_send_input_returns_false_without_active_queue():
    proxy = make_claude_sdk_proxy()

    assert asyncio.run(proxy.send_input("sess_1", "next prompt")) is False


def test_claude_agent_sdk_keeps_queue_after_result_and_runs_follow_up(monkeypatch, tmpdir):
    workspace = Path(str(tmpdir))
    prompts = []
    clients = []

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ResultMessage:
        is_error = False
        result = "done"

    class ClaudeSDKClient:
        def __init__(self, options):
            self.options = options
            self.connected = False
            self.disconnected = False
            self.query_count = 0
            clients.append(self)

        async def connect(self):
            self.connected = True

        async def query(self, prompt):
            assert self.connected is True
            self.query_count += 1
            prompts.append(prompt)

        async def receive_response(self):
            yield ResultMessage()

        async def disconnect(self):
            self.disconnected = True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=ClaudeAgentOptions, ClaudeSDKClient=ClaudeSDKClient),
    )
    proxy = make_claude_sdk_proxy(workspace=workspace)
    session = proxy._sm.create(AgentType.CLAUDE)
    proxy._session_workspaces[session.session_id] = workspace.resolve()

    async def run():
        done_event = asyncio.Event()
        task = asyncio.create_task(
            proxy._run_claude_agent_sdk(session.session_id, "hello", done_event, workspace)
        )
        for _ in range(20):
            if prompts == ["hello"] and proxy._sm.get(session.session_id).state == AgentState.COMPLETED:
                break
            await asyncio.sleep(0)
        assert prompts == ["hello"]
        assert proxy._sm.get(session.session_id).state == AgentState.COMPLETED
        assert task.done() is False
        assert session.session_id in proxy._sdk_prompt_queues
        assert proxy._session_workspaces[session.session_id] == workspace.resolve()
        accepted = await proxy.send_input(session.session_id, "next prompt")
        for _ in range(20):
            if prompts == ["hello", "next prompt"]:
                break
            await asyncio.sleep(0)
        done_event.set()
        await asyncio.wait_for(task, timeout=1)
        return accepted

    accepted = asyncio.run(run())

    assert accepted is True
    assert prompts == ["hello", "next prompt"]
    assert len(clients) == 1
    assert clients[0].query_count == 2
    assert clients[0].disconnected is True


def test_legacy_subprocess_send_input_writes_line_and_drains():
    class FakeStdin:
        def __init__(self):
            self.writes = []
            self.drained = False

        def write(self, data):
            self.writes.append(data)

        async def drain(self):
            self.drained = True

    stdin = FakeStdin()
    proxy = make_proxy(AgentType.CODEX)
    proxy._processes["sess_1"] = SimpleNamespace(stdin=stdin)

    accepted = asyncio.run(proxy.send_input("sess_1", "next prompt"))

    assert accepted is True
    assert stdin.writes == [b"next prompt\n"]
    assert stdin.drained is True


def test_legacy_subprocess_send_input_returns_false_without_stdin():
    proxy = make_proxy(AgentType.CODEX)

    assert asyncio.run(proxy.send_input("sess_1", "next prompt")) is False

    proxy._processes["sess_1"] = SimpleNamespace(stdin=None)
    assert asyncio.run(proxy.send_input("sess_1", "next prompt")) is False


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

    class ClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self):
            return None

        async def query(self, prompt):
            return None

        async def receive_response(self):
            yield ResultMessage()

        async def disconnect(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=ClaudeAgentOptions, ClaudeSDKClient=ClaudeSDKClient),
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
        done_event = asyncio.Event()
        task = asyncio.create_task(
            proxy._run_claude_agent_sdk(session.session_id, "hello", done_event)
        )
        for _ in range(20):
            if captured.get("options") is not None:
                break
            await asyncio.sleep(0)
        done_event.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run())

    assert captured["options"].kwargs["cwd"] == workspace.resolve()
