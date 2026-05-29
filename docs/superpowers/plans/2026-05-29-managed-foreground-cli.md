# Managed Foreground CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make software-launched Claude/Codex sessions appear as normal foreground CLI sessions while Local Core remains the owner of monitoring, approvals, interrupts, and close actions.

**Architecture:** Local Core will not scan or attach to arbitrary external processes. A new foreground CLI host process connects to Local API, creates a managed native session, streams session events to the terminal, accepts typed user prompts and slash commands, and sends approvals back through the existing permission response path. Provider adapters remain the only source of `permission_ack.forwarded=true`.

**Tech Stack:** Python 3.11-compatible service code, stdlib subprocess/asyncio, existing WebSocket Local API, existing Claude Agent SDK and Codex app-server adapters, pytest.

---

## File Structure

- Modify `src/bridge/session_manager.py`
  - Add session metadata fields for `launch_surface`, `control_mode`, and optional `frontend_pid`.
  - Keep persisted shape backward-compatible.
- Modify `src/agents/commands.py`
  - Add `agent.session.input` and `agent.cli.launch_foreground` command handlers.
  - Preserve `agent.session.launch_or_resume` as the managed provider session command.
- Modify `src/agents/runtime.py`
  - Add runtime wrapper methods for session input and foreground CLI process metadata updates.
- Modify `src/bridge/agent_proxy.py`
  - Add provider `send_input(session_id, text)` support.
  - Keep Claude SDK and Codex app-server native approval paths intact.
  - Keep app-server/SDK sessions alive for additional turns where provider APIs support it.
- Modify `src/bridge/server.py`
  - Register new commands.
  - Add capability mapping.
  - Broadcast CLI launch/session metadata in snapshots.
- Create `src/agents/foreground_cli.py`
  - Build safe foreground CLI host subprocess commands.
  - Hide machine paths from tracked config; resolve paths at runtime.
  - Use visible console only when requested by `agent.cli.launch_foreground`.
- Create `scripts/local-agent-cli.py`
  - Terminal host that connects to Local API, launches/resumes a managed session, prints events, reads stdin, and supports `/approve`, `/deny`, `/interrupt`, `/close`, `/exit`.
- Modify `scripts/local-hotkey-harness.py`
  - Use `agent.cli.launch_foreground` for user-facing launch hotkeys.
  - Keep explicit managed/headless launch only for approval smoke.
- Modify `scripts/local-api-smoke.py`
  - Add explicit `--launch-surface managed-headless` default for approval smoke.
  - Add foreground CLI launch smoke that asserts a foreground host process was started.
- Create or modify tests:
  - `tests/bridge/test_foreground_cli_commands.py`
  - `tests/bridge/test_agent_session_input.py`
  - `tests/bridge/test_agent_proxy.py`
  - `tests/harness/test_hotkey_harness.py`
  - `tests/bridge/test_local_api_smoke_cli.py`

## Product Semantics

- `agent.session.launch_or_resume`
  - Starts or resumes a managed provider session inside Local Core.
  - Used by smoke tests, automation, and the foreground CLI host.
  - Can monitor state and can forward native approvals.
- `agent.cli.launch_foreground`
  - Starts `scripts/local-agent-cli.py` in a foreground terminal.
  - The CLI host then calls `agent.session.launch_or_resume`.
  - This is the default user-facing launch path for keyboard/hotkey/UI shortcuts.
- External process scanning is out of scope.
- Raw externally-started Claude/Codex processes are out of scope.

## Task 1: Session Metadata and Snapshot Projection

**Files:**
- Modify: `src/bridge/session_manager.py`
- Modify: `src/bridge/server.py`
- Test: `tests/bridge/test_session_manager.py`
- Test: `tests/bridge/test_structured_agent_commands.py`

- [ ] **Step 1: Write failing session metadata tests**

Add tests that create a session, set metadata, and verify `to_dict()` includes the metadata while old persisted entries still load.

