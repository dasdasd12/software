import importlib.util
import asyncio
import io
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
CLI_SCRIPT = ROOT_DIR / "scripts" / "local-agent-cli.py"


def load_cli_module():
    spec = importlib.util.spec_from_file_location("local_agent_cli", CLI_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_agent_cli_parser_defaults_to_managed_session():
    module = load_cli_module()

    args = module.parse_args(
        ["--agent", "codex", "--workspace", "C:/project"],
        env={
            module.LAUNCH_TOKEN_ENV: "env-token",
            module.CLAUDE_HOOK_TOKEN_ENV: "hook-token",
            module.FOREGROUND_REGISTRATION_TOKEN_ENV: "registration-token",
        },
    )

    assert args.agent == "codex"
    assert args.workspace == "C:/project"
    assert args.client_kind == "desktop-ui"
    assert args.api_url == "ws://127.0.0.1:8765"
    assert args.token == "env-token"
    assert args.hook_token == "hook-token"
    assert args.registration_token == "registration-token"
    assert args.launch_id == ""
    assert args.native_cli is False


def test_cli_builds_hello_launch_and_input_commands():
    module = load_cli_module()

    hello = module.build_hello_message("desktop-ui", "token-value")
    assert hello["type"] == "hello"
    assert hello["client_kind"] == "desktop-ui"
    assert "agent:launch" in hello["capabilities"]
    assert hello["token"] == "token-value"

    launch = module.build_launch_command(
        "codex",
        "C:/project",
        context="hello",
        foreground_launch_id="fg_test",
    )
    assert launch["type"] == "command"
    assert launch["command"]["type"] == "agent.session.launch_or_resume"
    assert launch["command"]["payload"]["agent"] == "codex"
    assert launch["command"]["payload"]["workspace"] == "C:/project"
    assert launch["command"]["payload"]["context"] == "hello"
    assert launch["command"]["payload"]["launch_surface"] == "foreground_cli"
    assert launch["command"]["payload"]["control_mode"] == "managed_native"
    assert launch["command"]["payload"]["frontend_pid"] == module.os.getpid()
    assert launch["command"]["payload"]["foreground_launch_id"] == "fg_test"

    registered = module.build_register_foreground_command(
        "claude",
        "C:/project",
        "fg_native",
        "reg-token",
    )
    assert registered["command"]["type"] == "agent.session.register_foreground"
    assert registered["command"]["payload"]["agent"] == "claude"
    assert registered["command"]["payload"]["control_mode"] == "native_cli"
    assert registered["command"]["payload"]["foreground_launch_id"] == "fg_native"
    assert registered["command"]["payload"]["foreground_registration_token"] == "reg-token"

    input_message = module.build_input_command("sess_1", "hello")
    assert input_message["command"]["type"] == "agent.session.input"
    assert input_message["command"]["target"] == {"session_id": "sess_1"}
    assert input_message["command"]["payload"] == {"text": "hello"}


def test_cli_builds_permission_interrupt_and_close_commands():
    module = load_cli_module()

    approve = module.build_permission_response("sess_1", "req_1", True)
    assert approve["type"] == "permission_response"
    assert approve["session_id"] == "sess_1"
    assert approve["request_id"] == "req_1"
    assert approve["approved"] is True

    deny = module.build_permission_response("sess_1", "req_1", False)
    assert deny["approved"] is False

    interrupt = module.build_interrupt_command("sess_1")
    assert interrupt["command"]["type"] == "agent.run.interrupt"
    assert interrupt["command"]["target"] == {"session_id": "sess_1"}

    close = module.build_close_command("sess_1")
    assert close["command"]["type"] == "agent.session.close"
    assert close["command"]["target"] == {"session_id": "sess_1"}


def test_stdin_reader_thread_posts_lines_without_default_executor():
    module = load_cli_module()
    event_loop = module.asyncio.new_event_loop()
    module.asyncio.set_event_loop(event_loop)
    queue = module.asyncio.Queue()
    stdin = io.StringIO("hello\n")

    try:
        thread = module._start_stdin_reader(queue, stdin=stdin)
        line = event_loop.run_until_complete(module.asyncio.wait_for(queue.get(), timeout=1))
        exit_line = event_loop.run_until_complete(module.asyncio.wait_for(queue.get(), timeout=1))

        assert thread.daemon is True
        assert line == "hello"
        assert exit_line == "/exit"
    finally:
        event_loop.close()
        module.asyncio.set_event_loop(None)


def test_sender_sends_close_before_exit_when_session_active():
    module = load_cli_module()

    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send(self, raw):
            self.sent.append(module.json.loads(raw))

    async def run():
        ws = FakeWebSocket()
        state = {"session_id": "sess_1", "pending_permission": None}
        lines = module.asyncio.Queue()
        stop_event = module.asyncio.Event()
        await lines.put("/exit")

        await module._sender(ws, state, lines, stop_event)

        return ws.sent, stop_event.is_set()

    sent, stopped = asyncio.run(run())

    assert stopped is True
    assert len(sent) == 1
    assert sent[0]["command"]["type"] == "agent.session.close"
    assert sent[0]["command"]["target"] == {"session_id": "sess_1"}


def test_cli_builds_native_claude_hook_settings(tmpdir):
    module = load_cli_module()
    settings_path = module.write_claude_hook_settings(
        "ws://127.0.0.1:8765",
        "sess_native",
        directory=str(tmpdir),
    )

    data = module.json.loads(module.Path(settings_path).read_text(encoding="utf-8"))

    assert "PermissionRequest" in data["hooks"]
    assert data["hooks"]["PermissionRequest"][0]["hooks"][0]["command"].endswith("python.exe") or data["hooks"]["PermissionRequest"][0]["hooks"][0]["command"].endswith("python")
    assert "claude-code-hook.py" in data["hooks"]["PermissionRequest"][0]["hooks"][0]["args"][0]
    assert "--session-id" in data["hooks"]["PermissionRequest"][0]["hooks"][0]["args"]
    assert "--client-kind" in data["hooks"]["PermissionRequest"][0]["hooks"][0]["args"]
    assert "agent-hook" in data["hooks"]["PermissionRequest"][0]["hooks"][0]["args"]
    assert "PreToolUse" in data["hooks"]
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "AskUserQuestion|ExitPlanMode"


def test_native_claude_env_removes_launch_token_and_keeps_hook_token(monkeypatch):
    module = load_cli_module()
    args = module.parse_args(
        ["--agent", "claude", "--workspace", "C:/project", "--native-cli"],
        env={
            module.LAUNCH_TOKEN_ENV: "launch-token",
            module.CLAUDE_HOOK_TOKEN_ENV: "hook-token",
            module.FOREGROUND_REGISTRATION_TOKEN_ENV: "registration-token",
        },
    )
    monkeypatch.setenv(module.LAUNCH_TOKEN_ENV, "launch-token")
    monkeypatch.setenv(module.CLAUDE_HOOK_TOKEN_ENV, "hook-token")
    monkeypatch.setenv(module.FOREGROUND_REGISTRATION_TOKEN_ENV, "registration-token")

    env = module._native_claude_env(args)

    assert module.LAUNCH_TOKEN_ENV not in env
    assert module.FOREGROUND_REGISTRATION_TOKEN_ENV not in env
    assert env[module.CLAUDE_HOOK_TOKEN_ENV] == "hook-token"
