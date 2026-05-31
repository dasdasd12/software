import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = ROOT_DIR / "scripts" / "local-api-smoke.py"


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("local_api_smoke", SMOKE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = [json.dumps(message) for message in messages]
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        if not self.messages:
            raise AssertionError("fake websocket received too many reads")
        return self.messages.pop(0)


class RepeatingWebSocket(FakeWebSocket):
    def __init__(self, messages, repeated_message, *, max_reads=10000):
        super().__init__(messages)
        self.repeated_message = json.dumps(repeated_message)
        self.recv_count = 0
        self.max_reads = max_reads

    async def recv(self):
        self.recv_count += 1
        if self.recv_count > self.max_reads:
            raise AssertionError("approval-real did not stop at the configured deadline")
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0.001)
        return self.repeated_message


class SlowRepeatingWebSocket:
    def __init__(self, repeated_message, *, max_reads=10000):
        self.repeated_message = json.dumps(repeated_message)
        self.recv_count = 0
        self.max_reads = max_reads

    async def recv(self):
        self.recv_count += 1
        if self.recv_count > self.max_reads:
            raise AssertionError("wait_for_type_for_session did not stop at the configured deadline")
        if self.recv_count > 1:
            await asyncio.sleep(0.001)
        return self.repeated_message


class FakeConnect:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_client(module, *, workspace=None):
    return module.LocalApiSmokeClient(
        "ws://127.0.0.1:8765",
        1.0,
        False,
        "",
        "desktop-ui",
        "local-api-smoke",
        ["agent:launch", "permission:respond", "session:list"],
        workspace=workspace,
    )


def test_local_api_smoke_help_exposes_loopback_flags():
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--workspace" in result.stdout
    assert "--auto-start-service" in result.stdout
    assert "--config" in result.stdout
    assert "--wait-for-hotkey-approval" in result.stdout
    assert "--service-start-timeout" in result.stdout
    assert "foreground-cli" in result.stdout
    assert "foreground-approval-real" in result.stdout


def test_real_agent_launch_payload_includes_workspace(monkeypatch, tmpdir):
    module = load_smoke_module()
    workspace = str(tmpdir)
    ws = FakeWebSocket([
        {"type": "hello_ack"},
        {"type": "task_completed", "session_id": "sess_workspace"},
    ])
    monkeypatch.setattr(module.websockets, "connect", lambda url: FakeConnect(ws))

    asyncio.run(make_client(module, workspace=workspace).run_real_agent("codex", "hello"))

    launch = next(message for message in ws.sent if message["type"] == "agent_launch")
    assert launch["workspace"] == workspace


def test_approval_hotkey_mode_waits_for_external_ack_without_sending_response(monkeypatch, tmpdir):
    module = load_smoke_module()
    workspace = str(tmpdir)
    ws = FakeWebSocket([
        {"type": "hello_ack"},
        {
            "type": "task_update",
            "session_id": "sess_hotkey",
            "agent": "codex",
            "state": "submitted",
        },
        {
            "type": "permission_request",
            "session_id": "sess_hotkey",
            "request_id": "req_hotkey",
        },
        {
            "type": "permission_ack",
            "session_id": "sess_hotkey",
            "request_id": "req_hotkey",
            "approved": True,
            "forwarded": True,
            "evidence": {
                "adapter": "codex_app_server",
                "response_written": True,
                "decision_delivered": True,
            },
        },
        {"type": "task_completed", "session_id": "sess_hotkey"},
    ])
    monkeypatch.setattr(module.websockets, "connect", lambda url: FakeConnect(ws))

    asyncio.run(make_client(module, workspace=workspace).run_approval_real(
        "codex",
        "approval",
        True,
        True,
        wait_for_hotkey_approval=True,
    ))

    sent_types = [message["type"] for message in ws.sent]
    assert "permission_response" not in sent_types
    launch = next(message for message in ws.sent if message["type"] == "agent_launch")
    assert launch["workspace"] == workspace


