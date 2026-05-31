import importlib.util
import asyncio
import io
import threading
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
            module.FOREGROUND_EXIT_TOKEN_ENV: "exit-token",
        },
    )

    assert args.agent == "codex"
    assert args.workspace == "C:/project"
    assert args.client_kind == "desktop-ui"
    assert args.api_url == "ws://127.0.0.1:8765"
    assert args.token == "env-token"
    assert args.hook_token == "hook-token"
    assert args.registration_token == "registration-token"
    assert args.exit_token == "exit-token"
    assert args.launch_id == ""
    assert args.native_cli is False
    assert args.permission_mode == "default"


def test_local_agent_cli_parser_accepts_plan_permission_mode():
    module = load_cli_module()

    args = module.parse_args(
        ["--agent", "claude", "--workspace", "C:/project", "--native-cli", "--permission-mode", "plan"],
        env={},
    )

    assert args.permission_mode == "plan"


def test_local_agent_cli_parser_rejects_bypass_permission_mode():
    module = load_cli_module()

    try:
        module.parse_args(
            ["--agent", "claude", "--workspace", "C:/project", "--native-cli", "--permission-mode", "bypassPermissions"],
            env={},
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("unsafe permission mode was accepted")


def test_build_native_claude_command_uses_requested_permission_mode():
    module = load_cli_module()

    command = module.build_native_claude_command(
        "settings.json",
        permission_mode="plan",
        context="run approval smoke",
    )

    assert command[1:] == [
        "--permission-mode",
        "plan",
        "--settings",
        "settings.json",
        "--name",
        "AI Keyboard Claude",
        "run approval smoke",
    ]


def test_build_native_codex_command_uses_remote_workspace_and_prompt(monkeypatch):
    module = load_cli_module()
    monkeypatch.setattr(module.shutil, "which", lambda name: "codex.cmd" if name == "codex.cmd" else None)

    command = module.build_native_codex_command("ws://127.0.0.1:12345", "C:/project", "hello")

    assert command[0] == "codex.cmd"
    assert command[1:] == [
        "--no-alt-screen",
        "--remote",
        "ws://127.0.0.1:12345",
        "--cd",
        "C:\\project",
        "--ask-for-approval",
        "untrusted",
        "--sandbox",
        "workspace-write",
        "hello",
    ]


def test_build_native_codex_command_can_override_model_and_effort(monkeypatch):
    module = load_cli_module()
    monkeypatch.setattr(module.shutil, "which", lambda name: "codex.cmd" if name == "codex.cmd" else None)

    command = module.build_native_codex_command(
        "ws://127.0.0.1:12345",
        "C:/project",
        "hello",
        model="gpt-5.3-codex-spark",
        reasoning_effort="low",
    )

    assert "--model" in command
    assert command[command.index("--model") + 1] == "gpt-5.3-codex-spark"
    assert "--config" in command
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="low"'
    assert command[-1] == "hello"


def test_build_native_codex_command_appends_env_config_overrides(monkeypatch):
    module = load_cli_module()
    monkeypatch.setattr(module.shutil, "which", lambda name: "codex.cmd" if name == "codex.cmd" else None)

    command = module.build_native_codex_command(
        "ws://127.0.0.1:12345",
        "C:/project",
        "hello",
        config_overrides=["chatgpt_base_url=\"https://chatgpt.com\""],
    )

    assert "--config" in command
    assert "chatgpt_base_url=\"https://chatgpt.com\"" in command
    assert command[-1] == "hello"


def test_native_codex_config_overrides_parse_json_env(monkeypatch):
    module = load_cli_module()
    monkeypatch.setenv(
        module.CODEX_CONFIG_OVERRIDES_ENV,
        '["chatgpt_base_url=\\"https://chatgpt.com\\"", "model_reasoning_effort=\\"low\\""]',
    )

    assert module._native_codex_config_overrides() == [
        'chatgpt_base_url="https://chatgpt.com"',
        'model_reasoning_effort="low"',
    ]


def test_codex_native_proxy_builds_initial_turn_request():
    module = load_cli_module()
    proxy = module.CodexNativeProxy(
        "ws://127.0.0.1:8765",
        "sess_codex",
        "hook-token",
        "C:/project",
        initial_context="approval prompt",
        model="gpt-5.3-codex-spark",
        reasoning_effort="low",
    )

    request = proxy._initial_turn_request("thread_1")

    assert request["method"] == "turn/start"
    assert request["id"].startswith("ai-keyb-initial-turn-")
    params = request["params"]
    assert params["threadId"] == "thread_1"
    assert params["input"] == [{"type": "text", "text": "approval prompt", "text_elements": []}]
    assert params["cwd"].endswith("project")
    assert params["approvalPolicy"] == "untrusted"
    assert params["approvalsReviewer"] == "user"
    assert params["model"] == "gpt-5.3-codex-spark"
    assert params["effort"] == "low"


def test_codex_native_proxy_backend_connection_allows_large_messages():
    module = load_cli_module()
    captured = {}

    class FakeWebSockets:
        async def connect(self, uri, **kwargs):
            captured["uri"] = uri
            captured["kwargs"] = kwargs
            return object()

    proxy = module.CodexNativeProxy(
        "ws://127.0.0.1:8765",
        "sess_codex",
        "hook-token",
        "C:/project",
    )
    proxy.backend_uri = "ws://127.0.0.1:12345"

    result = asyncio.run(proxy._connect_backend(FakeWebSockets()))

    assert result is not None
    assert captured == {
        "uri": "ws://127.0.0.1:12345",
        "kwargs": {"max_size": None},
    }


def test_codex_native_proxy_suppresses_only_optional_large_tui_notifications():
    module = load_cli_module()
    proxy = module.CodexNativeProxy(
        "ws://127.0.0.1:8765",
        "sess_codex",
        "hook-token",
        "C:/project",
    )
    large_payload = "x" * (module.CODEX_TUI_SAFE_MESSAGE_BYTES + 1)

    assert proxy._should_suppress_backend_message_for_tui(
        {"method": "app/list/updated"},
        large_payload,
    )
    assert not proxy._should_suppress_backend_message_for_tui(
        {"method": "turn/started"},
        large_payload,
    )
    assert not proxy._should_suppress_backend_message_for_tui(
        {"method": "app/list/updated"},
        "small",
    )


def test_codex_native_proxy_closes_both_sockets_when_one_pipe_ends():
    module = load_cli_module()

    class ClosingClientWebSocket:
        def __init__(self):
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def send(self, raw):
            raise AssertionError(f"unexpected send: {raw}")

        async def close(self):
            self.closed = True

    class WaitingBackendWebSocket:
        def __init__(self):
            self.closed = False
            self.iter_cancelled = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.iter_cancelled = True
                raise

        async def send(self, raw):
            raise AssertionError(f"unexpected send: {raw}")

        async def close(self):
            self.closed = True

    async def run():
        proxy = module.CodexNativeProxy(
            "ws://127.0.0.1:8765",
            "sess_codex",
            "hook-token",
            "C:/project",
        )
        client_ws = ClosingClientWebSocket()
        backend_ws = WaitingBackendWebSocket()

        async def fake_connect_backend(_websockets):
            return backend_ws

        proxy._connect_backend = fake_connect_backend

        await asyncio.wait_for(proxy._handle_tui_client(client_ws), timeout=1.0)
        assert client_ws.closed is True
        assert backend_ws.closed is True
        assert backend_ws.iter_cancelled is True

    asyncio.run(run())


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

    exited = module.build_foreground_exited_command("sess_1", 0, "exit-token")
    assert exited["command"]["type"] == "agent.session.foreground_exited"
    assert exited["command"]["target"] == {"session_id": "sess_1"}
    assert exited["command"]["payload"]["exit_code"] == 0
    assert exited["command"]["payload"]["foreground_exit_token"] == "exit-token"


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


def test_cli_builds_native_claude_hook_settings(monkeypatch, tmpdir):
    module = load_cli_module()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.kimi.com/coding/")
    monkeypatch.setenv("ANTHROPIC_MODEL", "kimi-k2.6")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "kimi-k2.6")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "kimi-k2.6")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "kimi-k2.6")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-api-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret-auth-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-oauth-token")
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
    assert data["env"]["ANTHROPIC_AUTH_TOKEN"] == ""
    assert data["env"]["ANTHROPIC_BASE_URL"] == "https://api.kimi.com/coding/"
    assert data["env"]["ANTHROPIC_MODEL"] == "kimi-k2.6"
    assert data["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "kimi-k2.6"
    assert data["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "kimi-k2.6"
    assert data["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "kimi-k2.6"
    assert "ANTHROPIC_API_KEY" not in data["env"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in data["env"]


def test_native_claude_env_removes_launch_token_and_keeps_hook_token(monkeypatch):
    module = load_cli_module()
    args = module.parse_args(
        ["--agent", "claude", "--workspace", "C:/project", "--native-cli"],
        env={
            module.LAUNCH_TOKEN_ENV: "launch-token",
            module.CLAUDE_HOOK_TOKEN_ENV: "hook-token",
            module.FOREGROUND_REGISTRATION_TOKEN_ENV: "registration-token",
            module.FOREGROUND_EXIT_TOKEN_ENV: "exit-token",
        },
    )
    monkeypatch.setenv(module.LAUNCH_TOKEN_ENV, "launch-token")
    monkeypatch.setenv(module.CLAUDE_HOOK_TOKEN_ENV, "hook-token")
    monkeypatch.setenv(module.FOREGROUND_REGISTRATION_TOKEN_ENV, "registration-token")
    monkeypatch.setenv(module.FOREGROUND_EXIT_TOKEN_ENV, "exit-token")

    env = module._native_claude_env(args)

    assert module.LAUNCH_TOKEN_ENV not in env
    assert module.FOREGROUND_REGISTRATION_TOKEN_ENV not in env
    assert module.FOREGROUND_EXIT_TOKEN_ENV not in env
    assert env[module.CLAUDE_HOOK_TOKEN_ENV] == "hook-token"


def test_native_claude_env_removes_provider_auth_secrets(monkeypatch):
    module = load_cli_module()
    args = module.parse_args(
        ["--agent", "claude", "--workspace", "C:/project", "--native-cli"],
        env={module.CLAUDE_HOOK_TOKEN_ENV: "hook-token"},
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key")
    monkeypatch.setenv("Anthropic_Auth_Token", "auth-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    monkeypatch.setenv("NORMAL_CLAUDE_SETTING", "kept")

    env = module._native_claude_env(args)

    assert "ANTHROPIC_API_KEY" not in {key.upper() for key in env}
    assert "ANTHROPIC_AUTH_TOKEN" not in {key.upper() for key in env}
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in {key.upper() for key in env}
    assert env[module.CLAUDE_HOOK_TOKEN_ENV] == "hook-token"
    assert env["NORMAL_CLAUDE_SETTING"] == "kept"


def test_native_claude_env_filters_provider_auth_secrets_case_insensitively(monkeypatch):
    module = load_cli_module()
    args = module.parse_args(
        ["--agent", "claude", "--workspace", "C:/project", "--native-cli"],
        env={module.CLAUDE_HOOK_TOKEN_ENV: "hook-token"},
    )
    monkeypatch.setattr(module.os, "environ", {
        "Anthropic_Api_Key": "api-key",
        "anthropic_auth_token": "auth-token",
        "Claude_Code_Oauth_Token": "oauth-token",
        "PATH": "C:/Windows/System32",
    })

    env = module._native_claude_env(args)

    assert "Anthropic_Api_Key" not in env
    assert "anthropic_auth_token" not in env
    assert "Claude_Code_Oauth_Token" not in env
    assert env["PATH"] == "C:/Windows/System32"
    assert env[module.CLAUDE_HOOK_TOKEN_ENV] == "hook-token"


def test_native_claude_cli_notifies_foreground_exit_without_leaking_tokens(monkeypatch, tmpdir):
    module = load_cli_module()
    settings_path = tmpdir.join("settings.json")
    settings_path.write("{}")
    captured_popen = {}
    drained_event = threading.Event()

    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.recv_messages = [
                module.json.dumps({
                    "type": "event",
                    "event": {
                        "type": "agent.session.created",
                        "payload": {"session_id": "sess_native"},
                    },
                }),
                module.json.dumps({
                    "type": "event",
                    "event": {
                        "type": "agent_hook_event",
                        "payload": {"session_id": "sess_native"},
                    },
                }),
            ]
            self.recv_count = 0

        async def send(self, raw):
            self.sent.append(module.json.loads(raw))

        async def recv(self):
            if self.recv_messages:
                self.recv_count += 1
                if self.recv_count > 1:
                    drained_event.set()
                return self.recv_messages.pop(0)
            await module.asyncio.Future()

    class FakeProcess:
        def wait(self):
            return 0 if drained_event.wait(1.0) else 3

    def fake_popen(command, cwd=None, env=None):
        captured_popen["command"] = command
        captured_popen["cwd"] = cwd
        captured_popen["env"] = env
        return FakeProcess()

    monkeypatch.setattr(module, "write_claude_hook_settings", lambda api_url, session_id: str(settings_path))
    monkeypatch.setattr(
        module,
        "build_native_claude_command",
        lambda path, permission_mode="default", context="": [
            "claude",
            "--settings",
            path,
            "--permission-mode",
            permission_mode,
            context,
        ],
    )
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setenv(module.LAUNCH_TOKEN_ENV, "launch-token")
    monkeypatch.setenv(module.FOREGROUND_REGISTRATION_TOKEN_ENV, "registration-token")
    monkeypatch.setenv(module.FOREGROUND_EXIT_TOKEN_ENV, "exit-token")
    monkeypatch.setenv(module.CLAUDE_HOOK_TOKEN_ENV, "hook-token")
    args = module.parse_args(
        [
            "--agent",
            "claude",
            "--workspace",
            "C:/project",
            "--native-cli",
            "--launch-id",
            "fg_native",
            "--permission-mode",
            "plan",
            "--context",
            "run approval smoke",
        ],
        env={
            module.LAUNCH_TOKEN_ENV: "launch-token",
            module.CLAUDE_HOOK_TOKEN_ENV: "hook-token",
            module.FOREGROUND_REGISTRATION_TOKEN_ENV: "registration-token",
            module.FOREGROUND_EXIT_TOKEN_ENV: "exit-token",
        },
    )
    state = {}
    ws = FakeWebSocket()

    result = asyncio.run(module._run_native_claude_cli(ws, args, state))

    assert result == 0
    assert "--permission-mode" in captured_popen["command"]
    assert captured_popen["command"][captured_popen["command"].index("--permission-mode") + 1] == "plan"
    assert captured_popen["command"][-1] == "run approval smoke"
    assert captured_popen["env"][module.CLAUDE_HOOK_TOKEN_ENV] == "hook-token"
    assert module.LAUNCH_TOKEN_ENV not in captured_popen["env"]
    assert module.FOREGROUND_REGISTRATION_TOKEN_ENV not in captured_popen["env"]
    assert module.FOREGROUND_EXIT_TOKEN_ENV not in captured_popen["env"]
    assert ws.recv_count >= 2
    sent_types = [message["command"]["type"] for message in ws.sent if message.get("type") == "command"]
    assert sent_types == [
        "agent.session.register_foreground",
        "agent.session.foreground_exited",
    ]
    assert ws.sent[-1]["command"]["target"] == {"session_id": "sess_native"}
    assert ws.sent[-1]["command"]["payload"]["exit_code"] == 0
    assert ws.sent[-1]["command"]["payload"]["foreground_exit_token"] == "exit-token"


def test_native_codex_cli_starts_remote_proxy_and_notifies_foreground_exit(monkeypatch):
    module = load_cli_module()
    captured_popen = {}
    stopped = []
    drained_event = threading.Event()

    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.recv_messages = [
                module.json.dumps({
                    "type": "event",
                    "event": {
                        "type": "agent.session.created",
                        "payload": {"session_id": "sess_codex"},
                    },
                }),
                module.json.dumps({
                    "type": "event",
                    "event": {
                        "type": "task_update",
                        "session_id": "sess_codex",
                        "agent": "codex",
                        "state": "WORKING",
                    },
                }),
            ]
            self.recv_count = 0

        async def send(self, raw):
            self.sent.append(module.json.loads(raw))

        async def recv(self):
            if self.recv_messages:
                self.recv_count += 1
                if self.recv_count > 1:
                    drained_event.set()
                return self.recv_messages.pop(0)
            await module.asyncio.Future()

    class FakeProxy:
        def __init__(
            self,
            api_url,
            session_id,
            hook_token,
            workspace,
            initial_context="",
            model="",
            reasoning_effort="",
        ):
            assert api_url == "ws://127.0.0.1:8765"
            assert session_id == "sess_codex"
            assert hook_token == "hook-token"
            assert workspace == "C:/project"
            assert initial_context == "hello"
            assert model == ""
            assert reasoning_effort == ""

        async def start(self):
            return "ws://127.0.0.1:12345"

        async def stop(self):
            stopped.append(True)

    class FakeProcess:
        def wait(self):
            return 0 if drained_event.wait(1.0) else 4

    def fake_popen(command, cwd=None, env=None):
        captured_popen["command"] = command
        captured_popen["cwd"] = cwd
        captured_popen["env"] = env
        return FakeProcess()

    monkeypatch.setattr(module, "CodexNativeProxy", FakeProxy)
    monkeypatch.setattr(
        module,
        "build_native_codex_command",
        lambda remote_url, workspace, context="", model="", reasoning_effort="", config_overrides=None: [
            "codex",
            "--remote",
            remote_url,
            "--cd",
            workspace,
            model,
            reasoning_effort,
            ",".join(config_overrides or []),
            context,
        ],
    )
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setenv(module.LAUNCH_TOKEN_ENV, "launch-token")
    monkeypatch.setenv(module.FOREGROUND_REGISTRATION_TOKEN_ENV, "registration-token")
    monkeypatch.setenv(module.FOREGROUND_EXIT_TOKEN_ENV, "exit-token")
    monkeypatch.setenv(module.CLAUDE_HOOK_TOKEN_ENV, "hook-token")
    args = module.parse_args(
        ["--agent", "codex", "--workspace", "C:/project", "--native-cli", "--launch-id", "fg_codex", "--context", "hello"],
        env={
            module.LAUNCH_TOKEN_ENV: "launch-token",
            module.CLAUDE_HOOK_TOKEN_ENV: "hook-token",
            module.FOREGROUND_REGISTRATION_TOKEN_ENV: "registration-token",
            module.FOREGROUND_EXIT_TOKEN_ENV: "exit-token",
        },
    )
    state = {}
    ws = FakeWebSocket()

    result = asyncio.run(module._run_native_codex_cli(ws, args, state))

    assert result == 0
    assert captured_popen["command"] == [
        "codex",
        "--remote",
        "ws://127.0.0.1:12345",
        "--cd",
        "C:/project",
        "",
        "",
        "",
        "",
    ]
    assert captured_popen["cwd"] == "C:/project"
    assert module.LAUNCH_TOKEN_ENV not in captured_popen["env"]
    assert module.FOREGROUND_REGISTRATION_TOKEN_ENV not in captured_popen["env"]
    assert module.FOREGROUND_EXIT_TOKEN_ENV not in captured_popen["env"]
    assert module.CLAUDE_HOOK_TOKEN_ENV not in captured_popen["env"]
    assert stopped == [True]
    sent_types = [message["command"]["type"] for message in ws.sent if message.get("type") == "command"]
    assert sent_types == [
        "agent.session.register_foreground",
        "agent.session.foreground_exited",
    ]
    assert ws.sent[-1]["command"]["target"] == {"session_id": "sess_codex"}
    assert ws.sent[-1]["command"]["payload"]["foreground_exit_token"] == "exit-token"
