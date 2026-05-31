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
    CLAUDE_HOOK_TOKEN_ENV,
    FOREGROUND_EXIT_TOKEN_ENV,
    FOREGROUND_REGISTRATION_TOKEN_ENV,
    LAUNCH_TOKEN_ENV,
    ForegroundCliLauncher,
    build_foreground_cli_env,
    build_foreground_cli_command,
)
from agents.commands import AgentCommandService  # noqa: E402
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

    def launch(
        self,
        agent,
        workspace,
        foreground_launch_id=None,
        native_cli=False,
        permission_mode="default",
        context="",
        model="",
        reasoning_effort="",
        registration_token=None,
        hook_token=None,
        exit_token=None,
    ):
        self.launches.append((
            agent,
            workspace,
            foreground_launch_id,
            native_cli,
            permission_mode,
            context,
            model,
            reasoning_effort,
            registration_token,
            hook_token,
            exit_token,
        ))

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
        native_cli=True,
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
    assert "--native-cli" in command
    assert "--token" not in command
    assert "token-value" not in command


def test_foreground_cli_command_can_request_claude_plan_mode(tmpdir):
    command = build_foreground_cli_command(
        agent="claude",
        workspace=str(tmpdir),
        api_url="ws://127.0.0.1:8765",
        native_cli=True,
        permission_mode="plan",
        python_executable="python",
    )

    assert "--native-cli" in command
    assert "--permission-mode" in command
    assert command[command.index("--permission-mode") + 1] == "plan"


def test_foreground_cli_command_passes_context_as_argument(tmpdir):
    context = "Run python -c \"print('codex approval smoke')\""
    command = build_foreground_cli_command(
        agent="codex",
        workspace=str(tmpdir),
        api_url="ws://127.0.0.1:8765",
        native_cli=True,
        context=context,
        python_executable="python",
    )

    assert "--context" in command
    assert command[command.index("--context") + 1] == context


def test_foreground_cli_command_passes_model_overrides(tmpdir):
    command = build_foreground_cli_command(
        agent="codex",
        workspace=str(tmpdir),
        api_url="ws://127.0.0.1:8765",
        native_cli=True,
        model="gpt-5.3-codex-spark",
        reasoning_effort="low",
        python_executable="python",
    )

    assert "--model" in command
    assert command[command.index("--model") + 1] == "gpt-5.3-codex-spark"
    assert "--reasoning-effort" in command
    assert command[command.index("--reasoning-effort") + 1] == "low"


def test_foreground_cli_command_rejects_bypass_permission_mode(tmpdir):
    try:
        build_foreground_cli_command(
            agent="claude",
            workspace=str(tmpdir),
            api_url="ws://127.0.0.1:8765",
            native_cli=True,
            permission_mode="bypassPermissions",
        )
    except ValueError as exc:
        assert "permission_mode" in str(exc)
    else:
        raise AssertionError("unsafe permission mode was accepted")


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

    monkeypatch.setattr(sys, "platform", "linux")

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

    monkeypatch.setattr(sys, "platform", "linux")

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
            CLAUDE_HOOK_TOKEN_ENV: "old-hook-token",
            FOREGROUND_REGISTRATION_TOKEN_ENV: "old-registration-token",
            FOREGROUND_EXIT_TOKEN_ENV: "old-exit-token",
        },
        token="token-value",
        hook_token="hook-token-value",
        registration_token="registration-token-value",
        exit_token="exit-token-value",
    )

    assert env["PATH"] == "C:/Windows/System32"
    assert env["WT_SESSION"] == "terminal-session"
    assert env["PROCESSOR_ARCHITECTURE"] == "AMD64"
    assert env["NORMAL_SETTING"] == "visible"
    assert env["EXPLICIT_ENV"] == "present"
    assert env[LAUNCH_TOKEN_ENV] == "token-value"
    assert env[CLAUDE_HOOK_TOKEN_ENV] == "hook-token-value"
    assert env[FOREGROUND_REGISTRATION_TOKEN_ENV] == "registration-token-value"
    assert env[FOREGROUND_EXIT_TOKEN_ENV] == "exit-token-value"
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

    monkeypatch.setattr(sys, "platform", "linux")

    launcher.launch("claude", str(tmpdir))

    _, kwargs = calls[0]
    assert kwargs["env"]["PATH"] == "C:/Windows/System32"
    assert kwargs["env"]["EXPLICIT_ENV"] == "present"
    assert kwargs["env"][LAUNCH_TOKEN_ENV] == "token-value"
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]


