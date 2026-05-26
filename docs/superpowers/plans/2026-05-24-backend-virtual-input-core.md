# Backend Virtual Input Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend-only Local Core loop so virtual keyboard input can launch/resume/close/interrupt agent sessions, approve or deny permissions, switch focus/active tools, activate profiles, and project state back to simulated devices.

**Architecture:** Device transports produce bounded virtual input events, keyboard runtime resolves those events through active profile bindings into structured commands, and async command handlers own side effects through agent/keyboard/device services. Bridge WebSocket compatibility remains, but legacy messages are converted into the same command path instead of bypassing core routing.

**Tech Stack:** Python 3.11, asyncio, pytest, websockets, SQLite, existing Codex app-server and Claude SDK adapters.

---

## Current Status

This plan is now a historical implementation plan plus final status record.
The task checkboxes below are retained to show the original TDD execution
sequence; they are not the current progress source of truth.

Implemented backend scope:

- Async `CommandRouter`.
- Structured agent lifecycle commands for launch/resume, interrupt, close, and
  permission response.
- Unified permission command with client capability and approval policy gates.
- Per-device focus and symbolic target resolution.
- Keyboard binding resolver and action command factory.
- Profile, keymap, lighting config, profile compilation, active profile, and
  import/export persistence.
- Virtual device ingress, simulated transport session loop, device projection,
  active tool switching, and device config sync.
- Local API virtual-input path and smoke scenario.
- Workspace-aware real agent launch/resume payloads and default workspace
  resolution. Resolution prefers CLI `--workspace`, then `AI_KEYB_WORKSPACE`,
  then configured non-dot default workspace, then the nearest parent project
  root containing `software`, then the service start directory.
- Local hotkey harness for temporary external real loopback input. It connects
  as `desktop-ui` with client id `test-harness` and is not the formal product
  device transport.
- Smoke controls for real loopback testing: `--workspace`,
  `--auto-start-service`, `--config`, `--service-start-timeout`, and
  `--wait-for-hotkey-approval`.
- Diagnostics, redaction, import-boundary guards, and machine-path guards.

Maintained boundaries:

- Backend-only scope; no formal frontend or desktop shell was added.
- Device interaction remains simulator/virtual-input based.
- High-risk real approval testing currently uses the desktop-ui/test-harness
  loopback path. Formal device-transport security remains low-risk only.
- Physical USB HID, CDC, BLE, and 2.4G transports remain deferred.
- Legacy Local API compatibility remains.
- `permission_ack.forwarded=true` remains limited to provider-native delivery.

Final verification evidence recorded for this backend virtual-input integration:

```text
pytest tests -q -> 265 passed, 1 skipped in 3.49s
pytest tests/architecture/test_import_boundaries.py -q -> 4 passed
pytest tests/bridge/test_virtual_input_local_api.py -q -> 8 passed
scripts/local-api-smoke.py help includes --scenario {basic,permission,real-agent,approval-real,virtual-input}
```

The final backend virtual-input verification pass did not rerun external real
Codex or Claude CLI approval smoke. Earlier Codex native approval smoke is
separate evidence from the approval-forwarding work and should not be described
as rerun in the final virtual-input pass. Claude real loopback also requires
the Python Claude Agent SDK dependency and local provider authentication before
native callback evidence can be produced.

## Baseline

- Baseline branch: `backend-virtual-input-core`.
- Do not implement formal frontend or physical USB/CDC/BLE/2.4G transports.
- Do not remove legacy Local API messages.
- Do not let `src/devices` or `src/keyboard` import `src/bridge` or `AgentProxy`.
- Keep real native approval semantics: `permission_ack.forwarded=true` only after provider-native delivery.
- Main agent coordinates only. Worker agents write code/tests. Reviewer agents inspect and test after each worker.

## Target Pipeline

```text
DeviceTransportSession
  -> VirtualInputGateway
  -> KeyboardInputEvent
  -> BindingResolver / LayerState
  -> KeyboardAction
  -> ActionCommandFactory + TargetResolver
  -> async CommandRouter
  -> AgentRuntime / KeyboardRuntime / DeviceRuntime
  -> EventBus + StateStore
  -> DeviceProjectionRuntime
  -> DeviceFrame
```

## File Structure

Create or extend these focused modules:

