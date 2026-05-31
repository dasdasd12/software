# Software Backend V2 Implementation Status

This document records what the current backend implementation actually supports.
It is a status companion to the architecture documents, not a replacement for
the target architecture.

## Roadmap Direction

As of 2026-05-31, follow-on backend development is Claude Code first.
Claude Code is the reference provider for foreground launch, runtime
monitoring, user interaction, permission approval, interrupt, close, focus, and
device/virtual-input workflows.

Codex support remains in the repository as a parked compatibility and
regression path. It should not be advanced further in this phase, and shared
backend contracts should not be changed merely to preserve feature parity with
Codex. When a provider-specific choice is needed, prefer the Claude Code
behavior unless a later product decision reactivates Codex.

## Implemented

- Local Core Service runs as the software-side state owner for tests and local
  automation.
- Agent launches are workspace-aware. Launch payloads may carry `workspace`,
  the service can be started with `--workspace`, and default project workspace
  resolution uses this priority:
  1. CLI `--workspace`
  2. `AI_KEYB_WORKSPACE`
  3. configured non-dot default workspace
  4. nearest parent project root containing `software`
  5. service start directory
- Local API WebSocket supports `hello`, structured `command`, `snapshot`,
  `event`, and legacy compatibility messages:
  - `agent_launch`
  - `permission_response`
  - `interrupt`
  - `list_sessions`
- Local API security includes launch-token support, origin validation, client
  identity, and capability checks.
- Legacy messages are converted into the same internal command and permission
  paths where practical.
- Runtime state flows through `CommandEnvelope`, async `CommandRouter`,
  `StateStore`, and `EventBus` for structured command/event/snapshot paths.
- Structured agent lifecycle commands cover launch/resume, interrupt, close,
  and permission response handling.
- Unified permission command handling applies client capability checks and
  approval policy gates before provider-native forwarding.
- SQLite is the primary app store for product state and audit metadata.
- JSON import/export remains available for configuration interchange.
- Focus and symbolic target resolution are tracked per device.
- Keyboard bindings resolve active profile/layer input into command envelopes,
  including agent lifecycle, permission, focus, active-tool, and profile
  actions.
- Profile, keymap, lighting, active profile, import/export, and compiled device
  config paths are implemented for the backend model.
- Device backend has simulator transport, virtual input ingress, capability
  negotiation, slot mapping, slot generation mismatch handling, projected device
  snapshots, focus manager, active tool state, config sync, notification queue,
  and profile validation.
- Local hotkey harness exists as a temporary external test input surface for
  real loopback testing. It is not the product device transport and is not the
  firmware protocol contract.
- Local API includes the backend virtual-input path and the smoke script exposes
  the `virtual-input` scenario.
- Diagnostics cover local API, database, device transport, profile validation,
  config sync, redaction, and import-boundary/path guards.

## Agent Adapters and Loopback

### Claude Code

Claude Code has two implemented integration paths:

```text
foreground native CLI + Claude Code hooks
managed Python Agent SDK
```

The foreground native CLI path is the active product-like path. It starts a
visible Claude Code terminal through `scripts/local-agent-cli.py --native-cli`,
registers the session with Local API, and uses `scripts/claude-code-hook.py` to
turn Claude Code hook events into Local API permission and interaction events.

`permission_ack.forwarded=true` for the foreground path is returned only after
the hook process reports `claude_hook_delivered`, meaning the response has been
written back to Claude Code stdout. Evidence includes:

- `adapter: "claude_code_hook"`
- native hook channel
- session and request identifiers
- decision
- `response_written: true`

Current Claude Code foreground smoke shape:

```text
python scripts/local-api-smoke.py --scenario foreground-approval-real --agent claude --decision approve --require-forwarded --workspace <workspace> --auto-start-service --timeout 120 --json-log
python scripts/local-api-smoke.py --scenario foreground-approval-real --agent claude --decision deny    --require-forwarded --workspace <workspace> --auto-start-service --timeout 120 --json-log
```

The managed Agent SDK path remains available for headless service tests and
automation:

```text
python scripts/local-api-smoke.py --scenario approval-real --agent claude --decision approve --require-forwarded --workspace <workspace> --auto-start-service --wait-for-hotkey-approval
python scripts/local-hotkey-harness.py --workspace <workspace> --json-log
```