```python
def test_session_metadata_round_trips_without_breaking_old_payloads():
    session = Session(
        session_id="sess_meta",
        agent=AgentType.CODEX,
        launch_surface="foreground_cli",
        control_mode="managed_native",
        frontend_pid=1234,
    )

    payload = session.to_dict()

    assert payload["launch_surface"] == "foreground_cli"
    assert payload["control_mode"] == "managed_native"
    assert payload["frontend_pid"] == 1234
    restored = Session.from_dict(payload)
    assert restored.launch_surface == "foreground_cli"
    assert restored.control_mode == "managed_native"
    assert restored.frontend_pid == 1234


def test_old_session_payload_defaults_to_managed_headless_metadata():
    restored = Session.from_dict({
        "session_id": "sess_old",
        "agent": "claude",
        "state": "IDLE",
        "created_at": 1,
        "updated_at": 1,
    })

    assert restored.launch_surface == "managed_headless"
    assert restored.control_mode == "managed_native"
    assert restored.frontend_pid is None
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/bridge/test_session_manager.py::test_session_metadata_round_trips_without_breaking_old_payloads tests/bridge/test_session_manager.py::test_old_session_payload_defaults_to_managed_headless_metadata -q
```

Expected: FAIL because `Session` does not yet expose the metadata fields.

- [ ] **Step 3: Implement metadata fields**

Add fields to `Session`:

```python
launch_surface: str = "managed_headless"
control_mode: str = "managed_native"
frontend_pid: Optional[int] = None
```

Update `to_dict()` and `from_dict()` to round-trip those fields. Validate only known launch surfaces:

```python
VALID_LAUNCH_SURFACES = {"managed_headless", "foreground_cli"}
VALID_CONTROL_MODES = {"managed_native"}
```

Invalid persisted values should fall back to defaults rather than crashing restore.

- [ ] **Step 4: Add snapshot assertion**

Extend `tests/bridge/test_structured_agent_commands.py` to assert created session events and snapshot records include `launch_surface` and `control_mode`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests/bridge/test_session_manager.py tests/bridge/test_structured_agent_commands.py -q
```

Expected: PASS.

## Task 2: Managed Session Input Command

**Files:**
- Modify: `src/agents/commands.py`
- Modify: `src/agents/runtime.py`
- Modify: `src/bridge/server.py`
- Test: `tests/bridge/test_agent_session_input.py`

- [ ] **Step 1: Write failing tests for `agent.session.input`**

Create `tests/bridge/test_agent_session_input.py` with a fake controller:

```python
class FakeInputController:
    def __init__(self, service):
        self.service = service
        self.inputs = []

    def is_available(self):
        return True

    async def send_input(self, session_id, text):
        self.inputs.append((session_id, text))
        return True
```

Test accepted input:

```python
def test_structured_session_input_calls_controller_and_emits_event():
    server = make_server_with_input_controller()
    session = server.session_mgr.create(AgentType.CLAUDE)
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.input",
        target={"session_id": session.session_id},
        payload={"text": "run tests"},
        command_id="cmd_input",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.session.input.accepted"
    assert event["payload"]["session_id"] == session.session_id
    assert server.agents[AgentType.CLAUDE].inputs == [(session.session_id, "run tests")]
```

Test empty input rejection:

```python
def test_structured_session_input_rejects_empty_text():
    server = make_server_with_input_controller()
    session = server.session_mgr.create(AgentType.CLAUDE)
    queue = CaptureQueue()

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.session.input",
        target={"session_id": session.session_id},
        payload={"text": ""},
        command_id="cmd_empty_input",
    ), queue))

    error = read_error(queue)
    assert error["code"] == "INVALID_COMMAND"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m pytest tests/bridge/test_agent_session_input.py -q