- `src/core/command_router.py`: sync and async command dispatch.
- `src/core/target_resolution.py`: symbolic target resolution for focused targets and active selectors.
- `src/core/state_store.py`: snapshot fields for focus, active profile, active tools, and device projection state.
- `src/agents/commands.py`: agent lifecycle and permission command handlers.
- `src/agents/runtime.py`: small adapter boundary around existing `AgentProxy` and `SessionManager`.
- `src/keyboard/input.py`: keyboard input event dataclasses.
- `src/keyboard/bindings.py`: active layer state, binding matching, priority resolution.
- `src/keyboard/action_commands.py`: keyboard action to `CommandEnvelope`.
- `src/keyboard/runtime.py`: focus, active tool, and active profile command handlers.
- `src/keyboard/layouts.py`: default physical layout/key id registry.
- `src/keyboard/lighting.py`: lighting config model, validation, and compile payload.
- `src/keyboard/compiler.py`: profile to device offline subset compiler.
- `src/keyboard/profile_service.py`: profile CRUD and activation service.
- `src/devices/virtual_input.py`: simulated input frame model and decoder.
- `src/devices/command_adapter.py`: device input to command router adapter.
- `src/devices/session.py`: simulated transport session loop and resync entry point.
- `src/devices/config_sync.py`: profile/config/light sync chunking and simulated commit/reject.
- `src/devices/projection_runtime.py`: snapshot/event projection to device frames.
- `src/persistence/import_export.py`: conflict-aware config/profile import-export.
- `src/diagnostics/profile_diagnostics.py`: structured validation and sync diagnostics.

Tests should be added under:

- `tests/architecture/`
- `tests/bridge/`
- `tests/device/`
- `tests/keyboard/`
- `tests/persistence/`

## Review Gates

After each worker:

1. Spec compliance reviewer checks the worker result against this plan and architecture docs.
2. Code quality reviewer runs the worker's targeted tests and checks coupling, naming, error semantics, and maintainability.
3. Main agent only proceeds after Critical and Important findings are fixed.

## Task 1: Async Command Router

**Files:**
- Modify: `src/core/command_router.py`
- Modify: `src/app/lifecycle.py`
- Modify: `src/bridge/server.py`
- Test: `tests/architecture/test_async_command_router.py`

- [x] **Step 1: Write failing tests**

```python
async def test_dispatch_async_awaits_async_handler():
    calls = []
    async def handler(command):
        calls.append(command.command_id)
        return EventEnvelope(seq=0, type="system.async_done", payload={"ok": True})

    router.register("system.async", handler)
    event = await router.dispatch_async(command)

    assert calls == ["cmd_1"]
    assert event.type == "system.async_done"
```

Also cover:

- sync handlers still work through `dispatch_async`
- existing `dispatch` still works for sync handlers
- `dispatch` rejects async handlers with an explicit error
- unknown command keeps existing `KeyError` behavior

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/architecture/test_async_command_router.py -q
```

Expected: fails because `dispatch_async` does not exist.

- [x] **Step 3: Implement**

Add `dispatch_async()` using `inspect.isawaitable()`. Keep `dispatch()` for synchronous callers. Do not add domain logic to the router.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/architecture/test_async_command_router.py tests/bridge/test_local_api_websocket_flow.py -q
```

Expected: all selected tests pass.

## Task 2: Agent Lifecycle Structured Commands

**Files:**
- Create: `src/agents/runtime.py`
- Create: `src/agents/commands.py`
- Modify: `src/app/lifecycle.py`
- Modify: `src/bridge/server.py`
- Test: `tests/bridge/test_structured_agent_commands.py`

- [x] **Step 1: Write failing tests**

Cover:

- `agent.session.launch_or_resume` with `session_id="new"` creates a session and calls fake controller `launch`.
- `agent.session.launch_or_resume` with an existing session calls fake controller `resume`.
- `agent.run.interrupt` calls fake controller `send_interrupt` and marks session cancelled.
- `agent.session.close` calls fake controller `terminate`.
- legacy `agent_launch` and `interrupt` produce the same state and events as structured commands.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/bridge/test_structured_agent_commands.py -q
```

Expected: unknown command or missing handler failures.

- [x] **Step 3: Implement**

Create an `AgentCommandService` that owns lifecycle side effects against injected dependencies:

- `SessionManager`
- provider controller map
- session persistence callback
- event encoder/broadcaster callback where needed

Command handlers return structured events:

- `agent.session.created`
- `agent.session.state_changed`
- `agent.run.interrupted`
- `agent.session.closed`

Keep `src/bridge/server.py` as WebSocket/auth serialization and compatibility adapter only.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/bridge/test_structured_agent_commands.py tests/bridge/test_local_api_websocket_flow.py tests/bridge/test_session_list.py -q
```

Expected: legacy compatibility remains green.

## Task 3: Unified Permission Command

