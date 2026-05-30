from pathlib import Path
import asyncio
import json
import subprocess
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
BRIDGE_DIR = SRC_DIR / "bridge"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(BRIDGE_DIR))

from agents.foreground_cli import (  # noqa: E402
    LAUNCH_TOKEN_ENV,
    ForegroundCliLauncher,
    build_foreground_cli_env,
    build_foreground_cli_command,
)
from server import BridgeServer  # noqa: E402
from session_manager import AgentType  # noqa: E402


class CaptureQueue:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)

    def put_nowait(self, item):
        self.items.append(item)

    def get_nowait(self):
        return self.items.pop(0)


class FakeForegroundLauncher:
    def __init__(self, pid=4321):
        self.pid = pid
        self.launches = []

    def launch(self, agent, workspace, foreground_launch_id=None):
        self.launches.append((agent, workspace, foreground_launch_id))

        class Process:
            pass

        process = Process()
        process.pid = self.pid
        return process


class FakeController:
    def __init__(self, available=True):
        self.available = available
        self.terminated = []
        self.launched = []

    def is_available(self):
        return self.available

    async def launch(self, session_id, context="", workspace=None):
        self.launched.append((session_id, context, workspace))
        return None

    async def terminate(self, session_id):
        self.terminated.append(session_id)
        return True


def make_server(tmpdir):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
        "workspace": {"default": str(tmpdir)},
    }
    server = BridgeServer(config)
    server.agents[AgentType.CODEX] = FakeController()
    server.agents[AgentType.CLAUDE] = FakeController()
    server.agent_runtime.controllers = server.agents
    return server


def command_message(command_type, *, payload=None, command_id="cmd_test"):
    return {
        "type": "command",
        "command": {
            "command_id": command_id,
            "type": command_type,
            "source": {"kind": "test-client", "client_id": "pytest"},
            "payload": payload or {},
        },
    }


def read_event(queue):
    payload = json.loads(queue.get_nowait())
    assert payload["type"] == "event"
    return payload["event"]


def test_foreground_cli_command_uses_repo_script_and_workspace(tmpdir):
    command = build_foreground_cli_command(
        agent="codex",
        workspace=str(tmpdir),
        api_url="ws://127.0.0.1:8765",
        token="token-value",
        foreground_launch_id="fg_test",
        python_executable="python",
    )

    assert command[0] == "python"
    assert command[1].endswith("scripts/local-agent-cli.py")
    assert "--agent" in command
    assert "--workspace" in command
    assert str(tmpdir) in command
    assert "--api-url" in command
    assert "ws://127.0.0.1:8765" in command
    assert "--launch-id" in command
    assert "fg_test" in command
    assert "--token" not in command
    assert "token-value" not in command


def test_foreground_cli_command_rejects_shell_command_agent(tmpdir):
    try:
        build_foreground_cli_command(
            agent="codex && bad",
            workspace=str(tmpdir),
            api_url="ws://127.0.0.1:8765",
        )
    except ValueError as exc:
        assert "agent" in str(exc)
    else:
        raise AssertionError("unsafe agent value was accepted")


def test_foreground_cli_launcher_uses_popen_without_shell(monkeypatch, tmpdir):
    calls = []

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    launcher = ForegroundCliLauncher(
        api_url="ws://127.0.0.1:8765",
        token="token-value",
        python_executable="python",
    )

    process = launcher.launch("claude", str(tmpdir))

    assert process.pid == 4321
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[0] == "python"
    assert command[1].endswith("scripts/local-agent-cli.py")
    assert "--token" not in command
    assert "token-value" not in command
    assert kwargs["cwd"] == str(Path(str(tmpdir)).resolve())
    assert kwargs.get("shell") is None
    assert kwargs["env"][LAUNCH_TOKEN_ENV] == "token-value"


def test_foreground_cli_launcher_merges_custom_env_with_launch_token(monkeypatch, tmpdir):
    calls = []

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    launcher = ForegroundCliLauncher(
        api_url="ws://127.0.0.1:8765",
        token="token-value",
        python_executable="python",
        env={"EXTRA_ENV": "present", LAUNCH_TOKEN_ENV: "old-value"},
    )

    launcher.launch("claude", str(tmpdir))

    _, kwargs = calls[0]
    assert kwargs["env"]["EXTRA_ENV"] == "present"
    assert kwargs["env"][LAUNCH_TOKEN_ENV] == "token-value"