```

Expected: FAIL because the command is not registered.

- [ ] **Step 3: Implement command handler**

Add `AgentCommandService.send_input()`:

```python
async def send_input(self, command: CommandEnvelope) -> EventEnvelope:
    session_id = self._required_session_id(command)
    text = command.payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise AgentLifecycleError("INVALID_COMMAND", "text is required")
    session = self.runtime.require_session(session_id)
    controller = self.runtime.require_controller(session.agent)
    send_input = getattr(controller, "send_input", None)
    if send_input is None:
        raise AgentLifecycleError("INPUT_UNAVAILABLE", "controller does not accept session input")
    accepted = await send_input(session_id, text)
    if not accepted:
        raise AgentLifecycleError("INPUT_REJECTED", "session input was not accepted by controller")
    self.runtime.persist(session_id)
    return self._event("agent.session.input.accepted", session_id, accepted=True)
```

Register it in `LocalCoreRuntime.configure_agent_commands()` and capability map it to `agent:launch`.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests/bridge/test_agent_session_input.py tests/bridge/test_structured_agent_commands.py -q
```

Expected: PASS.

## Task 3: Provider Input Support

**Files:**
- Modify: `src/bridge/agent_proxy.py`
- Test: `tests/bridge/test_agent_proxy.py`

- [ ] **Step 1: Write failing Codex input test**

Add a fake Codex app-server client and assert `send_input()` starts a new turn with the known thread id and workspace.

```python
def test_codex_app_server_send_input_starts_turn_with_existing_thread(tmpdir):
    proxy = make_codex_app_server_proxy(workspace=tmpdir)
    proxy._codex_thread_ids["sess_1"] = "thread_1"
    proxy._codex_clients["sess_1"] = FakeCodexClient()

    asyncio.run(proxy.send_input("sess_1", "next prompt"))

    assert proxy._codex_clients["sess_1"].turns == [{
        "thread_id": "thread_1",
        "prompt": "next prompt",
        "cwd": str(Path(str(tmpdir)).resolve()),
    }]
```

- [ ] **Step 2: Write failing Claude input test**

Test that Claude SDK mode enqueues input into the session prompt queue.

```python
def test_claude_agent_sdk_send_input_enqueues_prompt():
    proxy = make_claude_sdk_proxy()
    proxy._sdk_prompt_queues["sess_1"] = asyncio.Queue()

    asyncio.run(proxy.send_input("sess_1", "next prompt"))

    assert asyncio.run(proxy._sdk_prompt_queues["sess_1"].get()) == "next prompt"
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/bridge/test_agent_proxy.py -q
```

Expected: FAIL because `send_input()` and `_sdk_prompt_queues` are missing.

- [ ] **Step 4: Implement provider input**

Add:

```python
self._sdk_prompt_queues: Dict[str, asyncio.Queue] = {}
self._session_workspaces: Dict[str, Path] = {}
```

In launch paths, store `self._session_workspaces[session_id] = launch_workspace`.

Add:

```python
async def send_input(self, session_id: str, text: str) -> bool:
    if self._uses_codex_app_server():
        client = self._codex_clients.get(session_id)
        thread_id = self._codex_thread_ids.get(session_id)
        if client is None or not thread_id:
            return False
        cwd = str(self._session_workspaces.get(session_id, self._workspace))
        await client.start_turn(thread_id=thread_id, prompt=text, cwd=cwd)
        self._sm.update_state(session_id, AgentState.SUBMITTED)
        return True
    if self._uses_claude_agent_sdk():
        queue = self._sdk_prompt_queues.get(session_id)
        if queue is None:
            return False
        await queue.put(text)
        self._sm.update_state(session_id, AgentState.SUBMITTED)
        return True
    proc = self._processes.get(session_id)
    if proc is None or proc.stdin is None:
        return False
    proc.stdin.write((text + "\n").encode("utf-8"))
    await proc.stdin.drain()
    return True
```

Update `_claude_sdk_prompt_stream()` to read from the queue until done:

```python
while not done_event.is_set():
    text = await queue.get()
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
    }
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests/bridge/test_agent_proxy.py tests/bridge/test_agent_session_input.py -q
```

Expected: PASS.