**Files:**
- Modify: `src/agents/commands.py`
- Modify: `src/bridge/server.py`
- Modify: `src/security/client_identity.py`
- Modify: `src/security/policy.py`
- Test: `tests/bridge/test_structured_permission_command.py`

- [x] **Step 1: Write failing tests**

Cover:

- `agent.permission.respond` accepts `target.permission_id` and optional `session_id`.
- desktop client can approve a pending fake permission.
- device client with low-risk capability can approve low-risk permission.
- device client cannot approve high-risk permission and gets `REQUIRE_DESKTOP_CONFIRM`.
- provider native forward failure leaves the request pending.
- permission history persists forwarded evidence.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/bridge/test_structured_permission_command.py -q
```

Expected: unknown command or old legacy-only path failures.

- [x] **Step 3: Implement**

Move permission decision logic behind an injectable command service method. Legacy `permission_response` should build and dispatch `agent.permission.respond`, then serialize the same `permission_ack`.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/bridge/test_structured_permission_command.py tests/bridge/test_permission_flow.py tests/bridge/test_security_local_api.py -q
```

Expected: existing native forwarding guarantees remain intact.

## Task 4: Focus and Target Resolution Runtime

**Files:**
- Create: `src/core/target_resolution.py`
- Modify: `src/keyboard/focus.py`
- Create: `src/keyboard/runtime.py`
- Modify: `src/core/state_store.py`
- Modify: `src/app/lifecycle.py`
- Test: `tests/keyboard/test_focus_commands.py`

- [x] **Step 1: Write failing tests**

Cover:

- `agent.focus.set` stores focus per device.
- `agent.focus.next_session` cycles sessions without affecting another device.
- `focused_permission` resolves in run, session, instance, then priority order.
- `focused_run` fallback emits a structured unresolved-target error.
- snapshot contains focus by device.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/keyboard/test_focus_commands.py -q
```

Expected: command handlers and snapshot focus fields are missing.

- [x] **Step 3: Implement**

Keep resolution pure and testable. Do not call provider adapters from target resolution.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/keyboard/test_focus_commands.py tests/keyboard/test_focus_profile_validation.py -q
```

Expected: all selected tests pass.

## Task 5: Keyboard Binding Resolver and Action Commands

**Files:**
- Create: `src/keyboard/input.py`
- Create: `src/keyboard/bindings.py`
- Create: `src/keyboard/action_commands.py`
- Modify: `src/keyboard/profile.py`
- Test: `tests/keyboard/test_binding_resolver.py`
- Test: `tests/keyboard/test_virtual_input_actions.py`

- [x] **Step 1: Write failing tests**

Cover:

- `Fn+Enter` press resolves to `agent.permission.respond` with `focused_permission`.
- `Fn+Esc` press resolves to `agent.run.interrupt` with `focused_run`.
- launch key resolves to `agent.session.launch_or_resume`.
- layer priority chooses the highest priority matching layer.
- release does not trigger a press binding.
- no match returns an empty action list, not an error.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/keyboard/test_binding_resolver.py tests/keyboard/test_virtual_input_actions.py -q
```

Expected: missing modules/classes.

- [x] **Step 3: Implement**

Use dataclasses for input events and resolved actions. The resolver should not dispatch commands itself. `action_commands.py` converts resolved actions into `CommandEnvelope` with source kind `device-transport`.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/keyboard/test_binding_resolver.py tests/keyboard/test_virtual_input_actions.py -q
```

Expected: all selected tests pass.

## Task 6: Profile, Keymap, and Lighting Configuration

**Files:**
- Create: `src/keyboard/layouts.py`
- Create: `src/keyboard/lighting.py`
- Create: `src/keyboard/compiler.py`
- Create: `src/keyboard/profile_service.py`
- Modify: `src/keyboard/profile.py`
- Modify: `src/devices/device_transport.py`
- Test: `tests/keyboard/test_profile_keymap_lighting.py`

- [x] **Step 1: Write failing tests**

Cover:

- default layout contains `K_FN`, `K_ENTER`, `K_ESC`, and launch/tool keys.
- profile rejects unknown key references.
- lighting brightness must be 0 to 100.
- lighting per-key override rejects unknown keys.
- device without `lighting` feature rejects lighting config sync.
- profile JSON round trip preserves lighting and key bindings.
- compiler output includes offline HID/layer/macro/lighting subset and marks agent actions as service-required.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/keyboard/test_profile_keymap_lighting.py -q
```

Expected: lighting/compiler/profile service modules missing.

- [x] **Step 3: Implement**

Keep profile storage semi-structured for now, but validation and compiled output must be structured. Do not add UI-only concepts.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/keyboard/test_profile_keymap_lighting.py tests/persistence/test_sqlite_app_store.py -q
```