def test_foreground_cli_scenario_sends_structured_cli_launch(monkeypatch, tmpdir):
    module = load_smoke_module()
    workspace = str(tmpdir)
    ws = FakeWebSocket([
        {"type": "hello_ack"},
        {
            "type": "event",
            "event": {
                "type": "agent.cli.launched",
                "payload": {
                    "agent": "claude",
                    "workspace": workspace,
                    "frontend_pid": 4321,
                    "foreground_launch_id": "fg_test",
                    "launch_surface": "foreground_cli",
                    "control_mode": "managed_native",
                },
            },
        },
        {
            "type": "event",
            "event": {
                "type": "agent.session.created",
                "payload": {
                    "agent": "claude",
                    "session_id": "sess_foreground",
                    "workspace": workspace,
                    "frontend_pid": 4321,
                    "foreground_launch_id": "fg_test",
                    "launch_surface": "foreground_cli",
                    "control_mode": "managed_native",
                },
            },
        },
        {
            "type": "event",
            "event": {
                "type": "agent.session.closed",
                "payload": {
                    "session_id": "sess_foreground",
                    "closed": True,
                    "accepted": True,
                },
            },
        },
    ])
    monkeypatch.setattr(module.websockets, "connect", lambda url: FakeConnect(ws))

    asyncio.run(make_client(module, workspace=workspace).run_foreground_cli("claude"))

    launch = next(message for message in ws.sent if message["type"] == "command")
    assert launch["command"]["type"] == "agent.cli.launch_foreground"
    assert launch["command"]["payload"] == {
        "agent": "claude",
        "workspace": workspace,
    }
    close = [message for message in ws.sent if message["type"] == "command"][-1]
    assert close["command"]["type"] == "agent.session.close"
    assert close["command"]["target"] == {"session_id": "sess_foreground"}


def test_foreground_approval_real_sends_context_and_responds_to_permission(monkeypatch, tmpdir):
    module = load_smoke_module()
    workspace = str(tmpdir)
    ws = FakeWebSocket([
        {"type": "hello_ack"},
        {
            "type": "event",
            "event": {
                "type": "agent.cli.launched",
                "payload": {
                    "agent": "codex",
                    "workspace": workspace,
                    "frontend_pid": 4321,
                    "foreground_launch_id": "fg_test",
                    "launch_surface": "foreground_cli",
                    "control_mode": "native_cli",
                },
            },
        },
        {
            "type": "event",
            "event": {
                "type": "agent.session.created",
                "payload": {
                    "agent": "codex",
                    "session_id": "sess_foreground",
                    "workspace": workspace,
                    "frontend_pid": 4321,
                    "foreground_launch_id": "fg_test",
                    "launch_surface": "foreground_cli",
                    "control_mode": "native_cli",
                },
            },
        },
        {
            "type": "permission_request",
            "session_id": "sess_foreground",
            "request_id": "req_codex",
            "agent": "codex",
        },
        {
            "type": "permission_ack",
            "session_id": "sess_foreground",
            "request_id": "req_codex",
            "approved": True,
            "forwarded": True,
            "evidence": {
                "adapter": "codex_cli_proxy",
                "response_written": True,
                "decision_delivered": True,
            },
        },
        {
            "type": "agent_message_delta",
            "session_id": "sess_foreground",
            "agent": "codex",
            "delta": "codex approval smoke",
        },
    ])
    monkeypatch.setattr(module.websockets, "connect", lambda url: FakeConnect(ws))

    asyncio.run(make_client(module, workspace=workspace).run_foreground_approval_real(
        "codex",
        "approval prompt",
        True,
        True,
    ))

    launch = next(message for message in ws.sent if message.get("type") == "command")
    assert launch["command"]["type"] == "agent.cli.launch_foreground"
    assert launch["command"]["payload"] == {
        "agent": "codex",
        "context": "approval prompt",
        "workspace": workspace,
    }
    permission = next(message for message in ws.sent if message.get("type") == "permission_response")
    assert permission["request_id"] == "req_codex"
    assert permission["session_id"] == "sess_foreground"
    assert permission["approved"] is True
    close = [message for message in ws.sent if message.get("type") == "command"][-1]
    assert close["command"]["type"] == "agent.session.close"
    assert close["command"]["target"] == {"session_id": "sess_foreground"}


def test_foreground_approval_real_fails_when_cli_exits_before_ack(monkeypatch, tmpdir):
    module = load_smoke_module()
    workspace = str(tmpdir)
    ws = FakeWebSocket([
        {"type": "hello_ack"},
        {
            "type": "event",
            "event": {
                "type": "agent.cli.launched",
                "payload": {
                    "agent": "codex",
                    "workspace": workspace,
                    "frontend_pid": 4321,
                    "foreground_launch_id": "fg_test",
                    "launch_surface": "foreground_cli",
                    "control_mode": "native_cli",
                },
            },
        },
        {
            "type": "event",
            "event": {
                "type": "agent.session.created",
                "payload": {
                    "agent": "codex",
                    "session_id": "sess_foreground",
                    "workspace": workspace,
                    "launch_surface": "foreground_cli",
                    "control_mode": "native_cli",
                },
            },
        },
        {
            "type": "event",
            "event": {
                "type": "agent.session.exited",
                "payload": {
                    "agent": "codex",
                    "session_id": "sess_foreground",
                    "exit_code": 1,
                },
            },
        },
    ])
    monkeypatch.setattr(module.websockets, "connect", lambda url: FakeConnect(ws))

    with pytest.raises(RuntimeError, match="exited before permission ack"):
        asyncio.run(make_client(module, workspace=workspace).run_foreground_approval_real(
            "codex",
            "approval prompt",
            True,
            True,
        ))