For the SDK path, `permission_ack.forwarded=true` is returned only after the SDK
permission callback has received and returned the decision. The SDK path
requires the Python Claude Agent SDK dependency and local provider
authentication.

### Codex Parked Compatibility

Codex native approval and foreground CLI support exists, including app-server
JSON-RPC/proxy forwarding and real approve/deny smoke coverage. This path is no
longer a hard acceptance target for the next backend phase.

Codex `exec --json` remains a fallback/legacy read-only path. It does not
support native approval forwarding and must not be used for hard approval
acceptance tests.

Keep Codex tests and compatibility code healthy when touching shared surfaces,
but do not expand Codex behavior, do not require Claude Code to match Codex, and
do not block Claude Code work on Codex parity.

## Permission Semantics

- Real native forwarding is required for Claude Code foreground hook and Claude
  SDK permission requests.
- Codex native forwarding remains required only when the parked Codex
  compatibility path is explicitly exercised.
- Fake and unsupported adapters may return `forwarded=false` only for tests,
  explicit fallback modes, or providers that do not expose a writable native
  permission channel.
- If native forwarding fails for a provider that requires forwarding, the Local
  API returns `PERMISSION_FORWARD_FAILED` and leaves the permission pending.
- Expired parked Codex app-server/proxy permission requests are declined through
  the native JSON-RPC channel so the provider does not wait forever.
- The Local API does not regress a session from a terminal state back to
  `WORKING` if a provider completes immediately after a permission response.

## Persistence

SQLite stores product and audit metadata including:

- profiles
- known devices
- agent instance presets
- sessions
- runs
- permission history
- approval policies
- UI preferences
- schema migration state

Permission history records forwarding outcome and native evidence:

```text
permission_id
session_id
run_id
action_type
risk_level
decision
source_client
timestamp
summary
forwarded
evidence
native
```

Runtime logs, local smoke logs, `data/`, and session scratch files are ignored
by Git.

## Diagnostics and Smoke

The smoke script supports these scenarios:

```text
basic
permission
real-agent
approval-real
foreground-approval-real
foreground-cli
virtual-input
```

The smoke script also supports real loopback controls:

```text
--workspace
--auto-start-service
--config
--service-start-timeout
--wait-for-hotkey-approval
```

Latest full backend/provider-loopback verification:

```text
pytest tests -q -> 404 passed
```

Earlier focused backend virtual-input checks:

```text
pytest tests/architecture/test_import_boundaries.py -q -> 4 passed
pytest tests/bridge/test_virtual_input_local_api.py -q -> 8 passed
```

The active real approval scenario is Claude Code foreground:

```text
python scripts/local-api-smoke.py --scenario foreground-approval-real --agent claude --decision approve --require-forwarded --workspace <workspace> --auto-start-service --timeout 120 --json-log
python scripts/local-api-smoke.py --scenario foreground-approval-real --agent claude --decision deny    --require-forwarded --workspace <workspace> --auto-start-service --timeout 120 --json-log
```

The Claude Code smoke uses a harmless stdout command:

```text
python -c "print('claude approval smoke')"
```

Earlier backend approval work also verified Codex approve/deny loopback with
the local Codex CLI, but that evidence is now regression-only:

- Local API receives `permission_request`
- smoke sends `permission_response`
- adapter writes the native response
- Local API returns `permission_ack.forwarded=true`
- the provider either executes the harmless command after approval or reports
  that it was not run after denial

## Known Gaps

- There is no formal frontend or desktop shell yet.
- The service is still started manually or by scripts.
- Runtime paths now have explicit workspace resolution for agent launches, but
  packaging still needs a product-owned workspace/config selection flow.
- POSIX process-tree cleanup should be revisited before Linux/macOS packaging.
- USB HID, CDC, BLE, and 2.4G hardware transports are not implemented yet.
- Claude Code foreground CLI with hooks is the active hard acceptance provider
  for command approval. The managed SDK path remains a headless support path.
- Codex app-server/proxy support is parked. Codex fallback `exec --json`
  remains non-forwarding.
- Physical keyboard interaction is still represented by simulator/virtual input
  paths in this backend scope. The local hotkey harness is a temporary external
  test input surface, not formal product device transport.