Expected: profile persistence round trips remain green.

## Task 7: Active Profile and Import/Export Persistence

**Files:**
- Modify: `src/persistence/migrations.py`
- Modify: `src/persistence/repositories.py`
- Create: `src/persistence/import_export.py`
- Modify: `src/app/lifecycle.py`
- Test: `tests/persistence/test_active_config_import_export.py`

- [x] **Step 1: Write failing tests**

Cover:

- empty DB migration creates settings/app config storage.
- active profile persists across `SQLiteAppStore.open()`.
- import conflict with same profile ID does not overwrite unless explicit replace is requested.
- import with `rename_on_conflict` creates a new profile ID.
- unsupported schema version is rejected.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/persistence/test_active_config_import_export.py -q
```

Expected: settings/import-export APIs missing.

- [x] **Step 3: Implement**

Use a small key/value settings repository for `active_profile_id`, active tool by device, and global config flags. Keep migrations forward-only.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/persistence/test_active_config_import_export.py tests/persistence/test_sqlite_app_store.py -q
```

Expected: all selected persistence tests pass.

## Task 8: Virtual Device Ingress and Projection Runtime

**Files:**
- Create: `src/devices/virtual_input.py`
- Create: `src/devices/command_adapter.py`
- Create: `src/devices/session.py`
- Create: `src/devices/projection_runtime.py`
- Modify: `src/devices/manager.py`
- Modify: `src/devices/projection.py`
- Test: `tests/device/test_virtual_input_gateway.py`
- Test: `tests/device/test_virtual_device_commands.py`
- Test: `tests/device/test_projection_runtime.py`

- [x] **Step 1: Write failing tests**

Cover:

- `INPUT_EVENT` frame decodes to a key press event.
- slot generation mismatch returns `UNKNOWN_SLOT_GENERATION`.
- unknown frame returns a structured recoverable error frame.
- device connect sends snapshot frames in order: snapshot begin, slot map, profile summary, focus, notifications/permissions, snapshot end.
- permission event projects an incremental `PERMISSION_REQUEST_PUSH`.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/device/test_virtual_input_gateway.py tests/device/test_virtual_device_commands.py tests/device/test_projection_runtime.py -q
```

Expected: virtual ingress/projection runtime modules missing.

- [x] **Step 3: Implement**

The device session may depend on `CommandRouter`, `DeviceManager`, `DeviceProtocolCodec`, and keyboard action services. It must not depend on `bridge.server` or `AgentProxy`.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/device/test_virtual_input_gateway.py tests/device/test_virtual_device_commands.py tests/device/test_projection_runtime.py tests/device/test_device_projection.py tests/device/test_device_transport.py -q
```

Expected: all selected device tests pass.

## Task 9: Active Tool Switching

**Files:**
- Modify: `docs/architecture/event_command_model.md`
- Modify: `docs/architecture/keyboard/keymap_layer_action_model.md`
- Create: `src/keyboard/tool_state.py`
- Modify: `src/keyboard/runtime.py`
- Modify: `src/keyboard/action_commands.py`
- Modify: `src/core/state_store.py`
- Test: `tests/keyboard/test_tool_switch.py`

- [x] **Step 1: Write failing tests**

Cover:

- `keyboard.tool.switch` accepts known tools for a device.
- `keyboard.tool.next` cycles configured tools.
- unknown tool is rejected without mutating state.
- active tool appears in snapshot and emits `keyboard.tool.changed`.
- virtual input can trigger tool switch through a binding.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/keyboard/test_tool_switch.py -q
```

Expected: command and state missing.

- [x] **Step 3: Implement**

Define initial tools as backend control modes, not UI widgets:

- `agent_control`
- `session_list`
- `permissions`
- `profile_config`
- `device_status`

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/keyboard/test_tool_switch.py tests/keyboard/test_virtual_input_actions.py -q
```

Expected: all selected tests pass.

## Task 10: Device Config Sync

**Files:**
- Create: `src/devices/config_sync.py`
- Modify: `src/devices/device_transport.py`
- Modify: `src/devices/protocol_codec.py`
- Modify: `src/keyboard/compiler.py`
- Test: `tests/device/test_config_sync.py`

- [x] **Step 1: Write failing tests**

Cover:

- sync validates device capabilities before sending.
- compiled payload is chunked below `max_payload_size`.
- frame order is `PROFILE_SYNC_BEGIN`, one or more `PROFILE_SYNC_CHUNK`, `PROFILE_SYNC_END`.
- simulator commit result is observable.
- reject does not change active synced profile marker.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/device/test_config_sync.py -q
```

Expected: config sync module missing.

- [x] **Step 3: Implement**

Keep sync transport-independent. The simulator can provide deterministic accept/reject behavior for tests.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/device/test_config_sync.py tests/device/test_device_projection.py -q
```