def test_foreground_cli_scenario_requires_managed_session_created(monkeypatch, tmpdir):
    module = load_smoke_module()
    workspace = str(tmpdir)
    ws = RepeatingWebSocket(
        [
            {"type": "hello_ack"},
            {
                "type": "event",
                "event": {
                    "type": "agent.cli.launched",
                    "payload": {
                        "agent": "claude",
                        "workspace": workspace,
                        "frontend_pid": 4321,
                        "foreground_launch_id": "fg_test",
                        "launch_surface": "foreground_cli",
                        "control_mode": "managed_native",
                    },
                },
            },
        ],
        {"type": "heartbeat_ack"},
    )
    monkeypatch.setattr(module.websockets, "connect", lambda url: FakeConnect(ws))
    client = make_client(module, workspace=workspace)
    client.timeout = 0.02

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.run_foreground_cli("claude"))

    message = str(exc_info.value)
    assert "foreground CLI launch and managed session" in message
    assert "launched=True" in message
    assert "created=False" in message


def test_foreground_cli_scenario_ignores_unmatched_managed_session(monkeypatch, tmpdir):
    module = load_smoke_module()
    workspace = str(tmpdir)
    ws = RepeatingWebSocket(
        [
            {"type": "hello_ack"},
            {
                "type": "event",
                "event": {
                    "type": "agent.cli.launched",
                    "payload": {
                        "agent": "claude",
                        "workspace": workspace,
                        "frontend_pid": 4321,
                        "foreground_launch_id": "fg_expected",
                        "launch_surface": "foreground_cli",
                        "control_mode": "managed_native",
                    },
                },
            },
            {
                "type": "event",
                "event": {
                    "type": "agent.session.created",
                    "payload": {
                        "agent": "claude",
                        "session_id": "sess_other",
                        "workspace": workspace,
                        "frontend_pid": 9999,
                        "foreground_launch_id": "fg_other",
                        "launch_surface": "foreground_cli",
                        "control_mode": "managed_native",
                    },
                },
            },
        ],
        {"type": "heartbeat_ack"},
    )
    monkeypatch.setattr(module.websockets, "connect", lambda url: FakeConnect(ws))
    client = make_client(module, workspace=workspace)
    client.timeout = 0.02

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.run_foreground_cli("claude"))

    message = str(exc_info.value)
    assert "foreground CLI launch and managed session" in message
    assert "launched=True" in message
    assert "created=False" in message


def test_approval_real_deadline_is_not_extended_by_repeated_task_updates(monkeypatch):
    module = load_smoke_module()
    long_message = "provider still streaming updates " + ("a" * 140) + ("x" * 300)
    ws = RepeatingWebSocket(
        [
            {"type": "hello_ack"},
            {
                "type": "task_update",
                "session_id": "sess_deadline",
                "agent": "codex",
                "state": "submitted",
            },
            {
                "type": "permission_request",
                "session_id": "sess_deadline",
                "request_id": "req_deadline",
            },
            {
                "type": "permission_ack",
                "session_id": "sess_deadline",
                "request_id": "req_deadline",
                "approved": True,
                "forwarded": True,
                "evidence": {
                    "adapter": "codex_app_server",
                    "response_written": True,
                    "decision_delivered": True,
                },
            },
        ],
        {
            "type": "task_update",
            "session_id": "sess_deadline",
            "state": "retrying",
            "message": long_message,
            "details": "x" * 200,
        },
    )
    monkeypatch.setattr(module.websockets, "connect", lambda url: FakeConnect(ws))

    client = module.LocalApiSmokeClient(
        "ws://127.0.0.1:8765",
        0.05,
        False,
        "",
        "desktop-ui",
        "local-api-smoke",
        ["agent:launch", "permission:respond", "session:list"],
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.run_approval_real("codex", "approval", True, True))

    message = str(exc_info.value)
    assert "waiting for task_completed/task_failed" in message
    assert "session_id=sess_deadline" in message
    assert "task_update" in message
    assert "retrying" in message
    assert "provider still streaming updates" in message
    assert "...(truncated)" in message
    assert "x" * 80 not in message
    assert ws.recv_count < ws.max_reads