def test_foreground_cli_launcher_uses_new_console_on_windows(monkeypatch, tmpdir):
    calls = []

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NEW_CONSOLE", 0x10, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    launcher = ForegroundCliLauncher(
        api_url="ws://127.0.0.1:8765",
        python_executable="python",
    )

    launcher.launch("codex", str(tmpdir))

    command, kwargs = calls[0]
    assert command[0] == "python"
    assert command[1].endswith("scripts/local-agent-cli.py")
    assert command[:4] != ["cmd.exe", "/d", "/c", "start"]
    assert str(Path(str(tmpdir)).resolve()) in command
    assert kwargs.get("shell") is None
    assert kwargs["cwd"] == str(Path(str(tmpdir)).resolve())
    assert kwargs["close_fds"] is True
    assert kwargs["creationflags"] & 0x10
    assert kwargs["creationflags"] & 0x200
    assert "stdout" not in kwargs
    assert "stderr" not in kwargs


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
    assert event["payload"]["control_mode"] == "native_cli"
    assert event["payload"]["permission_mode"] == "default"
    assert len(launcher.launches) == 1
    launch = launcher.launches[0]
    assert launch[:4] == ("claude", workspace, event["payload"]["foreground_launch_id"], True)
    assert launch[4] == "default"
    assert launch[5] == ""
    assert launch[6] == ""
    assert launch[7] == ""
    assert launch[8].startswith("reg_")
    assert launch[9].startswith("hook_")
    assert launch[10].startswith("exit_")


def test_cli_launch_foreground_passes_plan_permission_mode(tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={"agent": "claude", "workspace": workspace, "permission_mode": "plan"},
        command_id="cmd_cli_launch_plan",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.cli.launched"
    assert event["payload"]["permission_mode"] == "plan"
    launch = launcher.launches[0]
    assert launch[4] == "plan"


def test_cli_launch_foreground_passes_context_to_terminal_host(tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    queue = CaptureQueue()
    server.connected_clients.add(queue)
    context = "Run python -c \"print('codex approval smoke')\""

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={"agent": "codex", "workspace": workspace, "context": context},
        command_id="cmd_cli_launch_context",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.cli.launched"
    launch = launcher.launches[0]
    assert launch[0] == "codex"
    assert launch[5] == context


def test_cli_launch_foreground_passes_model_overrides_to_terminal_host(tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={
            "agent": "codex",
            "workspace": workspace,
            "model": "gpt-5.3-codex-spark",
            "reasoning_effort": "low",
        },
        command_id="cmd_cli_launch_model",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.cli.launched"
    launch = launcher.launches[0]
    assert launch[6] == "gpt-5.3-codex-spark"
    assert launch[7] == "low"


def test_cli_launch_foreground_codex_defaults_to_native_cli(tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={"agent": "codex", "workspace": workspace},
        command_id="cmd_cli_launch_codex",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.cli.launched"
    assert event["payload"]["agent"] == "codex"
    assert event["payload"]["control_mode"] == "native_cli"
    launch = launcher.launches[0]
    assert launch[:4] == ("codex", workspace, event["payload"]["foreground_launch_id"], True)
    assert launch[5] == ""
    assert launch[6] == ""
    assert launch[7] == ""
    assert launch[8].startswith("reg_")
    assert launch[9].startswith("hook_")
    assert launch[10].startswith("exit_")


def test_cli_launch_foreground_rejects_bypass_permission_mode_payload(tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={"agent": "claude", "workspace": workspace, "permission_mode": "bypassPermissions"},
        command_id="cmd_cli_launch_bypass",
    ), queue))

    payload = json.loads(queue.get_nowait())
    assert payload["type"] == "error"
    assert payload["code"] == "INVALID_PERMISSION_MODE"
    assert launcher.launches == []


def test_cli_launch_foreground_rejects_plan_mode_for_managed_foreground(tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={
            "agent": "codex",
            "workspace": workspace,
            "native_cli": False,
            "permission_mode": "plan",
        },
        command_id="cmd_cli_launch_plan_managed",
    ), queue))

    payload = json.loads(queue.get_nowait())
    assert payload["type"] == "error"
    assert payload["code"] == "INVALID_PERMISSION_MODE"
    assert "native foreground Claude" in payload["message"]
    assert launcher.launches == []


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
    assert launcher.hook_token is None


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
    assert launcher.hook_token is None


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


def test_disconnect_cleanup_terminates_unregistered_native_foreground_launch(monkeypatch, tmpdir):
    workspace = str(Path(str(tmpdir)).resolve())
    server = make_server(tmpdir)
    launcher = FakeForegroundLauncher(pid=4321)
    server.agent_commands._foreground_cli_launcher = launcher
    terminated = []
    monkeypatch.setattr(
        AgentCommandService,
        "_terminate_process_tree",
        staticmethod(lambda pid: terminated.append(pid)),
    )
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={"agent": "codex", "workspace": workspace},
        command_id="cmd_cli_launch_cleanup",
    ), queue))

    event = read_event(queue)
    launch_id = event["payload"]["foreground_launch_id"]
    assert launch_id in server.agent_commands._foreground_registrations_by_launch_id
    assert launch_id in server.agent_commands._foreground_root_pids_by_launch_id

    asyncio.run(server._cleanup_foreground_cli_sessions_for_queue(queue))

    assert terminated == [4321]
    assert launch_id not in server.agent_commands._foreground_registrations_by_launch_id
    assert launch_id not in server.agent_commands._foreground_root_pids_by_launch_id
