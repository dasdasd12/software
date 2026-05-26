import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


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


def test_auto_start_terminates_spawned_service_when_startup_check_fails(monkeypatch):
    module = load_smoke_module()

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