def test_wait_for_type_for_session_deadline_is_not_extended_by_mismatched_updates():
    module = load_smoke_module()
    client = module.LocalApiSmokeClient(
        "ws://127.0.0.1:8765",
        0.01,
        False,
        "",
        "desktop-ui",
        "local-api-smoke",
        ["agent:launch", "permission:respond", "session:list"],
    )
    ws = SlowRepeatingWebSocket({
        "type": "task_update",
        "session_id": "other_session",
        "state": "streaming",
    })

    with pytest.raises(TimeoutError) as exc_info:
        asyncio.run(client.wait_for_type_for_session(ws, "permission_request", "sess_deadline"))

    message = str(exc_info.value)
    assert "permission_request" in message
    assert "session_id=sess_deadline" in message
    assert "last_payload_type=task_update" in message
    assert ws.recv_count < ws.max_reads


def test_service_start_command_includes_config_and_workspace(tmpdir):
    module = load_smoke_module()
    config = Path(str(tmpdir)) / "config.yaml"
    workspace = Path(str(tmpdir)) / "workspace"

    command = module.build_service_start_command(config, workspace)

    assert command[:3] == [sys.executable, str(ROOT_DIR / "src" / "bridge" / "server.py"), "--config"]
    assert str(config) in command
    assert "--workspace" in command
    assert str(workspace) in command


def test_service_start_command_resolves_relative_config_from_repo_root():
    module = load_smoke_module()

    command = module.build_service_start_command("src/bridge/config.yaml", None)

    assert str(ROOT_DIR / "src" / "bridge" / "config.yaml") in command


def test_stop_spawned_service_windows_uses_taskkill_process_tree(monkeypatch):
    module = load_smoke_module()

    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.terminated = False
            self.killed = False
            self.waits = []

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waits.append(timeout)
            return 0

        def poll(self):
            return None

    taskkill_calls = []

    def fake_run(command, check):
        taskkill_calls.append((command, check))

    process = FakeProcess()
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.stop_spawned_service(process)

    assert taskkill_calls == [(["taskkill", "/PID", "4321", "/T", "/F"], True)]
    assert process.waits == [5]
    assert process.terminated is False
    assert process.killed is False


def test_stop_spawned_service_windows_falls_back_when_taskkill_fails(monkeypatch):
    module = load_smoke_module()

    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    def fake_run(command, check):
        raise FileNotFoundError("taskkill")

    process = FakeProcess()
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.stop_spawned_service(process)

    assert process.terminated is True
    assert process.killed is False


def test_stop_spawned_service_non_windows_does_not_call_taskkill(monkeypatch):
    module = load_smoke_module()

    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    def fake_run(command, check):
        raise AssertionError("taskkill should not be called outside Windows")

    process = FakeProcess()
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.stop_spawned_service(process)

    assert process.terminated is True
    assert process.killed is False


def test_auto_start_terminates_spawned_service_when_startup_check_fails(monkeypatch):
    module = load_smoke_module()
    monkeypatch.setattr(module.sys, "platform", "linux")

    class FakeProcess:
        def __init__(self):
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    process = FakeProcess()
    checks = []

    async def fake_wait_for_service_hello(client, timeout):
        checks.append(timeout)
        raise RuntimeError("no hello")

    monkeypatch.setattr(module, "wait_for_service_hello", fake_wait_for_service_hello)
    monkeypatch.setattr(module, "start_local_core_service", lambda config, workspace: process)
    client = make_client(module)

    try:
        asyncio.run(module.ensure_local_core_service(client, True, "config.yaml", "workspace", 3.0))
    except RuntimeError as exc:
        assert "no hello" in str(exc)
    else:
        raise AssertionError("auto-start failure did not propagate")

    assert checks == [1.0, 3.0]
    assert process.terminated is True
    assert process.killed is False


def test_auto_start_terminates_spawned_service_when_startup_wait_aborts(monkeypatch):
    module = load_smoke_module()
    monkeypatch.setattr(module.sys, "platform", "linux")

    class StartupAbort(BaseException):
        pass

    class FakeProcess:
        def __init__(self):
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    process = FakeProcess()
    checks = []

    async def fake_wait_for_service_hello(client, timeout):
        checks.append(timeout)
        if len(checks) == 1:
            raise RuntimeError("not already running")
        raise StartupAbort("startup wait aborted")

    monkeypatch.setattr(module, "wait_for_service_hello", fake_wait_for_service_hello)
    monkeypatch.setattr(module, "start_local_core_service", lambda config, workspace: process)
    client = make_client(module)

    try:
        asyncio.run(module.ensure_local_core_service(client, True, "config.yaml", "workspace", 3.0))
    except StartupAbort as exc:
        assert "startup wait aborted" in str(exc)
    else:
        raise AssertionError("startup abort did not propagate")

    assert checks == [1.0, 3.0]
    assert process.terminated is True
    assert process.killed is False