Expected: all selected tests pass.

## Task 11: Local API and Smoke Coverage

**Files:**
- Modify: `src/bridge/server.py`
- Modify: `scripts/local-api-smoke.py`
- Test: `tests/bridge/test_virtual_input_local_api.py`

- [x] **Step 1: Write failing tests**

Cover:

- structured command path can launch, interrupt, close, and permission respond with fake controllers.
- Local API compatibility messages still work.
- smoke scenario `virtual-input` can send a virtual key sequence through Local API or simulator adapter.
- snapshot after virtual actions includes active profile, focus, active tool, sessions, permissions, and device state.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/bridge/test_virtual_input_local_api.py -q
```

Expected: smoke scenario and structured handlers missing.

- [x] **Step 3: Implement**

Add a backend-only smoke path. Do not add formal frontend or desktop shell.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/bridge/test_virtual_input_local_api.py tests/bridge/test_local_api_websocket_flow.py tests/bridge/test_permission_flow.py -q
```

Expected: all selected bridge tests pass.

## Task 12: Diagnostics and Import Boundary Guard

**Files:**
- Create: `src/diagnostics/profile_diagnostics.py`
- Modify: `src/diagnostics/health.py`
- Test: `tests/diagnostics/test_backend_diagnostics.py`
- Test: `tests/architecture/test_import_boundaries.py`

- [x] **Step 1: Write failing tests**

Cover:

- diagnostics report local API, DB, device transport, profile validation, and config sync status.
- diagnostic export redacts tokens and API keys.
- `src/devices` and `src/keyboard` do not import `bridge` or `AgentProxy`.
- docs and runtime config contain no machine-specific absolute path in new files.

- [x] **Step 2: Verify RED**

Run:

```text
pytest tests/diagnostics/test_backend_diagnostics.py tests/architecture/test_import_boundaries.py -q
```

Expected: diagnostics/import-boundary tests missing or failing.

- [x] **Step 3: Implement**

Keep diagnostics read-only. Do not start external CLIs from diagnostics tests.

- [x] **Step 4: Verify GREEN**

Run:

```text
pytest tests/diagnostics/test_backend_diagnostics.py tests/architecture/test_import_boundaries.py -q
```

Expected: all selected tests pass.

## Final Integration

- [x] Run targeted suites from every worker.
- [x] Run full test suite:

```text
pytest tests -q
```

Result recorded by final implementation verification:

```text
265 passed, 1 skipped in 3.49s
```

- [x] Run final focused backend virtual-input checks.

```text
pytest tests/architecture/test_import_boundaries.py -q -> 4 passed
pytest tests/bridge/test_virtual_input_local_api.py -q -> 8 passed
```

- [x] Verify virtual input smoke scenario is exposed.

```text
scripts/local-api-smoke.py help includes --scenario {basic,permission,real-agent,approval-real,virtual-input}
scripts/local-api-smoke.py help includes --workspace, --auto-start-service, --config, --service-start-timeout, --wait-for-hotkey-approval
```

- [ ] Real Codex approval smoke regression was not rerun in the final
  backend virtual-input verification pass:

```text
python scripts/local-api-smoke.py --scenario approval-real --agent codex --decision approve --require-forwarded --timeout 120 --json-log
```

Current V2 loopback form when a separate hotkey harness should submit the
approval:

```text
python scripts/local-api-smoke.py --scenario approval-real --agent codex --decision approve --require-forwarded --workspace <workspace> --auto-start-service --wait-for-hotkey-approval --timeout 120 --json-log
python scripts/local-hotkey-harness.py --workspace <workspace> --json-log
```

- [ ] Real Codex denial smoke regression was not rerun in the final backend
  virtual-input verification pass:

```text
python scripts/local-api-smoke.py --scenario approval-real --agent codex --decision deny --require-forwarded --timeout 120 --json-log
```

- [ ] Claude approval regression was not rerun in the final backend
  virtual-input verification pass. The current V2 loopback form mirrors the
  Codex shape with `--agent claude`, but requires the Python Claude Agent SDK
  dependency and local provider authentication.
- [x] Verify no new hardcoded machine paths in architecture and plan docs:

Result: no machine-specific path matches in architecture and plan docs.

- [ ] Request final spec compliance review.
- [ ] Request final code quality review.
- [ ] Commit and push only after reviews and verification pass.