## Task 4: Foreground CLI Launcher

**Files:**
- Create: `src/agents/foreground_cli.py`
- Modify: `src/agents/commands.py`
- Modify: `src/app/lifecycle.py`
- Modify: `src/bridge/server.py`
- Test: `tests/bridge/test_foreground_cli_commands.py`

- [ ] **Step 1: Write failing command-builder tests**

Create tests for `build_foreground_cli_command()`:

```python
def test_foreground_cli_command_uses_repo_script_and_workspace(tmpdir):
    command = build_foreground_cli_command(
        agent="codex",
        workspace=str(tmpdir),
        api_url="ws://127.0.0.1:8765",
        token="token-value",
        python_executable="python",
    )

    assert command[0] == "python"
    assert command[1].endswith("scripts/local-agent-cli.py")
    assert "--agent" in command
    assert "--workspace" in command
    assert str(tmpdir) in command
```

- [ ] **Step 2: Write failing Local API command test**

Test `agent.cli.launch_foreground` calls a fake launcher and emits `agent.cli.launched`.

```python
def test_cli_launch_foreground_emits_event_with_frontend_pid():
    server = make_server()
    server.foreground_cli_launcher = FakeForegroundLauncher(pid=4321)
    queue = CaptureQueue()
    server.connected_clients.add(queue)

    asyncio.run(server._cmd_structured_command(command_message(
        "agent.cli.launch_foreground",
        payload={"agent": "claude", "workspace": "C:/project"},
        command_id="cmd_cli_launch",
    ), queue))

    event = read_event(queue)
    assert event["type"] == "agent.cli.launched"
    assert event["payload"]["agent"] == "claude"
    assert event["payload"]["frontend_pid"] == 4321
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/bridge/test_foreground_cli_commands.py -q
```

Expected: FAIL because launcher module and command are missing.

- [ ] **Step 4: Implement foreground launcher**

Implement `ForegroundCliLauncher.launch(agent, workspace, api_url, token)` using `subprocess.Popen`.

On Windows, use `CREATE_NEW_CONSOLE`:

```python
creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
subprocess.Popen(command, cwd=workspace, env=env, creationflags=creationflags)
```

On other platforms, fall back to `subprocess.Popen(command, cwd=workspace, env=env)`.

The command must launch `scripts/local-agent-cli.py`, not raw arbitrary shell commands.

- [ ] **Step 5: Register Local API command**

Add `AgentCommandService.launch_foreground_cli()` and register `agent.cli.launch_foreground`. The event payload must include:

```python
{
    "agent": "claude" or "codex",
    "workspace": "<resolved workspace>",
    "frontend_pid": 4321,
    "launch_surface": "foreground_cli",
    "control_mode": "managed_native",
}
```

- [ ] **Step 6: Run focused tests**

Run:

```powershell
python -m pytest tests/bridge/test_foreground_cli_commands.py tests/bridge/test_structured_agent_commands.py -q
```

Expected: PASS.

## Task 5: Local Agent CLI Host

**Files:**
- Create: `scripts/local-agent-cli.py`
- Test: `tests/bridge/test_local_agent_cli.py`

- [ ] **Step 1: Write failing parser tests**

Test default options:

```python
def test_local_agent_cli_parser_defaults_to_managed_session():
    args = parse_args(["--agent", "codex", "--workspace", "C:/project"])

    assert args.agent == "codex"
    assert args.workspace == "C:/project"
    assert args.client_kind == "desktop-ui"
```

- [ ] **Step 2: Write failing message builder tests**

Test launch and input messages:

```python
def test_cli_builds_launch_and_input_commands():
    launch = build_launch_command("codex", "C:/project")
    assert launch["type"] == "command"
    assert launch["command"]["type"] == "agent.session.launch_or_resume"
    assert launch["command"]["payload"]["agent"] == "codex"
    assert launch["command"]["payload"]["workspace"] == "C:/project"

    input_message = build_input_command("sess_1", "hello")
    assert input_message["command"]["type"] == "agent.session.input"
    assert input_message["command"]["target"] == {"session_id": "sess_1"}
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/bridge/test_local_agent_cli.py -q
```