def test_foreground_cli_env_keeps_terminal_environment_and_filters_secrets():
    env = build_foreground_cli_env(
        base_env={
            "PATH": "C:/Windows/System32",
            "WT_SESSION": "terminal-session",
            "PROCESSOR_ARCHITECTURE": "AMD64",
            "OPENAI_API_KEY": "secret-value",
            "ANTHROPIC_AUTH_TOKEN": "secret-value",
            "CODEX_TOKEN": "secret-value",
            "AWS_ACCESS_KEY_ID": "secret-value",
            "SSH_PRIVATE_KEY": "secret-value",
            "NORMAL_SETTING": "visible",
        },
        extra_env={
            "EXPLICIT_ENV": "present",
            "CODEX_API_KEY": "secret-value",
            LAUNCH_TOKEN_ENV: "old-token",
        },
        token="token-value",
    )

    assert env["PATH"] == "C:/Windows/System32"
    assert env["WT_SESSION"] == "terminal-session"
    assert env["PROCESSOR_ARCHITECTURE"] == "AMD64"
    assert env["NORMAL_SETTING"] == "visible"
    assert env["EXPLICIT_ENV"] == "present"
    assert env[LAUNCH_TOKEN_ENV] == "token-value"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CODEX_TOKEN" not in env
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "SSH_PRIVATE_KEY" not in env
    assert "CODEX_API_KEY" not in env


def test_foreground_cli_env_respects_explicit_empty_base_env():
    env = build_foreground_cli_env(base_env={}, extra_env=None, token=None)

    assert env == {}


def test_foreground_cli_launcher_filters_sensitive_service_env(monkeypatch, tmpdir):
    calls = []

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setenv("PATH", "C:/Windows/System32")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-value")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    launcher = ForegroundCliLauncher(
        api_url="ws://127.0.0.1:8765",
        token="token-value",
        python_executable="python",
        env={"EXPLICIT_ENV": "present"},
    )

    launcher.launch("claude", str(tmpdir))

    _, kwargs = calls[0]
    assert kwargs["env"]["PATH"] == "C:/Windows/System32"
    assert kwargs["env"]["EXPLICIT_ENV"] == "present"
    assert kwargs["env"][LAUNCH_TOKEN_ENV] == "token-value"
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]


def test_foreground_cli_launcher_uses_create_new_console_on_windows(monkeypatch, tmpdir):
    calls = []

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    launcher = ForegroundCliLauncher(api_url="ws://127.0.0.1:8765")

    launcher.launch("codex", str(tmpdir))

    assert calls[0][1]["creationflags"] == 16


def test_cli_launch_foreground_emits_event_with_frontend_pid(tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={"agent": "claude", "workspace": workspace},
        command_id="cmd_cli_launch",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.cli.launched"
    assert event["payload"]["agent"] == "claude"
    assert event["payload"]["workspace"] == workspace
    assert event["payload"]["frontend_pid"] == 4321
    assert event["payload"]["foreground_launch_id"].startswith("fg_")
    assert event["payload"]["launch_surface"] == "foreground_cli"
    assert event["payload"]["control_mode"] == "managed_native"
    assert launcher.launches == [("claude", workspace, event["payload"]["foreground_launch_id"])]


def test_cli_launch_foreground_rejects_unavailable_agent_before_launcher(tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    server.agents[AgentType.CLAUDE] = FakeController(available=False)
    server.agent_runtime.controllers = server.agents
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={"agent": "claude", "workspace": workspace},
        command_id="cmd_cli_launch_unavailable",
    ), queue))

    payload = json.loads(queue.get_nowait())
    assert payload["type"] == "error"
    assert payload["code"] == "AGENT_UNAVAILABLE"
    assert payload["message"] == "claude executable not found"
    assert launcher.launches == []


def test_local_api_url_uses_loopback_for_wildcard_bind_hosts(tmpdir):
    server = make_server(tmpdir)

    for host in ("0.0.0.0", "::", ""):
        server.cfg["server"]["host"] = host
        assert server._local_api_url() == "ws://127.0.0.1:8765"


def test_foreground_cli_launcher_uses_desktop_client_grant_without_global_token(tmpdir):
    server = make_server(tmpdir)
    server.security = server.security.from_dict({
        "auth_enabled": True,
        "clients": [{
            "token": "grant-token",
            "client_kind": "desktop-ui",
            "client_id": "local-agent-cli",
            "capabilities": ["agent:launch"],
        }],
    })

    launcher = server._build_foreground_cli_launcher()

    assert launcher.token == "grant-token"


def test_foreground_cli_launcher_prefers_global_launch_token(tmpdir):
    server = make_server(tmpdir)
    server.security = server.security.from_dict({
        "auth_enabled": True,
        "launch_token": "global-token",
        "clients": [{
            "token": "grant-token",
            "client_kind": "desktop-ui",
            "client_id": "local-agent-cli",
            "capabilities": ["agent:launch"],
        }],
    })

    launcher = server._build_foreground_cli_launcher()

    assert launcher.token == "global-token"


def test_disconnect_cleanup_terminates_owned_foreground_cli_session(tmpdir):
    server = make_server(tmpdir)
    controller = FakeController()
    server.agents[AgentType.CODEX] = controller
    server.agent_runtime.controllers = server.agents
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.launch_or_resume",
        payload={
            "agent": "codex",
            "workspace": str(Path(str(tmpdir)).resolve()),
            "launch_surface": "foreground_cli",
        },
        command_id="cmd_fg_launch",
    ), queue))
    event = read_event(queue)
    session_id = event["payload"]["session_id"]

    asyncio.run(server._cleanup_foreground_cli_sessions_for_queue(queue))

    assert controller.terminated == [session_id]