Expected: FAIL because script is missing.

- [ ] **Step 4: Implement CLI host**

The script must:

- connect to Local API WebSocket
- send `hello` as `desktop-ui` with `agent:launch`, `permission:respond`, and `session:list`
- send `agent.session.launch_or_resume`
- print `agent_message_delta`, `task_update`, `permission_request`, `permission_ack`, `task_completed`, and `task_failed`
- read stdin lines
- route normal lines to `agent.session.input`
- route `/approve` and `/deny` to `permission_response` for the focused pending permission
- route `/interrupt` and `/close` to existing structured commands
- route `/exit` to close the CLI host

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests/bridge/test_local_agent_cli.py tests/bridge/test_local_api_smoke_cli.py -q
```

Expected: PASS.

## Task 6: Hotkey and Smoke Integration

**Files:**
- Modify: `scripts/local-hotkey-harness.py`
- Modify: `scripts/local-api-smoke.py`
- Test: `tests/harness/test_hotkey_harness.py`
- Test: `tests/bridge/test_local_api_smoke_cli.py`

- [ ] **Step 1: Write failing hotkey tests**

Update launch hotkey tests so `Ctrl+Alt+Shift+1` and `Ctrl+Alt+Shift+2` emit `agent.cli.launch_foreground`, not direct managed session launch.

- [ ] **Step 2: Write failing smoke parser tests**

Assert approval smoke still sends managed session launch, while foreground smoke uses CLI launch.

- [ ] **Step 3: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/harness/test_hotkey_harness.py tests/bridge/test_local_api_smoke_cli.py -q
```

Expected: FAIL until scripts are updated.

- [ ] **Step 4: Update scripts**

Hotkey launch commands use:

```json
{
  "type": "agent.cli.launch_foreground",
  "payload": {"agent": "codex"}
}
```

Approval smoke keeps:

```json
{
  "type": "agent.session.launch_or_resume",
  "payload": {"agent": "codex", "context": "..."}
}
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests/harness/test_hotkey_harness.py tests/bridge/test_local_api_smoke_cli.py -q
```

Expected: PASS.

## Task 7: Real Verification

**Files:**
- No code files unless previous tasks reveal defects.

- [ ] **Step 1: Run focused backend tests**

Run:

```powershell
python -m pytest tests/bridge/test_agent_proxy.py tests/bridge/test_agent_session_input.py tests/bridge/test_foreground_cli_commands.py tests/bridge/test_local_agent_cli.py tests/harness/test_hotkey_harness.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full tests**

Run:

```powershell
python -m pytest tests -q
```

Expected: PASS.

- [ ] **Step 3: Run Claude managed approval smoke**

Run:

```powershell
.\.venv-claude\Scripts\python.exe scripts/local-api-smoke.py --scenario approval-real --agent claude --decision approve --require-forwarded --workspace (Resolve-Path ..).Path --auto-start-service --timeout 120 --json-log
```

Expected: `permission_ack.forwarded=true` with `adapter="claude_agent_sdk"`.

- [ ] **Step 4: Run foreground CLI launch smoke**

Run:

```powershell
python scripts/local-api-smoke.py --scenario foreground-cli --agent claude --workspace (Resolve-Path ..).Path --auto-start-service --timeout 60 --json-log
```

Expected: event `agent.cli.launched` with non-empty `frontend_pid`.

## Self-Review

- Spec coverage: The plan covers no external scanning, internally launched sessions only, foreground CLI host, managed monitoring, native approval forwarding, hotkey launch, and smoke verification.
- Placeholder scan: No task depends on undefined future work. Provider API limitations are isolated to `send_input()` returning `False` instead of claiming support.
- Type consistency: Session metadata uses `launch_surface`, `control_mode`, and `frontend_pid` consistently across session dicts, events, and tests.
